"""Immutable pointer and hashes for a reviewed standalone process export."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
import re
from typing import Any, Mapping


RUN_EXPORT_SCHEMA_VERSION = 1


@dataclass(frozen=True)
class RunExportRecord:
    export_id: str
    run_id: str
    assessment_revision_id: str
    decision_id: str
    created_at_utc: str
    destination_profile: str
    manifest_path: str
    report_path: str
    new_profile_name: str
    source_profile_sha256: str
    profile_sha256: str
    manifest_sha256: str
    report_sha256: str
    draft_sha256: str
    confirmation_status: str
    export_sha256: str

    def __post_init__(self) -> None:
        for name in ("export_id", "run_id", "assessment_revision_id", "decision_id", "destination_profile", "manifest_path", "report_path", "new_profile_name", "confirmation_status"):
            value = getattr(self, name)
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"{name} must be a non-empty string")
        parsed = datetime.fromisoformat(self.created_at_utc.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            raise ValueError("export timestamp must include a timezone")
        object.__setattr__(self, "created_at_utc", parsed.astimezone(timezone.utc).isoformat(timespec="microseconds").replace("+00:00", "Z"))
        for name in ("source_profile_sha256", "profile_sha256", "manifest_sha256", "report_sha256", "draft_sha256", "export_sha256"):
            if not isinstance(getattr(self, name), str) or not re.fullmatch(r"[0-9a-f]{64}", getattr(self, name)):
                raise ValueError(f"{name} must be a lowercase SHA-256 digest")
        if self.export_sha256 != self.calculate_hash(self._payload()):
            raise ValueError("export record hash does not match its contents")

    def _payload(self) -> dict[str, Any]:
        return {
            "schema_version": RUN_EXPORT_SCHEMA_VERSION,
            "export_id": self.export_id,
            "run_id": self.run_id,
            "assessment_revision_id": self.assessment_revision_id,
            "decision_id": self.decision_id,
            "created_at_utc": self.created_at_utc,
            "destination_profile": self.destination_profile,
            "manifest_path": self.manifest_path,
            "report_path": self.report_path,
            "new_profile_name": self.new_profile_name,
            "source_profile_sha256": self.source_profile_sha256,
            "profile_sha256": self.profile_sha256,
            "manifest_sha256": self.manifest_sha256,
            "report_sha256": self.report_sha256,
            "draft_sha256": self.draft_sha256,
            "confirmation_status": self.confirmation_status,
        }

    @staticmethod
    def calculate_hash(payload: Mapping[str, Any]) -> str:
        encoded = json.dumps(dict(payload), ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")
        return hashlib.sha256(encoded).hexdigest()

    @classmethod
    def create(cls, **values) -> "RunExportRecord":
        parsed = datetime.fromisoformat(values["created_at_utc"].replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            raise ValueError("export timestamp must include a timezone")
        values = {
            **values,
            "created_at_utc": parsed.astimezone(timezone.utc).isoformat(timespec="microseconds").replace("+00:00", "Z"),
        }
        payload = {
            "schema_version": RUN_EXPORT_SCHEMA_VERSION,
            **values,
        }
        return cls(**values, export_sha256=cls.calculate_hash(payload))

    def to_dict(self) -> dict[str, Any]:
        return {**self._payload(), "export_sha256": self.export_sha256}

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "RunExportRecord":
        required = {"schema_version", "export_id", "run_id", "assessment_revision_id", "decision_id", "created_at_utc", "destination_profile", "manifest_path", "report_path", "new_profile_name", "source_profile_sha256", "profile_sha256", "manifest_sha256", "report_sha256", "draft_sha256", "confirmation_status", "export_sha256"}
        if not isinstance(payload, Mapping) or set(payload) != required or payload["schema_version"] != RUN_EXPORT_SCHEMA_VERSION:
            raise ValueError("unsupported or malformed run export record")
        values = {key: payload[key] for key in required - {"schema_version", "export_sha256"}}
        return cls(**values, export_sha256=payload["export_sha256"])
