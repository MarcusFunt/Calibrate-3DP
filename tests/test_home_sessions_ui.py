"""Home, session management, and application settings contracts."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path
import tempfile
import unittest

from calibrate3dp.app.models import ProfileSelection, SessionSnapshot
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


class HomeAndSessionTests(unittest.TestCase):
    def test_home_status_matches_persisted_session(self):
        with tempfile.TemporaryDirectory() as directory:
            service = SessionService(SessionRepository(Path(directory)))
            snapshot = _session()
            service.repository.create(snapshot)
            summary = service.list_recent()[0]

            self.assertEqual(summary.status, snapshot.status)

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
