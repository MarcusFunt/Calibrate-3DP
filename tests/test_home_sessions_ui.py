"""Home, session management, and application settings contracts."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path
import tempfile
import unittest

from calibrate3dp.app.models import ProfileSelection, SessionSnapshot
from calibrate3dp.app.pages.home_page import HomePage
from calibrate3dp.app.window import AppShell
from calibrate3dp.app.services.session_service import SessionService
from calibrate3dp.app.services.settings_service import AppSettings, AppSettingsService
from calibrate3dp.experiments import CandidateAssessment, ExperimentResults
from calibrate3dp.ironing import create_initial_ironing_experiment
from calibrate3dp.profiles import ProfileDocument, ResolvedProfile
from calibrate3dp.storage.session_store import SessionRepository


def _profile(name: str, kind: str, settings: dict[str, object]) -> ResolvedProfile:
    document = ProfileDocument(
        name=name,
        kind=kind,
        scope="test-library",
        raw={"name": name, "type": kind, **settings},
        source=f"profiles/{name}.json",
    )
    return ResolvedProfile(
        profile=document,
        settings=settings,
        provenance={key: document for key in settings},
        chain=(document,),
    )


def _selection() -> ProfileSelection:
    return ProfileSelection(
        printer=_profile("Printer", "machine", {"bed_shape": "220x220"}),
        filament=_profile("PLA", "filament", {"nozzle_temperature": "210"}),
        process=_profile(
            "Process",
            "process",
            {"ironing_flow": "10", "ironing_speed": "30", "ironing_type": "top"},
        ),
    )


def _session(session_id: str = "session-home") -> SessionSnapshot:
    plan = create_initial_ironing_experiment(
        plan_id=f"{session_id}-plan",
        baseline_settings={"ironing_flow": "10", "ironing_speed": "30"},
        flow_values=(8, 10, 12),
        speed_values=(20, 30, 40),
    )
    results = ExperimentResults(
        plan_id=plan.plan_id,
        assessments=(CandidateAssessment("I005", ratings={"finish": 4}),),
    )
    return SessionSnapshot(
        session_id=session_id,
        created_at_utc="2026-10-08T10:00:00Z",
        updated_at_utc="2026-10-08T10:00:00Z",
        module_id="ironing",
        current_step="results",
        profile_selection=_selection(),
        plan=plan,
        results=results,
        status="awaiting_results",
    )


class _FakeDpg:
    def __init__(self) -> None:
        self.keys_down: set[int] = set()
        self.mvKey_LControl = 527
        self.mvKey_RControl = 531
        tags = {
            "page_frame", "route_heading", "page_home", "page_new_calibration",
            "page_sessions", "page_settings", "module_selection_panel",
            "experiment_review_panel", "generation_panel", "results_panel",
            "recommendation_panel", "export_panel", "profile_selection_panel",
            "session_recovery_notice",
        }
        tags.update(f"nav_{page}" for page in ("home", "new_calibration", "sessions", "settings"))
        self.items = {tag: {"show": False} for tag in tags}

    def does_item_exist(self, tag: str) -> bool:
        return tag in self.items

    def configure_item(self, tag: str, **kwargs: object) -> None:
        self.items.setdefault(tag, {}).update(kwargs)

    def set_value(self, tag: str, value: object) -> None:
        self.items.setdefault(tag, {})["value"] = value

    def bind_item_theme(self, *_: object) -> None:
        return None

    def is_key_down(self, key: int) -> bool:
        return key in self.keys_down


class _ResultsPageStub:
    def __init__(self) -> None:
        self.context = None

    def set_context(self, *, session: SessionSnapshot, plan: object) -> None:
        self.context = session, plan


class HomeAndSessionTests(unittest.TestCase):
    def test_home_status_matches_persisted_session(self):
        with tempfile.TemporaryDirectory() as directory:
            service = SessionService(SessionRepository(Path(directory)))
            snapshot = _session()
            service.repository.create(snapshot)
            summary = service.list_recent()[0]

            self.assertEqual(summary.status, snapshot.status)
            self.assertEqual(HomePage.status_label(summary.status), "Awaiting results")

    def test_resume_opens_saved_step(self):
        with tempfile.TemporaryDirectory() as directory:
            service = SessionService(SessionRepository(Path(directory)))
            snapshot = _session()
            service.repository.create(snapshot)
            dpg = _FakeDpg()
            shell = AppShell(services={"session_service": service}, dpg_module=dpg)
            results_page = _ResultsPageStub()
            shell._results_page = results_page
            shell._resume_session(snapshot.session_id)

            self.assertEqual(shell.current_page, "new_calibration")
            self.assertEqual(shell.resumed_step, "results")
            self.assertIsNotNone(shell.services["session"].plan)
            self.assertTrue(dpg.items["results_panel"]["show"])
            self.assertFalse(dpg.items["generation_panel"]["show"])
            self.assertIsNotNone(results_page.context)

    def test_reviewed_plan_creates_a_durable_ready_to_print_session(self):
        with tempfile.TemporaryDirectory() as directory:
            service = SessionService(SessionRepository(Path(directory)))
            shell = AppShell(services={"session_service": service})
            snapshot = _session()
            shell.profile_selection = snapshot.profile_selection

            shell._on_experiment_plan_ready(snapshot.plan)

            saved = service.resume(shell.active_session.session_id)
            self.assertEqual(saved.current_step, "generation")
            self.assertEqual(saved.status, "ready_to_print")
            self.assertEqual(saved.plan.plan_id, snapshot.plan.plan_id)

    def test_control_number_shortcut_navigates_to_session_list(self):
        with tempfile.TemporaryDirectory() as directory:
            dpg = _FakeDpg()
            dpg.keys_down.add(dpg.mvKey_LControl)
            service = SessionService(SessionRepository(Path(directory)))
            shell = AppShell(services={"session_service": service}, dpg_module=dpg)

            shell._on_keyboard_shortcut(None, None, "sessions")

            self.assertEqual(shell.current_page, "sessions")
            self.assertTrue(dpg.items["page_sessions"]["show"])

    def test_resume_reports_missing_artifacts_without_removing_the_session(self):
        with tempfile.TemporaryDirectory() as directory:
            repository = SessionRepository(Path(directory))
            service = SessionService(repository)
            snapshot = replace(_session(), artifact_paths=("runs/moved.gcode",))
            repository.create(snapshot)
            dpg = _FakeDpg()
            shell = AppShell(services={"session_service": service}, dpg_module=dpg)

            shell._resume_session(snapshot.session_id)

            self.assertIn("runs/moved.gcode", shell._session_recovery_message)
            self.assertTrue(service.list_recent())
            self.assertTrue(dpg.items["results_panel"]["show"])

    def test_archive_preserves_artifacts(self):
        with tempfile.TemporaryDirectory() as directory:
            repository = SessionRepository(Path(directory))
            service = SessionService(repository)
            snapshot = replace(_session(), artifact_paths=("runs/test.gcode",))
            repository.create(snapshot)
            artifact = repository.sessions_root / snapshot.session_id / "runs" / "test.gcode"
            artifact.parent.mkdir(parents=True)
            artifact.write_text("G1 X1\n", encoding="utf-8")

            service.archive(snapshot.session_id)

            self.assertTrue(artifact.is_file())
            self.assertEqual(artifact.read_text(encoding="utf-8"), "G1 X1\n")
            self.assertEqual(service.list_recent(), ())
            self.assertTrue(service.resume(snapshot.session_id).archived)

    def test_settings_survive_restart(self):
        with tempfile.TemporaryDirectory() as directory:
            settings_path = Path(directory) / "settings.json"
            first = AppSettingsService(settings_path)
            saved = AppSettings(
                workspace_root=Path(directory) / "workspace",
                default_export_root=Path(directory) / "exports",
                include_diagnostics_paths=True,
                include_diagnostics_profile_counts=False,
                orca_executable=Path(directory) / "orca-slicer.exe",
                orca_config_roots=(Path(directory) / "orca-profiles",),
            )

            first.save(saved)
            restarted = AppSettingsService(settings_path)

            self.assertEqual(restarted.settings, saved)


if __name__ == "__main__":
    unittest.main()
