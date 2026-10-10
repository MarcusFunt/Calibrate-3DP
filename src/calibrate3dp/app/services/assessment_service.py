"""Persistent manual assessment workflow for immutable grouped runs."""

from __future__ import annotations

from dataclasses import replace
import hashlib
from pathlib import Path, PurePosixPath
import shutil
from typing import Mapping, Sequence
from uuid import uuid4

from calibrate3dp.domain.assessment import AssessmentRevision, PrintAttestation, utc_now
from calibrate3dp.domain.records import ArtifactRecord
from calibrate3dp.experiments import CandidateAssessment, ExperimentResults
from calibrate3dp.storage.library_store import LibraryRepository, LibraryStoreError, MissingRunArtifactError


_PHOTO_SIGNATURES = {
    ".jpg": ("image/jpeg", lambda data: data.startswith(b"\xff\xd8\xff")),
    ".jpeg": ("image/jpeg", lambda data: data.startswith(b"\xff\xd8\xff")),
    ".png": ("image/png", lambda data: data.startswith(b"\x89PNG\r\n\x1a\n")),
    ".webp": ("image/webp", lambda data: len(data) >= 12 and data[:4] == b"RIFF" and data[8:12] == b"WEBP"),
}
_MAX_PHOTO_BYTES = 25 * 1024 * 1024


class AssessmentServiceError(ValueError):
    """An assessment cannot be safely attached to this run."""


class AssessmentService:
    """Validate human-entered outcomes and persist immutable revisions."""

    def __init__(self, repository: LibraryRepository) -> None:
        if not isinstance(repository, LibraryRepository):
            raise TypeError("repository must be a LibraryRepository")
        self.repository = repository

    def empty_draft(self, run_id: str) -> ExperimentResults:
        run = self.repository.get_run(run_id)
        return ExperimentResults(
            run.plan.plan_id,
            tuple(CandidateAssessment(candidate.candidate_id) for candidate in run.plan.candidates),
        )

    def save(
        self,
        run_id: str,
        *,
        expected_revision: int,
        results: ExperimentResults,
        attestation: PrintAttestation,
        photo_sources_by_candidate: Mapping[str, Sequence[str | Path]] | None = None,
    ) -> AssessmentRevision:
        run = self.repository.get_run(run_id)
        if not isinstance(results, ExperimentResults):
            raise TypeError("results must be ExperimentResults")
        if not isinstance(attestation, PrintAttestation):
            raise TypeError("attestation must be PrintAttestation")
        try:
            results.validate_for(run.plan)
        except (TypeError, ValueError) as exc:
            raise AssessmentServiceError(f"results do not match this run: {exc}") from exc
        if results.plan_id != run.plan.plan_id:
            raise AssessmentServiceError("assessment plan does not match the saved run")
        latest = self.repository.latest_assessment_revision(run_id)
        current_revision = latest.revision_no if latest else 0
        if current_revision != expected_revision:
            from calibrate3dp.storage.library_store import AssessmentRevisionConflictError
            raise AssessmentRevisionConflictError(
                f"assessment changed while editing (expected revision {expected_revision}, current {current_revision})"
            )
        revision_no = expected_revision + 1
        previous_photos = {
            artifact.relative_path: artifact
            for artifact in (latest.photo_artifacts if latest else ())
        }
        previous_photo_owners = {
            item.candidate_id: set(item.photo_paths)
            for item in (latest.results.assessments if latest else ())
        }
        photo_sources_by_candidate = photo_sources_by_candidate or {}
        known = {candidate.candidate_id for candidate in run.plan.candidates}
        if set(photo_sources_by_candidate) - known:
            raise AssessmentServiceError("photo attachments reference an unknown candidate")
        created_paths: list[Path] = []
        artifact_by_path: dict[str, ArtifactRecord] = {}
        updated_assessments: list[CandidateAssessment] = []
        try:
            for assessment in results.assessments:
                candidate_id = assessment.candidate_id
                paths: list[str] = []
                for prior_path in assessment.photo_paths:
                    prior = previous_photos.get(prior_path)
                    if prior is None or prior_path not in previous_photo_owners.get(candidate_id, set()):
                        raise AssessmentServiceError(
                            "photo references must be copied into the run before they can be saved"
                        )
                    self.repository._verify_assessment_photo(run_id, prior)
                    artifact_by_path[prior.relative_path] = prior
                    paths.append(prior.relative_path)
                for source in photo_sources_by_candidate.get(candidate_id, ()):
                    artifact = self._copy_photo(run_id, revision_no, Path(source), created_paths)
                    artifact_by_path[artifact.relative_path] = artifact
                    paths.append(artifact.relative_path)
                if len(paths) != len(set(paths)):
                    raise AssessmentServiceError(f"candidate {candidate_id} has duplicate photo attachments")
                updated_assessments.append(replace(assessment, photo_paths=tuple(paths)))
            saved_results = replace(results, assessments=tuple(updated_assessments))
            revision = AssessmentRevision.create(
                assessment_revision_id=f"assessment-{uuid4().hex}",
                run_id=run_id,
                revision_no=revision_no,
                created_at_utc=utc_now(),
                results=saved_results,
                attestation=attestation,
                photo_artifacts=tuple(artifact_by_path[key] for key in sorted(artifact_by_path)),
            )
            return self.repository.save_assessment_revision(revision, expected_revision=expected_revision)
        except Exception:
            for path in reversed(created_paths):
                try:
                    relative = path.relative_to(self.repository.root.resolve()).as_posix()
                    self.repository._safe_photo_path(run_id, relative).unlink(missing_ok=True)
                except (LibraryStoreError, OSError, ValueError):
                    pass
            for directory in {path.parent for path in created_paths}:
                try:
                    relative = directory.relative_to(self.repository.root.resolve()).as_posix()
                    safe_directory = self.repository._safe_photo_path(run_id, relative)
                    safe_directory.rmdir()
                except (LibraryStoreError, OSError, ValueError):
                    pass
            raise

    def load_latest(self, run_id: str) -> AssessmentRevision | None:
        return self.repository.latest_assessment_revision(run_id)

    def load(self, assessment_revision_id: str) -> AssessmentRevision:
        return self.repository.get_assessment_revision(assessment_revision_id)

    def photo_path(self, revision: AssessmentRevision, relative_path: str) -> Path:
        if not isinstance(revision, AssessmentRevision):
            raise TypeError("revision must be an AssessmentRevision")
        artifact = next((item for item in revision.photo_artifacts if item.relative_path == relative_path), None)
        if artifact is None:
            raise AssessmentServiceError("photo is not part of this assessment revision")
        return self.repository._verify_assessment_photo(revision.run_id, artifact)

    def _copy_photo(
        self, run_id: str, revision_no: int, source: Path, created_paths: list[Path]
    ) -> ArtifactRecord:
        if source.is_symlink() or not source.is_file():
            raise AssessmentServiceError("photo must be a regular file, not a symlink")
        suffix = source.suffix.lower()
        signature = _PHOTO_SIGNATURES.get(suffix)
        if signature is None:
            raise AssessmentServiceError("photo must be a JPEG, PNG, or WebP image")
        if source.stat().st_size > _MAX_PHOTO_BYTES:
            raise AssessmentServiceError("photo exceeds the 25 MiB evidence limit")
        data = source.read_bytes()
        media_type, valid = signature
        if not valid(data):
            raise AssessmentServiceError("photo file extension does not match supported image content")
        relative = PurePosixPath("runs") / run_id / "evidence" / "assessments" / f"revision-{revision_no}-{uuid4().hex}" / f"{uuid4().hex}{suffix}"
        try:
            path = self.repository._safe_photo_path(run_id, relative.as_posix())
        except LibraryStoreError as exc:
            raise AssessmentServiceError(f"photo evidence directory is unsafe: {exc}") from exc
        path.parent.mkdir(parents=True, exist_ok=False)
        try:
            path = self.repository._safe_photo_path(run_id, relative.as_posix())
            with path.open("xb") as stream:
                stream.write(data)
        except Exception:
            try:
                safe_parent = self.repository._safe_photo_path(run_id, relative.parent.as_posix())
                shutil.rmtree(safe_parent, ignore_errors=True)
            except LibraryStoreError:
                pass
            raise
        created_paths.append(path)
        return ArtifactRecord(relative.as_posix(), media_type, len(data), hashlib.sha256(data).hexdigest())
