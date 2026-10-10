"""Reviewed process-profile export from immutable grouped-run evidence."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
from typing import Any
from uuid import uuid4

from calibrate3dp.app.services.acceptance_service import AcceptanceService
from calibrate3dp.app.services.export_service import ExportDraft, ExportResult, ExportService, ExportServiceError, StaleExportDraftError, _report_paths
from calibrate3dp.app.services.run_decision_service import RunDecisionService
from calibrate3dp.domain.run_export import RunExportRecord
from calibrate3dp.profiles import clone_profile_with_patch
from calibrate3dp.storage.library_store import LibraryRepository


class RunExportServiceError(ValueError):
    """Saved run evidence cannot support a reviewed Orca process export."""


@dataclass(frozen=True)
class RunExportDraft:
    run_id: str
    assessment_revision_id: str
    decision_id: str
    new_profile_name: str
    export_draft: ExportDraft
    draft_sha256: str


class RunExportService:
    """Build a current-context review and write a standalone process JSON copy."""

    def __init__(self, library, *, acceptance: AcceptanceService | None = None) -> None:
        self.library = library
        self.repository: LibraryRepository = library.repository
        self.acceptance = acceptance or AcceptanceService()
        profiles = library.profiles
        self.writer = ExportService(
            self.repository._sessions,
            acceptance_service=self.acceptance,
            protected_roots_provider=lambda: profiles.setup_state.config_roots,
            orca_version_provider=lambda: profiles.setup_state.version_banner,
        )

    def build_review(self, run_id: str, decision_id: str, *, new_profile_name: str) -> RunExportDraft:
        run = self.repository.get_run(run_id)
        decision = self.repository.get_run_decision(decision_id)
        if decision.run_id != run.run_id:
            raise RunExportServiceError("decision belongs to a different run")
        if not decision.can_accept or decision.action != "accept" or not decision.selected_candidate_id:
            raise RunExportServiceError("export requires a saved accepted decision")
        assessment = self.repository.get_assessment_revision(decision.assessment_revision_id)
        if assessment.run_id != run.run_id or assessment.assessment_revision_id != decision.assessment_revision_id:
            raise RunExportServiceError("decision is not tied to this exact assessment revision")
        if self.repository.latest_assessment_revision(run_id).assessment_revision_id != assessment.assessment_revision_id:
            raise StaleExportDraftError("a newer assessment revision exists; evaluate it before exporting")
        if not assessment.attestation.physically_accepted:
            raise RunExportServiceError("physical print and review attestation is required for export")
        if run.status != "settings_validated" or run.validation.get("state") != "sample_settings_validated":
            raise RunExportServiceError("run did not pass grouped sample-settings validation")
        if run.validation.get("print_ready") is True:
            # This pass has no complete support-matrix and physical safety qualification authority.
            raise RunExportServiceError("run readiness state is inconsistent with the current fail-closed policy")
        if not run.artifacts:
            raise RunExportServiceError("run has no finalized generation artifacts")
        for artifact in run.artifacts:
            self.repository.artifact_path(artifact)
        config = self.repository.get_configuration_for_run(run_id)
        if config is None:
            raise RunExportServiceError("run has no immutable configuration snapshot")
        current_hashes = self.library.current_source_hashes(run.profiles)
        expected_hashes = dict(run.profiles.source_hashes)
        stale_roles = [role for role in ("printer", "process", "filament") if current_hashes.get(role) != expected_hashes.get(role)]
        if stale_roles:
            raise StaleExportDraftError("source profile changed or is unavailable since generation: " + ", ".join(stale_roles))
        results = assessment.results
        results.validate_for(run.plan)
        if results.selected_candidate_id != decision.selected_candidate_id or results.tied_candidate_ids:
            raise RunExportServiceError("assessment selection changed or still contains unresolved ties")
        if set(item.candidate_id for item in results.assessments) != set(item.candidate_id for item in run.plan.candidates):
            raise RunExportServiceError("every candidate needs a saved manual verdict before export")
        by_id = {item.candidate_id: item for item in results.assessments}
        if any(by_id[item.candidate_id].verdict not in {"pass", "fail"} for item in run.plan.candidates):
            raise RunExportServiceError("uncertain, missing, or unreviewed outcomes block export")
        if by_id[decision.selected_candidate_id].verdict != "pass":
            raise RunExportServiceError("selected candidate must have a pass verdict")

        confirmation, confirmation_plan = RunDecisionService(self.repository, acceptance=self.acceptance)._successful_confirmation(run, assessment)
        opt_out = decision.opt_out_record
        opt_out_reason = opt_out.get("reason") if isinstance(opt_out, dict) else None
        policy = self.acceptance.evaluate(
            run.plan, results,
            confirmation_plan=confirmation_plan,
            confirmation=confirmation,
            confirmation_opt_out_reason=opt_out_reason,
        )
        if not policy.can_accept:
            raise RunExportServiceError("current confirmation evidence no longer qualifies: " + "; ".join(policy.reasons))
        if policy.can_accept != decision.can_accept:
            raise StaleExportDraftError("saved decision no longer matches current evidence")

        selection = run.profiles
        source = selection.process.profile
        source_hash = expected_hashes.get("process")
        if source_hash is None:
            raise RunExportServiceError("saved process profile has no source fingerprint")
        selected_settings = run.plan.settings_for(decision.selected_candidate_id)
        allowed = ("ironing_flow", "ironing_speed")
        patch: dict[str, Any] = {}
        changes: dict[str, dict[str, Any]] = {}
        setting_provenance: dict[str, str] = {}
        for key in allowed:
            if key not in selected_settings:
                raise RunExportServiceError(f"selected candidate is missing calibrated setting {key!r}")
            old = selection.process.settings.get(key, source.raw.get(key))
            new = selected_settings[key]
            if old != new:
                patch[key] = new
                changes[key] = {"old": old, "new": new}
                provenance = selection.process.provenance.get(key)
                setting_provenance[key] = provenance.name if provenance is not None else source.name
        if not patch:
            raise RunExportServiceError("selected candidate has no change to the supported process settings")
        try:
            profile_payload = clone_profile_with_patch(source, new_name=new_profile_name.strip(), patch=patch)
        except (AttributeError, TypeError, ValueError) as exc:
            raise RunExportServiceError(f"could not build a standalone process profile: {exc}") from exc
        link = self.repository.get_run_config_link(run_id) or {}
        confirmation_status = "opted_out" if opt_out else "confirmed"
        confirmation_reason = opt_out_reason
        supporting_runs = [run_id]
        if confirmation is not None:
            supporting_runs.append(confirmation.run_id)
        warnings = [
            "Print-ready remains false; this export does not establish machine, temperature, movement, physical-handling, or support-matrix safety.",
            f"Orca setup identity: {self.library.profiles.setup_state.version_banner or 'version not resolved'}; standalone JSON import is not qualified by this export.",
        ]
        if opt_out:
            warnings.append("Confirmation was explicitly skipped; this opt-out is not a physical-print or slicer-safety qualification.")
        compatibility = tuple(dict.fromkeys((*selection.compatibility_warnings, *warnings)))
        draft = ExportDraft(
            session_id=run_id,
            module_id=run.plan.module_id,
            plan_id=run.plan.plan_id,
            source_profile_name=source.name,
            source_profile_sha256=source_hash,
            new_profile_name=new_profile_name.strip(),
            selected_candidate_id=decision.selected_candidate_id,
            setting_changes=changes,
            supporting_run_ids=tuple(supporting_runs),
            candidate_ids=tuple(item.candidate_id for item in results.assessments),
            compatibility_warnings=compatibility,
            report_paths=_report_paths(tuple(item.relative_path for item in run.artifacts)),
            confirmation_status=confirmation_status,
            confirmation_reason=confirmation_reason,
            orca_version=self.library.profiles.setup_state.version_banner,
            profile_payload=profile_payload,
            source_profile_path=source.source,
            run_id=run.run_id,
            assessment_revision_id=assessment.assessment_revision_id,
            decision_id=decision.decision_id,
            safety_warnings=tuple(warnings),
            evidence_paths=tuple(item.relative_path for item in run.artifacts) + tuple(
                item.relative_path for item in assessment.photo_artifacts
            ),
            orca_setup_sha256=_setup_hash(
                self.library.profiles.setup_state.version_banner,
                self.library.profiles.setup_state.config_roots,
                self.library.profiles.setup_state.executable,
            ),
            setting_provenance=setting_provenance,
        )
        digest = _draft_hash(draft)
        return RunExportDraft(run_id, assessment.assessment_revision_id, decision_id, new_profile_name.strip(), draft, digest)

    def write_reviewed(self, review: RunExportDraft, destination: str | Path) -> tuple[RunExportRecord, ExportResult]:
        if not isinstance(review, RunExportDraft):
            raise TypeError("review must be a RunExportDraft")
        current = self.build_review(review.run_id, review.decision_id, new_profile_name=review.new_profile_name)
        if current.draft_sha256 != review.draft_sha256:
            raise StaleExportDraftError("run evidence, profile context, or export values changed after review")
        if _draft_hash(review.export_draft) != review.draft_sha256:
            raise StaleExportDraftError("reviewed export payload changed after review")
        result = self.writer.export(current.export_draft, destination)
        now = datetime.now(timezone.utc).isoformat(timespec="microseconds").replace("+00:00", "Z")
        decision = self.repository.get_run_decision(review.decision_id)
        record = RunExportRecord.create(
            export_id=f"export-{uuid4().hex}",
            run_id=review.run_id,
            assessment_revision_id=review.assessment_revision_id,
            decision_id=review.decision_id,
            created_at_utc=now,
            destination_profile=str(result.profile_path.resolve()),
            manifest_path=str(result.manifest_path.resolve()),
            report_path=str(result.report_path.resolve()),
            new_profile_name=review.new_profile_name,
            source_profile_sha256=current.export_draft.source_profile_sha256,
            profile_sha256=result.sha256s["profile"],
            manifest_sha256=result.sha256s["manifest"],
            report_sha256=result.sha256s["report"],
            draft_sha256=review.draft_sha256,
            confirmation_status="opted_out" if decision.opt_out_record else "confirmed",
        )
        try:
            self.repository.save_run_export(record)
        except Exception:
            for path in (result.profile_path, result.manifest_path, result.report_path):
                path.unlink(missing_ok=True)
            raise
        return record, result

    def verify_export(self, record: RunExportRecord) -> bool:
        for filename, digest in (
            (record.destination_profile, record.profile_sha256),
            (record.manifest_path, record.manifest_sha256),
            (record.report_path, record.report_sha256),
        ):
            path = Path(filename)
            if not path.is_file() or _sha256(path.read_bytes()) != digest:
                return False
        return True


def _draft_hash(draft: ExportDraft) -> str:
    payload = {
        "session_id": draft.session_id,
        "run_id": draft.run_id,
        "assessment_revision_id": draft.assessment_revision_id,
        "decision_id": draft.decision_id,
        "module_id": draft.module_id,
        "plan_id": draft.plan_id,
        "source_profile_name": draft.source_profile_name,
        "source_profile_sha256": draft.source_profile_sha256,
        "source_profile_path": draft.source_profile_path,
        "new_profile_name": draft.new_profile_name,
        "selected_candidate_id": draft.selected_candidate_id,
        "setting_changes": {key: dict(value) for key, value in draft.setting_changes.items()},
        "supporting_run_ids": draft.supporting_run_ids,
        "candidate_ids": draft.candidate_ids,
        "compatibility_warnings": draft.compatibility_warnings,
        "report_paths": draft.report_paths,
        "safety_warnings": draft.safety_warnings,
        "evidence_paths": draft.evidence_paths,
        "confirmation_status": draft.confirmation_status,
        "confirmation_reason": draft.confirmation_reason,
        "orca_version": draft.orca_version,
        "orca_setup_sha256": draft.orca_setup_sha256,
        "setting_provenance": dict(draft.setting_provenance),
        "profile_payload": dict(draft.profile_payload),
    }
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _setup_hash(version: str | None, roots, executable: str | Path | None = None) -> str:
    executable_path = Path(executable).expanduser().resolve(strict=False) if executable else None
    payload = {
        "version": version,
        "config_roots": sorted(str(Path(root).expanduser().resolve()) for root in roots),
        "executable": str(executable_path) if executable_path is not None else None,
        "executable_sha256": _sha256_file(executable_path) if executable_path is not None else None,
    }
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _sha256_file(path: Path) -> str | None:
    if not path.is_file():
        return None
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()
