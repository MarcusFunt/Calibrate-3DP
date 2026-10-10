"""Versioned printer, material, profile-snapshot, and calibration-run records."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
import math
import re
from types import MappingProxyType
from typing import Any, Mapping, Sequence

from calibrate3dp.app.models import ProfileSelection
from calibrate3dp.experiments import ExperimentPlan
from calibrate3dp.profiles import ProfileDocument, ResolvedProfile


RECORD_SCHEMA_VERSION = 1
RUN_STATUSES = frozenset({
    "generating", "settings_validated", "validation_failed", "generation_failed", "cancelled",
})


class RecordValidationError(ValueError):
    """Raised when persisted domain data is missing, malformed, or unsafe."""


@dataclass(frozen=True)
class ProfileSnapshot:
    """Resolved profile values plus raw ancestry and setting provenance."""

    profile: ResolvedProfile
    source_sha256: str

    def __post_init__(self) -> None:
        if not isinstance(self.profile, ResolvedProfile):
            raise RecordValidationError("profile snapshot requires a ResolvedProfile")
        if not re.fullmatch(r"[0-9a-f]{64}", self.source_sha256):
            raise RecordValidationError("source_sha256 must be a lowercase SHA-256 digest")

    @classmethod
    def capture(
        cls, profile: ResolvedProfile, source_sha256: str | None = None
    ) -> "ProfileSnapshot":
        if source_sha256 is None:
            document = profile.profile
            canonical = json.dumps(
                dict(document.raw), sort_keys=True, ensure_ascii=False,
                separators=(",", ":"), allow_nan=False,
            )
            source_sha256 = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
        return cls(profile, source_sha256)

    def to_dict(self) -> dict[str, Any]:
        profile = self.profile
        return {
            "schema_version": RECORD_SCHEMA_VERSION,
            "source_sha256": self.source_sha256,
            "profile_identity": _document_to_dict(profile.profile),
            "settings": deepcopy(dict(profile.settings)),
            "provenance": {
                key: list(document.identity)
                for key, document in sorted(profile.provenance.items())
            },
            "chain": [_document_to_dict(document) for document in profile.chain],
        }

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "ProfileSnapshot":
        required = {"schema_version", "source_sha256", "profile_identity", "settings", "provenance", "chain"}
        if not isinstance(payload, Mapping) or set(payload) != required:
            raise RecordValidationError("profile snapshot has an invalid shape")
        if type(payload["schema_version"]) is not int or payload["schema_version"] != RECORD_SCHEMA_VERSION:
            raise RecordValidationError("unsupported profile snapshot schema version")
        chain_raw = payload["chain"]
        if not isinstance(chain_raw, Sequence) or isinstance(chain_raw, (str, bytes)):
            raise RecordValidationError("profile snapshot chain must be a list")
        chain = tuple(_document_from_dict(item) for item in chain_raw)
        root = _document_from_dict(payload["profile_identity"])
        docs = {item.identity: item for item in (*chain, root)}
        provenance_raw = payload["provenance"]
        if not isinstance(provenance_raw, Mapping):
            raise RecordValidationError("profile provenance must be an object")
        provenance: dict[str, ProfileDocument] = {}
        for key, identity in provenance_raw.items():
            if not isinstance(key, str) or not isinstance(identity, list) or len(identity) != 3:
                raise RecordValidationError("profile provenance entries must identify a profile")
            item = docs.get(tuple(identity))
            if item is None:
                raise RecordValidationError(f"profile provenance for {key!r} is absent from its chain")
            provenance[key] = item
        settings = payload["settings"]
        if not isinstance(settings, Mapping):
            raise RecordValidationError("resolved profile settings must be an object")
        _require_json(settings, "resolved profile settings")
        return cls(
            ResolvedProfile(root, deepcopy(dict(settings)), MappingProxyType(provenance), chain),
            payload["source_sha256"],
        )


@dataclass(frozen=True)
class PrinterRecord:
    printer_id: str
    display_name: str
    model: str
    nozzle: str
    machine_profile: ProfileSnapshot
    process_profile: ProfileSnapshot
    created_at_utc: str

    def __post_init__(self) -> None:
        _non_empty(self.printer_id, "printer_id")
        _non_empty(self.display_name, "display_name")
        _non_empty(self.model, "model")
        _non_empty(self.nozzle, "nozzle")
        object.__setattr__(self, "created_at_utc", _timestamp(self.created_at_utc))
        if not isinstance(self.machine_profile, ProfileSnapshot) or self.machine_profile.profile.profile.kind not in {"machine", "printer"}:
            raise RecordValidationError("machine_profile must be a machine or printer snapshot")
        if not isinstance(self.process_profile, ProfileSnapshot) or self.process_profile.profile.profile.kind != "process":
            raise RecordValidationError("process_profile must be a process snapshot")

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": RECORD_SCHEMA_VERSION,
            "printer_id": self.printer_id,
            "display_name": self.display_name,
            "model": self.model,
            "nozzle": self.nozzle,
            "machine_profile": self.machine_profile.to_dict(),
            "process_profile": self.process_profile.to_dict(),
            "created_at_utc": _timestamp(self.created_at_utc),
        }

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "PrinterRecord":
        _require_shape(payload, {"schema_version", "printer_id", "display_name", "model", "nozzle", "machine_profile", "process_profile", "created_at_utc"}, "printer record")
        _require_schema(payload)
        return cls(
            payload["printer_id"], payload["display_name"], payload["model"], payload["nozzle"],
            ProfileSnapshot.from_dict(payload["machine_profile"]),
            ProfileSnapshot.from_dict(payload["process_profile"]), payload["created_at_utc"],
        )


@dataclass(frozen=True)
class MaterialRecord:
    material_id: str
    display_name: str
    nozzle_context: str
    filament_profile: ProfileSnapshot
    toolhead_context: str | None
    created_at_utc: str

    def __post_init__(self) -> None:
        _non_empty(self.material_id, "material_id")
        _non_empty(self.display_name, "display_name")
        _non_empty(self.nozzle_context, "nozzle_context")
        object.__setattr__(self, "created_at_utc", _timestamp(self.created_at_utc))
        if not isinstance(self.filament_profile, ProfileSnapshot) or self.filament_profile.profile.profile.kind != "filament":
            raise RecordValidationError("filament_profile must be a filament snapshot")
        if self.toolhead_context is not None:
            _non_empty(self.toolhead_context, "toolhead_context")

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": RECORD_SCHEMA_VERSION,
            "material_id": self.material_id,
            "display_name": self.display_name,
            "nozzle_context": self.nozzle_context,
            "filament_profile": self.filament_profile.to_dict(),
            "toolhead_context": self.toolhead_context,
            "created_at_utc": _timestamp(self.created_at_utc),
        }

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "MaterialRecord":
        _require_shape(payload, {"schema_version", "material_id", "display_name", "nozzle_context", "filament_profile", "toolhead_context", "created_at_utc"}, "material record")
        _require_schema(payload)
        return cls(
            payload["material_id"], payload["display_name"], payload["nozzle_context"],
            ProfileSnapshot.from_dict(payload["filament_profile"]), payload["toolhead_context"], payload["created_at_utc"],
        )


@dataclass(frozen=True)
class ArtifactRecord:
    relative_path: str
    media_type: str
    size_bytes: int
    sha256: str

    def __post_init__(self) -> None:
        _non_empty(self.relative_path, "artifact relative_path")
        _non_empty(self.media_type, "artifact media_type")
        path = self.relative_path.replace("\\", "/")
        if path.startswith("/") or re.match(r"^[A-Za-z]:", path) or ".." in path.split("/"):
            raise RecordValidationError("artifact path must remain inside the workspace")
        if type(self.size_bytes) is not int or self.size_bytes < 0:
            raise RecordValidationError("artifact size_bytes must be a non-negative integer")
        if not re.fullmatch(r"[0-9a-f]{64}", self.sha256):
            raise RecordValidationError("artifact sha256 must be a lowercase SHA-256 digest")

    def to_dict(self) -> dict[str, Any]:
        return {"relative_path": self.relative_path.replace("\\", "/"), "media_type": self.media_type, "size_bytes": self.size_bytes, "sha256": self.sha256}

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "ArtifactRecord":
        _require_shape(payload, {"relative_path", "media_type", "size_bytes", "sha256"}, "artifact record")
        return cls(payload["relative_path"], payload["media_type"], payload["size_bytes"], payload["sha256"])


@dataclass(frozen=True)
class CalibrationRunRecord:
    run_id: str
    plate_code: str
    printer_id: str
    material_id: str
    status: str
    created_at_utc: str
    plan: ExperimentPlan
    profiles: ProfileSelection
    sample_map: tuple[Mapping[str, Any], ...]
    validation: Mapping[str, Any]
    artifacts: tuple[ArtifactRecord, ...] = ()

    def __post_init__(self) -> None:
        _non_empty(self.run_id, "run_id")
        if any(character in self.run_id for character in ("/", "\\", ":")) or self.run_id in {".", ".."}:
            raise RecordValidationError("run_id must be a safe path component")
        if not isinstance(self.plate_code, str) or not re.fullmatch(r"[A-Z0-9]{6}", self.plate_code):
            raise RecordValidationError("plate_code must contain exactly six uppercase letters or digits")
        _non_empty(self.printer_id, "printer_id")
        _non_empty(self.material_id, "material_id")
        if self.status not in RUN_STATUSES:
            raise RecordValidationError(f"unknown run status {self.status!r}")
        object.__setattr__(self, "created_at_utc", _timestamp(self.created_at_utc))
        if not isinstance(self.plan, ExperimentPlan) or not isinstance(self.profiles, ProfileSelection):
            raise RecordValidationError("a run requires a typed plan and profile selection")
        sample_payload = [deepcopy(dict(item)) for item in self.sample_map]
        if not sample_payload:
            raise RecordValidationError("run sample_map cannot be empty")
        _require_json(sample_payload, "run sample_map")
        samples = tuple(MappingProxyType(item) for item in sample_payload)
        validation = deepcopy(dict(self.validation))
        _require_json(validation, "run validation")
        object.__setattr__(self, "sample_map", samples)
        object.__setattr__(self, "validation", MappingProxyType(validation))
        artifacts = tuple(self.artifacts)
        if any(not isinstance(item, ArtifactRecord) for item in artifacts):
            raise RecordValidationError("run artifacts must be ArtifactRecord values")
        if len({item.relative_path for item in artifacts}) != len(artifacts):
            raise RecordValidationError("run artifact paths must be unique")
        object.__setattr__(self, "artifacts", artifacts)

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": RECORD_SCHEMA_VERSION,
            "run_id": self.run_id,
            "plate_code": self.plate_code,
            "printer_id": self.printer_id,
            "material_id": self.material_id,
            "status": self.status,
            "created_at_utc": _timestamp(self.created_at_utc),
            "plan": self.plan.to_dict(),
            "profiles": self.profiles.to_dict(),
            "sample_map": [deepcopy(dict(item)) for item in self.sample_map],
            "validation": deepcopy(dict(self.validation)),
            "artifacts": [item.to_dict() for item in self.artifacts],
        }

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "CalibrationRunRecord":
        _require_shape(payload, {"schema_version", "run_id", "plate_code", "printer_id", "material_id", "status", "created_at_utc", "plan", "profiles", "sample_map", "validation", "artifacts"}, "calibration run")
        _require_schema(payload)
        if not isinstance(payload["sample_map"], list) or not isinstance(payload["validation"], Mapping) or not isinstance(payload["artifacts"], list):
            raise RecordValidationError("run sample map, validation, or artifacts have an invalid shape")
        return cls(
            payload["run_id"], payload["plate_code"], payload["printer_id"], payload["material_id"],
            payload["status"], payload["created_at_utc"], ExperimentPlan.from_dict(payload["plan"]),
            ProfileSelection.from_dict(payload["profiles"]), tuple(payload["sample_map"]), payload["validation"],
            tuple(ArtifactRecord.from_dict(item) for item in payload["artifacts"]),
        )


def _document_to_dict(document: ProfileDocument) -> dict[str, Any]:
    return {
        "name": document.name,
        "kind": document.kind,
        "scope": document.scope,
        "source": document.source,
        "raw": deepcopy(dict(document.raw)),
    }


def _document_from_dict(payload: Mapping[str, Any]) -> ProfileDocument:
    _require_shape(payload, {"name", "kind", "scope", "source", "raw"}, "profile document")
    return ProfileDocument(payload["name"], payload["kind"], payload["scope"], payload["raw"], payload["source"])


def _require_schema(payload: Mapping[str, Any]) -> None:
    if type(payload["schema_version"]) is not int or payload["schema_version"] != RECORD_SCHEMA_VERSION:
        raise RecordValidationError("unsupported record schema version")


def _require_shape(payload: Any, keys: set[str], label: str) -> None:
    if not isinstance(payload, Mapping) or set(payload) != keys:
        raise RecordValidationError(f"{label} has an invalid shape")


def _require_json(value: Any, label: str) -> None:
    try:
        json.dumps(value, ensure_ascii=False, sort_keys=True, allow_nan=False)
    except (TypeError, ValueError, OverflowError) as exc:
        raise RecordValidationError(f"{label} must contain valid JSON values: {exc}") from exc


def _non_empty(value: Any, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise RecordValidationError(f"{name} must be a non-empty string")
    return value


def _timestamp(value: Any) -> str:
    if not isinstance(value, str) or not value.strip():
        raise RecordValidationError("created_at_utc must be an ISO-8601 timestamp")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise RecordValidationError("created_at_utc must be an ISO-8601 timestamp") from exc
    if parsed.tzinfo is None:
        raise RecordValidationError("created_at_utc must include a timezone")
    return parsed.astimezone(timezone.utc).isoformat(timespec="microseconds").replace("+00:00", "Z")


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="microseconds").replace("+00:00", "Z")
