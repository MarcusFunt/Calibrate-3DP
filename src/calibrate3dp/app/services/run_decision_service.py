"""Evidence-backed recommendation and immutable follow-up configuration service."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

from calibrate3dp.app.services.acceptance_service import (
    AcceptanceDecision,
    AcceptanceService,
    ConfirmationEvidence,
    RecommendationAction,
)
from calibrate3dp.app.services.experiment_service import ExperimentService
from calibrate3dp.app.services.generation_port import GenerationState, ValidationState
from calibrate3dp.domain.assessment import AssessmentRevision
from calibrate3dp.domain.experiment_config import SavedExperimentConfiguration
from calibrate3dp.domain.run_decision import RunDecisionRecord
from calibrate3dp.storage.library_store import LibraryRepository


class RunDecisionServiceError(ValueError):
    """Saved run evidence cannot support a recommendation or follow-up."""


class RunDecisionService:
    def __init__(
        self,
        repository: LibraryRepository,
        *,
        acceptance: AcceptanceService | None = None,
    ) -> None:
        self.repository = repository
        self.acceptance = acceptance or AcceptanceService(ExperimentService())

    def evaluate(
        self,
        run_id: str,
        assessment_revision_id: str,
        *,
        confirmation_opt_out_reason: str | None = None,
    ) -> RunDecisionRecord:
        run = self.repository.get_run(run_id)
        assessment = self.repository.get_assessment_revision(assessment_revision_id)
        if assessment.run_id != run.run_id:
            raise RunDecisionServiceError("assessment revision belongs to a different run")
        if run.status != "settings_validated" or run.validation.get("state") != "sample_settings_validated":
            decision = _blocked(
                run_id, assessment, "inconclusive",
                "This run did not pass the grouped sample-settings validation gate.",
            )
            return self.repository.save_run_decision(decision)
        confirmation, confirmation_plan = self._successful_confirmation(run, assessment)
        result = self.acceptance.evaluate(
            run.plan,
            assessment.results,
            confirmation_plan=confirmation_plan,
            confirmation=confirmation,
            confirmation_opt_out_reason=confirmation_opt_out_reason,
        )
        if not assessment.attestation.physically_accepted:
            reasons = (
                *result.reasons,
                "Physical print and review are not both attested; this recommendation is exploratory and cannot unlock acceptance or export.",
            )
            action = result.action
            if action is RecommendationAction.ACCEPT:
                action = RecommendationAction.REFINE if result.proposed_plan is not None else RecommendationAction.INCONCLUSIVE
            result = AcceptanceDecision(
                action=action,
                reasons=reasons,
                proposed_plan=result.proposed_plan,
                can_accept=False,
                confirmation_required=False,
                opt_out_record=result.opt_out_record,
            )
        if result.can_accept:
            blockers = []
            artifact_problem = _artifact_problem(self.repository, run)
            if artifact_problem:
                blockers.append(artifact_problem)
            if not _preflight_passed(run):
                blockers.append("the saved G-code preflight is missing, failed, or incomplete")
            if blockers:
                result = AcceptanceDecision(
                    action=RecommendationAction.INCONCLUSIVE,
                    reasons=(*result.reasons, "Acceptance remains locked because " + "; ".join(blockers) + "."),
                    proposed_plan=result.proposed_plan,
                    can_accept=False,
                    confirmation_required=False,
                    opt_out_record=result.opt_out_record,
                )
        confirmation_record = (
            confirmation
            if result.can_accept and result.opt_out_record is None and confirmation is not None
            else None
        )
        record = _from_policy(run_id, assessment, result, confirmation=confirmation_record)
        return self.repository.save_run_decision(record)

    def create_followup(self, decision_id: str, *, kind: str) -> SavedExperimentConfiguration:
        if kind not in {"refinement", "confirmation"}:
            raise RunDecisionServiceError("follow-up kind must be refinement or confirmation")
        decision = self.repository.get_run_decision(decision_id)
        if decision.action not in {RecommendationAction.REFINE.value, RecommendationAction.EXTEND_BOUNDARY.value}:
            raise RunDecisionServiceError("this decision does not support a follow-up experiment")
        if decision.selected_candidate_id is None:
            raise RunDecisionServiceError("select a candidate before creating a follow-up")
        if decision.proposed_plan is None:
            raise RunDecisionServiceError("the decision has no validated next plan")
        if kind == "confirmation" and decision.action == RecommendationAction.EXTEND_BOUNDARY.value:
            raise RunDecisionServiceError("extend a boundary winner before creating a confirmation run")
        assessment = self.repository.get_assessment_revision(decision.assessment_revision_id)
        if assessment.run_id != decision.run_id:
            raise RunDecisionServiceError("decision assessment link is inconsistent")
        parent_configuration = self.repository.get_configuration_for_run(decision.run_id)
        if parent_configuration is None:
            raise RunDecisionServiceError("this historical run has no frozen configuration to continue")
        run = self.repository.get_run(decision.run_id)
        if kind == "confirmation":
            plan = self.acceptance.create_confirmation_plan(run.plan, assessment.results)
            relation = "confirmation"
        else:
            plan = decision.proposed_plan
            relation = "refinement"
        if plan.parent_plan_id != run.plan.plan_id:
            raise RunDecisionServiceError("follow-up plan must name its source plan as parent")
        configuration = SavedExperimentConfiguration(
            config_id=f"config-{uuid4().hex}",
            experiment_id=f"{parent_configuration.experiment_id}-{relation}-{uuid4().hex[:8]}",
            revision_no=1,
            printer_id=parent_configuration.printer_id,
            material_id=parent_configuration.material_id,
            profile_selection=parent_configuration.profile_selection,
            plan=plan,
            layout_options=parent_configuration.layout_options,
            created_at_utc=datetime.now(timezone.utc).isoformat(timespec="microseconds").replace("+00:00", "Z"),
            relation_type=relation,
            parent_run_id=run.run_id,
            parent_assessment_revision_id=assessment.assessment_revision_id,
            parent_candidate_id=decision.selected_candidate_id,
            schema_version=parent_configuration.schema_version,
        )
        self.repository.save_configuration(configuration)
        return configuration

    def _successful_confirmation(
        self, run, assessment: AssessmentRevision
    ) -> tuple[ConfirmationEvidence | None, Any | None]:
        if _artifact_problem(self.repository, run) is not None or not _preflight_passed(run):
            return None, None
        for child in self.repository.list_child_runs(run.run_id):
            link = self.repository.get_run_config_link(child.run_id) or {}
            if link.get("relation_type") != "confirmation":
                continue
            config = self.repository.get_configuration_for_run(child.run_id)
            result_revision = self.repository.latest_assessment_revision(child.run_id)
            if (
                config is None or result_revision is None
                or link.get("parent_assessment_revision_id") != assessment.assessment_revision_id
                or child.status != "settings_validated"
                or child.validation.get("state") != "sample_settings_validated"
                or _artifact_problem(self.repository, child) is not None
                or not _preflight_passed(child)
                or not result_revision.attestation.physically_accepted
                or len(config.plan.candidates) != 1
            ):
                continue
            candidate = config.plan.candidates[0]
            outcome = next((item for item in result_revision.results.assessments if item.candidate_id == candidate.candidate_id), None)
            if outcome is None or outcome.verdict != "pass":
                continue
            return ConfirmationEvidence(
                run_id=child.run_id,
                plan_id=config.plan.plan_id,
                candidate_id=candidate.candidate_id,
                state=GenerationState.SUCCEEDED,
                validation_state=ValidationState.VALID,
                settings=config.plan.settings_for(candidate.candidate_id),
                assessment_revision_id=result_revision.assessment_revision_id,
            ), config.plan
        return None, None


def _artifact_problem(repository: LibraryRepository, run) -> str | None:
    if not run.artifacts:
        return "the run has no finalized generation artifacts"
    try:
        for artifact in run.artifacts:
            repository.artifact_path(artifact)
    except Exception as exc:
        return f"a generation artifact is missing, unsafe, or corrupt ({exc})"
    return None


def _preflight_passed(run) -> bool:
    report = run.validation.get("gcode_preflight")
    return (
        isinstance(report, dict)
        and report.get("passed") is True
        and report.get("parser_coverage_complete") is True
    )


def _blocked(run_id: str, assessment: AssessmentRevision, action: str, reason: str) -> RunDecisionRecord:
    return RunDecisionRecord(
        decision_id=f"decision-{uuid4().hex}", run_id=run_id,
        assessment_revision_id=assessment.assessment_revision_id,
        created_at_utc=datetime.now(timezone.utc).isoformat(timespec="microseconds").replace("+00:00", "Z"),
        action=action, reasons=(reason,), selected_candidate_id=assessment.results.selected_candidate_id,
        proposed_plan=None, can_accept=False, confirmation_required=False,
    )


def _from_policy(
    run_id: str,
    assessment: AssessmentRevision,
    result: AcceptanceDecision,
    *,
    confirmation: ConfirmationEvidence | None = None,
) -> RunDecisionRecord:
    confirmation_record = None
    if confirmation is not None and confirmation.assessment_revision_id is not None:
        confirmation_record = {
            "run_id": confirmation.run_id,
            "plan_id": confirmation.plan_id,
            "candidate_id": confirmation.candidate_id,
            "assessment_revision_id": confirmation.assessment_revision_id,
        }
    return RunDecisionRecord(
        decision_id=f"decision-{uuid4().hex}", run_id=run_id,
        assessment_revision_id=assessment.assessment_revision_id,
        created_at_utc=datetime.now(timezone.utc).isoformat(timespec="microseconds").replace("+00:00", "Z"),
        action=result.action.value, reasons=result.reasons,
        selected_candidate_id=assessment.results.selected_candidate_id,
        proposed_plan=result.proposed_plan, can_accept=result.can_accept,
        confirmation_required=result.confirmation_required,
        opt_out_record=result.opt_out_record,
        confirmation_evidence=confirmation_record,
    )
