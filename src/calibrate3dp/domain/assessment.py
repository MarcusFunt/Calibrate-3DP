"""Immutable human assessment revisions for generated calibration runs."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
import math
import re
from typing import Any, Mapping, Sequence

from calibrate3dp.domain.records import ArtifactRecord
from calibrate3dp.experiments import ExperimentResults


ASSESSMENT_SCHEMA_VERSION = 1


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="microseconds").replace("+00:00", "Z")


@dataclass(frozen=True)
class PrintAttestation:
    """User-entered print and inspection state; never inferred from G-code."""

    physical_print_performed: bool | None
    physical_review_completed: bool
    synthetic: bool = False
    notes: str = ""
    recorded_at_utc: str = ""
    label_legible: bool | None = None
    frame_adhesion_sound: bool | None = None
    samples_separable: bool | None = None
    trial_material: str = ""
    trial_nozzle: str = ""
    layer_height_mm: float | None = None
    orientation: str = ""

    def __post_init__(self) -> None:
        if self.physical_print_performed is not None and not isinstance(self.physical_print_performed, bool):
            raise ValueError("physical_print_performed must be true, false, or not recorded")
        if not isinstance(self.physical_review_completed, bool) or not isinstance(self.synthetic, bool):
            raise ValueError("attestation flags must be booleans")
        for name in ("label_legible", "frame_adhesion_sound", "samples_separable"):
            value = getattr(self, name)
            if value is not None and not isinstance(value, bool):
                raise ValueError(f"{name} must be true, false, or not recorded")
        if self.synthetic and self.physical_print_performed is not False:
            raise ValueError("synthetic results must explicitly state that no physical print was performed")
        if self.physical_review_completed and self.physical_print_performed is not True:
            raise ValueError("physical review requires an explicitly reported physical print")
        if not isinstance(self.notes, str):
            raise ValueError("attestation notes must be text")
        for name in ("trial_material", "trial_nozzle", "orientation"):
            if not isinstance(getattr(self, name), str):
                raise ValueError(f"{name} must be text")
        if self.layer_height_mm is not None and (
            isinstance(self.layer_height_mm, bool) or not isinstance(self.layer_height_mm, (int, float))
            or not math.isfinite(self.layer_height_mm) or self.layer_height_mm <= 0
        ):
            raise ValueError("layer_height_mm must be finite and greater than zero")
        timestamp = self.recorded_at_utc or utc_now()
        parsed = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            raise ValueError("attestation timestamp must include a timezone")
        object.__setattr__(self, "recorded_at_utc", parsed.astimezone(timezone.utc).isoformat(timespec="microseconds").replace("+00:00", "Z"))

    @property
    def physically_accepted(self) -> bool:
        return (
            self.physical_print_performed is True
            and self.physical_review_completed
            and not self.synthetic
            and self.label_legible is True
            and self.frame_adhesion_sound is True
            and self.samples_separable is True
            and bool(self.trial_material.strip())
            and bool(self.trial_nozzle.strip())
            and self.layer_height_mm is not None
            and bool(self.orientation.strip())
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": ASSESSMENT_SCHEMA_VERSION,
            "physical_print_performed": self.physical_print_performed,
            "physical_review_completed": self.physical_review_completed,
            "synthetic": self.synthetic,
            "notes": self.notes,
            "recorded_at_utc": self.recorded_at_utc,
            "label_legible": self.label_legible,
            "frame_adhesion_sound": self.frame_adhesion_sound,
            "samples_separable": self.samples_separable,
            "trial_material": self.trial_material,
            "trial_nozzle": self.trial_nozzle,
            "layer_height_mm": self.layer_height_mm,
            "orientation": self.orientation,
        }

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "PrintAttestation":
        required = {"schema_version", "physical_print_performed", "physical_review_completed", "synthetic", "notes", "recorded_at_utc", "label_legible", "frame_adhesion_sound", "samples_separable", "trial_material", "trial_nozzle", "layer_height_mm", "orientation"}
        if not isinstance(payload, Mapping) or set(payload) != required or payload["schema_version"] != ASSESSMENT_SCHEMA_VERSION:
            raise ValueError("unsupported or malformed print attestation")
        return cls(
            payload["physical_print_performed"], payload["physical_review_completed"], payload["synthetic"],
            payload["notes"], payload["recorded_at_utc"], payload["label_legible"],
            payload["frame_adhesion_sound"], payload["samples_separable"], payload["trial_material"],
            payload["trial_nozzle"], payload["layer_height_mm"], payload["orientation"],
        )


@dataclass(frozen=True)
class AssessmentRevision:
    assessment_revision_id: str
    run_id: str
    revision_no: int
    created_at_utc: str
    results: ExperimentResults
    attestation: PrintAttestation
    photo_artifacts: tuple[ArtifactRecord, ...]
    assessment_sha256: str

    def __post_init__(self) -> None:
        for name in ("assessment_revision_id", "run_id"):
            value = getattr(self, name)
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"{name} must be a non-empty string")
        if type(self.revision_no) is not int or self.revision_no < 1:
            raise ValueError("revision_no must be a positive integer")
        parsed = datetime.fromisoformat(self.created_at_utc.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            raise ValueError("assessment timestamp must include a timezone")
        object.__setattr__(self, "created_at_utc", parsed.astimezone(timezone.utc).isoformat(timespec="microseconds").replace("+00:00", "Z"))
        if not isinstance(self.results, ExperimentResults) or not isinstance(self.attestation, PrintAttestation):
            raise ValueError("assessment revision requires typed results and print attestation")
        photos = tuple(self.photo_artifacts)
        if any(not isinstance(item, ArtifactRecord) for item in photos):
            raise ValueError("photo_artifacts must be ArtifactRecord values")
        if len({item.relative_path for item in photos}) != len(photos):
            raise ValueError("photo artifact paths must be unique")
        object.__setattr__(self, "photo_artifacts", photos)
        referenced: list[str] = []
        for assessment in self.results.assessments:
            referenced.extend(assessment.photo_paths)
        if len(referenced) != len(set(referenced)):
            raise ValueError("one photo artifact cannot be assigned to multiple candidate assessments")
        if set(referenced) != {item.relative_path for item in photos}:
            raise ValueError("assessment photo references and immutable photo artifact records disagree")
        if not re.fullmatch(r"[0-9a-f]{64}", self.assessment_sha256):
            raise ValueError("assessment_sha256 must be a lowercase SHA-256 digest")
        if self.assessment_sha256 != self.calculate_hash(self.results, self.attestation, photos):
            raise ValueError("assessment hash does not match its immutable payload")

    @staticmethod
    def calculate_hash(results: ExperimentResults, attestation: PrintAttestation, photos: Sequence[ArtifactRecord]) -> str:
        payload = {
            "schema_version": ASSESSMENT_SCHEMA_VERSION,
            "results": results.to_dict(),
            "attestation": attestation.to_dict(),
            "photo_artifacts": [item.to_dict() for item in photos],
        }
        encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")
        return hashlib.sha256(encoded).hexdigest()

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": ASSESSMENT_SCHEMA_VERSION,
            "assessment_revision_id": self.assessment_revision_id,
            "run_id": self.run_id,
            "revision_no": self.revision_no,
            "created_at_utc": self.created_at_utc,
            "results": self.results.to_dict(),
            "attestation": self.attestation.to_dict(),
            "photo_artifacts": [item.to_dict() for item in self.photo_artifacts],
            "assessment_sha256": self.assessment_sha256,
        }

    @classmethod
    def create(cls, *, assessment_revision_id: str, run_id: str, revision_no: int, created_at_utc: str, results: ExperimentResults, attestation: PrintAttestation, photo_artifacts: Sequence[ArtifactRecord]) -> "AssessmentRevision":
        photos = tuple(photo_artifacts)
        return cls(assessment_revision_id, run_id, revision_no, created_at_utc, results, attestation, photos, cls.calculate_hash(results, attestation, photos))

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "AssessmentRevision":
        required = {"schema_version", "assessment_revision_id", "run_id", "revision_no", "created_at_utc", "results", "attestation", "photo_artifacts", "assessment_sha256"}
        if not isinstance(payload, Mapping) or set(payload) != required or payload["schema_version"] != ASSESSMENT_SCHEMA_VERSION:
            raise ValueError("unsupported or malformed assessment revision")
        photos = payload["photo_artifacts"]
        if not isinstance(photos, list):
            raise ValueError("photo_artifacts must be a list")
        return cls(payload["assessment_revision_id"], payload["run_id"], payload["revision_no"], payload["created_at_utc"], ExperimentResults.from_dict(payload["results"]), PrintAttestation.from_dict(payload["attestation"]), tuple(ArtifactRecord.from_dict(item) for item in photos), payload["assessment_sha256"])
