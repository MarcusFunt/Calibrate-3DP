"""Decision policy, tie resolution, and confirmation workflow tests."""

from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
import tempfile
import unittest

from calibrate3dp.app.models import ProfileSelection, SessionSnapshot
from calibrate3dp.app.services.acceptance_service import (
    AcceptanceService,
    ConfirmationEvidence,
    RecommendationAction,
)
from calibrate3dp.app.services.experiment_service import ExperimentService
from calibrate3dp.app.services.generation_port import GenerationState, ValidationState
from calibrate3dp.app.pages.recommendation_page import RecommendationPage
from calibrate3dp.experiments import CandidateAssessment, ExperimentResults
from calibrate3dp.ironing import create_initial_ironing_experiment
from calibrate3dp.profiles import ProfileDocument, ResolvedProfile
from calibrate3dp.app.services.session_service import SessionService
from calibrate3dp.storage.session_store import SessionRepository


class FakeDpg:
    def __init__(self):
        self.items = {}
        self.callbacks = {}

    @contextmanager
    def group(self, *args, **kwargs):
        yield

    @contextmanager
    def child_window(self, *args, **kwargs):
        yield

    def add_spacer(self, **kwargs):
        return None

    def add_text(self, value="", **kwargs):
        tag = kwargs.get("tag")
        if tag:
            self.items[tag] = {"value": value, **kwargs}

    def add_combo(self, *, label="", items=(), **kwargs):
        self._add_widget(label=label, items=list(items), **kwargs)

    def add_input_text(self, *, label="", **kwargs):
        self._add_widget(label=label, **kwargs)

    def add_button(self, *, label, **kwargs):
        self._add_widget(label=label, **kwargs)

    def _add_widget(self, **kwargs):
        tag = kwargs.get("tag")
        if tag:
            self.items[tag] = dict(kwargs)
            if kwargs.get("callback"):
                self.callbacks[tag] = kwargs["callback"]

    def set_value(self, tag, value):
        self.items.setdefault(tag, {})["value"] = value

    def get_value(self, tag):
        return self.items.get(tag, {}).get("value")

    def configure_item(self, tag, **kwargs):
        self.items.setdefault(tag, {}).update(kwargs)

    def does_item_exist(self, tag):
        return tag in self.items


class FakeSessionService:
    def __init__(self):
        self.repository = object()
        self.saved = []

    def save(self, snapshot):
        self.saved.append(snapshot)


class RecommendationServiceTests(unittest.TestCase):
    def setUp(self):
        self.plan = _make_plan("iron-recommendation")
        self.experiment_service = ExperimentService()
        self.acceptance_service = AcceptanceService(self.experiment_service)

    def test_boundary_winner_explains_extension(self):
        results = _results(self.plan, selected="I001")

        decision = self.acceptance_service.evaluate(self.plan, results)

        self.assertEqual(decision.action, RecommendationAction.EXTEND_BOUNDARY)
        self.assertTrue(any("lower boundary" in reason for reason in decision.reasons))
        self.assertIsNotNone(decision.proposed_plan)
        self.assertEqual(decision.proposed_plan.parent_plan_id, self.plan.plan_id)

    def test_interior_winner_explains_narrowing(self):
        results = _results(self.plan, selected="I005")

        decision = self.acceptance_service.evaluate(self.plan, results)

        self.assertEqual(decision.action, RecommendationAction.REFINE)
        self.assertTrue(any("interior" in reason.lower() for reason in decision.reasons))
        self.assertIsNotNone(decision.proposed_plan)
        self.assertIn("interior narrowing", decision.proposed_plan.rationale)

    def test_tie_requires_explicit_resolution(self):
        results = _results(self.plan, selected="I001", tied=("I002",))
        timestamp = datetime.now(timezone.utc).isoformat()
        session = SessionSnapshot(
            session_id="recommendation-tie",
            created_at_utc=timestamp,
            updated_at_utc=timestamp,
            module_id=self.plan.module_id,
            current_step="recommendation",
            profile_selection=_profiles(),
            plan=self.plan,
            results=results,
            status="awaiting_results",
        )
        session_service = FakeSessionService()
        page = RecommendationPage(
            FakeDpg(), self.acceptance_service, self.experiment_service, session_service
        )
        page.set_context(session=session, plan=self.plan, results=results)

        self.assertEqual(page.decision.action, RecommendationAction.RESOLVE_TIE)
        self.assertFalse(page.can_accept)
        self.assertTrue(page.resolve_tie("I002"))
        self.assertEqual(page.results.selected_candidate_id, "I002")
        self.assertEqual(page.results.tied_candidate_ids, ())
        self.assertEqual(page.results.accepted, False)

    def test_acceptance_waits_for_confirmation_run(self):
        results = _results(self.plan, selected="I005")
        confirmation_plan = self.acceptance_service.create_confirmation_plan(
            self.plan, results, plan_id="iron-recommendation-confirm"
        )
        evidence = ConfirmationEvidence(
            run_id="confirmation-run-1",
            plan_id=confirmation_plan.plan_id,
            candidate_id=confirmation_plan.candidates[0].candidate_id,
            state=GenerationState.SUCCEEDED,
            validation_state=ValidationState.VALID,
            settings=confirmation_plan.settings_for(confirmation_plan.candidates[0].candidate_id),
        )

        blocked = self.acceptance_service.evaluate(self.plan, results)
        allowed = self.acceptance_service.evaluate(
            self.plan,
            results,
            confirmation_plan=confirmation_plan,
            confirmation=evidence,
        )

        self.assertFalse(blocked.can_accept)
        self.assertTrue(blocked.confirmation_required)
        self.assertTrue(allowed.can_accept)
        self.assertEqual(allowed.action, RecommendationAction.ACCEPT)

    def test_failed_or_canceled_confirmation_does_not_unlock_acceptance(self):
        results = _results(self.plan, selected="I005")
        confirmation_plan = self.acceptance_service.create_confirmation_plan(
            self.plan, results, plan_id="iron-recommendation-confirm-failed"
        )
        for state in (GenerationState.FAILED, GenerationState.CANCELED):
            evidence = ConfirmationEvidence(
                run_id=f"{state.value}-run",
                plan_id=confirmation_plan.plan_id,
                candidate_id=confirmation_plan.candidates[0].candidate_id,
                state=state,
                validation_state=ValidationState.INVALID,
                settings=confirmation_plan.settings_for(confirmation_plan.candidates[0].candidate_id),
            )
            decision = self.acceptance_service.evaluate(
                self.plan,
                results,
                confirmation_plan=confirmation_plan,
                confirmation=evidence,
            )
            self.assertFalse(decision.can_accept)

    def test_opt_out_is_recorded_in_session_report(self):
        results = _results(self.plan, selected="I005")
        timestamp = datetime.now(timezone.utc).isoformat()
        session = SessionSnapshot(
            session_id="recommendation-opt-out",
            created_at_utc=timestamp,
            updated_at_utc=timestamp,
            module_id=self.plan.module_id,
            current_step="recommendation",
            profile_selection=_profiles(),
            plan=self.plan,
            results=results,
            status="awaiting_results",
        )

        with tempfile.TemporaryDirectory() as directory:
            repository = SessionRepository(Path(directory) / "workbench")
            repository.create(session)
            service = SessionService(repository)
            page = RecommendationPage(
                FakeDpg(), self.acceptance_service, self.experiment_service, service
            )
            page.set_context(session=session, plan=self.plan, results=results)
            page.render()

            self.assertTrue(page.opt_out_confirmation("The user wants to proceed without another print."))

            self.assertTrue(page.can_accept)
            self.assertIn(page.report_path, page.session.artifact_paths)
            report = repository.read_json_artifact(page.session.session_id, page.report_path)
            opt_out = report["events"][-1]
            self.assertEqual(opt_out["type"], "confirmation_opt_out")
            self.assertEqual(
                opt_out["reason"], "The user wants to proceed without another print."
            )

    def test_failed_confirmation_is_saved_and_does_not_unlock_acceptance(self):
        results = _results(self.plan, selected="I005")
        session, service = self._saved_session("recommendation-failed-run", results)
        page = RecommendationPage(
            FakeDpg(), self.acceptance_service, self.experiment_service, service,
            on_confirmation_requested=lambda *_: None,
        )
        page.set_context(session=session, plan=self.plan, results=results)
        page.render()

        self.assertTrue(page.request_confirmation())
        confirmation_plan = page.confirmation_plan
        self.assertIsNotNone(confirmation_plan)
        self.assertTrue(page.record_confirmation(
            confirmation_plan,
            state=GenerationState.FAILED,
            validation_state=ValidationState.INVALID,
        ))
        self.assertFalse(page.can_accept)
        saved = service.resume(session.session_id)
        reloaded = RecommendationPage(
            FakeDpg(), self.acceptance_service, self.experiment_service, service
        )
        reloaded.set_context(session=saved, plan=self.plan, results=results)

        self.assertFalse(reloaded.can_accept)
        self.assertEqual(reloaded.confirmation.state, GenerationState.FAILED)
        self.assertTrue(reloaded.decision.confirmation_required)

    def test_mark_inconclusive_survives_session_reload(self):
        results = _results(self.plan, selected="I005")
        session, service = self._saved_session("recommendation-inconclusive", results)
        page = RecommendationPage(
            FakeDpg(), self.acceptance_service, self.experiment_service, service
        )
        page.set_context(session=session, plan=self.plan, results=results)

        self.assertTrue(page.mark_inconclusive("Surface quality was inconsistent."))
        saved = service.resume(session.session_id)
        reloaded = RecommendationPage(
            FakeDpg(), self.acceptance_service, self.experiment_service, service
        )
        reloaded.set_context(session=saved, plan=self.plan, results=results)

        self.assertFalse(reloaded.can_accept)
        self.assertEqual(reloaded.decision.action, RecommendationAction.INCONCLUSIVE)
        self.assertIn("Surface quality was inconsistent.", reloaded.decision.reasons)

    def test_refinement_report_explains_changed_ranges_after_reload(self):
        results = _results(self.plan, selected="I005")
        session, service = self._saved_session("recommendation-refinement", results)
        proposed_plans = []
        page = RecommendationPage(
            FakeDpg(), self.acceptance_service, self.experiment_service, service,
            on_refinement_ready=lambda _session, plan: proposed_plans.append(plan),
        )
        page.set_context(session=session, plan=self.plan, results=results)
        page.render()

        self.assertTrue(page.request_refinement())
        self.assertEqual(len(proposed_plans), 1)
        proposed = proposed_plans[0]
        saved = service.resume(session.session_id)
        reloaded = RecommendationPage(
            FakeDpg(), self.acceptance_service, self.experiment_service, service
        )
        reloaded.set_context(
            session=saved,
            plan=proposed,
            results=_results(proposed, selected="I005"),
        )
        explanation = reloaded._range_summary(None)

        self.assertIsNotNone(reloaded.previous_plan)
        self.assertEqual(reloaded.previous_plan.plan_id, self.plan.plan_id)
        self.assertIn("Previous plan: iron-recommendation", explanation)
        self.assertIn("Changed Ironing flow", explanation)
        self.assertIn("Safety limits:", explanation)

    def _saved_session(self, session_id, results):
        timestamp = datetime.now(timezone.utc).isoformat()
        session = SessionSnapshot(
            session_id=session_id,
            created_at_utc=timestamp,
            updated_at_utc=timestamp,
            module_id=self.plan.module_id,
            current_step="recommendation",
            profile_selection=_profiles(),
            plan=self.plan,
            results=results,
            status="awaiting_results",
        )
        directory = Path(tempfile.mkdtemp())
        self.addCleanup(lambda: __import__("shutil").rmtree(directory, ignore_errors=True))
        repository = SessionRepository(directory / "workbench")
        repository.create(session)
        return session, SessionService(repository)


def _make_plan(plan_id: str):
    return create_initial_ironing_experiment(
        plan_id=plan_id,
        baseline_settings={"ironing_flow": "10", "ironing_speed": "30", "ironing_type": "top"},
        flow_values=(8, 10, 12),
        speed_values=(20, 30, 40),
    )


def _results(plan, *, selected: str, tied: tuple[str, ...] = ()) -> ExperimentResults:
    passed = {selected, *tied}
    return ExperimentResults(
        plan_id=plan.plan_id,
        assessments=tuple(
            CandidateAssessment(
                candidate.candidate_id,
                verdict="pass" if candidate.candidate_id in passed else "fail",
            )
            for candidate in plan.candidates
        ),
        selected_candidate_id=selected,
        accepted=False,
        tied_candidate_ids=tied,
    )


def _profiles() -> ProfileSelection:
    def profile(name: str, kind: str) -> ResolvedProfile:
        raw = {"type": kind, "name": name}
        if kind == "process":
            raw.update({"ironing_flow": "10", "ironing_speed": "30", "ironing_type": "top"})
        document = ProfileDocument(name, kind, "fixture", raw, f"{name}.json")
        return ResolvedProfile(document, document.raw, {}, (document,))

    return ProfileSelection(profile("Printer", "machine"), profile("PLA", "filament"), profile("Fine", "process"))


if __name__ == "__main__":
    unittest.main()
