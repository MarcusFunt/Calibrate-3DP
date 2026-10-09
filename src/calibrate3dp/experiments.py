"""Deterministic experiment grids and validated manual assessments.

This module deliberately knows nothing about slicer UI or printer transport.
It turns an explicit baseline and parameter sweep into candidate settings and
records a user's evaluation of the printed candidates.
"""

from __future__ import annotations

from dataclasses import dataclass
from copy import deepcopy
from itertools import product
import json
import math
from types import MappingProxyType
from typing import Any, Mapping, Sequence


class ExperimentDefinitionError(ValueError):
    """Raised when an experiment or a manual result is internally invalid."""


class ExperimentStateError(ValueError):
    """Raised when results do not belong to the plan being evaluated."""


def _non_empty_string(value: Any, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ExperimentDefinitionError(f"{name} must be a non-empty string")
    return value


def _copy_json_value(value: Any, name: str) -> Any:
    """Validate strict JSON data and return a detached copy."""
    def normalize(item: Any) -> Any:
        if item is None or isinstance(item, (str, bool, int)):
            return item
        if isinstance(item, float):
            if not math.isfinite(item):
                raise ValueError("non-finite numbers are not JSON values")
            return item
        if isinstance(item, Mapping):
            if any(not isinstance(key, str) for key in item):
                raise ValueError("JSON object keys must be strings")
            return {key: normalize(nested) for key, nested in item.items()}
        if isinstance(item, (list, tuple)):
            return [normalize(nested) for nested in item]
        raise TypeError(f"unsupported JSON value {type(item).__name__}")

    try:
        return normalize(value)
    except (TypeError, ValueError, OverflowError) as exc:
        raise ExperimentDefinitionError(f"{name} must contain finite JSON values: {exc}") from exc


def _json_key(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)


@dataclass(frozen=True)
class SweepDimension:
    """One ordered parameter axis in a calibration experiment."""

    key: str
    values: tuple[Any, ...]
    label: str = ""

    def __post_init__(self) -> None:
        _non_empty_string(self.key, "sweep dimension key")
        if isinstance(self.values, (str, bytes)) or not isinstance(self.values, Sequence):
            raise ExperimentDefinitionError("sweep dimension values must be a non-empty sequence")
        values = tuple(_copy_json_value(value, f"values for {self.key!r}") for value in self.values)
        if not values:
            raise ExperimentDefinitionError(f"sweep dimension {self.key!r} must have values")
        serialized = [_json_key(value) for value in values]
        if len(serialized) != len(set(serialized)):
            raise ExperimentDefinitionError(f"sweep dimension {self.key!r} contains duplicate values")
        if not isinstance(self.label, str):
            raise ExperimentDefinitionError("sweep dimension label must be a string")
        object.__setattr__(self, "values", values)


@dataclass(frozen=True)
class ExperimentCandidate:
    """One tested settings patch in an experiment plan."""

    candidate_id: str
    overrides: Mapping[str, Any]

    def __post_init__(self) -> None:
        _non_empty_string(self.candidate_id, "candidate id")
        if not isinstance(self.overrides, Mapping):
            raise ExperimentDefinitionError("candidate overrides must be a JSON object")
        if any(not isinstance(key, str) or not key for key in self.overrides):
            raise ExperimentDefinitionError("candidate override keys must be non-empty strings")
        copied = _copy_json_value(dict(self.overrides), "candidate overrides")
        object.__setattr__(self, "overrides", MappingProxyType(copied))


@dataclass(frozen=True)
class ExperimentPlan:
    """An immutable description of a baseline and a finite candidate set."""

    plan_id: str
    module_id: str
    baseline_settings: Mapping[str, Any]
    dimensions: tuple[SweepDimension, ...]
    candidates: tuple[ExperimentCandidate, ...]
    parent_plan_id: str | None = None
    rationale: str | None = None

    def __post_init__(self) -> None:
        _non_empty_string(self.plan_id, "plan id")
        _non_empty_string(self.module_id, "module id")
        if self.parent_plan_id is not None:
            _non_empty_string(self.parent_plan_id, "parent plan id")
            if self.parent_plan_id == self.plan_id:
                raise ExperimentDefinitionError("a plan cannot be its own parent")
        if self.rationale is not None and not isinstance(self.rationale, str):
            raise ExperimentDefinitionError("plan rationale must be a string or None")
        if not isinstance(self.baseline_settings, Mapping):
            raise ExperimentDefinitionError("baseline settings must be a JSON object")
        if any(not isinstance(key, str) or not key for key in self.baseline_settings):
            raise ExperimentDefinitionError("baseline setting keys must be non-empty strings")
        baseline = _copy_json_value(dict(self.baseline_settings), "baseline settings")
        if any(not isinstance(key, str) or not key for key in baseline):
            raise ExperimentDefinitionError("baseline setting keys must be non-empty strings")

        dimensions = tuple(self.dimensions)
        candidates = tuple(self.candidates)
        if not dimensions:
            raise ExperimentDefinitionError("an experiment must have at least one sweep dimension")
        if not candidates:
            raise ExperimentDefinitionError("an experiment must have at least one candidate")
        if any(not isinstance(item, SweepDimension) for item in dimensions):
            raise ExperimentDefinitionError("experiment dimensions must be SweepDimension instances")
        if any(not isinstance(item, ExperimentCandidate) for item in candidates):
            raise ExperimentDefinitionError("experiment candidates must be ExperimentCandidate instances")

        keys = [dimension.key for dimension in dimensions]
        if len(keys) != len(set(keys)):
            raise ExperimentDefinitionError("sweep dimension keys must be unique")
        missing = [key for key in keys if key not in baseline]
        if missing:
            raise ExperimentDefinitionError(
                "baseline settings are missing sweep keys: " + ", ".join(missing)
            )

        candidate_ids = [candidate.candidate_id for candidate in candidates]
        if len(candidate_ids) != len(set(candidate_ids)):
            raise ExperimentDefinitionError("candidate ids must be unique")
        allowed = {dimension.key: {_json_key(v) for v in dimension.values} for dimension in dimensions}
        for candidate in candidates:
            unknown = set(candidate.overrides) - set(keys)
            if unknown:
                raise ExperimentDefinitionError(
                    f"candidate {candidate.candidate_id!r} overrides non-sweep keys: "
                    + ", ".join(sorted(unknown))
                )
            missing_candidate_keys = set(keys) - set(candidate.overrides)
            if missing_candidate_keys:
                raise ExperimentDefinitionError(
                    f"candidate {candidate.candidate_id!r} is missing sweep keys: "
                    + ", ".join(sorted(missing_candidate_keys))
                )
            for key, value in candidate.overrides.items():
                if _json_key(value) not in allowed[key]:
                    raise ExperimentDefinitionError(
                        f"candidate {candidate.candidate_id!r} has an out-of-range value for {key!r}"
                    )

        object.__setattr__(self, "baseline_settings", MappingProxyType(baseline))
        object.__setattr__(self, "dimensions", dimensions)
        object.__setattr__(self, "candidates", candidates)

    def settings_for(self, candidate_id: str) -> dict[str, Any]:
        """Return a detached full settings map for one candidate."""
        for candidate in self.candidates:
            if candidate.candidate_id == candidate_id:
                settings = deepcopy(dict(self.baseline_settings))
                settings.update(deepcopy(dict(candidate.overrides)))
                return settings
        raise ExperimentDefinitionError(f"unknown candidate id {candidate_id!r}")

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-ready, versioned representation of this plan."""
        return {
            "schema_version": 1,
            "plan_id": self.plan_id,
            "module_id": self.module_id,
            "baseline_settings": deepcopy(dict(self.baseline_settings)),
            "dimensions": [
                {"key": item.key, "values": deepcopy(list(item.values)), "label": item.label}
                for item in self.dimensions
            ],
            "candidates": [
                {"candidate_id": item.candidate_id, "overrides": deepcopy(dict(item.overrides))}
                for item in self.candidates
            ],
            "parent_plan_id": self.parent_plan_id,
            "rationale": self.rationale,
        }

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "ExperimentPlan":
        """Load a version-1 plan payload without filling in omitted fields."""
        required = {
            "schema_version",
            "plan_id",
            "module_id",
            "baseline_settings",
            "dimensions",
            "candidates",
            "parent_plan_id",
            "rationale",
        }
        _validate_payload(payload, required, "experiment plan")
        if type(payload["schema_version"]) is not int or payload["schema_version"] != 1:
            raise ExperimentDefinitionError("unsupported experiment plan schema_version")
        dimensions_raw = payload["dimensions"]
        candidates_raw = payload["candidates"]
        if isinstance(dimensions_raw, (str, bytes)) or not isinstance(dimensions_raw, Sequence):
            raise ExperimentDefinitionError("experiment plan dimensions must be a sequence")
        if isinstance(candidates_raw, (str, bytes)) or not isinstance(candidates_raw, Sequence):
            raise ExperimentDefinitionError("experiment plan candidates must be a sequence")
        dimensions = []
        for item in dimensions_raw:
            _validate_payload(item, {"key", "values", "label"}, "sweep dimension")
            dimensions.append(SweepDimension(item["key"], item["values"], item["label"]))
        candidates = []
        for item in candidates_raw:
            _validate_payload(item, {"candidate_id", "overrides"}, "experiment candidate")
            candidates.append(ExperimentCandidate(item["candidate_id"], item["overrides"]))
        return cls(
            plan_id=payload["plan_id"],
            module_id=payload["module_id"],
            baseline_settings=payload["baseline_settings"],
            dimensions=tuple(dimensions),
            candidates=tuple(candidates),
            parent_plan_id=payload["parent_plan_id"],
            rationale=payload["rationale"],
        )


@dataclass(frozen=True)
class CandidateAssessment:
    """Manual observations for one printed candidate."""

    candidate_id: str
    ratings: Mapping[str, int] | None = None
    defect_tags: tuple[str, ...] = ()
    notes: str = ""
    verdict: str | None = None
    photo_paths: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        _non_empty_string(self.candidate_id, "assessment candidate id")
        ratings = {} if self.ratings is None else self.ratings
        if not isinstance(ratings, Mapping):
            raise ExperimentDefinitionError("assessment ratings must be an object")
        clean_ratings: dict[str, int] = {}
        for name, rating in ratings.items():
            _non_empty_string(name, "rating name")
            if isinstance(rating, bool) or not isinstance(rating, int) or not 1 <= rating <= 5:
                raise ExperimentDefinitionError("ratings must be integers from 1 through 5")
            clean_ratings[name] = rating

        if isinstance(self.defect_tags, (str, bytes)) or not isinstance(self.defect_tags, Sequence):
            raise ExperimentDefinitionError("defect tags must be a sequence of non-empty strings")
        tags = tuple(_non_empty_string(tag, "defect tag") for tag in self.defect_tags)
        if len(tags) != len(set(tags)):
            raise ExperimentDefinitionError("defect tags must be unique")
        if not isinstance(self.notes, str):
            raise ExperimentDefinitionError("assessment notes must be a string")
        if self.verdict is not None and (
            not isinstance(self.verdict, str)
            or self.verdict not in {"pass", "fail", "uncertain", "missing"}
        ):
            raise ExperimentDefinitionError(
                "verdict must be pass, fail, uncertain, missing, or None"
            )
        if self.verdict in {"uncertain", "missing"} and clean_ratings:
            raise ExperimentDefinitionError(
                f"{self.verdict} candidates cannot have numeric ratings"
            )
        if isinstance(self.photo_paths, (str, bytes)) or not isinstance(self.photo_paths, Sequence):
            raise ExperimentDefinitionError("photo paths must be a sequence of non-empty strings")
        photo_paths = tuple(_non_empty_string(path, "photo path") for path in self.photo_paths)
        if len(photo_paths) != len(set(photo_paths)):
            raise ExperimentDefinitionError("photo paths must be unique")
        object.__setattr__(self, "ratings", MappingProxyType(clean_ratings))
        object.__setattr__(self, "defect_tags", tags)
        object.__setattr__(self, "photo_paths", photo_paths)


@dataclass(frozen=True)
class ExperimentResults:
    """Manual decisions recorded against a specific experiment plan."""

    plan_id: str
    assessments: tuple[CandidateAssessment, ...]
    selected_candidate_id: str | None = None
    accepted: bool | None = None
    tied_candidate_ids: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        _non_empty_string(self.plan_id, "results plan id")
        assessments = tuple(self.assessments)
        if any(not isinstance(item, CandidateAssessment) for item in assessments):
            raise ExperimentDefinitionError("results assessments must be CandidateAssessment instances")
        if self.selected_candidate_id is not None:
            _non_empty_string(self.selected_candidate_id, "selected candidate id")
        if self.accepted is not None and not isinstance(self.accepted, bool):
            raise ExperimentDefinitionError("accepted must be True, False, or None")
        if self.accepted is True and self.selected_candidate_id is None:
            raise ExperimentDefinitionError("accepted results must select a candidate")
        if isinstance(self.tied_candidate_ids, (str, bytes)) or not isinstance(
            self.tied_candidate_ids, Sequence
        ):
            raise ExperimentDefinitionError("tied candidate ids must be a sequence")
        tied_ids = tuple(_non_empty_string(item, "tied candidate id") for item in self.tied_candidate_ids)
        if len(tied_ids) != len(set(tied_ids)):
            raise ExperimentDefinitionError("tied candidate ids must be unique")
        if self.selected_candidate_id in tied_ids:
            raise ExperimentDefinitionError("the selected candidate cannot also be a tie candidate")
        if tied_ids and self.selected_candidate_id is None:
            raise ExperimentDefinitionError("tie candidates require a selected candidate")
        if self.accepted is True and tied_ids:
            raise ExperimentDefinitionError("tied results cannot be accepted")
        object.__setattr__(self, "assessments", assessments)
        object.__setattr__(self, "tied_candidate_ids", tied_ids)

    def validate_for(self, plan: ExperimentPlan) -> None:
        """Raise if any entered result refers to a different or unknown plan item."""
        if not isinstance(plan, ExperimentPlan):
            raise ExperimentStateError("results must be validated against an ExperimentPlan")
        if self.plan_id != plan.plan_id:
            raise ExperimentStateError(
                f"results for plan {self.plan_id!r} cannot be applied to {plan.plan_id!r}"
            )
        known = {candidate.candidate_id for candidate in plan.candidates}
        assessment_ids = [assessment.candidate_id for assessment in self.assessments]
        if len(assessment_ids) != len(set(assessment_ids)):
            raise ExperimentDefinitionError("a candidate may only be assessed once")
        assessed = set(assessment_ids)
        unknown = assessed - known
        if unknown:
            raise ExperimentStateError("unknown assessed candidates: " + ", ".join(sorted(unknown)))
        if self.selected_candidate_id is not None:
            if self.selected_candidate_id not in known:
                raise ExperimentStateError(f"unknown selected candidate {self.selected_candidate_id!r}")
            if self.selected_candidate_id not in assessed:
                raise ExperimentStateError("selected candidate must have a manual assessment")
        tied = set(self.tied_candidate_ids)
        unknown_ties = tied - known
        if unknown_ties:
            raise ExperimentStateError("unknown tied candidates: " + ", ".join(sorted(unknown_ties)))
        unassessed_ties = tied - assessed
        if unassessed_ties:
            raise ExperimentStateError(
                "tie candidates must have manual assessments: " + ", ".join(sorted(unassessed_ties))
            )
        if self.accepted is True:
            by_id = {item.candidate_id: item for item in self.assessments}
            if assessed != known or any(by_id[item].verdict not in {"pass", "fail"} for item in known):
                raise ExperimentStateError("accepted results require an explicit pass or fail for every candidate")
            if by_id[self.selected_candidate_id].verdict != "pass":
                raise ExperimentStateError("the accepted candidate must have a pass verdict")

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-ready, versioned representation of manual results."""
        return {
            "schema_version": 2,
            "plan_id": self.plan_id,
            "assessments": [
                {
                    "candidate_id": item.candidate_id,
                    "ratings": dict(item.ratings),
                    "defect_tags": list(item.defect_tags),
                    "notes": item.notes,
                    "verdict": item.verdict,
                    "photo_paths": list(item.photo_paths),
                }
                for item in self.assessments
            ],
            "selected_candidate_id": self.selected_candidate_id,
            "accepted": self.accepted,
            "tied_candidate_ids": list(self.tied_candidate_ids),
        }

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "ExperimentResults":
        """Load result payloads from versions 1 and 2."""
        base_required = {
            "schema_version",
            "plan_id",
            "assessments",
            "selected_candidate_id",
            "accepted",
        }
        if not isinstance(payload, Mapping):
            raise ExperimentDefinitionError("experiment results payload must be an object")
        version = payload.get("schema_version")
        if type(version) is not int or version not in {1, 2}:
            raise ExperimentDefinitionError("unsupported experiment results schema_version")
        required = base_required if version == 1 else base_required | {"tied_candidate_ids"}
        _validate_payload(payload, required, "experiment results")
        assessments_raw = payload["assessments"]
        if isinstance(assessments_raw, (str, bytes)) or not isinstance(assessments_raw, Sequence):
            raise ExperimentDefinitionError("experiment assessments must be a sequence")
        assessments = []
        for item in assessments_raw:
            assessment_keys = {"candidate_id", "ratings", "defect_tags", "notes"}
            if version == 2:
                assessment_keys |= {"verdict", "photo_paths"}
            _validate_payload(item, assessment_keys, "assessment")
            assessments.append(
                CandidateAssessment(
                    candidate_id=item["candidate_id"],
                    ratings=item["ratings"],
                    defect_tags=item["defect_tags"],
                    notes=item["notes"],
                    verdict=None if version == 1 else item["verdict"],
                    photo_paths=() if version == 1 else item["photo_paths"],
                )
            )
        return cls(
            plan_id=payload["plan_id"],
            assessments=tuple(assessments),
            selected_candidate_id=payload["selected_candidate_id"],
            # Version 1 had no explicit verdict, so a historical acceptance
            # cannot satisfy the v2 evidence gate until the user reviews it.
            accepted=(None if version == 1 and payload["accepted"] is True else payload["accepted"]),
            tied_candidate_ids=() if version == 1 else payload["tied_candidate_ids"],
        )


def _validate_payload(payload: Any, required_keys: set[str], name: str) -> None:
    if not isinstance(payload, Mapping):
        raise ExperimentDefinitionError(f"{name} payload must be an object")
    actual_keys = set(payload)
    missing = required_keys - actual_keys
    extra = actual_keys - required_keys
    if missing or extra:
        details = []
        if missing:
            details.append("missing keys: " + ", ".join(sorted(str(key) for key in missing)))
        if extra:
            details.append("unexpected keys: " + ", ".join(sorted(str(key) for key in extra)))
        raise ExperimentDefinitionError(f"invalid {name} payload (" + "; ".join(details) + ")")


def create_grid_experiment(
    *,
    plan_id: str,
    module_id: str,
    baseline_settings: Mapping[str, Any],
    dimensions: Sequence[SweepDimension],
    candidate_prefix: str = "C",
    parent_plan_id: str | None = None,
    max_candidates: int = 64,
    rationale: str | None = None,
) -> ExperimentPlan:
    """Create a stable row-major Cartesian product of dimension values."""
    _non_empty_string(candidate_prefix, "candidate prefix")
    if isinstance(max_candidates, bool) or not isinstance(max_candidates, int) or max_candidates < 1:
        raise ExperimentDefinitionError("max_candidates must be a positive integer")
    dimension_tuple = tuple(dimensions)
    if any(not isinstance(item, SweepDimension) for item in dimension_tuple):
        raise ExperimentDefinitionError("dimensions must contain SweepDimension instances")
    keys = [dimension.key for dimension in dimension_tuple]
    if len(keys) != len(set(keys)):
        raise ExperimentDefinitionError("sweep dimension keys must be unique")
    if not dimension_tuple:
        raise ExperimentDefinitionError("an experiment must have at least one sweep dimension")
    count = math.prod(len(dimension.values) for dimension in dimension_tuple)
    if count > max_candidates:
        raise ExperimentDefinitionError(
            f"grid has {count} candidates, exceeding max_candidates={max_candidates}"
        )
    if not isinstance(baseline_settings, Mapping):
        raise ExperimentDefinitionError("baseline settings must be a JSON object")
    if any(not isinstance(key, str) or not key for key in baseline_settings):
        raise ExperimentDefinitionError("baseline setting keys must be non-empty strings")
    missing = [key for key in keys if key not in baseline_settings]
    if missing:
        raise ExperimentDefinitionError("baseline settings are missing sweep keys: " + ", ".join(missing))

    width = max(3, len(str(count)))
    candidates = []
    for index, values in enumerate(product(*(dimension.values for dimension in dimension_tuple)), start=1):
        overrides = {
            dimension.key: deepcopy(value)
            for dimension, value in zip(dimension_tuple, values)
        }
        candidates.append(
            ExperimentCandidate(candidate_id=f"{candidate_prefix}{index:0{width}d}", overrides=overrides)
        )
    return ExperimentPlan(
        plan_id=plan_id,
        module_id=module_id,
        baseline_settings=baseline_settings,
        dimensions=dimension_tuple,
        candidates=tuple(candidates),
        parent_plan_id=parent_plan_id,
        rationale=rationale,
    )
