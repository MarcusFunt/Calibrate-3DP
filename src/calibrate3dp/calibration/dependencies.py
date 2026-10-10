"""Deterministic evaluation of calibration prerequisites and lifecycle state."""

from __future__ import annotations

from dataclasses import dataclass
from types import MappingProxyType
from typing import Iterable, Mapping

from .state import (
    CalibrationEvidence,
    CalibrationState,
    CalibrationStatus,
    JsonValue,
    frozen_mapping,
    freeze_json,
)


_SOURCE_KINDS = frozenset({"input", "calibration_result"})
_OPERATORS = frozenset({"equals", "one_of", "present", "accepted"})
_SEVERITIES = frozenset({"required", "recommended"})
_ACTIVE_RUN_STATUSES = frozenset({"generating", "in_progress"})
_SUCCESS_RUN_STATUSES = frozenset({"settings_validated", "succeeded", "success", "completed"})
_FAILED_RUN_STATUSES = frozenset({"failed", "canceled", "cancelled"})


@dataclass(frozen=True)
class DependencyRule:
    """One inspectable, serializable prerequisite or recommendation."""

    rule_id: str
    calibration_id: str
    source_kind: str
    source_key: str
    operator: str
    expected_value: JsonValue = None
    severity: str = "required"
    invalidates: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        for field_name in ("rule_id", "calibration_id", "source_key"):
            value = getattr(self, field_name)
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"{field_name} must be a non-empty string")
        if self.source_kind not in _SOURCE_KINDS:
            raise ValueError(f"unsupported source_kind {self.source_kind!r}")
        if self.operator not in _OPERATORS:
            raise ValueError(f"unsupported operator {self.operator!r}")
        if self.severity not in _SEVERITIES:
            raise ValueError(f"unsupported severity {self.severity!r}")
        if self.operator == "accepted" and self.source_kind != "calibration_result":
            raise ValueError("accepted operator requires a calibration_result source")
        if self.operator == "one_of" and not isinstance(self.expected_value, (list, tuple, set, frozenset)):
            raise ValueError("one_of expected_value must be a sequence")
        invalidates = tuple(self.invalidates)
        if any(not isinstance(key, str) or not key.strip() for key in invalidates):
            raise ValueError("invalidates keys must be non-empty strings")
        object.__setattr__(self, "expected_value", freeze_json(self.expected_value))
        object.__setattr__(self, "invalidates", tuple(sorted(set(invalidates))))


@dataclass(frozen=True)
class DependencyContext:
    """Current resolved values; absent keys are unknown, present nulls are known."""

    values: Mapping[str, JsonValue]

    def __post_init__(self) -> None:
        if not isinstance(self.values, Mapping):
            raise TypeError("values must be a mapping")
        object.__setattr__(self, "values", frozen_mapping(self.values, "values"))


@dataclass(frozen=True)
class DependencyGraph:
    """Validated rules and prerequisite edges for a calibration catalog."""

    rules: tuple[DependencyRule, ...]
    calibration_ids: tuple[str, ...]
    dependencies: Mapping[str, tuple[str, ...]]

    @classmethod
    def from_rules(cls, rules: Iterable[DependencyRule]) -> "DependencyGraph":
        normalized = tuple(rules)
        if any(not isinstance(rule, DependencyRule) for rule in normalized):
            raise TypeError("rules must contain DependencyRule values")
        ids = [rule.rule_id for rule in normalized]
        duplicate_ids = sorted({rule_id for rule_id in ids if ids.count(rule_id) > 1})
        if duplicate_ids:
            raise ValueError(f"duplicate rule ID(s): {', '.join(duplicate_ids)}")

        calibration_ids = tuple(sorted({rule.calibration_id for rule in normalized}))
        known = set(calibration_ids)
        deps: dict[str, set[str]] = {calibration_id: set() for calibration_id in calibration_ids}
        for rule in normalized:
            if rule.source_kind == "calibration_result":
                if rule.source_key not in known:
                    raise ValueError(
                        f"unknown calibration reference {rule.source_key!r} in rule {rule.rule_id!r}"
                    )
                deps[rule.calibration_id].add(rule.source_key)

        _validate_acyclic(deps)
        ordered = tuple(sorted(normalized, key=lambda rule: (rule.calibration_id, rule.rule_id)))
        frozen_dependencies = MappingProxyType({
            key: tuple(sorted(values)) for key, values in sorted(deps.items())
        })
        return cls(ordered, calibration_ids, frozen_dependencies)


class DependencyEvaluator:
    """Pure deterministic evaluator for graph rules and saved calibration evidence."""

    def __init__(self, graph: DependencyGraph) -> None:
        if not isinstance(graph, DependencyGraph):
            raise TypeError("graph must be a DependencyGraph")
        self.graph = graph

    def evaluate(
        self,
        context: DependencyContext,
        records: Mapping[str, CalibrationEvidence],
    ) -> tuple[CalibrationState, ...]:
        if not isinstance(context, DependencyContext):
            raise TypeError("context must be a DependencyContext")
        if not isinstance(records, Mapping):
            raise TypeError("records must be a mapping keyed by calibration ID")
        for key, evidence in records.items():
            if key not in self.graph.calibration_ids:
                raise ValueError(f"unknown calibration evidence key {key!r}")
            if not isinstance(evidence, CalibrationEvidence):
                raise TypeError(f"evidence for {key!r} must be a CalibrationEvidence")

        rules_by_calibration: dict[str, list[DependencyRule]] = {
            calibration_id: [] for calibration_id in self.graph.calibration_ids
        }
        for rule in self.graph.rules:
            rules_by_calibration[rule.calibration_id].append(rule)

        state_by_calibration: dict[str, CalibrationState] = {}
        # Graph validation guarantees prerequisites precede dependents in this order.
        for calibration_id in _dependency_order(self.graph):
            evidence = records.get(calibration_id)
            rule_reasons: list[tuple[str, str]] = []
            rule_recommendations: list[tuple[str, str]] = []
            for rule in rules_by_calibration[calibration_id]:
                passed, explanation = _evaluate_rule(rule, context, records, state_by_calibration)
                if passed:
                    continue
                message = f"{rule.rule_id}: {explanation}"
                if rule.severity == "required":
                    rule_reasons.append((rule.rule_id, message))
                else:
                    rule_recommendations.append((rule.rule_id, message))

            reasons = tuple(message for _, message in sorted(rule_reasons))
            recommendations = tuple(message for _, message in sorted(rule_recommendations))
            can_start = not reasons
            input_snapshot = _input_snapshot(self.graph, calibration_id, context)
            status = _lifecycle_status(
                calibration_id,
                evidence,
                context,
                rules_by_calibration[calibration_id],
                can_start,
            )
            state_by_calibration[calibration_id] = CalibrationState(
                calibration_id=calibration_id,
                status=status,
                can_start=can_start,
                reasons=reasons,
                recommendations=recommendations,
                input_snapshot=input_snapshot,
            )

        return tuple(state_by_calibration[key] for key in sorted(state_by_calibration))

    @staticmethod
    def affected_by_change(
        changed_keys: Iterable[str], graph: DependencyGraph
    ) -> frozenset[str]:
        """Return direct input dependents plus all downstream result dependents."""
        changed = frozenset(changed_keys)
        if any(not isinstance(key, str) for key in changed):
            raise TypeError("changed_keys must contain strings")
        affected: set[str] = set()
        for rule in graph.rules:
            if rule.source_kind == "input" and (
                rule.source_key in changed or changed.intersection(rule.invalidates)
            ):
                affected.add(rule.calibration_id)

        grew = True
        while grew:
            grew = False
            for rule in graph.rules:
                if (
                    rule.source_kind == "calibration_result"
                    and rule.source_key in affected
                    and rule.calibration_id not in affected
                ):
                    affected.add(rule.calibration_id)
                    grew = True
        return frozenset(affected)


def _evaluate_rule(
    rule: DependencyRule,
    context: DependencyContext,
    records: Mapping[str, CalibrationEvidence],
    evaluated: Mapping[str, CalibrationState],
) -> tuple[bool, str]:
    if rule.source_kind == "input":
        if rule.source_key not in context.values:
            return False, f"required input {rule.source_key!r} is unknown (not supplied)"
        actual = context.values[rule.source_key]
        if rule.operator == "equals":
            passed = actual == rule.expected_value
            return passed, _comparison_message(rule, actual, "equal to")
        if rule.operator == "one_of":
            expected = tuple(rule.expected_value)  # type: ignore[arg-type]
            passed = actual in expected
            return passed, _comparison_message(rule, actual, "one of")
        if rule.operator == "present":
            passed = actual is not None
            return passed, f"input {rule.source_key!r} is null" if not passed else ""
        return False, f"operator {rule.operator!r} cannot be used with an input"

    evidence = records.get(rule.source_key)
    prerequisite_state = evaluated.get(rule.source_key)
    if evidence is None:
        return False, f"calibration result {rule.source_key!r} is unknown"
    current_accepted = _has_current_acceptance(rule.source_key, evidence, prerequisite_state)
    if rule.operator == "accepted":
        if current_accepted:
            return True, ""
        if evidence.accepted_decision_evidence and prerequisite_state is not None and prerequisite_state.status == CalibrationStatus.STALE:
            return False, f"calibration result {rule.source_key!r} is stale"
        return False, f"calibration result {rule.source_key!r} has no current accepted decision"
    if rule.operator == "present":
        passed = evidence.latest_run_status is not None
        return passed, f"calibration result {rule.source_key!r} has no saved run" if not passed else ""
    actual_status = evidence.latest_run_status
    if actual_status is None:
        return False, f"calibration result {rule.source_key!r} has no saved run"
    if rule.operator == "equals":
        passed = actual_status == rule.expected_value
        return passed, _comparison_message(rule, actual_status, "equal to")
    if rule.operator == "one_of":
        expected = tuple(rule.expected_value)  # type: ignore[arg-type]
        passed = actual_status in expected
        return passed, _comparison_message(rule, actual_status, "one of")
    return False, f"operator {rule.operator!r} cannot be used with a calibration result"


def _comparison_message(rule: DependencyRule, actual: object, comparator: str) -> str:
    if comparator == "one of":
        expected = tuple(rule.expected_value)  # type: ignore[arg-type]
        return (
            f"input/result {rule.source_key!r} has value {actual!r}; expected one of {expected!r}"
        )
    return f"input/result {rule.source_key!r} has value {actual!r}; expected {comparator} {rule.expected_value!r}"


def _input_snapshot(
    graph: DependencyGraph, calibration_id: str, context: DependencyContext
) -> Mapping[str, JsonValue]:
    keys = {
        key
        for rule in graph.rules
        if rule.calibration_id == calibration_id and rule.source_kind == "input"
        for key in (rule.source_key, *rule.invalidates)
    }
    return MappingProxyType({
        key: freeze_json(context.values[key])  # type: ignore[dict-item]
        for key in sorted(keys)
        if key in context.values
    })


def _evidence_is_stale(
    evidence: CalibrationEvidence,
    context: DependencyContext,
    rules: Iterable[DependencyRule],
) -> bool:
    invalidated_keys = {
        key
        for rule in rules
        for key in rule.invalidates
    }
    for key in invalidated_keys:
        if key not in context.values or key not in evidence.captured_input_values:
            return True
        if context.values[key] != evidence.captured_input_values[key]:
            return True
    return False


def _has_current_acceptance(
    calibration_id: str,
    evidence: CalibrationEvidence,
    state: CalibrationState | None,
) -> bool:
    del calibration_id
    return (
        evidence.latest_run_status in _SUCCESS_RUN_STATUSES
        and evidence.assessment_state == "accepted"
        and evidence.accepted_decision_evidence
        and state is not None
        and state.status != CalibrationStatus.STALE
    )


def _lifecycle_status(
    calibration_id: str,
    evidence: CalibrationEvidence | None,
    context: DependencyContext,
    rules: Iterable[DependencyRule],
    can_start: bool,
) -> CalibrationStatus:
    if evidence is None or evidence.latest_run_status is None:
        return CalibrationStatus.BLOCKED if not can_start else CalibrationStatus.UNTESTED

    run_status = evidence.latest_run_status.casefold()
    if run_status in _ACTIVE_RUN_STATUSES:
        return CalibrationStatus.IN_PROGRESS

    if _evidence_is_stale(evidence, context, rules):
        return CalibrationStatus.STALE

    if (
        evidence.latest_run_status in _SUCCESS_RUN_STATUSES
        and evidence.assessment_state == "accepted"
        and evidence.accepted_decision_evidence
    ):
        return CalibrationStatus.ACCEPTED

    if evidence.latest_run_status in _SUCCESS_RUN_STATUSES and evidence.assessment_state is None:
        return CalibrationStatus.NEEDS_REVIEW

    if not can_start:
        return CalibrationStatus.BLOCKED
    return CalibrationStatus.READY


def _dependency_order(graph: DependencyGraph) -> tuple[str, ...]:
    ordered: list[str] = []
    visited: set[str] = set()

    def visit(calibration_id: str) -> None:
        if calibration_id in visited:
            return
        visited.add(calibration_id)
        for prerequisite in graph.dependencies[calibration_id]:
            visit(prerequisite)
        ordered.append(calibration_id)

    for calibration_id in graph.calibration_ids:
        visit(calibration_id)
    return tuple(ordered)


def _validate_acyclic(dependencies: Mapping[str, set[str]]) -> None:
    visiting: set[str] = set()
    visited: set[str] = set()

    def visit(calibration_id: str, trail: tuple[str, ...]) -> None:
        if calibration_id in visiting:
            cycle = " -> ".join((*trail, calibration_id))
            raise ValueError(f"dependency cycle detected: {cycle}")
        if calibration_id in visited:
            return
        visiting.add(calibration_id)
        for prerequisite in sorted(dependencies[calibration_id]):
            visit(prerequisite, (*trail, calibration_id))
        visiting.remove(calibration_id)
        visited.add(calibration_id)

    for calibration_id in sorted(dependencies):
        visit(calibration_id, ())
