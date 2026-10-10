"""Immutable, versioned configurations for the grouped ironing workflow."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
import math
import re
from types import MappingProxyType
from typing import Any, Mapping

from calibrate3dp.app.models import ProfileSelection
from calibrate3dp.experiments import ExperimentPlan


CONFIG_SCHEMA_VERSION = 1
_PROFILE_ROLES = frozenset({"printer", "process", "filament"})
_SWEEP_KEYS = ("ironing_flow", "ironing_speed")
_LAYOUT_KEYS = frozenset({
    "strategy", "rows", "columns", "geometry_backend", "geometry_backend_version",
    "margin_mm", "specimen_width_mm", "specimen_depth_mm", "specimen_height_mm",
    "gap_mm", "connector_width_mm", "connector_height_mm", "connector_gap_mm",
    "frame_width_mm", "voxel_mm", "label_pixel_mm", "code_pixel_mm",
})


class ExperimentConfigurationError(ValueError):
    """Raised when a saved configuration is incomplete or incompatible."""


def default_layout_options() -> dict[str, Any]:
    """Return the complete layout input set used by the current geometry backend."""
    return {
        "strategy": "connected-grid",
        "rows": 3,
        "columns": 3,
        "geometry_backend": "stdlib-voxel",
        "geometry_backend_version": "1",
        "margin_mm": 4.8,
        "specimen_width_mm": 30.0,
        "specimen_depth_mm": 30.0,
        "specimen_height_mm": 6.4,
        "gap_mm": 8.0,
        "connector_width_mm": 2.4,
        "connector_height_mm": 0.8,
        "connector_gap_mm": 2.8,
        "frame_width_mm": 2.4,
        "voxel_mm": 0.4,
        "label_pixel_mm": 0.8,
        "code_pixel_mm": 0.8,
    }


@dataclass(frozen=True)
class SavedExperimentConfiguration:
    """A frozen ironing plan and all contextual inputs used to generate it."""

    config_id: str
    experiment_id: str
    revision_no: int
    printer_id: str
    material_id: str
    profile_selection: ProfileSelection
    plan: ExperimentPlan
    layout_options: Mapping[str, Any]
    created_at_utc: str
    relation_type: str = "initial"
    parent_run_id: str | None = None
    parent_assessment_revision_id: str | None = None
    parent_candidate_id: str | None = None

    def __post_init__(self) -> None:
        for field_name in ("config_id", "experiment_id", "printer_id", "material_id"):
            _non_empty(getattr(self, field_name), field_name)
        if isinstance(self.revision_no, bool) or not isinstance(self.revision_no, int) or self.revision_no < 1:
            raise ExperimentConfigurationError("revision_no must be a positive integer")
        if not isinstance(self.profile_selection, ProfileSelection):
            raise ExperimentConfigurationError("profile_selection must be a ProfileSelection")
        if not isinstance(self.plan, ExperimentPlan) or self.plan.module_id != "ironing":
            raise ExperimentConfigurationError("configuration requires a grouped ironing plan")
        if not isinstance(self.layout_options, Mapping):
            raise ExperimentConfigurationError("layout_options must be an object")
        object.__setattr__(self, "created_at_utc", _timestamp(self.created_at_utc))
        self._validate_profile_hashes()
        object.__setattr__(self, "layout_options", MappingProxyType(_validated_layout(self.layout_options)))
        self._validate_plan()
        if self.relation_type not in {"initial", "refinement", "confirmation"}:
            raise ExperimentConfigurationError("relation_type must be initial, refinement, or confirmation")
        if self.relation_type == "initial":
            if self.parent_run_id is not None or self.parent_assessment_revision_id is not None or self.parent_candidate_id is not None or self.plan.parent_plan_id is not None:
                raise ExperimentConfigurationError("initial configurations cannot have parent evidence")
        else:
            _non_empty(self.parent_run_id, "parent_run_id")
            _non_empty(self.parent_assessment_revision_id, "parent_assessment_revision_id")
            _non_empty(self.parent_candidate_id, "parent_candidate_id")
            if self.plan.parent_plan_id is None:
                raise ExperimentConfigurationError("follow-up plans must reference their parent plan")
            if self.relation_type == "confirmation" and len(self.plan.candidates) != 1:
                raise ExperimentConfigurationError("confirmation plans must contain exactly one candidate")

    @property
    def source_profile_hashes(self) -> Mapping[str, str]:
        return self.profile_selection.source_hashes

    @property
    def fixed_settings(self) -> Mapping[str, Any]:
        return MappingProxyType({
            key: value for key, value in self.plan.baseline_settings.items()
            if key not in _SWEEP_KEYS and key not in {"name", "type", "inherits"}
        })

    @property
    def input_sha256(self) -> str:
        return hashlib.sha256(_canonical_json(self._payload()).encode("utf-8")).hexdigest()

    def sample_label_for(self, candidate_id: str) -> str:
        for label, candidate in zip("ABCDEFGHI", self.plan.candidates, strict=True):
            if candidate.candidate_id == candidate_id:
                return f"Sample-{label}"
        raise ExperimentConfigurationError(f"unknown candidate id {candidate_id!r}")

    def to_dict(self) -> dict[str, Any]:
        return {**self._payload(), "input_sha256": self.input_sha256}

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "SavedExperimentConfiguration":
        required = {
            "schema_version", "config_id", "experiment_id", "revision_no", "printer_id", "material_id",
            "profile_selection", "plan", "layout_options", "created_at_utc", "relation_type",
            "parent_run_id", "parent_assessment_revision_id", "parent_candidate_id", "input_sha256",
        }
        if not isinstance(payload, Mapping) or set(payload) != required:
            raise ExperimentConfigurationError("experiment configuration has an invalid shape")
        if type(payload["schema_version"]) is not int or payload["schema_version"] != CONFIG_SCHEMA_VERSION:
            raise ExperimentConfigurationError("unsupported experiment configuration schema_version")
        if not isinstance(payload["profile_selection"], Mapping) or not isinstance(payload["plan"], Mapping):
            raise ExperimentConfigurationError("configuration profiles and plan must be objects")
        if not isinstance(payload["layout_options"], Mapping):
            raise ExperimentConfigurationError("configuration layout_options must be an object")
        try:
            config = cls(
                config_id=payload["config_id"],
                experiment_id=payload["experiment_id"],
                revision_no=payload["revision_no"],
                printer_id=payload["printer_id"],
                material_id=payload["material_id"],
                profile_selection=ProfileSelection.from_dict(payload["profile_selection"]),
                plan=ExperimentPlan.from_dict(payload["plan"]),
                layout_options=payload["layout_options"],
                created_at_utc=payload["created_at_utc"],
                relation_type=payload["relation_type"],
                parent_run_id=payload["parent_run_id"],
                parent_assessment_revision_id=payload["parent_assessment_revision_id"],
                parent_candidate_id=payload["parent_candidate_id"],
            )
        except (TypeError, ValueError, KeyError) as exc:
            if isinstance(exc, ExperimentConfigurationError):
                raise
            raise ExperimentConfigurationError(f"invalid experiment configuration: {exc}") from exc
        digest = payload["input_sha256"]
        if not isinstance(digest, str) or not re.fullmatch(r"[0-9a-f]{64}", digest):
            raise ExperimentConfigurationError("input_sha256 must be a lowercase SHA-256 digest")
        if digest != config.input_sha256:
            raise ExperimentConfigurationError("configuration input hash does not match its contents")
        return config

    def _payload(self) -> dict[str, Any]:
        return {
            "schema_version": CONFIG_SCHEMA_VERSION,
            "config_id": self.config_id,
            "experiment_id": self.experiment_id,
            "revision_no": self.revision_no,
            "printer_id": self.printer_id,
            "material_id": self.material_id,
            "profile_selection": self.profile_selection.to_dict(),
            "plan": self.plan.to_dict(),
            "layout_options": dict(self.layout_options),
            "created_at_utc": self.created_at_utc,
            "relation_type": self.relation_type,
            "parent_run_id": self.parent_run_id,
            "parent_assessment_revision_id": self.parent_assessment_revision_id,
            "parent_candidate_id": self.parent_candidate_id,
        }

    def _validate_profile_hashes(self) -> None:
        hashes = self.profile_selection.source_hashes
        if set(hashes) != _PROFILE_ROLES or any(
            not isinstance(value, str) or not re.fullmatch(r"[0-9a-f]{64}", value)
            for value in hashes.values()
        ):
            raise ExperimentConfigurationError("all source profile SHA-256 hashes are required")

    def _validate_plan(self) -> None:
        dimensions = {item.key: item for item in self.plan.dimensions}
        if set(dimensions) != set(_SWEEP_KEYS):
            raise ExperimentConfigurationError("ironing config must sweep flow and speed only")
        flow, speed = (dimensions[key].values for key in _SWEEP_KEYS)
        expected_dimension_size = 1 if self.relation_type == "confirmation" else 3
        expected_candidate_count = 1 if self.relation_type == "confirmation" else 9
        if len(flow) != expected_dimension_size or len(speed) != expected_dimension_size or len(self.plan.candidates) != expected_candidate_count:
            raise ExperimentConfigurationError(
                "confirmation config must contain one exact candidate" if self.relation_type == "confirmation"
                else "grouped ironing config must contain a 3×3 nine-candidate grid"
            )
        for key, values in (("ironing_flow", flow), ("ironing_speed", speed)):
            numeric = tuple(_positive_number(value, key) for value in values)
            if any(right <= left for left, right in zip(numeric, numeric[1:])):
                raise ExperimentConfigurationError(f"{key} values must be strictly increasing")
        expected = (
            [{"ironing_flow": flow[0], "ironing_speed": speed[0]}]
            if self.relation_type == "confirmation"
            else [
                {"ironing_flow": flow[row], "ironing_speed": speed[column]}
                for row in range(3) for column in range(3)
            ]
        )
        for index, (candidate, settings) in enumerate(zip(self.plan.candidates, expected, strict=True)):
            if dict(candidate.overrides) != settings:
                raise ExperimentConfigurationError(
                    f"candidate order must map deterministically to Sample-{chr(ord('A') + index)}"
                )


def _validated_layout(layout: Mapping[str, Any]) -> dict[str, Any]:
    values = dict(layout)
    if set(values) != _LAYOUT_KEYS:
        missing = sorted(_LAYOUT_KEYS - set(values))
        extra = sorted(set(values) - _LAYOUT_KEYS)
        raise ExperimentConfigurationError(
            "layout options have invalid fields: "
            + "; ".join(filter(None, (
                "missing " + ", ".join(missing) if missing else "",
                "unexpected " + ", ".join(extra) if extra else "",
            )))
        )
    if values["strategy"] != "connected-grid":
        raise ExperimentConfigurationError("unsupported layout strategy; expected connected-grid")
    if values["geometry_backend"] != "stdlib-voxel" or values["geometry_backend_version"] != "1":
        raise ExperimentConfigurationError("unsupported geometry backend or version")
    if type(values["rows"]) is not int or values["rows"] != 3 or type(values["columns"]) is not int or values["columns"] != 3:
        raise ExperimentConfigurationError("connected ironing layout must have three rows and columns")
    for name in _LAYOUT_KEYS - {"strategy", "rows", "columns", "geometry_backend", "geometry_backend_version"}:
        _positive_number(values[name], name)
    return values


def _positive_number(value: Any, name: str) -> float:
    try:
        number = float(str(value).removesuffix("%").strip())
    except (TypeError, ValueError) as exc:
        raise ExperimentConfigurationError(f"{name} values must be finite positive numbers") from exc
    if isinstance(value, bool) or not math.isfinite(number) or number <= 0:
        raise ExperimentConfigurationError(f"{name} values must be finite positive numbers")
    return number


def _canonical_json(value: Any) -> str:
    try:
        return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)
    except (TypeError, ValueError, OverflowError) as exc:
        raise ExperimentConfigurationError(f"configuration is not valid JSON: {exc}") from exc


def _non_empty(value: Any, name: str) -> None:
    if not isinstance(value, str) or not value.strip():
        raise ExperimentConfigurationError(f"{name} must be a non-empty string")


def _timestamp(value: Any) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ExperimentConfigurationError("created_at_utc must be an ISO-8601 timestamp")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ExperimentConfigurationError("created_at_utc must be an ISO-8601 timestamp") from exc
    if parsed.tzinfo is None:
        raise ExperimentConfigurationError("created_at_utc must include a timezone")
    return parsed.astimezone(timezone.utc).isoformat(timespec="microseconds").replace("+00:00", "Z")
