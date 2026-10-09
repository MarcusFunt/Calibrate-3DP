"""Manual candidate assessment and explicit result acceptance UI."""

from __future__ import annotations

from dataclasses import replace
from time import monotonic
from typing import Any, Callable, Sequence

from calibrate3dp.app.models import SessionSnapshot
from calibrate3dp.app.widgets.assessment_editor import AssessmentEditor
from calibrate3dp.experiments import (
    CandidateAssessment,
    ExperimentPlan,
    ExperimentResults,
    ExperimentStateError,
)


class ResultsPage:
    """Collect per-candidate observations and save them to the active session."""

    SAVE_DEBOUNCE_SECONDS = 0.300

    def __init__(
        self,
        dpg: Any,
        session_service: Any | None,
        *,
        clock: Callable[[], float] = monotonic,
        on_back: Callable[[], None] | None = None,
        on_recommendation: Callable[[], None] | None = None,
    ) -> None:
        self.dpg = dpg
        self.session_service = session_service
        self.clock = clock
        self.on_back = on_back
        self.on_recommendation = on_recommendation
        self.session: SessionSnapshot | None = None
        self.plan: ExperimentPlan | None = None
        self.results: ExperimentResults | None = None
        self.save_status = "Unsaved"
        self.save_error = ""
        self._rendered = False
        self._notes_due_at: float | None = None
        self._selected_tie_id: str | None = None
        self._editor: AssessmentEditor | None = None

    @property
    def can_accept(self) -> bool:
        """Acceptance requires a selected pass and an explicit verdict per candidate."""
        if self.plan is None or self.results is None or self.results.tied_candidate_ids:
            return False
        assessments = {item.candidate_id: item for item in self.results.assessments}
        candidate_ids = {item.candidate_id for item in self.plan.candidates}
        if set(assessments) != candidate_ids or self.results.selected_candidate_id is None:
            return False
        if any(assessments[item].verdict not in {"pass", "fail"} for item in candidate_ids):
            return False
        return assessments[self.results.selected_candidate_id].verdict == "pass"

    def set_context(self, *, session: SessionSnapshot | None, plan: ExperimentPlan) -> None:
        """Bind the page to one reviewed plan and its resumable session state."""
        if not isinstance(plan, ExperimentPlan):
            raise TypeError("plan must be an ExperimentPlan")
        if session is not None and not isinstance(session, SessionSnapshot):
            raise TypeError("session must be a SessionSnapshot or None")
        self.plan = plan
        self.session = session
        stored_results = session.results if session is not None else None
        if stored_results is not None:
            try:
                stored_results.validate_for(plan)
            except ExperimentStateError:
                stored_results = None
        self.results = stored_results or ExperimentResults(plan_id=plan.plan_id, assessments=())
        self.save_status = "Saved" if stored_results is not None else "Unsaved"
        self.save_error = ""
        self._notes_due_at = None
        if self._rendered:
            if self._editor is not None:
                self._editor.render(self._candidate_ids())
            self._refresh_ui()

    def render(self) -> None:
        """Build the result-entry panel; callbacks run on Dear PyGui's UI thread."""
        if self._rendered:
            self._refresh_ui()
            return
        dpg = self.dpg
        with dpg.group(tag="results_panel", show=False):
            dpg.add_spacer(height=14)
            dpg.add_text("PRINT RESULTS", color=(92, 191, 178, 255))
            dpg.add_text(
                "Record what you observed on each specimen. Missing and uncertain prints stay explicit outcomes.",
                color=(165, 180, 195, 255),
                wrap=850,
            )
            dpg.add_text("", tag="results_save_status", wrap=850)
            dpg.add_text("", tag="results_save_error", wrap=850,
                         color=(235, 130, 125, 255))
            with dpg.group(horizontal=True):
                dpg.add_button(label="Back to generation", callback=self._on_back)
                dpg.add_button(
                    label="Retry save",
                    tag="results_retry_save",
                    enabled=False,
                    callback=self._on_retry_save,
                )
                dpg.add_combo(
                    label="Preferred candidate",
                    items=[],
                    default_value="No winner",
                    tag="results_winner",
                    callback=self._on_winner_changed,
                    width=260,
                )
                dpg.add_combo(
                    label="Tie candidate",
                    items=[],
                    tag="results_tie_candidate",
                    callback=self._on_tie_candidate_changed,
                    width=220,
                )
                dpg.add_button(
                    label="Toggle tie",
                    tag="results_toggle_tie",
                    callback=self._on_toggle_tie,
                )
                dpg.add_button(
                    label="Review recommendation",
                    tag="results_accept",
                    enabled=False,
                    callback=self._on_accept,
                )
            dpg.add_text("", tag="results_decision_summary", wrap=850)
            candidate_ids = self._candidate_ids()
            self._editor = AssessmentEditor(
                dpg,
                on_candidate=self._on_candidate_changed,
                on_rating=self.record_rating,
                on_verdict=self.set_verdict,
                on_tags=self.set_defect_tags,
                on_notes=self.set_notes,
                on_attach=self.attach_photo,
            )
            self._editor.render(candidate_ids)
        self._rendered = True
        self._refresh_ui()

    def assessment_for(self, candidate_id: str) -> CandidateAssessment:
        assessment = self._assessments().get(candidate_id)
        return assessment or CandidateAssessment(candidate_id=candidate_id)

    def record_rating(self, candidate_id: str, criterion: str, rating: int | None) -> bool:
        assessment = self.assessment_for(candidate_id)
        ratings = dict(assessment.ratings)
        if rating is None:
            ratings.pop(criterion, None)
        else:
            ratings[criterion] = rating
        updated = self._assessment(
            assessment,
            ratings=ratings,
        )
        self._store_assessment(updated)
        return self._persist()

    def set_verdict(self, candidate_id: str, verdict: str | None) -> bool:
        assessment = self.assessment_for(candidate_id)
        ratings = (
            {}
            if isinstance(verdict, str) and verdict in {"uncertain", "missing"}
            else dict(assessment.ratings)
        )
        updated = self._assessment(assessment, ratings=ratings, verdict=verdict)
        self._store_assessment(updated)
        return self._persist()

    def set_defect_tags(self, candidate_id: str, tags: Sequence[str]) -> bool:
        updated = self._assessment(self.assessment_for(candidate_id), defect_tags=tuple(tags))
        self._store_assessment(updated)
        return self._persist()

    def set_notes(self, candidate_id: str, notes: str) -> None:
        self._store_assessment(self._assessment(self.assessment_for(candidate_id), notes=notes))
        self.save_status = "Saving"
        self.save_error = ""
        self._notes_due_at = self.clock() + self.SAVE_DEBOUNCE_SECONDS
        self._refresh_ui()

    def select_winner(self, candidate_id: str | None) -> bool:
        if candidate_id is not None:
            self._require_candidate(candidate_id)
            self._ensure_assessment(candidate_id)
        ties = tuple(item for item in self._results().tied_candidate_ids if item != candidate_id)
        selected = ExperimentResults(
            plan_id=self._results().plan_id,
            assessments=self._results().assessments,
            selected_candidate_id=candidate_id,
            accepted=None,
            tied_candidate_ids=ties if candidate_id is not None else (),
        )
        self.results = selected
        self._notes_due_at = None
        self._refresh_ui()
        return self._persist()

    def set_tie(self, candidate_id: str, tied: bool) -> bool:
        self._require_candidate(candidate_id)
        if tied and candidate_id == self._results().selected_candidate_id:
            raise ValueError("the preferred candidate cannot also be a tie candidate")
        if tied:
            self._ensure_assessment(candidate_id)
        current = set(self._results().tied_candidate_ids)
        if tied:
            current.add(candidate_id)
        else:
            current.discard(candidate_id)
        self.results = ExperimentResults(
            plan_id=self._results().plan_id,
            assessments=self._results().assessments,
            selected_candidate_id=self._results().selected_candidate_id,
            accepted=None,
            tied_candidate_ids=tuple(
                item.candidate_id for item in self.plan.candidates if item.candidate_id in current
            ),
        )
        self._notes_due_at = None
        self._refresh_ui()
        return self._persist()

    def set_accepted(self, accepted: bool) -> bool:
        if accepted and not self.can_accept:
            return False
        self.results = replace(self._results(), accepted=accepted)
        self._notes_due_at = None
        self._refresh_ui()
        return self._persist()

    def attach_photo(self, candidate_id: str, source_path: str) -> bool:
        self._require_candidate(candidate_id)
        if self.session is None or self.session_service is None:
            self._set_save_failure("A saved session is required before attaching evidence.")
            return False
        try:
            relative_path = self.session_service.repository.copy_photo_to_session(
                self.session.session_id, source_path
            )
            assessment = self.assessment_for(candidate_id)
            if relative_path in assessment.photo_paths:
                return True
            updated = self._assessment(
                assessment,
                photo_paths=(*assessment.photo_paths, relative_path),
            )
            self._store_assessment(updated)
            self._notes_due_at = None
            self._refresh_ui()
            return self._persist()
        except Exception as exc:
            self._set_save_failure(f"Could not attach photo: {exc}")
            return False

    def retry_save(self) -> bool:
        self._notes_due_at = None
        return self._persist()

    def flush_pending(self) -> bool:
        """Persist pending note edits immediately during application shutdown."""
        if self._notes_due_at is None:
            return True
        self._notes_due_at = None
        return self._persist()

    def tick(self) -> bool:
        """Flush debounced notes when their 300 ms quiet period has elapsed."""
        if self._notes_due_at is None or self.clock() < self._notes_due_at:
            return False
        self._notes_due_at = None
        return self._persist()

    def _candidate_ids(self) -> tuple[str, ...]:
        if self.plan is None:
            return ()
        return tuple(item.candidate_id for item in self.plan.candidates)

    def _assessments(self) -> dict[str, CandidateAssessment]:
        return {item.candidate_id: item for item in self._results().assessments}

    def _results(self) -> ExperimentResults:
        if self.results is None:
            if self.plan is None:
                raise RuntimeError("set_context must be called before editing results")
            self.results = ExperimentResults(plan_id=self.plan.plan_id, assessments=())
        return self.results

    def _assessment(self, current: CandidateAssessment, **changes: Any) -> CandidateAssessment:
        values = {
            "candidate_id": current.candidate_id,
            "ratings": dict(current.ratings),
            "defect_tags": current.defect_tags,
            "notes": current.notes,
            "verdict": current.verdict,
            "photo_paths": current.photo_paths,
        }
        values.update(changes)
        return CandidateAssessment(**values)

    def _store_assessment(self, assessment: CandidateAssessment) -> None:
        assessments = self._assessments()
        assessments[assessment.candidate_id] = assessment
        ordered = tuple(
            assessments[item.candidate_id]
            for item in self.plan.candidates
            if item.candidate_id in assessments
        )
        self.results = ExperimentResults(
            plan_id=self._results().plan_id,
            assessments=ordered,
            selected_candidate_id=self._results().selected_candidate_id,
            accepted=None,
            tied_candidate_ids=self._results().tied_candidate_ids,
        )
        self._notes_due_at = None
        self._refresh_ui()

    def _ensure_assessment(self, candidate_id: str) -> None:
        if candidate_id not in self._assessments():
            self._store_assessment(CandidateAssessment(candidate_id=candidate_id))

    def _require_candidate(self, candidate_id: str) -> None:
        if candidate_id not in self._candidate_ids():
            raise ValueError(f"unknown candidate id {candidate_id!r}")

    def _persist(self) -> bool:
        self._notes_due_at = None
        if self.session is None or self.session_service is None:
            self._set_save_failure("Connect a saved session to save result changes.")
            return False
        self.save_status = "Saving"
        self.save_error = ""
        self._refresh_ui()
        updated_session = replace(
            self.session,
            current_step="results",
            results=self._results(),
        )
        try:
            self.session_service.save(updated_session)
        except Exception as exc:
            self._set_save_failure(str(exc))
            return False
        self.session = updated_session
        self.save_status = "Saved"
        self.save_error = ""
        self._refresh_ui()
        return True

    def _set_save_failure(self, message: str) -> None:
        self.save_status = "Save Failed"
        self.save_error = message
        self._refresh_ui()

    def _refresh_ui(self) -> None:
        if not self._rendered:
            return
        dpg = self.dpg
        self._set("results_save_status", self.save_status)
        self._set("results_save_error", self.save_error)
        self._configure(
            "results_retry_save",
            enabled=self.save_status == "Save Failed" and self.session is not None and self.session_service is not None,
        )
        if self.plan is None:
            self._set("results_decision_summary", "Accept a reviewed candidate grid to enter results.")
            self._configure("results_winner", items=["No winner"], enabled=False)
            self._configure("results_tie_candidate", items=[], enabled=False)
            self._configure("results_toggle_tie", enabled=False)
            self._configure("results_accept", enabled=False)
            if self._editor is not None:
                self._editor.refresh(None)
            return
        ids = self._candidate_ids()
        winner_items = ["No winner", *ids]
        tie_items = list(ids)
        self._configure("results_winner", items=winner_items)
        self._configure("results_tie_candidate", items=tie_items)
        selected = self._results().selected_candidate_id
        self._set("results_winner", selected or "No winner")
        if self._selected_tie_id not in ids:
            self._selected_tie_id = ids[0] if ids else None
        self._set("results_tie_candidate", self._selected_tie_id or "")
        ties = self._results().tied_candidate_ids
        summary = f"Preferred: {selected or 'not selected'}"
        if ties:
            summary += "  ·  Tie: " + ", ".join(ties)
        if self._results().accepted is True:
            summary += "  ·  Accepted"
        self._set("results_decision_summary", summary)
        self._configure(
            "results_toggle_tie",
            label="Remove tie" if self._selected_tie_id in ties else "Add tie",
            enabled=bool(ids) and self.session is not None and self.session_service is not None,
        )
        self._configure(
            "results_accept",
            enabled=self.can_accept and self.session is not None and self.session_service is not None,
            label="Review recommendation",
        )
        if self._editor is not None:
            selected_editor = self._editor.selected_candidate_id or (ids[0] if ids else None)
            self._editor.selected_candidate_id = selected_editor
            if selected_editor is not None:
                self._editor.refresh(self.assessment_for(selected_editor))

    def _on_candidate_changed(self, candidate_id: str) -> None:
        if self._editor is not None:
            self._editor.selected_candidate_id = candidate_id
            self._editor.refresh(self.assessment_for(candidate_id))

    def _on_winner_changed(self, sender: Any, app_data: Any, user_data: Any = None) -> None:
        del sender, user_data
        candidate_id = None if app_data == "No winner" else app_data
        if candidate_id is None or candidate_id in self._candidate_ids():
            self.select_winner(candidate_id)

    def _on_tie_candidate_changed(self, sender: Any, app_data: Any, user_data: Any = None) -> None:
        del sender, user_data
        if isinstance(app_data, str) and app_data in self._candidate_ids():
            self._selected_tie_id = app_data
            self._refresh_ui()

    def _on_toggle_tie(self, sender: Any, app_data: Any, user_data: Any = None) -> None:
        del sender, app_data, user_data
        if self._selected_tie_id is None:
            return
        tied = self._selected_tie_id in self._results().tied_candidate_ids
        try:
            self.set_tie(self._selected_tie_id, not tied)
        except ValueError as exc:
            self.save_error = str(exc)
            self._refresh_ui()

    def _on_accept(self, sender: Any, app_data: Any, user_data: Any = None) -> None:
        del sender, app_data, user_data
        if self.can_accept and self.on_recommendation is not None:
            self.on_recommendation()

    def _on_retry_save(self, sender: Any, app_data: Any, user_data: Any = None) -> None:
        del sender, app_data, user_data
        self.retry_save()

    def _on_back(self, sender: Any = None, app_data: Any = None, user_data: Any = None) -> None:
        del sender, app_data, user_data
        if self.on_back is not None:
            self.on_back()

    def _set(self, tag: str, value: Any) -> None:
        if self.dpg.does_item_exist(tag):
            self.dpg.set_value(tag, value)

    def _configure(self, tag: str, **values: Any) -> None:
        if self.dpg.does_item_exist(tag):
            self.dpg.configure_item(tag, **values)
