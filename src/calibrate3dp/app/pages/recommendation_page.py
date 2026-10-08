"""Explainable next-step and acceptance UI for calibration results."""

from __future__ import annotations

from dataclasses import replace
from hashlib import sha256
from typing import Any, Callable, Mapping
from uuid import uuid4

from calibrate3dp.app.models import SessionSnapshot
from calibrate3dp.app.services.acceptance_service import (
    AcceptanceDecision,
    AcceptanceService,
    ConfirmationEvidence,
    RecommendationAction,
)
from calibrate3dp.app.services.experiment_service import ExperimentService
from calibrate3dp.app.services.generation_port import GenerationState, ValidationState
from calibrate3dp.experiments import ExperimentPlan, ExperimentResults


class RecommendationPage:
    """Present a policy explanation and coordinate persisted next steps."""

    def __init__(
        self,
        dpg: Any,
        acceptance_service: AcceptanceService,
        experiment_service: ExperimentService,
        session_service: Any | None,
        *,
        on_refinement_ready: Callable[[SessionSnapshot, ExperimentPlan], None] | None = None,
        on_confirmation_requested: Callable[[SessionSnapshot, ExperimentPlan, str], None] | None = None,
    ) -> None:
        self.dpg = dpg
        self.acceptance_service = acceptance_service
        self.experiment_service = experiment_service
        self.session_service = session_service
        self.on_refinement_ready = on_refinement_ready
        self.on_confirmation_requested = on_confirmation_requested
        self.session: SessionSnapshot | None = None
        self.plan: ExperimentPlan | None = None
        self.results: ExperimentResults | None = None
        self.confirmation_plan: ExperimentPlan | None = None
        self.previous_plan: ExperimentPlan | None = None
        self.confirmation: ConfirmationEvidence | None = None
        self.confirmation_opt_out_reason: str | None = None
        self.marked_inconclusive_reason: str | None = None
        self.decision: AcceptanceDecision | None = None
        self.report_path: str | None = None
        self.report: dict[str, Any] = {"schema_version": 1, "events": []}
        self.pending_confirmation_run_id: str | None = None
        self.save_error = ""
        self._rendered = False

    @property
    def can_accept(self) -> bool:
        return (
            self.decision is not None
            and self.decision.can_accept
            and self.marked_inconclusive_reason is None
        )

    def set_context(
        self,
        *,
        session: SessionSnapshot | None,
        plan: ExperimentPlan,
        results: ExperimentResults,
    ) -> None:
        if session is not None and not isinstance(session, SessionSnapshot):
            raise TypeError("session must be a SessionSnapshot or None")
        if not isinstance(plan, ExperimentPlan) or not isinstance(results, ExperimentResults):
            raise TypeError("recommendation context requires a plan and its results")
        self.session = session
        self.plan = plan
        self.results = results
        self.report_path = _report_path(plan.plan_id)
        self.confirmation = None
        self.confirmation_opt_out_reason = None
        self.marked_inconclusive_reason = None
        self.confirmation_plan = None
        self.previous_plan = None
        self.pending_confirmation_run_id = None
        self.save_error = ""
        self.report = {"schema_version": 1, "plan_id": plan.plan_id, "events": []}
        self._load_report()
        self._load_previous_plan()
        self._evaluate()
        if self._rendered:
            self._refresh_ui()

    def render(self) -> None:
        if self._rendered:
            self._refresh_ui()
            return
        dpg = self.dpg
        with dpg.group(tag="recommendation_panel", show=False):
            dpg.add_spacer(height=14)
            dpg.add_text("RECOMMENDATION", color=(92, 191, 178, 255))
            dpg.add_text(
                "See how the selected result changes the next range, and what evidence is still needed to accept it.",
                color=(165, 180, 195, 255),
                wrap=850,
            )
            dpg.add_text("", tag="recommendation_heading", wrap=850)
            dpg.add_text("", tag="recommendation_ranges", wrap=850)
            dpg.add_text("", tag="recommendation_fixed", wrap=850)
            dpg.add_text("", tag="recommendation_reasons", wrap=850)
            dpg.add_text("", tag="recommendation_error", wrap=850,
                         color=(235, 130, 125, 255))
            with dpg.group(horizontal=True):
                dpg.add_combo(
                    label="Tie-break candidate",
                    items=[],
                    tag="recommendation_tie_candidate",
                    width=210,
                )
                dpg.add_button(
                    label="Resolve tie",
                    tag="recommendation_resolve_tie",
                    enabled=False,
                    callback=self._on_resolve_tie,
                )
                dpg.add_button(
                    label="Refine",
                    tag="recommendation_refine",
                    enabled=False,
                    callback=self._on_refine,
                )
                dpg.add_button(
                    label="Run confirmation",
                    tag="recommendation_confirmation",
                    enabled=False,
                    callback=self._on_confirmation,
                )
                dpg.add_button(
                    label="Accept result",
                    tag="recommendation_accept",
                    enabled=False,
                    callback=self._on_accept,
                )
            dpg.add_input_text(
                label="Reason for skipping confirmation",
                tag="recommendation_opt_out_reason",
                width=-1,
            )
            dpg.add_button(
                label="Record opt-out",
                tag="recommendation_opt_out",
                enabled=False,
                callback=self._on_opt_out,
            )
            dpg.add_button(
                label="Mark inconclusive",
                tag="recommendation_inconclusive",
                callback=self._on_inconclusive,
            )
        self._rendered = True
        self._refresh_ui()

    def resolve_tie(self, candidate_id: str) -> bool:
        if self.plan is None or self.results is None or not self.results.tied_candidate_ids:
            return False
        tied_group = {self.results.selected_candidate_id, *self.results.tied_candidate_ids}
        if candidate_id not in tied_group:
            raise ValueError("choose one of the candidates in the current tie")
        resolved = replace(
            self.results,
            selected_candidate_id=candidate_id,
            tied_candidate_ids=(),
            accepted=False,
        )
        if not self._save_results(resolved):
            return False
        self.results = resolved
        self._evaluate()
        self._refresh_ui()
        return True

    def request_refinement(self) -> bool:
        if (
            self.session is None
            or self.session_service is None
            or self.decision is None
            or self.decision.proposed_plan is None
            or self.decision.action not in {RecommendationAction.REFINE, RecommendationAction.EXTEND_BOUNDARY}
        ):
            return False
        proposed = self.decision.proposed_plan
        event = {
            "type": "refinement_selected",
            "plan_id": self.plan.plan_id,
            "source_plan": self.plan.to_dict(),
            "selected_candidate_id": self.results.selected_candidate_id,
            "proposed_plan": proposed.to_dict(),
            "reasons": list(self.decision.reasons),
        }
        if not self._persist_event(event):
            return False
        updated = replace(
            self.session,
            plan=proposed,
            results=None,
            current_step="generation",
            status="ready_to_print",
        )
        try:
            self.session_service.save(updated)
        except Exception as exc:
            self.save_error = f"Could not save the next experiment: {exc}"
            self._refresh_ui()
            return False
        self.session = updated
        self.plan = proposed
        self.results = ExperimentResults(plan_id=proposed.plan_id, assessments=())
        self.report_path = _report_path(proposed.plan_id)
        self.report = {"schema_version": 1, "plan_id": proposed.plan_id, "events": []}
        self.confirmation = None
        self.confirmation_plan = None
        self.confirmation_opt_out_reason = None
        self.marked_inconclusive_reason = None
        self._evaluate()
        self._refresh_ui()
        if self.on_refinement_ready is not None:
            self.on_refinement_ready(updated, proposed)
        return True

    def request_confirmation(self) -> bool:
        if (
            self.plan is None
            or self.results is None
            or self.decision is None
            or not self.decision.confirmation_required
            or self.decision.action is RecommendationAction.EXTEND_BOUNDARY
            or self.results.tied_candidate_ids
            or self.session is None
            or self.session_service is None
            or self.on_confirmation_requested is None
        ):
            return False
        confirmation_plan = self.acceptance_service.create_confirmation_plan(
            self.plan, self.results
        )
        run_id = uuid4().hex
        candidate_id = confirmation_plan.candidates[0].candidate_id
        event = {
            "type": "confirmation_requested",
            "run_id": run_id,
            "source_plan_id": self.plan.plan_id,
            "source_candidate_id": self.results.selected_candidate_id,
            "plan_id": confirmation_plan.plan_id,
            "candidate_id": candidate_id,
            "settings": confirmation_plan.settings_for(candidate_id),
        }
        if not self._persist_event(event):
            return False
        self.confirmation_plan = confirmation_plan
        self.pending_confirmation_run_id = run_id
        self.on_confirmation_requested(self.session, confirmation_plan, run_id)
        return True

    def record_confirmation(
        self,
        confirmation_plan: ExperimentPlan,
        *,
        state: GenerationState,
        validation_state: ValidationState,
        run_id: str | None = None,
    ) -> bool:
        if self.plan is None or self.results is None or self.session is None:
            return False
        confirmation_plan = confirmation_plan or self.confirmation_plan
        if confirmation_plan is None:
            return False
        actual_run_id = run_id or self.pending_confirmation_run_id
        if actual_run_id is None:
            return False
        candidate = confirmation_plan.candidates[0]
        evidence = ConfirmationEvidence(
            run_id=actual_run_id,
            plan_id=confirmation_plan.plan_id,
            candidate_id=candidate.candidate_id,
            state=state,
            validation_state=validation_state,
            settings=confirmation_plan.settings_for(candidate.candidate_id),
        )
        event = {
            "type": "confirmation_run",
            "run_id": evidence.run_id,
            "source_plan_id": self.plan.plan_id,
            "source_candidate_id": self.results.selected_candidate_id,
            "plan_id": evidence.plan_id,
            "candidate_id": evidence.candidate_id,
            "state": evidence.state.value,
            "validation_state": evidence.validation_state.value,
            "settings": dict(evidence.settings),
        }
        if not self._persist_event(event):
            return False
        self.confirmation_plan = confirmation_plan
        self.confirmation = evidence
        self.confirmation_opt_out_reason = None
        self._evaluate()
        self._refresh_ui()
        return True

    def opt_out_confirmation(self, reason: str) -> bool:
        if (
            self.plan is None
            or self.results is None
            or self.decision is None
            or self.marked_inconclusive_reason is not None
        ):
            return False
        try:
            proposed = self.acceptance_service.evaluate(
                self.plan,
                self.results,
                confirmation=self.confirmation,
                confirmation_plan=self.confirmation_plan,
                confirmation_opt_out_reason=reason,
            )
        except ValueError as exc:
            self.save_error = str(exc)
            self._refresh_ui()
            return False
        if not proposed.can_accept or proposed.opt_out_record is None:
            self.save_error = "The current result cannot be accepted with an opt-out."
            self._refresh_ui()
            return False
        if not self._persist_event(dict(proposed.opt_out_record)):
            return False
        self.confirmation_opt_out_reason = str(proposed.opt_out_record["reason"])
        self.confirmation = None
        self._evaluate()
        self._refresh_ui()
        return True

    def accept(self) -> bool:
        if not self.can_accept or self.session is None or self.session_service is None:
            return False
        accepted = replace(self.results, accepted=True)
        updated = replace(
            self.session,
            current_step="export",
            status="export_ready",
            results=accepted,
        )
        try:
            self.session_service.save(updated)
        except Exception as exc:
            self.save_error = f"Could not save acceptance: {exc}"
            self._refresh_ui()
            return False
        self.session = updated
        self.results = accepted
        self._evaluate()
        self._refresh_ui()
        return True

    def mark_inconclusive(self, reason: str = "") -> bool:
        if self.plan is None or self.session is None or self.session_service is None:
            return False
        text = reason.strip() if isinstance(reason, str) else ""
        event = {
            "type": "marked_inconclusive",
            "plan_id": self.plan.plan_id,
            "reason": text or "The user marked this result inconclusive.",
        }
        previous_reason = self.marked_inconclusive_reason
        self.marked_inconclusive_reason = event["reason"]
        if not self._persist_event(event):
            self.marked_inconclusive_reason = previous_reason
            return False
        self.session = replace(self.session, current_step="recommendation", status="inconclusive")
        try:
            self.session_service.save(self.session)
        except Exception as exc:
            self.save_error = f"Could not save inconclusive decision: {exc}"
            self._refresh_ui()
            return False
        self._refresh_ui()
        return True

    def _save_results(self, results: ExperimentResults) -> bool:
        if self.session is None or self.session_service is None:
            self.save_error = "A saved session is required to resolve a tie."
            self._refresh_ui()
            return False
        updated = replace(self.session, current_step="recommendation", results=results)
        try:
            self.session_service.save(updated)
        except Exception as exc:
            self.save_error = f"Could not save tie resolution: {exc}"
            self._refresh_ui()
            return False
        self.session = updated
        return True

    def _persist_event(self, event: Mapping[str, Any]) -> bool:
        if self.session is None or self.session_service is None or self.report_path is None:
            self.save_error = "A saved session is required to record this decision."
            self._refresh_ui()
            return False
        repository = getattr(self.session_service, "repository", None)
        if repository is None:
            self.save_error = "The session service cannot write report artifacts."
            self._refresh_ui()
            return False
        events = list(self.report.get("events", []))
        events.append(dict(event))
        report = {
            "schema_version": 1,
            "plan_id": self.plan.plan_id,
            "events": events,
        }
        try:
            path = repository.write_json_artifact(
                self.session.session_id, self.report_path, report
            )
            artifact_paths = tuple(dict.fromkeys((*self.session.artifact_paths, path)))
            updated = replace(
                self.session,
                current_step="recommendation",
                artifact_paths=artifact_paths,
            )
            self.session_service.save(updated)
        except Exception as exc:
            self.save_error = f"Could not save recommendation report: {exc}"
            self._refresh_ui()
            return False
        self.report = report
        self.session = updated
        self.save_error = ""
        return True

    def _load_report(self) -> None:
        if (
            self.session is None
            or self.session_service is None
            or self.report_path not in self.session.artifact_paths
        ):
            return
        repository = getattr(self.session_service, "repository", None)
        if repository is None:
            return
        try:
            report = repository.read_json_artifact(self.session.session_id, self.report_path)
        except Exception as exc:
            self.save_error = f"Could not read the recommendation report: {exc}"
            return
        if not isinstance(report, dict) or report.get("plan_id") != self.plan.plan_id:
            return
        events = report.get("events", [])
        if not isinstance(events, list):
            return
        self.report = report
        for event in reversed(events):
            if (
                isinstance(event, dict)
                and event.get("type") == "marked_inconclusive"
                and event.get("plan_id") == self.plan.plan_id
            ):
                self.marked_inconclusive_reason = event.get("reason")
                break
        selected_id = self.results.selected_candidate_id
        for event in reversed(events):
            if not isinstance(event, dict) or event.get("source_plan_id", event.get("plan_id")) != self.plan.plan_id:
                continue
            if event.get("source_candidate_id", event.get("candidate_id")) != selected_id:
                continue
            if event.get("type") == "confirmation_opt_out":
                self.confirmation_opt_out_reason = event.get("reason")
                break
            if event.get("type") == "confirmation_run":
                try:
                    self.confirmation_plan = self.acceptance_service.create_confirmation_plan(
                        self.plan,
                        self.results,
                        plan_id=event["plan_id"],
                    )
                    self.confirmation = ConfirmationEvidence(
                        run_id=event["run_id"],
                        plan_id=event["plan_id"],
                        candidate_id=event["candidate_id"],
                        state=GenerationState(event["state"]),
                        validation_state=ValidationState(event["validation_state"]),
                        settings=event["settings"],
                    )
                except (KeyError, TypeError, ValueError):
                    self.confirmation = None
                break

    def _load_previous_plan(self) -> None:
        if (
            self.session is None
            or self.session_service is None
            or self.plan is None
            or self.plan.parent_plan_id is None
        ):
            return
        repository = getattr(self.session_service, "repository", None)
        parent_path = _report_path(self.plan.parent_plan_id)
        if repository is None or parent_path not in self.session.artifact_paths:
            return
        try:
            report = repository.read_json_artifact(self.session.session_id, parent_path)
        except Exception:
            return
        if not isinstance(report, dict) or report.get("plan_id") != self.plan.parent_plan_id:
            return
        events = report.get("events", [])
        if not isinstance(events, list):
            return
        for event in reversed(events):
            if not isinstance(event, dict) or event.get("type") != "refinement_selected":
                continue
            proposed = event.get("proposed_plan")
            source = event.get("source_plan")
            if not isinstance(proposed, dict) or proposed.get("plan_id") != self.plan.plan_id:
                continue
            if not isinstance(source, dict):
                continue
            try:
                self.previous_plan = ExperimentPlan.from_dict(source)
            except (KeyError, TypeError, ValueError):
                self.previous_plan = None
            return

    def _evaluate(self) -> None:
        if self.plan is None or self.results is None:
            self.decision = None
            return
        self.decision = self.acceptance_service.evaluate(
            self.plan,
            self.results,
            confirmation_plan=self.confirmation_plan,
            confirmation=self.confirmation,
            confirmation_opt_out_reason=self.confirmation_opt_out_reason,
        )
        if self.marked_inconclusive_reason is not None:
            self.decision = AcceptanceDecision(
                action=RecommendationAction.INCONCLUSIVE,
                reasons=(self.marked_inconclusive_reason,),
            )

    def _refresh_ui(self) -> None:
        if not self._rendered:
            return
        self._set("recommendation_error", self.save_error)
        if self.plan is None or self.results is None or self.decision is None:
            self._set("recommendation_heading", "Open a saved result to review its next step.")
            self._configure("recommendation_refine", enabled=False)
            self._configure("recommendation_confirmation", enabled=False)
            self._configure("recommendation_accept", enabled=False)
            self._configure("recommendation_resolve_tie", enabled=False)
            self._configure("recommendation_opt_out", enabled=False)
            return

        decision = self.decision
        self._set("recommendation_heading", decision.action.value.replace("_", " ").upper())
        self._set("recommendation_ranges", self._range_summary(decision.proposed_plan))
        self._set("recommendation_fixed", self._fixed_summary())
        self._set("recommendation_reasons", "\n".join(f"• {item}" for item in decision.reasons))
        tie_candidates = tuple(
            item
            for item in (self.results.selected_candidate_id, *self.results.tied_candidate_ids)
            if item is not None
        )
        self._configure("recommendation_tie_candidate", items=list(tie_candidates))
        if tie_candidates:
            self._set("recommendation_tie_candidate", tie_candidates[0])
        self._configure(
            "recommendation_resolve_tie",
            enabled=bool(self.results.tied_candidate_ids) and self.session is not None and self.session_service is not None,
        )
        can_refine = (
            decision.proposed_plan is not None
            and self.session is not None
            and self.session_service is not None
        )
        self._configure(
            "recommendation_refine",
            label="Extend boundary" if decision.action is RecommendationAction.EXTEND_BOUNDARY else "Refine",
            enabled=can_refine and not self.results.tied_candidate_ids,
        )
        can_confirm = (
            decision.confirmation_required
            and decision.action is not RecommendationAction.EXTEND_BOUNDARY
            and not self.results.tied_candidate_ids
            and self.session is not None
            and self.session_service is not None
            and self.on_confirmation_requested is not None
        )
        self._configure("recommendation_confirmation", enabled=can_confirm)
        self._configure(
            "recommendation_opt_out",
            enabled=(
                decision.confirmation_required
                and decision.action is not RecommendationAction.EXTEND_BOUNDARY
                and self.session is not None
                and self.session_service is not None
            ),
        )
        self._configure(
            "recommendation_accept",
            enabled=decision.can_accept and self.session is not None and self.session_service is not None,
        )

    def _range_summary(self, proposed_plan: ExperimentPlan | None) -> str:
        if self.plan is None or self.results is None:
            return ""
        selected_id = self.results.selected_candidate_id
        selected = self.plan.settings_for(selected_id) if selected_id is not None else {}
        lines = [f"Current plan: {self.plan.plan_id}"]
        for dimension in self.plan.dimensions:
            values = dimension.values
            line = f"{dimension.label or dimension.key}: {values[0]} → {values[-1]}"
            if dimension.key in selected:
                line += f"  ·  selected {selected[dimension.key]}"
            if proposed_plan is not None:
                next_dimension = next(
                    (item for item in proposed_plan.dimensions if item.key == dimension.key), None
                )
                if next_dimension is not None:
                    line += f"  ·  next {next_dimension.values[0]} → {next_dimension.values[-1]}"
            lines.append(line)
        if self.previous_plan is not None:
            lines.insert(1, f"Previous plan: {self.previous_plan.plan_id}")
            previous_dimensions = {item.key: item for item in self.previous_plan.dimensions}
            current_dimensions = {item.key: item for item in self.plan.dimensions}
            for key, dimension in current_dimensions.items():
                previous = previous_dimensions.get(key)
                if previous is not None and previous.values != dimension.values:
                    lines.append(
                        f"Changed {dimension.label or key}: "
                        f"{', '.join(map(str, previous.values))} → "
                        f"{', '.join(map(str, dimension.values))}"
                    )
        if selected_id is not None:
            lines.append(f"Selected candidate: {selected_id}")
        limits = self.acceptance_service.limits
        if limits:
            lines.append(
                "Configured limits: "
                + "; ".join(
                    f"{key} {bounds[0]} to {bounds[1]}" for key, bounds in sorted(limits.items())
                )
            )
        else:
            lines.append(
                "Safety limits: ironing flow must be non-negative and ironing speed positive; "
                "no additional bounds are configured."
            )
        return "\n".join(lines)

    def _fixed_summary(self) -> str:
        if self.plan is None:
            return ""
        sweep_keys = {dimension.key for dimension in self.plan.dimensions}
        fixed = {
            key: value
            for key, value in self.plan.baseline_settings.items()
            if key not in sweep_keys and key not in {"name", "type", "inherits"}
        }
        if not fixed:
            return "Fixed profile settings: none beyond the selected sweep."
        return "Fixed profile settings:\n" + "\n".join(
            f"{key}: {value}" for key, value in sorted(fixed.items())
        )

    def _on_resolve_tie(self, sender: Any, app_data: Any, user_data: Any = None) -> None:
        del sender, user_data
        if isinstance(app_data, str):
            self.resolve_tie(app_data)

    def _on_refine(self, sender: Any, app_data: Any, user_data: Any = None) -> None:
        del sender, app_data, user_data
        self.request_refinement()

    def _on_confirmation(self, sender: Any, app_data: Any, user_data: Any = None) -> None:
        del sender, app_data, user_data
        self.request_confirmation()

    def _on_accept(self, sender: Any, app_data: Any, user_data: Any = None) -> None:
        del sender, app_data, user_data
        self.accept()

    def _on_opt_out(self, sender: Any, app_data: Any, user_data: Any = None) -> None:
        del sender, app_data, user_data
        reason = self.dpg.get_value("recommendation_opt_out_reason")
        if isinstance(reason, str):
            self.opt_out_confirmation(reason)

    def _on_inconclusive(self, sender: Any, app_data: Any, user_data: Any = None) -> None:
        del sender, app_data, user_data
        self.mark_inconclusive()

    def _set(self, tag: str, value: Any) -> None:
        if self.dpg.does_item_exist(tag):
            self.dpg.set_value(tag, value)

    def _configure(self, tag: str, **values: Any) -> None:
        if self.dpg.does_item_exist(tag):
            self.dpg.configure_item(tag, **values)


def _report_path(plan_id: str) -> str:
    digest = sha256(plan_id.encode("utf-8")).hexdigest()[:16]
    return f"reports/recommendation-{digest}.json"
