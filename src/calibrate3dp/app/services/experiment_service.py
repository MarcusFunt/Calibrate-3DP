"""Application adapter for deterministic module plans and review metadata."""

from __future__ import annotations

from dataclasses import dataclass, replace
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from types import MappingProxyType
from typing import Any, Mapping, Sequence
from uuid import uuid4

from calibrate3dp.app.models import ProfileSelection
from calibrate3dp.experiments import (
    ExperimentCandidate,
    ExperimentPlan,
    ExperimentResults,
    SweepDimension,
)
from calibrate3dp.ironing import IroningCalibrationError, create_initial_ironing_experiment, propose_ironing_refinement


FLOW_KEY = "ironing_flow"
SPEED_KEY = "ironing_speed"
SWEEP_KEYS = (FLOW_KEY, SPEED_KEY)


class ExperimentServiceError(ValueError):
    """Raised when a baseline cannot safely produce the requested experiment."""


class ModuleUnavailableError(ExperimentServiceError):
    """Raised when a planned but unimplemented module is selected."""


@dataclass(frozen=True)
class ExperimentOptions:
    """Optional three-value sweep edits and explicit Orca schema limits."""

    flow_values: Sequence[Any] | None = None
    speed_values: Sequence[Any] | None = None
    schema_limits: Mapping[str, Sequence[Any]] | None = None
    plan_id: str | None = None


@dataclass(frozen=True)
class ModuleCard:
    module_id: str
    label: str
    description: str
    available: bool
    availability_reason: str | None = None


@dataclass(frozen=True)
class CandidatePlateEntry:
    candidate_id: str
    flow_value: Any
    speed_value: Any
    plate_label: str
    specimen_label: str


@dataclass(frozen=True)
class ExperimentReview:
    plan: ExperimentPlan
    fixed_settings: Mapping[str, Any]
    plate_map: tuple[CandidatePlateEntry, ...]
    assumptions: tuple[str, ...]
    warnings: tuple[str, ...]
    estimate_message: str


class ExperimentService:
    """Create the first ironing sweep through the tested domain planner."""

    modules = (
        ModuleCard(
            module_id="ironing",
            label="Ironing Finish",
            description="Compare ironing flow and speed for top-surface finish.",
            available=True,
        ),
        ModuleCard(
            module_id="bridge_quality",
            label="Bridge Quality",
            description="Bridge tuning will use a dedicated planner and specimen geometry.",
            available=False,
            availability_reason="Bridge planner and geometry adapter are not implemented yet.",
        ),
        ModuleCard(
            module_id="support_interface",
            label="Support Interface / Removal",
            description="Support tuning will use a dedicated planner and removal rubric.",
            available=False,
            availability_reason="Support planner and geometry adapter are not implemented yet.",
        ),
    )

    def create_initial(
        self,
        module_id: str,
        profiles: ProfileSelection,
        options: ExperimentOptions | Mapping[str, Any] | None = None,
    ) -> ExperimentPlan:
        """Build a baseline-relative 3×3 sweep; never read slicer defaults."""
        if module_id != "ironing":
            card = next((item for item in self.modules if item.module_id == module_id), None)
            reason = card.availability_reason if card else "No planner is registered for this module."
            raise ModuleUnavailableError(f"{module_id!r} cannot start: {reason}")
        if not isinstance(profiles, ProfileSelection):
            raise ExperimentServiceError("profiles must be a validated ProfileSelection")

        selected = _coerce_options(options)
        baseline = dict(profiles.process.settings)
        missing = [key for key in SWEEP_KEYS if key not in baseline or baseline[key] in (None, "")]
        if missing:
            raise ExperimentServiceError(
                "The selected process profile is missing required baseline setting(s): "
                + ", ".join(missing)
            )

        limits = _parse_limits(selected.schema_limits)
        flow_values = _axis_values(
            baseline[FLOW_KEY],
            FLOW_KEY,
            selected.flow_values,
            (Decimal("0.8"), Decimal("1"), Decimal("1.2")),
            limits.get(FLOW_KEY),
        )
        speed_values = _axis_values(
            baseline[SPEED_KEY],
            SPEED_KEY,
            selected.speed_values,
            (Decimal(2) / Decimal(3), Decimal(1), Decimal(4) / Decimal(3)),
            limits.get(SPEED_KEY),
        )
        plan_id = selected.plan_id or f"ironing-{uuid4().hex}"

        try:
            planned = create_initial_ironing_experiment(
                plan_id=plan_id,
                baseline_settings=baseline,
                flow_values=flow_values,
                speed_values=speed_values,
            )
        except IroningCalibrationError as exc:
            raise ExperimentServiceError(str(exc)) from exc

        # The domain planner validates spacing and candidate order. Restore the
        # selected profile's displayed precision after its numeric normalization.
        flow_lookup = {_numeric_key(value, FLOW_KEY): value for value in flow_values}
        speed_lookup = {_numeric_key(value, SPEED_KEY): value for value in speed_values}
        candidates = []
        for candidate in planned.candidates:
            overrides = dict(candidate.overrides)
            overrides[FLOW_KEY] = flow_lookup[_numeric_key(overrides[FLOW_KEY], FLOW_KEY)]
            overrides[SPEED_KEY] = speed_lookup[_numeric_key(overrides[SPEED_KEY], SPEED_KEY)]
            candidates.append(ExperimentCandidate(candidate.candidate_id, overrides))
        dimensions = (
            SweepDimension(FLOW_KEY, tuple(flow_values), "Ironing flow"),
            SweepDimension(SPEED_KEY, tuple(speed_values), "Ironing speed"),
        )
        try:
            return replace(planned, dimensions=dimensions, candidates=tuple(candidates))
        except (TypeError, ValueError) as exc:
            raise ExperimentServiceError(f"candidate matrix is invalid: {exc}") from exc

    def review(self, plan: ExperimentPlan, profiles: ProfileSelection) -> ExperimentReview:
        """Describe fixed inputs, independent plates, and known assumptions."""
        if not isinstance(plan, ExperimentPlan) or plan.module_id != "ironing":
            raise ExperimentServiceError("review requires an ironing ExperimentPlan")
        if not isinstance(profiles, ProfileSelection):
            raise ExperimentServiceError("review requires the selected ProfileSelection")
        if {dimension.key for dimension in plan.dimensions} != set(SWEEP_KEYS):
            raise ExperimentServiceError("review requires ironing_flow and ironing_speed dimensions")

        fixed = {
            key: value
            for key, value in plan.baseline_settings.items()
            if key not in SWEEP_KEYS and key not in {"name", "type", "inherits"}
        }
        plate_map = tuple(
            CandidatePlateEntry(
                candidate_id=candidate.candidate_id,
                flow_value=candidate.overrides[FLOW_KEY],
                speed_value=candidate.overrides[SPEED_KEY],
                plate_label=f"Separate plate {candidate.candidate_id}",
                specimen_label=candidate.candidate_id,
            )
            for candidate in plan.candidates
        )

        warnings = list(profiles.compatibility_warnings)
        operation = plan.baseline_settings.get("ironing_type")
        if operation in (None, "", "no", "none"):
            warnings.append(
                "The imported process profile does not explicitly enable ironing; the generation "
                "step must request an operation before slicing."
            )
        warnings.append("No verified duration or material estimator is available for this profile set.")
        return ExperimentReview(
            plan=plan,
            fixed_settings=MappingProxyType(fixed),
            plate_map=plate_map,
            assumptions=(
                "The default grid uses three flow values by three speed values around the imported baseline.",
                "Every other imported process setting remains fixed at its resolved baseline value.",
                "One candidate is assigned to each separate plate because 3MF per-object overrides are unverified.",
            ),
            warnings=tuple(warnings),
            estimate_message="Duration and material estimates are not available for this Orca profile set.",
        )

    def propose_refinement(
        self,
        plan: ExperimentPlan,
        results: ExperimentResults,
        limits: Mapping[str, tuple[Any, Any]] | None = None,
    ) -> ExperimentPlan:
        """Delegate adaptive proposals to the existing ironing planner."""
        try:
            return propose_ironing_refinement(
                plan,
                results,
                next_plan_id=f"{plan.plan_id}-refine-{uuid4().hex[:8]}",
                limits=limits,
            )
        except IroningCalibrationError as exc:
            raise ExperimentServiceError(str(exc)) from exc


def _coerce_options(
    options: ExperimentOptions | Mapping[str, Any] | None,
) -> ExperimentOptions:
    if options is None:
        return ExperimentOptions()
    if isinstance(options, ExperimentOptions):
        return options
    if not isinstance(options, Mapping):
        raise ExperimentServiceError("options must be ExperimentOptions or a mapping")
    unknown = set(options) - {"flow_values", "speed_values", "schema_limits", "plan_id"}
    if unknown:
        raise ExperimentServiceError("unknown experiment option(s): " + ", ".join(sorted(unknown)))
    return ExperimentOptions(**options)


def _axis_values(
    baseline: Any,
    key: str,
    explicit_values: Sequence[Any] | None,
    factors: tuple[Decimal, Decimal, Decimal],
    limits: tuple[Decimal, Decimal] | None,
) -> tuple[Any, Any, Any]:
    base = _as_decimal(baseline, key)
    places = _precision(baseline)
    quantizer = Decimal(1).scaleb(-places)
    values = (
        tuple(_as_decimal(value, key) for value in explicit_values)
        if explicit_values is not None
        else tuple(base * factor for factor in factors)
    )
    if len(values) != 3:
        raise ExperimentServiceError(f"{key} must have exactly three editable values")

    rounded: list[Decimal] = []
    for value in values:
        if limits is not None:
            value = min(max(value, limits[0]), limits[1])
        try:
            value = value.quantize(quantizer, rounding=ROUND_HALF_UP)
        except InvalidOperation as exc:
            raise ExperimentServiceError(f"{key} cannot be represented at profile precision") from exc
        if key == FLOW_KEY and value < 0:
            raise ExperimentServiceError("ironing_flow values must be non-negative")
        if key == SPEED_KEY and value <= 0:
            raise ExperimentServiceError("ironing_speed values must be greater than zero")
        rounded.append(value)

    if len(set(rounded)) != 3:
        bound_text = " after applying the explicit schema limits" if limits is not None else " at the imported profile precision"
        raise ExperimentServiceError(
            f"{key} contains duplicate values{bound_text}; edit the range to keep three distinct candidates"
        )
    if rounded != sorted(rounded):
        raise ExperimentServiceError(f"{key} values must be ordered from low to high")
    if rounded[1] - rounded[0] != rounded[2] - rounded[1]:
        raise ExperimentServiceError(f"{key} values must be evenly spaced")
    return tuple(_format_profile_value(value, baseline, places, key) for value in rounded)  # type: ignore[return-value]


def _parse_limits(
    limits: Mapping[str, Sequence[Any]] | None,
) -> dict[str, tuple[Decimal, Decimal]]:
    if limits is None:
        return {}
    if not isinstance(limits, Mapping):
        raise ExperimentServiceError("schema_limits must be a mapping")
    unknown = set(limits) - set(SWEEP_KEYS)
    if unknown:
        raise ExperimentServiceError("schema limits include undeclared setting(s): " + ", ".join(sorted(unknown)))
    parsed: dict[str, tuple[Decimal, Decimal]] = {}
    for key, pair in limits.items():
        if isinstance(pair, (str, bytes)) or not isinstance(pair, Sequence) or len(pair) != 2:
            raise ExperimentServiceError(f"schema limit for {key!r} must have a minimum and maximum")
        lower, upper = (_as_decimal(value, f"{key} schema limit") for value in pair)
        if lower > upper:
            raise ExperimentServiceError(f"schema minimum for {key!r} exceeds its maximum")
        parsed[key] = (lower, upper)
    return parsed


def _as_decimal(value: Any, name: str) -> Decimal:
    if isinstance(value, bool) or value is None:
        raise ExperimentServiceError(f"{name} must be a finite numeric value")
    rendered = str(value).strip()
    if name in (FLOW_KEY, f"{FLOW_KEY} schema limit") and rendered.endswith("%"):
        rendered = rendered[:-1].strip()
    try:
        numeric = Decimal(rendered)
    except (InvalidOperation, ValueError) as exc:
        raise ExperimentServiceError(f"{name} must be a finite numeric value") from exc
    if not numeric.is_finite():
        raise ExperimentServiceError(f"{name} must be a finite numeric value")
    return numeric


def _precision(value: Any) -> int:
    rendered = str(value).strip().rstrip("%")
    if "." not in rendered:
        return 0
    return len(rendered.rsplit(".", 1)[1])


def _format_profile_value(value: Decimal, exemplar: Any, places: int, key: str) -> Any:
    rendered = format(value, f".{places}f") if places else format(value.quantize(Decimal(1)), "f")
    if isinstance(exemplar, str):
        if key == FLOW_KEY and exemplar.strip().endswith("%"):
            rendered += "%"
        return rendered
    if isinstance(exemplar, bool) or not isinstance(exemplar, (int, float)):
        raise ExperimentServiceError(f"baseline {key} must be a numeric string or number")
    if isinstance(exemplar, int) and places == 0:
        return int(value)
    return float(value)


def _numeric_key(value: Any, key: str) -> Decimal:
    return _as_decimal(value, key)
