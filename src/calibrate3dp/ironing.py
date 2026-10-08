"""Initial and adaptive manual calibration plans for Orca-style ironing."""

from __future__ import annotations

from decimal import Decimal, InvalidOperation
from typing import Any, Mapping, Sequence

from .experiments import (
    ExperimentDefinitionError,
    ExperimentPlan,
    ExperimentResults,
    SweepDimension,
    create_grid_experiment,
)


class IroningCalibrationError(ExperimentDefinitionError):
    """Raised when an ironing plan cannot be created or refined safely."""


class RefinementLimitError(IroningCalibrationError):
    """Raised when a proposed refinement crosses a configured range limit."""


_FLOW = "ironing_flow"
_SPEED = "ironing_speed"
_IRONING_KEYS = (_FLOW, _SPEED)


def _decimal(value: Any, name: str, *, allow_percent: bool = False) -> Decimal:
    if isinstance(value, bool) or value is None:
        raise IroningCalibrationError(f"{name} must be a finite number")
    try:
        rendered = str(value).strip()
        if rendered.endswith("%"):
            if not allow_percent:
                raise IroningCalibrationError(f"{name} must not use a percent unit")
            rendered = rendered[:-1].strip()
        parsed = Decimal(rendered)
    except (InvalidOperation, ValueError, AttributeError) as exc:
        raise IroningCalibrationError(f"{name} must be a finite number") from exc
    if not parsed.is_finite():
        raise IroningCalibrationError(f"{name} must be a finite number")
    return parsed


def _numeric_sweep(values: Sequence[Any], key: str) -> tuple[Decimal, ...]:
    if isinstance(values, (str, bytes)) or not isinstance(values, Sequence):
        raise IroningCalibrationError(f"{key} sweep values must be a sequence")
    parsed = tuple(
        _decimal(value, f"{key} sweep value", allow_percent=key == _FLOW)
        for value in values
    )
    if len(parsed) < 3:
        raise IroningCalibrationError(f"{key} sweep requires at least three values")
    ordered = tuple(sorted(parsed))
    if len(set(ordered)) != len(ordered):
        raise IroningCalibrationError(f"{key} sweep values must be distinct")
    step = ordered[1] - ordered[0]
    if step <= 0 or any(right - left != step for left, right in zip(ordered, ordered[1:])):
        raise IroningCalibrationError(f"{key} sweep values must be equally spaced")
    if key == _FLOW and ordered[0] < 0:
        raise IroningCalibrationError("ironing_flow sweep values must be non-negative")
    if key == _SPEED and ordered[0] <= 0:
        raise IroningCalibrationError("ironing_speed sweep values must be greater than zero")
    return ordered


def _profile_number(value: Decimal, exemplar: Any, key: str) -> Any:
    """Format generated values like the imported profile's numeric field."""
    if isinstance(exemplar, str):
        percent_unit = key == _FLOW and exemplar.strip().endswith("%")
        rendered = format(value, "f")
        if "." in rendered:
            rendered = rendered.rstrip("0").rstrip(".")
        rendered = "0" if rendered in ("-0", "") else rendered
        return f"{rendered}%" if percent_unit else rendered
    if isinstance(exemplar, bool) or not isinstance(exemplar, (int, float)):
        raise IroningCalibrationError("ironing baseline values must be numeric strings or numbers")
    if isinstance(exemplar, int) and value == value.to_integral_value():
        return int(value)
    return float(value)


def _baseline_value(settings: Mapping[str, Any], key: str) -> Any:
    if key not in settings:
        raise IroningCalibrationError(f"baseline settings are missing {key!r}")
    value = settings[key]
    numeric = _decimal(value, f"baseline {key}", allow_percent=key == _FLOW)
    if isinstance(value, bool) or not isinstance(value, (str, int, float)):
        raise IroningCalibrationError(f"baseline {key} must be a numeric string or number")
    if key == _FLOW and numeric < 0:
        raise IroningCalibrationError("baseline ironing_flow must be non-negative")
    if key == _SPEED and numeric <= 0:
        raise IroningCalibrationError("baseline ironing_speed must be greater than zero")
    return value


def create_initial_ironing_experiment(
    *,
    plan_id: str,
    baseline_settings: Mapping[str, Any],
    flow_values: Sequence[Any],
    speed_values: Sequence[Any],
    max_candidates: int = 16,
) -> ExperimentPlan:
    """Create a sorted, uniform, two-axis ironing grid from explicit inputs."""
    if not isinstance(baseline_settings, Mapping):
        raise IroningCalibrationError("baseline settings must be a profile settings object")
    try:
        flow_baseline = _baseline_value(baseline_settings, _FLOW)
        speed_baseline = _baseline_value(baseline_settings, _SPEED)
        flow = _numeric_sweep(flow_values, _FLOW)
        speed = _numeric_sweep(speed_values, _SPEED)
        dimensions = (
            SweepDimension(_FLOW, tuple(_profile_number(v, flow_baseline, _FLOW) for v in flow), "Ironing flow"),
            SweepDimension(_SPEED, tuple(_profile_number(v, speed_baseline, _SPEED) for v in speed), "Ironing speed"),
        )
        return create_grid_experiment(
            plan_id=plan_id,
            module_id="ironing",
            baseline_settings=baseline_settings,
            dimensions=dimensions,
            candidate_prefix="I",
            max_candidates=max_candidates,
            rationale="Initial ironing flow and speed sweep; all other profile settings are held at baseline.",
        )
    except ExperimentDefinitionError as exc:
        if isinstance(exc, IroningCalibrationError):
            raise
        raise IroningCalibrationError(str(exc)) from exc


def _validate_limits(
    limits: Mapping[str, tuple[Any, Any]] | None,
) -> dict[str, tuple[Decimal, Decimal]]:
    if limits is None:
        return {}
    if not isinstance(limits, Mapping):
        raise IroningCalibrationError("limits must map ironing setting names to (minimum, maximum)")
    if any(not isinstance(key, str) for key in limits):
        raise IroningCalibrationError("ironing limit names must be strings")
    unknown = set(limits) - set(_IRONING_KEYS)
    if unknown:
        raise IroningCalibrationError("unknown ironing limit keys: " + ", ".join(sorted(unknown)))
    parsed: dict[str, tuple[Decimal, Decimal]] = {}
    for key, bounds in limits.items():
        if isinstance(bounds, (str, bytes)) or not isinstance(bounds, Sequence) or len(bounds) != 2:
            raise IroningCalibrationError(f"limits for {key!r} must be a (minimum, maximum) pair")
        lower, upper = (
            _decimal(value, f"{key} limit", allow_percent=key == _FLOW)
            for value in bounds
        )
        if lower > upper:
            raise IroningCalibrationError(f"minimum {key} limit must not exceed its maximum")
        parsed[key] = (lower, upper)
    return parsed


def _refined_values(
    key: str,
    current_values: Sequence[Any],
    winner: Any,
    exemplar: Any,
    limits: tuple[Decimal, Decimal] | None,
) -> tuple[tuple[Any, ...], str]:
    points = _numeric_sweep(current_values, key)
    winner_value = _decimal(
        winner,
        f"selected candidate {key}",
        allow_percent=key == _FLOW,
    )
    if winner_value not in points:
        raise IroningCalibrationError(f"selected candidate value for {key!r} is outside its sweep")
    index = points.index(winner_value)
    half_step = (points[1] - points[0]) / Decimal(2)
    if index == 0:
        start = winner_value - half_step * (len(points) - 1)
        mode = "lower boundary extension"
    elif index == len(points) - 1:
        start = winner_value
        mode = "upper boundary extension"
    else:
        winner_index = (len(points) - 1) // 2
        start = winner_value - half_step * winner_index
        mode = "interior narrowing"
    refined = tuple(start + half_step * offset for offset in range(len(points)))

    implicit_minimum = Decimal(0)
    for value in refined:
        if value < implicit_minimum or (key == _SPEED and value <= 0):
            raise RefinementLimitError(
                f"{mode} for {key} would propose an invalid non-positive value"
            )
        if limits is not None and not limits[0] <= value <= limits[1]:
            raise RefinementLimitError(
                f"{mode} for {key} would propose {value}, outside configured limits "
                f"[{limits[0]}, {limits[1]}]"
            )
    return tuple(_profile_number(value, exemplar, key) for value in refined), mode


def propose_ironing_refinement(
    plan: ExperimentPlan,
    results: ExperimentResults,
    *,
    next_plan_id: str,
    limits: Mapping[str, tuple[Any, Any]] | None = None,
) -> ExperimentPlan:
    """Halve the selected region's step, extending an edge winner outwards."""
    if not isinstance(plan, ExperimentPlan):
        raise IroningCalibrationError("plan must be an ExperimentPlan")
    if not isinstance(results, ExperimentResults):
        raise IroningCalibrationError("results must be ExperimentResults")
    if plan.module_id != "ironing":
        raise IroningCalibrationError("only ironing experiment plans can use ironing refinement")
    if results.accepted is not False:
        raise IroningCalibrationError("only rejected results can be refined; accepted or undecided results stop")
    if results.selected_candidate_id is None:
        raise IroningCalibrationError("select a best candidate before proposing a refinement")
    if len(plan.dimensions) != 2 or {dimension.key for dimension in plan.dimensions} != set(_IRONING_KEYS):
        raise IroningCalibrationError("ironing refinement requires flow and speed dimensions only")
    results.validate_for(plan)
    bounds = _validate_limits(limits)
    winner_settings = plan.settings_for(results.selected_candidate_id)
    baseline = dict(plan.baseline_settings)
    proposed: dict[str, tuple[Any, ...]] = {}
    modes: dict[str, str] = {}
    for key in _IRONING_KEYS:
        dimension = next(dimension for dimension in plan.dimensions if dimension.key == key)
        exemplar = _baseline_value(baseline, key)
        proposed[key], modes[key] = _refined_values(
            key,
            dimension.values,
            winner_settings[key],
            exemplar,
            bounds.get(key),
        )

    rationale = (
        f"Refinement of {plan.plan_id} after rejecting its result: "
        f"{_FLOW} uses {modes[_FLOW]} around the selected value; "
        f"{_SPEED} uses {modes[_SPEED]} around the selected value."
    )
    try:
        return create_grid_experiment(
            plan_id=next_plan_id,
            module_id="ironing",
            baseline_settings=winner_settings,
            dimensions=(
                SweepDimension(_FLOW, proposed[_FLOW], "Ironing flow"),
                SweepDimension(_SPEED, proposed[_SPEED], "Ironing speed"),
            ),
            candidate_prefix="I",
            parent_plan_id=plan.plan_id,
            max_candidates=64,
            rationale=rationale,
        )
    except ExperimentDefinitionError as exc:
        if isinstance(exc, IroningCalibrationError):
            raise
        raise IroningCalibrationError(str(exc)) from exc
