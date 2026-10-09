"""UI-facing session models, independent of the desktop framework."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
import json
from types import MappingProxyType
from typing import Any, Mapping

from calibrate3dp.experiments import ExperimentPlan, ExperimentResults
from calibrate3dp.profiles import ProfileDocument, ResolvedProfile


PROFILE_ROLES = ("printer", "filament", "process")
SESSION_SCHEMA_VERSION = 1


class SessionModelError(ValueError):
    """Raised when a saved session model is incomplete or internally invalid."""


def _non_empty_string(value: Any, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise SessionModelError(f"{name} must be a non-empty string")
    return value


def _json_copy(value: Any, name: str) -> Any:
    try:
        return json.loads(json.dumps(value, ensure_ascii=False, allow_nan=False))
    except (TypeError, ValueError, OverflowError) as exc:
        raise SessionModelError(f"{name} must contain finite JSON values: {exc}") from exc


def _required_object(value: Any, required: set[str], name: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise SessionModelError(f"{name} must be an object")
    actual = set(value)
    missing = required - actual
    extra = actual - required
    if missing or extra:
        details = []
        if missing:
            details.append("missing " + ", ".join(sorted(missing)))
        if extra:
            details.append("unexpected " + ", ".join(sorted(extra)))
        raise SessionModelError(f"{name} has invalid fields: {'; '.join(details)}")
    return value


def _document_to_dict(document: ProfileDocument) -> dict[str, Any]:
    return {
        "name": document.name,
        "kind": document.kind,
        "scope": document.scope,
        "source": document.source,
        "raw": _json_copy(dict(document.raw), "profile raw data"),
    }


def _document_from_dict(payload: Any) -> ProfileDocument:
    data = _required_object(payload, {"name", "kind", "scope", "source", "raw"}, "profile document")
    return ProfileDocument(
        name=data["name"],
        kind=data["kind"],
        scope=data["scope"],
        source=data["source"],
        raw=data["raw"],
    )


def _resolved_profile_to_dict(profile: ResolvedProfile) -> dict[str, Any]:
    return {
        "profile": _document_to_dict(profile.profile),
        "settings": _json_copy(dict(profile.settings), "resolved profile settings"),
        "provenance": {
            key: _document_to_dict(document)
            for key, document in profile.provenance.items()
        },
        "chain": [_document_to_dict(document) for document in profile.chain],
    }


def _resolved_profile_from_dict(payload: Any) -> ResolvedProfile:
    data = _required_object(payload, {"profile", "settings", "provenance", "chain"}, "resolved profile")
    if not isinstance(data["settings"], Mapping):
        raise SessionModelError("resolved profile settings must be an object")
    if not isinstance(data["provenance"], Mapping):
        raise SessionModelError("resolved profile provenance must be an object")
    if isinstance(data["chain"], (str, bytes)) or not isinstance(data["chain"], (list, tuple)):
        raise SessionModelError("resolved profile chain must be a sequence")
    return ResolvedProfile(
        profile=_document_from_dict(data["profile"]),
        settings=_json_copy(dict(data["settings"]), "resolved profile settings"),
        provenance={
            _non_empty_string(key, "provenance setting"):
            _document_from_dict(document)
            for key, document in data["provenance"].items()
        },
        chain=tuple(_document_from_dict(document) for document in data["chain"]),
    )


@dataclass(frozen=True)
class ProfileSelection:
    """Exactly one selected printer, filament, and process baseline."""

    printer: ResolvedProfile
    filament: ResolvedProfile
    process: ResolvedProfile
    source_paths: Mapping[str, str] = field(default_factory=dict)
    source_hashes: Mapping[str, str] = field(default_factory=dict)
    compatibility_warnings: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        profiles = {
            "printer": self.printer,
            "filament": self.filament,
            "process": self.process,
        }
        for role, profile in profiles.items():
            if not isinstance(profile, ResolvedProfile):
                raise SessionModelError(f"{role} must be a ResolvedProfile")

        paths = dict(self.source_paths)
        if not paths:
            paths = {role: profile.profile.source for role, profile in profiles.items()}
        if set(paths) != set(PROFILE_ROLES):
            raise SessionModelError("source paths must contain printer, filament, and process")
        for role, path in paths.items():
            _non_empty_string(path, f"{role} source path")

        hashes = dict(self.source_hashes)
        if hashes and set(hashes) != set(PROFILE_ROLES):
            raise SessionModelError("source hashes must contain printer, filament, and process")
        for role, digest in hashes.items():
            _non_empty_string(digest, f"{role} source hash")

        if isinstance(self.compatibility_warnings, (str, bytes)):
            raise SessionModelError("compatibility warnings must be a sequence of strings")
        warnings = tuple(self.compatibility_warnings)
        for warning in warnings:
            _non_empty_string(warning, "compatibility warning")

        object.__setattr__(self, "source_paths", MappingProxyType(paths))
        object.__setattr__(self, "source_hashes", MappingProxyType(hashes))
        object.__setattr__(self, "compatibility_warnings", warnings)

    def with_source_hashes(self, hashes: Mapping[str, str]) -> "ProfileSelection":
        """Return this selection with one fingerprint for each selected profile."""
        return ProfileSelection(
            printer=self.printer,
            filament=self.filament,
            process=self.process,
            source_paths=self.source_paths,
            source_hashes=hashes,
            compatibility_warnings=self.compatibility_warnings,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": SESSION_SCHEMA_VERSION,
            "profiles": {
                "printer": _resolved_profile_to_dict(self.printer),
                "filament": _resolved_profile_to_dict(self.filament),
                "process": _resolved_profile_to_dict(self.process),
            },
            "source_paths": dict(self.source_paths),
            "source_hashes": dict(self.source_hashes),
            "compatibility_warnings": list(self.compatibility_warnings),
        }

    @classmethod
    def from_dict(cls, payload: Any) -> "ProfileSelection":
        data = _required_object(
            payload,
            {"schema_version", "profiles", "source_paths", "source_hashes", "compatibility_warnings"},
            "profile selection",
        )
        if type(data["schema_version"]) is not int or data["schema_version"] != SESSION_SCHEMA_VERSION:
            raise SessionModelError("unsupported profile selection schema_version")
        profiles = _required_object(data["profiles"], set(PROFILE_ROLES), "selected profiles")
        if not isinstance(data["source_paths"], Mapping) or not isinstance(data["source_hashes"], Mapping):
            raise SessionModelError("profile selection paths and hashes must be objects")
        if isinstance(data["compatibility_warnings"], (str, bytes)) or not isinstance(
            data["compatibility_warnings"], (list, tuple)
        ):
            raise SessionModelError("compatibility warnings must be a sequence")
        return cls(
            printer=_resolved_profile_from_dict(profiles["printer"]),
            filament=_resolved_profile_from_dict(profiles["filament"]),
            process=_resolved_profile_from_dict(profiles["process"]),
            source_paths=data["source_paths"],
            source_hashes=data["source_hashes"],
            compatibility_warnings=tuple(data["compatibility_warnings"]),
        )


def _utc_timestamp(value: str, field_name: str) -> str:
    _non_empty_string(value, field_name)
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise SessionModelError(f"{field_name} must be an ISO-8601 timestamp") from exc
    if parsed.tzinfo is None:
        raise SessionModelError(f"{field_name} must include a timezone")
    return parsed.astimezone(timezone.utc).isoformat(timespec="microseconds").replace("+00:00", "Z")


@dataclass(frozen=True)
class SessionSnapshot:
    """Versioned, resumable state for one calibration session."""

    session_id: str
    created_at_utc: str
    updated_at_utc: str
    module_id: str
    current_step: str
    profile_selection: ProfileSelection
    plan: ExperimentPlan | None = None
    results: ExperimentResults | None = None
    run_ids: tuple[str, ...] = ()
    artifact_paths: tuple[str, ...] = ()
    saved_state_version: int = SESSION_SCHEMA_VERSION
    status: str = "setup"
    archived: bool = False

    def __post_init__(self) -> None:
        _non_empty_string(self.session_id, "session id")
        if any(character in self.session_id for character in ("/", "\\", ":")):
            raise SessionModelError("session id cannot contain path separators")
        if self.session_id in {".", ".."}:
            raise SessionModelError("session id is not a safe path component")
        object.__setattr__(self, "created_at_utc", _utc_timestamp(self.created_at_utc, "created_at_utc"))
        object.__setattr__(self, "updated_at_utc", _utc_timestamp(self.updated_at_utc, "updated_at_utc"))
        _non_empty_string(self.module_id, "module id")
        _non_empty_string(self.current_step, "current step")
        _non_empty_string(self.status, "session status")
        if not isinstance(self.profile_selection, ProfileSelection):
            raise SessionModelError("profile_selection must be a ProfileSelection")
        if self.plan is not None and not isinstance(self.plan, ExperimentPlan):
            raise SessionModelError("plan must be an ExperimentPlan or None")
        if self.results is not None and not isinstance(self.results, ExperimentResults):
            raise SessionModelError("results must be ExperimentResults or None")
        if self.plan is not None:
            if self.plan.module_id != self.module_id:
                raise SessionModelError("plan module_id must match the session module_id")
            if self.results is not None:
                self.results.validate_for(self.plan)
        if type(self.saved_state_version) is not int or self.saved_state_version != SESSION_SCHEMA_VERSION:
            raise SessionModelError("unsupported saved_state_version")
        if not isinstance(self.archived, bool):
            raise SessionModelError("archived must be a boolean")

        run_ids = tuple(self.run_ids)
        for run_id in run_ids:
            _non_empty_string(run_id, "run id")
        if len(run_ids) != len(set(run_ids)):
            raise SessionModelError("run ids must be unique")
        artifact_paths = tuple(self.artifact_paths)
        for path in artifact_paths:
            _non_empty_string(path, "artifact path")
        if len(artifact_paths) != len(set(artifact_paths)):
            raise SessionModelError("artifact paths must be unique")
        object.__setattr__(self, "run_ids", run_ids)
        object.__setattr__(self, "artifact_paths", artifact_paths)

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.saved_state_version,
            "session_id": self.session_id,
            "created_at_utc": self.created_at_utc,
            "updated_at_utc": self.updated_at_utc,
            "module_id": self.module_id,
            "current_step": self.current_step,
            "profile_selection": self.profile_selection.to_dict(),
            "plan": None if self.plan is None else self.plan.to_dict(),
            "results": None if self.results is None else self.results.to_dict(),
            "run_ids": list(self.run_ids),
            "artifact_paths": list(self.artifact_paths),
            "status": self.status,
            "archived": self.archived,
        }

    @classmethod
    def from_dict(cls, payload: Any) -> "SessionSnapshot":
        from calibrate3dp.experiments import ExperimentPlan, ExperimentResults

        required = {
            "schema_version",
            "session_id",
            "created_at_utc",
            "updated_at_utc",
            "module_id",
            "current_step",
            "profile_selection",
            "plan",
            "results",
            "run_ids",
            "artifact_paths",
            "status",
            "archived",
        }
        data = _required_object(payload, required, "session snapshot")
        if type(data["schema_version"]) is not int or data["schema_version"] != SESSION_SCHEMA_VERSION:
            raise SessionModelError("unsupported session snapshot schema_version")
        for field_name in ("run_ids", "artifact_paths"):
            if isinstance(data[field_name], (str, bytes)) or not isinstance(data[field_name], (list, tuple)):
                raise SessionModelError(f"{field_name} must be a sequence")
        plan = None if data["plan"] is None else ExperimentPlan.from_dict(data["plan"])
        results = None if data["results"] is None else ExperimentResults.from_dict(data["results"])
        return cls(
            session_id=data["session_id"],
            created_at_utc=data["created_at_utc"],
            updated_at_utc=data["updated_at_utc"],
            module_id=data["module_id"],
            current_step=data["current_step"],
            profile_selection=ProfileSelection.from_dict(data["profile_selection"]),
            plan=plan,
            results=results,
            run_ids=tuple(data["run_ids"]),
            artifact_paths=tuple(data["artifact_paths"]),
            saved_state_version=data["schema_version"],
            status=data["status"],
            archived=data["archived"],
        )


@dataclass(frozen=True)
class SessionSummary:
    """Compact list-row state for Home and the Sessions page."""

    session_id: str
    module_id: str
    status: str
    updated_at_utc: str
    profile_names: Mapping[str, str]

    def __post_init__(self) -> None:
        _non_empty_string(self.session_id, "session id")
        _non_empty_string(self.module_id, "module id")
        _non_empty_string(self.status, "session status")
        object.__setattr__(self, "updated_at_utc", _utc_timestamp(self.updated_at_utc, "updated_at_utc"))
        names = dict(self.profile_names)
        if set(names) != set(PROFILE_ROLES):
            raise SessionModelError("profile_names must contain printer, filament, and process")
        for role, name in names.items():
            _non_empty_string(name, f"{role} profile name")
        object.__setattr__(self, "profile_names", MappingProxyType(names))
