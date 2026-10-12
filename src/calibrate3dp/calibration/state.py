"""Immutable calibration lifecycle values, independent of the UI."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, field
from enum import Enum
from types import MappingProxyType
from typing import Mapping, TypeAlias


JsonValue: TypeAlias = (
    None | bool | int | float | str | list["JsonValue"] | dict[str, "JsonValue"]
)


class CalibrationStatus(str, Enum):
    UNTESTED = "untested"
    BLOCKED = "blocked"
    READY = "ready"
    IN_PROGRESS = "in_progress"
    NEEDS_REVIEW = "needs_review"
    ACCEPTED = "accepted"
    STALE = "stale"


def freeze_json(value: object) -> object:
    """Copy JSON-shaped data into recursively immutable containers."""
    if isinstance(value, Mapping):
        return frozen_mapping(value, "JSON object")
    if isinstance(value, (list, tuple)):
        return tuple(freeze_json(item) for item in value)
    if isinstance(value, (set, frozenset)):
        return tuple(freeze_json(item) for item in sorted(value, key=repr))
    return deepcopy(value)


def frozen_mapping(values: Mapping[str, object], field_name: str) -> Mapping[str, object]:
    if not isinstance(values, Mapping):
        raise TypeError(f"{field_name} must be a mapping")
    if any(not isinstance(key, str) for key in values):
        raise TypeError(f"{field_name} keys must be strings")
    return MappingProxyType({key: freeze_json(value) for key, value in values.items()})


@dataclass(frozen=True)
class CalibrationEvidence:
    """Latest saved evidence for one calibration family."""

    latest_run_status: str | None = None
    captured_input_values: Mapping[str, JsonValue] = field(default_factory=dict)
    assessment_state: str | None = None
    accepted_decision_evidence: bool = False
    run_id: str | None = None
    assessment_revision_id: str | None = None
    decision_id: str | None = None

    def __post_init__(self) -> None:
        if self.latest_run_status is not None and not isinstance(self.latest_run_status, str):
            raise TypeError("latest_run_status must be a string or None")
        if self.assessment_state is not None and not isinstance(self.assessment_state, str):
            raise TypeError("assessment_state must be a string or None")
        if not isinstance(self.accepted_decision_evidence, bool):
            raise TypeError("accepted_decision_evidence must be a bool")
        object.__setattr__(
            self,
            "captured_input_values",
            frozen_mapping(self.captured_input_values, "captured_input_values"),
        )


@dataclass(frozen=True)
class DependencyRuleOutcome:
    """One evaluated prerequisite or recommendation."""

    rule_id: str
    severity: str
    passed: bool
    message: str = ""

    def __post_init__(self) -> None:
        if not isinstance(self.rule_id, str) or not self.rule_id.strip():
            raise ValueError("rule_id must be a non-empty string")
        if self.severity not in {"required", "recommended"}:
            raise ValueError("severity must be 'required' or 'recommended'")
        if not isinstance(self.passed, bool):
            raise TypeError("passed must be a bool")
        if not isinstance(self.message, str):
            raise TypeError("message must be a string")


@dataclass(frozen=True)
class CalibrationState:
    """Evaluated lifecycle status and the context/rules explaining it."""

    calibration_id: str
    status: CalibrationStatus
    can_start: bool
    reasons: tuple[str, ...] = ()
    recommendations: tuple[str, ...] = ()
    input_snapshot: Mapping[str, JsonValue] = field(default_factory=dict)
    rule_outcomes: tuple[DependencyRuleOutcome, ...] = ()

    def __post_init__(self) -> None:
        if not self.calibration_id:
            raise ValueError("calibration_id must not be empty")
        if not isinstance(self.status, CalibrationStatus):
            object.__setattr__(self, "status", CalibrationStatus(self.status))
        if not isinstance(self.can_start, bool):
            raise TypeError("can_start must be a bool")
        object.__setattr__(self, "reasons", tuple(str(item) for item in self.reasons))
        object.__setattr__(
            self,
            "recommendations",
            tuple(str(item) for item in self.recommendations),
        )
        object.__setattr__(self, "input_snapshot", frozen_mapping(self.input_snapshot, "input_snapshot"))
        outcomes = tuple(self.rule_outcomes)
        if any(not isinstance(item, DependencyRuleOutcome) for item in outcomes):
            raise TypeError("rule_outcomes must contain DependencyRuleOutcome values")
        outcome_ids = [item.rule_id for item in outcomes]
        if len(outcome_ids) != len(set(outcome_ids)):
            raise ValueError("rule_outcomes must have unique rule IDs")
        object.__setattr__(self, "rule_outcomes", outcomes)
