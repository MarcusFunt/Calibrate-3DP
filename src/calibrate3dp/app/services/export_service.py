"""Reviewed, evidence-scoped export of accepted calibration settings."""

from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
import json
import os
from pathlib import Path, PurePosixPath
import tempfile
from types import MappingProxyType
from typing import Any, Mapping, Sequence

from calibrate3dp.app.models import SessionSnapshot
from calibrate3dp.app.services.acceptance_service import (
    AcceptanceService,
    ConfirmationEvidence,
)
from calibrate3dp.app.services.generation_port import GenerationState, ValidationState
from calibrate3dp.experiments import ExperimentPlan, ExperimentResults
from calibrate3dp.profiles import ProfileDocument, clone_profile_with_patch
from calibrate3dp.storage.session_store import SessionRepository


MODULE_EXPORT_SETTINGS: Mapping[str, tuple[str, ...]] = MappingProxyType(
    {"ironing": ("ironing_flow", "ironing_speed")}
)


class ExportServiceError(ValueError):
    """Raised when an export is not evidence-backed or safely writable."""


@dataclass(frozen=True)
class ExportDraft:
    """Immutable review data and loss-preserving profile payload."""

    session_id: str
    module_id: str
    plan_id: str
    source_profile_name: str
    source_profile_sha256: str
    new_profile_name: str
    selected_candidate_id: str
    setting_changes: Mapping[str, Mapping[str, Any]]
    supporting_run_ids: tuple[str, ...]
    candidate_ids: tuple[str, ...]
    compatibility_warnings: tuple[str, ...]
    report_paths: tuple[str, ...]
    confirmation_status: str
    confirmation_reason: str | None
    orca_version: str | None
    profile_payload: Mapping[str, Any]

    def __post_init__(self) -> None:
        for field_name in (
            "session_id",
            "module_id",
            "plan_id",
            "source_profile_name",
            "source_profile_sha256",
            "new_profile_name",
            "selected_candidate_id",
            "confirmation_status",
        ):
            value = getattr(self, field_name)
            if not isinstance(value, str) or not value.strip():
                raise ExportServiceError(f"{field_name} must be a non-empty string")
        digest = self.source_profile_sha256
        if len(digest) != 64 or any(character not in "0123456789abcdefABCDEF" for character in digest):
            raise ExportServiceError("source_profile_sha256 must be a 64-character SHA-256 digest")
        changes = {
            key: MappingProxyType(_json_mapping(values, f"setting change {key}"))
            for key, values in self.setting_changes.items()
        }
        if any(set(values) != {"old", "new"} for values in changes.values()):
            raise ExportServiceError("setting changes must contain exactly old and new values")
        object.__setattr__(self, "setting_changes", MappingProxyType(changes))
        object.__setattr__(self, "supporting_run_ids", _unique_strings(self.supporting_run_ids, "run ids"))
        object.__setattr__(self, "candidate_ids", _unique_strings(self.candidate_ids, "candidate ids"))
        object.__setattr__(self, "compatibility_warnings", _unique_strings(self.compatibility_warnings, "warnings"))
        object.__setattr__(self, "report_paths", _unique_strings(self.report_paths, "report paths"))
        object.__setattr__(self, "profile_payload", MappingProxyType(_json_mapping(self.profile_payload, "profile payload")))

    @property
    def changed_setting_keys(self) -> tuple[str, ...]:
        return tuple(sorted(self.setting_changes))


@dataclass(frozen=True)
class ExportResult:
    """Paths and content digests for the preset, manifest, and report."""

    profile_path: Path
    manifest_path: Path
    report_path: Path
    sha256s: Mapping[str, str]

    def __post_init__(self) -> None:
        object.__setattr__(self, "profile_path", Path(self.profile_path))
        object.__setattr__(self, "manifest_path", Path(self.manifest_path))
        object.__setattr__(self, "report_path", Path(self.report_path))
        object.__setattr__(self, "sha256s", MappingProxyType(dict(self.sha256s)))


class ExportService:
    """Build an accepted-only process profile and write it to a chosen path."""

    def __init__(
        self,
        repository: SessionRepository,
        *,
        acceptance_service: AcceptanceService | None = None,
        protected_roots: Sequence[str | Path] = (),
        orca_version: str | None = None,
    ) -> None:
        if not isinstance(repository, SessionRepository):
            raise TypeError("repository must be a SessionRepository")
        self.repository = repository
        self.acceptance_service = acceptance_service or AcceptanceService()
        self.protected_roots = tuple(Path(path).expanduser().resolve() for path in protected_roots)
        self.orca_version = orca_version.strip() if isinstance(orca_version, str) and orca_version.strip() else None

    @property
    def bundle_export_available(self) -> bool:
        """Bundle export remains off until a matching Orca version is verified."""
        return False

    def build_draft(self, session: SessionSnapshot, *, new_profile_name: str) -> ExportDraft:
        """Build a reviewable profile copy after checking acceptance evidence."""
        if not isinstance(session, SessionSnapshot):
            raise TypeError("session must be a SessionSnapshot")
        plan = session.plan
        results = session.results
        if plan is None or results is None:
            raise ExportServiceError("an experiment plan and saved results are required for export")
        if results.accepted is not True:
            raise ExportServiceError("accept the selected candidate before exporting a profile")
        if session.module_id != plan.module_id:
            raise ExportServiceError("session and experiment module IDs do not match")
        if plan.module_id not in MODULE_EXPORT_SETTINGS:
            raise ExportServiceError(f"module {plan.module_id!r} has no verified export settings")
        try:
            results.validate_for(plan)
        except (TypeError, ValueError) as exc:
            raise ExportServiceError(f"accepted results do not match their plan: {exc}") from exc
        if results.selected_candidate_id is None:
            raise ExportServiceError("accepted results must identify a selected candidate")

        confirmation, confirmation_plan, opt_out_reason = self._acceptance_evidence(
            session, plan, results
        )
        decision = self.acceptance_service.evaluate(
            plan,
            results,
            confirmation_plan=confirmation_plan,
            confirmation=confirmation,
            confirmation_opt_out_reason=opt_out_reason,
        )
        if not decision.can_accept:
            detail = "; ".join(decision.reasons)
            raise ExportServiceError(
                "export requires a successful validated confirmation or a recorded reasoned opt-out"
                + (f": {detail}" if detail else "")
            )

        source = session.profile_selection.process.profile
        if not isinstance(source, ProfileDocument) or source.kind != "process":
            raise ExportServiceError("the selected process profile is unavailable")
        if not isinstance(new_profile_name, str) or not new_profile_name.strip():
            raise ExportServiceError("new profile name must be a non-empty string")
        if new_profile_name.strip() == source.name:
            raise ExportServiceError("new profile name must differ from the source profile name")

        candidate_settings = plan.settings_for(results.selected_candidate_id)
        effective_source = session.profile_selection.process.settings
        changes: dict[str, dict[str, Any]] = {}
        patch: dict[str, Any] = {}
        for key in MODULE_EXPORT_SETTINGS[plan.module_id]:
            if key not in candidate_settings:
                raise ExportServiceError(f"accepted candidate does not define calibrated setting {key!r}")
            old_value = effective_source.get(key, source.raw.get(key))
            new_value = candidate_settings[key]
            if old_value != new_value:
                changes[key] = {"old": old_value, "new": new_value}
                patch[key] = new_value
        if not patch:
            raise ExportServiceError("the accepted candidate does not change any exportable setting")
        try:
            profile_payload = clone_profile_with_patch(
                source,
                new_name=new_profile_name.strip(),
                patch=patch,
            )
        except ValueError as exc:
            raise ExportServiceError(f"could not build the profile draft: {exc}") from exc

        source_hash = session.profile_selection.source_hashes.get("process")
        if source_hash is None:
            source_hash = _sha256(_json_bytes(dict(source.raw)))
        confirmation_status = "confirmed" if confirmation is not None and decision.can_accept else "opted_out"
        confirmation_reason = opt_out_reason
        supporting_runs = list(session.run_ids)
        if confirmation is not None:
            supporting_runs.append(confirmation.run_id)
        candidate_ids = [item.candidate_id for item in results.assessments]
        if confirmation_plan is not None:
            candidate_ids.extend(item.candidate_id for item in confirmation_plan.candidates)
        return ExportDraft(
            session_id=session.session_id,
            module_id=plan.module_id,
            plan_id=plan.plan_id,
            source_profile_name=source.name,
            source_profile_sha256=source_hash,
            new_profile_name=new_profile_name.strip(),
            selected_candidate_id=results.selected_candidate_id,
            setting_changes=changes,
            supporting_run_ids=tuple(supporting_runs),
            candidate_ids=tuple(candidate_ids),
            compatibility_warnings=session.profile_selection.compatibility_warnings,
            report_paths=_report_paths(session.artifact_paths),
            confirmation_status=confirmation_status,
            confirmation_reason=confirmation_reason,
            orca_version=self.orca_version,
            profile_payload=profile_payload,
        )

    def export(self, draft: ExportDraft, destination: str | Path) -> ExportResult:
        """Write three review artifacts to an explicitly selected JSON path."""
        if not isinstance(draft, ExportDraft):
            raise TypeError("draft must be an ExportDraft")
        if not isinstance(destination, (str, Path)) or not str(destination).strip():
            raise ExportServiceError("choose a destination file before exporting")
        target = Path(destination).expanduser().resolve()
        if target.suffix.lower() != ".json":
            raise ExportServiceError("choose a .json destination for the new process preset")
        if not target.parent.is_dir():
            raise ExportServiceError("the selected destination directory does not exist")
        manifest_path = target.with_name(f"{target.stem}.manifest.json")
        report_path = target.with_name(f"{target.stem}.report.md")
        destinations = (target, manifest_path, report_path)
        if any(path.exists() for path in destinations):
            raise ExportServiceError("an export file already exists at the selected destination")
        self._ensure_safe_destination(target, draft)

        profile_bytes = _json_bytes(dict(draft.profile_payload))
        report_bytes = _report_markdown(draft).encode("utf-8")
        manifest = _manifest(draft, _sha256(profile_bytes), _sha256(report_bytes))
        manifest_bytes = _json_bytes(manifest)
        payloads = (profile_bytes, manifest_bytes, report_bytes)
        temporary: list[Path] = []
        written: list[Path] = []
        try:
            for destination_path, payload in zip(destinations, payloads):
                with tempfile.NamedTemporaryFile(
                    mode="wb", prefix=".calibrate3dp-", suffix=".tmp",
                    dir=target.parent, delete=False,
                ) as stream:
                    temporary_path = Path(stream.name)
                    temporary.append(temporary_path)
                    stream.write(payload)
                    stream.flush()
                    os.fsync(stream.fileno())
            for temporary_path, destination_path in zip(temporary, destinations):
                os.replace(temporary_path, destination_path)
                written.append(destination_path)
        except OSError as exc:
            for path in (*temporary, *written):
                try:
                    path.unlink(missing_ok=True)
                except OSError:
                    pass
            raise ExportServiceError(f"could not write export files: {exc}") from exc
        return ExportResult(
            profile_path=target,
            manifest_path=manifest_path,
            report_path=report_path,
            sha256s={
                "profile": _sha256(profile_bytes),
                "manifest": _sha256(manifest_bytes),
                "report": _sha256(report_bytes),
            },
        )

    def export_bundle(self, draft: ExportDraft, destination: str | Path) -> ExportResult:
        """Reject bundle creation unless a versioned adapter has been verified."""
        del draft, destination
        if not self.bundle_export_available:
            raise ExportServiceError(
                "import-bundle export is disabled until the adapter passes the Orca integration suite"
            )
        raise ExportServiceError("no verified bundle writer is configured")

    def _acceptance_evidence(
        self,
        session: SessionSnapshot,
        plan: ExperimentPlan,
        results: ExperimentResults,
    ) -> tuple[ConfirmationEvidence | None, ExperimentPlan | None, str | None]:
        if results.selected_candidate_id is None:
            return None, None, None
        for artifact_path in reversed(session.artifact_paths):
            if artifact_path not in _report_paths(session.artifact_paths):
                continue
            try:
                report = self.repository.read_json_artifact(session.session_id, artifact_path)
            except Exception as exc:
                raise ExportServiceError(f"could not read saved recommendation evidence: {exc}") from exc
            if not isinstance(report, dict) or report.get("plan_id") != plan.plan_id:
                continue
            events = report.get("events", [])
            if not isinstance(events, list):
                continue
            for event in reversed(events):
                if not isinstance(event, dict):
                    continue
                if event.get("source_plan_id", event.get("plan_id")) != plan.plan_id:
                    continue
                if event.get("source_candidate_id", event.get("candidate_id")) != results.selected_candidate_id:
                    continue
                if event.get("type") == "confirmation_opt_out":
                    return None, None, event.get("reason")
                if event.get("type") != "confirmation_run":
                    continue
                try:
                    confirmation_plan = self.acceptance_service.create_confirmation_plan(
                        plan, results, plan_id=event["plan_id"]
                    )
                    confirmation = ConfirmationEvidence(
                        run_id=event["run_id"],
                        plan_id=event["plan_id"],
                        candidate_id=event["candidate_id"],
                        state=GenerationState(event["state"]),
                        validation_state=ValidationState(event["validation_state"]),
                        settings=event["settings"],
                    )
                except (KeyError, TypeError, ValueError) as exc:
                    raise ExportServiceError(f"saved confirmation evidence is invalid: {exc}") from exc
                return confirmation, confirmation_plan, None
        return None, None, None

    def _ensure_safe_destination(self, target: Path, draft: ExportDraft) -> None:
        destinations = (
            target,
            target.with_name(f"{target.stem}.manifest.json"),
            target.with_name(f"{target.stem}.report.md"),
        )
        for destination in destinations:
            for root in self.protected_roots:
                if _is_within(destination, root):
                    raise ExportServiceError("exports cannot be written inside an Orca profile/config root")
            source_path = self._source_file_for(draft)
            if source_path is not None and destination == source_path:
                raise ExportServiceError("an export cannot overwrite its source profile")

    def _source_file_for(self, draft: ExportDraft) -> Path | None:
        try:
            session = self.repository.load(draft.session_id)
        except Exception:
            return None
        source = session.profile_selection.process.profile.source
        if "!" in source:
            return None
        try:
            return Path(source).expanduser().resolve()
        except OSError:
            return None


def _manifest(draft: ExportDraft, profile_hash: str, report_hash: str) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "session_id": draft.session_id,
        "module_id": draft.module_id,
        "plan_id": draft.plan_id,
        "source_profile": {
            "name": draft.source_profile_name,
            "sha256": draft.source_profile_sha256,
        },
        "output_profile": {"name": draft.new_profile_name, "sha256": profile_hash},
        "accepted_candidate_id": draft.selected_candidate_id,
        "candidate_ids": list(draft.candidate_ids),
        "supporting_run_ids": list(draft.supporting_run_ids),
        "changes": {key: dict(values) for key, values in draft.setting_changes.items()},
        "confirmation_status": draft.confirmation_status,
        "confirmation_reason": draft.confirmation_reason,
        "orca_version": draft.orca_version,
        "compatibility_warnings": list(draft.compatibility_warnings),
        "report_paths": list(draft.report_paths),
        "report_sha256": report_hash,
        "evidence_scope": {
            "result_source": "manual candidate assessments",
            "accepted_candidate_id": draft.selected_candidate_id,
            "confirmation_status": draft.confirmation_status,
            "supporting_run_ids": list(draft.supporting_run_ids),
            "report_paths": list(draft.report_paths),
        },
        "import_behavior": "manual import only; this application does not import or activate the preset",
    }


def _report_markdown(draft: ExportDraft) -> str:
    rows = [
        "# Calibration profile export",
        "",
        f"- Session: `{draft.session_id}`",
        f"- Module: `{draft.module_id}`",
        f"- Plan: `{draft.plan_id}`",
        f"- Accepted candidate: `{draft.selected_candidate_id}`",
        f"- Source profile: `{draft.source_profile_name}` (`sha256: {draft.source_profile_sha256}`)",
        f"- New profile: `{draft.new_profile_name}`",
        f"- Confirmation: `{draft.confirmation_status}`",
        "",
        "## Setting changes",
        "",
        "| Setting | Source value | Accepted value |",
        "| --- | --- | --- |",
    ]
    for key, values in sorted(draft.setting_changes.items()):
        rows.append(f"| `{key}` | `{_markdown_value(values['old'])}` | `{_markdown_value(values['new'])}` |")
    rows.extend(("", "## Evidence scope", "", "- Candidate IDs: " + ", ".join(f"`{item}`" for item in draft.candidate_ids)))
    rows.append("- Run IDs: " + (", ".join(f"`{item}`" for item in draft.supporting_run_ids) or "none recorded"))
    rows.append("- Recommendation reports: " + (", ".join(f"`{item}`" for item in draft.report_paths) or "none"))
    if draft.confirmation_reason:
        rows.extend(("", "Confirmation opt-out reason: " + draft.confirmation_reason))
    if draft.compatibility_warnings:
        rows.extend(("", "## Compatibility warnings", ""))
        rows.extend(f"- {warning}" for warning in draft.compatibility_warnings)
    rows.extend(("", "## Import instructions", ""))
    if draft.orca_version:
        rows.append(
            f"In OrcaSlicer `{draft.orca_version}`, choose `File` → `Import` → "
            "`Import Configs...` and select this JSON. Then review the imported values "
            "in the process preset list before selecting the preset. Standalone JSON "
            "import is not integration-tested for this detected version."
        )
    else:
        rows.append(
            "OrcaSlicer was not detected, so the version-specific import path is "
            "unverified. Confirm the installed version's process-preset import steps "
            "before using this JSON."
        )
    rows.append("This application does not import or activate the preset.")
    return "\n".join(rows) + "\n"


def _report_paths(paths: Sequence[str]) -> tuple[str, ...]:
    reports = []
    for path in paths:
        if not isinstance(path, str) or not path.startswith("reports/"):
            continue
        parsed = PurePosixPath(path)
        if parsed.is_absolute() or ".." in parsed.parts:
            continue
        reports.append(parsed.as_posix())
    return tuple(dict.fromkeys(reports))


def _is_within(path: Path, root: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
        return True
    except ValueError:
        return False


def _json_mapping(value: Mapping[str, Any], name: str) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise ExportServiceError(f"{name} must be a JSON object")
    try:
        copied = json.loads(json.dumps(dict(value), ensure_ascii=False, allow_nan=False))
    except (TypeError, ValueError, OverflowError) as exc:
        raise ExportServiceError(f"{name} must contain finite JSON values: {exc}") from exc
    if not isinstance(copied, dict):
        raise ExportServiceError(f"{name} must be a JSON object")
    return copied


def _unique_strings(values: Sequence[str], name: str) -> tuple[str, ...]:
    if isinstance(values, (str, bytes)):
        raise ExportServiceError(f"{name} must be a sequence of strings")
    items = tuple(values)
    if any(not isinstance(item, str) or not item.strip() for item in items):
        raise ExportServiceError(f"{name} must contain non-empty strings")
    return tuple(dict.fromkeys(items))


def _json_bytes(value: Any) -> bytes:
    try:
        return (json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n").encode("utf-8")
    except (TypeError, ValueError, OverflowError) as exc:
        raise ExportServiceError(f"export values must be finite JSON values: {exc}") from exc


def _sha256(payload: bytes) -> str:
    return sha256(payload).hexdigest()


def _markdown_value(value: Any) -> str:
    if value is None:
        return "not set"
    if isinstance(value, (dict, list, tuple)):
        return json.dumps(value, ensure_ascii=False, sort_keys=True)
    return str(value).replace("`", "\\`").replace("|", "\\|")
