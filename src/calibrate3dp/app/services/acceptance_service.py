"""Explainable module decisions and confirmation-run acceptance gates."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from types import MappingProxyType
from typing import Any, Mapping
from uuid import uuid4

from calibrate3dp.app.services.experiment_service import ExperimentService
from calibrate3dp.app.services.generation_port import GenerationState, ValidationState
from calibrate3dp.experiments import (
    ExperimentCandidate,
    ExperimentPlan,
    ExperimentResults,
    SweepDimension,
)


class RecommendationAction(str, Enum):
    """The next user decision supported by the current evidence."""

    INCONCLUSIVE = "inconclusive"
    RESOLVE_TIE = "resolve_tie"
    REFINE = "refine"
    EXTEND_BOUNDARY = "extend_boundary"
    ACCEPT = "accept"


@dataclass(frozen=True)
class ConfirmationEvidence:
    """Outcome and exact settings reported by a confirmation generation run."""

    run_id: str
    plan_id: str
    candidate_id: str
    state: GenerationState
    validation_state: ValidationState
    settings: Mapping[str, Any]
    assessment_revision_id: str | None = None

    def __post_init__(self) -> None:
        for name in ("run_id", "plan_id", "candidate_id"):
            value = getattr(self, name)
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"confirmation {name} must be a non-empty string")
        if self.state not in tuple(GenerationState):
            raise ValueError("confirmation state is invalid")
        if self.validation_state not in tuple(ValidationState):
            raise ValueError("confirmation validation state is invalid")
        if not isinstance(self.settings, Mapping):
            raise ValueError("confirmation settings must be a mapping")
        if self.assessment_revision_id is not None and (
            not isinstance(self.assessment_revision_id, str) or not self.assessment_revision_id.strip()
        ):
            raise ValueError("confirmation assessment_revision_id must be a non-empty string or None")
        object.__setattr__(self, "settings", MappingProxyType(dict(self.settings)))


@dataclass(frozen=True)
class AcceptanceDecision:
    """Policy output for the recommendation page; it never writes profiles."""

    action: RecommendationAction
    reasons: tuple[str, ...]
    proposed_plan: ExperimentPlan | None = None
    can_accept: bool = False
    confirmation_required: bool = False
    opt_out_record: Mapping[str, Any] | None = None

    def __post_init__(self) -> None:
        if self.action not in tuple(RecommendationAction):
            raise ValueError("recommendation action is invalid")
        reasons = tuple(self.reasons)
        if any(not isinstance(item, str) or not item.strip() for item in reasons):
            raise ValueError("recommendation reasons must be non-empty strings")
        if not isinstance(self.can_accept, bool) or not isinstance(self.confirmation_required, bool):
            raise ValueError("acceptance readiness values must be booleans")
        if self.can_accept and self.confirmation_required:
            raise ValueError("an acceptable decision cannot still require confirmation")
        if self.opt_out_record is not None:
            if not isinstance(self.opt_out_record, Mapping):
                raise ValueError("opt_out_record must be a mapping")
            object.__setattr__(self, "opt_out_record", MappingProxyType(dict(self.opt_out_record)))
        object.__setattr__(self, "reasons", reasons)


class AcceptanceService:
    """Evaluate ironing results and require explicit confirmation evidence."""

    def __init__(
        self,
        experiment_service: ExperimentService | None = None,
        *,
        limits: Mapping[str, tuple[Any, Any]] | None = None,
    ) -> None:
        self.experiment_service = experiment_service or ExperimentService()
        self.limits = MappingProxyType(dict(limits or {}))

    def evaluate(
        self,
        plan: ExperimentPlan,
        results: ExperimentResults,
        *,
        confirmation_plan: ExperimentPlan | None = None,
        confirmation: ConfirmationEvidence | None = None,
        confirmation_opt_out_reason: str | None = None,
    ) -> AcceptanceDecision:
        """Return recommendation reasons and whether evidence permits acceptance."""
        if not isinstance(plan, ExperimentPlan) or not isinstance(results, ExperimentResults):
            return _decision(
                RecommendationAction.INCONCLUSIVE,
                "A reviewed experiment plan and its results are required.",
            )
        if plan.module_id != "ironing":
            return _decision(
                RecommendationAction.INCONCLUSIVE,
                f"No acceptance policy is registered for module {plan.module_id!r}.",
            )
        try:
            results.validate_for(plan)
        except (TypeError, ValueError) as exc:
            return _decision(RecommendationAction.INCONCLUSIVE, f"Results do not match this plan: {exc}")

        if results.tied_candidate_ids:
            tied = ", ".join((results.selected_candidate_id or "", *results.tied_candidate_ids)).strip(", ")
            return _decision(
                RecommendationAction.RESOLVE_TIE,
                f"Candidates {tied} are tied. Choose one candidate before refinement or acceptance.",
            )
        if results.selected_candidate_id is None:
            return _decision(RecommendationAction.INCONCLUSIVE, "Select a candidate before continuing.")

        assessments = {item.candidate_id: item for item in results.assessments}
        candidate_ids = {item.candidate_id for item in plan.candidates}
        if candidate_ids != set(assessments):
            unreviewed = sorted(candidate_ids - set(assessments))
            return _decision(
                RecommendationAction.INCONCLUSIVE,
                "Record an explicit pass or fail verdict for every candidate. Unreviewed: "
                + ", ".join(unreviewed),
            )
        unresolved = sorted(
            candidate_id
            for candidate_id in candidate_ids
            if assessments[candidate_id].verdict not in {"pass", "fail"}
        )
        if unresolved:
            return _decision(
                RecommendationAction.INCONCLUSIVE,
                "Uncertain, missing, and unreviewed specimens need review before a recommendation: "
                + ", ".join(unresolved),
            )

        selected_id = results.selected_candidate_id
        if assessments[selected_id].verdict != "pass":
            return _decision(
                RecommendationAction.INCONCLUSIVE,
                f"Selected candidate {selected_id} does not have a pass verdict.",
            )

        try:
            proposal = self.experiment_service.propose_refinement(
                plan,
                _as_refinable(results),
                limits=self.limits or None,
            )
        except (TypeError, ValueError) as exc:
            return _decision(RecommendationAction.INCONCLUSIVE, f"No safe next range is available: {exc}")

        boundary_reasons = _boundary_reasons(plan, selected_id)
        if boundary_reasons:
            return AcceptanceDecision(
                action=RecommendationAction.EXTEND_BOUNDARY,
                reasons=(
                    *boundary_reasons,
                    proposal.rationale,
                    "Extend a boundary winner before accepting a final setting.",
                ),
                proposed_plan=proposal,
            )
        reasons = (
            f"Candidate {selected_id} is inside the current flow and speed ranges.",
            "The next grid narrows both ranges around the selected settings.",
            proposal.rationale,
        )
        action = RecommendationAction.REFINE

        opt_out_record = _make_opt_out_record(plan, selected_id, confirmation_opt_out_reason)
        confirmed = self._confirmation_matches(
            plan, results, confirmation_plan, confirmation
        )
        if confirmed:
            return AcceptanceDecision(
                action=RecommendationAction.ACCEPT,
                reasons=(*reasons, "The exact selected settings passed a successful confirmation run."),
                proposed_plan=proposal,
                can_accept=True,
            )
        if opt_out_record is not None:
            return AcceptanceDecision(
                action=RecommendationAction.ACCEPT,
                reasons=(*reasons, "Confirmation was skipped by explicit user choice; the reason must be recorded."),
                proposed_plan=proposal,
                can_accept=True,
                opt_out_record=opt_out_record,
            )

        if confirmation is not None:
            reasons = (*reasons, _confirmation_failure_reason(confirmation))
        reasons = (
            *reasons,
            "Run a confirmation print at the selected settings or record a reasoned opt-out before acceptance.",
        )
        return AcceptanceDecision(
            action=action,
            reasons=reasons,
            proposed_plan=proposal,
            confirmation_required=True,
        )

    def create_confirmation_plan(
        self,
        plan: ExperimentPlan,
        results: ExperimentResults,
        *,
        plan_id: str | None = None,
    ) -> ExperimentPlan:
        """Build a one-candidate plan that repeats the selected exact settings."""
        results.validate_for(plan)
        if results.tied_candidate_ids:
            raise ValueError("resolve tied candidates before creating a confirmation plan")
        if results.selected_candidate_id is None:
            raise ValueError("select a candidate before creating a confirmation plan")
        settings = plan.settings_for(results.selected_candidate_id)
        dimensions = (
            SweepDimension("ironing_flow", (settings["ironing_flow"],), "Ironing flow"),
            SweepDimension("ironing_speed", (settings["ironing_speed"],), "Ironing speed"),
        )
        return ExperimentPlan(
            plan_id=plan_id or f"{plan.plan_id}-confirm-{uuid4().hex[:8]}",
            module_id=plan.module_id,
            baseline_settings=plan.baseline_settings,
            dimensions=dimensions,
            candidates=(
                ExperimentCandidate(
                    candidate_id="CONFIRM",
                    overrides={
                        "ironing_flow": settings["ironing_flow"],
                        "ironing_speed": settings["ironing_speed"],
                    },
                ),
            ),
            parent_plan_id=plan.plan_id,
            rationale=(
                f"Confirmation print for {results.selected_candidate_id} using its exact selected settings."
            ),
        )

    @staticmethod
    def _confirmation_matches(
        plan: ExperimentPlan,
        results: ExperimentResults,
        confirmation_plan: ExperimentPlan | None,
        confirmation: ConfirmationEvidence | None,
    ) -> bool:
        if confirmation_plan is None or confirmation is None:
            return False
        if results.selected_candidate_id is None:
            return False
        if (
            confirmation.plan_id != confirmation_plan.plan_id
            or confirmation_plan.parent_plan_id != plan.plan_id
            or len(confirmation_plan.candidates) != 1
            or confirmation.state is not GenerationState.SUCCEEDED
            or confirmation.validation_state is not ValidationState.VALID
        ):
            return False
        confirmed_candidate = confirmation_plan.candidates[0].candidate_id
        expected = plan.settings_for(results.selected_candidate_id)
        return (
            confirmation.candidate_id == confirmed_candidate
            and confirmation_plan.settings_for(confirmed_candidate) == expected
            and dict(confirmation.settings) == expected
        )


def _as_refinable(results: ExperimentResults) -> ExperimentResults:
    """Pass an explicitly rejected copy to the existing adaptive planner."""
    from dataclasses import replace

    return replace(results, accepted=False)


def _decision(action: RecommendationAction, reason: str) -> AcceptanceDecision:
    return AcceptanceDecision(action=action, reasons=(reason,))


def _boundary_reasons(plan: ExperimentPlan, candidate_id: str) -> tuple[str, ...]:
    candidate = next(item for item in plan.candidates if item.candidate_id == candidate_id)
    reasons = []
    for dimension in plan.dimensions:
        value = candidate.overrides[dimension.key]
        if value == dimension.values[0]:
            reasons.append(
                f"Candidate {candidate_id} is at the lower boundary of {dimension.key}; the next grid extends below it."
            )
        elif value == dimension.values[-1]:
            reasons.append(
                f"Candidate {candidate_id} is at the upper boundary of {dimension.key}; the next grid extends above it."
            )
    return tuple(reasons)


def _make_opt_out_record(
    plan: ExperimentPlan,
    selected_candidate_id: str,
    reason: str | None,
) -> Mapping[str, Any] | None:
    if reason is None:
        return None
    if not isinstance(reason, str) or not reason.strip():
        raise ValueError("a confirmation opt-out requires a non-empty reason")
    return {
        "type": "confirmation_opt_out",
        "plan_id": plan.plan_id,
        "candidate_id": selected_candidate_id,
        "reason": reason.strip(),
        "recorded_at_utc": datetime.now(timezone.utc).isoformat(timespec="microseconds").replace("+00:00", "Z"),
    }


def _confirmation_failure_reason(confirmation: ConfirmationEvidence) -> str:
    if confirmation.state is not GenerationState.SUCCEEDED:
        return f"Confirmation run ended as {confirmation.state.value}; it does not unlock acceptance."
    if confirmation.validation_state is not ValidationState.VALID:
        return "Confirmation output did not pass validation; it does not unlock acceptance."
    return "Confirmation run settings do not match the selected candidate exactly."
