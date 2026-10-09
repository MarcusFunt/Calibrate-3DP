"""Persistence and resume contract for calibration sessions."""

from __future__ import annotations

from dataclasses import replace
import hashlib
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest

from calibrate3dp.app.models import ProfileSelection, SessionSnapshot
from calibrate3dp.app.services.session_service import SessionService
from calibrate3dp.experiments import CandidateAssessment, ExperimentResults
from calibrate3dp.ironing import create_initial_ironing_experiment
from calibrate3dp.profiles import ProfileDocument, ResolvedProfile
from calibrate3dp.storage.session_store import (
    SessionNotFoundError,
    SessionRepository,
    SessionStateError,
    UnsafeArtifactPathError,
)


def make_profile(name: str, kind: str, settings: dict[str, object]) -> ResolvedProfile:
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


def make_selection() -> ProfileSelection:
    return ProfileSelection(
        printer=make_profile("Test Printer", "machine", {"bed_shape": "220x220"}),
        filament=make_profile("Test PLA", "filament", {"nozzle_temperature": "210"}),
        process=make_profile(
            "Test Process",
            "process",
            {
                "ironing_type": "top",
                "ironing_flow": "10",
                "ironing_speed": "30",
                "ironing_pattern": "rectilinear",
            },
        ),
        compatibility_warnings=("Test profile set only.",),
    )


def make_snapshot(
    session_id: str = "session-001",
    *,
    updated_at_utc: str = "2025-01-01T00:00:00Z",
    artifact_paths: tuple[str, ...] = (),
) -> SessionSnapshot:
    plan = create_initial_ironing_experiment(
        plan_id=f"{session_id}-plan-1",
        baseline_settings={
            "ironing_type": "top",
            "ironing_flow": "10",
            "ironing_speed": "30",
            "ironing_pattern": "rectilinear",
        },
        flow_values=(8, 10, 12),
        speed_values=(20, 30, 40),
    )
    results = ExperimentResults(
        plan_id=plan.plan_id,
        assessments=(CandidateAssessment("I005", ratings={"finish": 4}),),
        selected_candidate_id="I005",
        accepted=False,
    )
    return SessionSnapshot(
        session_id=session_id,
        created_at_utc="2025-01-01T00:00:00Z",
        updated_at_utc=updated_at_utc,
        module_id="ironing",
        current_step="results",
        profile_selection=make_selection(),
        plan=plan,
        results=results,
        run_ids=(f"{session_id}-run-1",),
        artifact_paths=artifact_paths,
        status="awaiting_results",
    )


class SessionStoreTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.root = Path(self.temp_dir.name)
        self.repository = SessionRepository(self.root)

    def test_session_round_trip_restores_profiles_plan_and_results(self):
        snapshot = make_snapshot()

        self.repository.create(snapshot)
        restored = self.repository.load(snapshot.session_id)

        self.assertEqual(restored.profile_selection.printer.profile.name, "Test Printer")
        self.assertEqual(restored.profile_selection.process.settings["ironing_speed"], "30")
        self.assertEqual(restored.profile_selection.compatibility_warnings, ("Test profile set only.",))
        self.assertEqual(restored.plan.to_dict(), snapshot.plan.to_dict())
        self.assertEqual(restored.results.to_dict(), snapshot.results.to_dict())
        self.assertEqual(restored.run_ids, snapshot.run_ids)

        profile_snapshot = (
            self.root
            / "sessions"
            / snapshot.session_id
            / "profiles"
            / "profile-selection.json"
        )
        self.assertTrue(profile_snapshot.is_file())
        payload = json.loads(profile_snapshot.read_text(encoding="utf-8"))
        self.assertEqual(payload["schema_version"], 1)

    def test_save_updates_timestamp_atomically(self):
        snapshot = make_snapshot()
        self.repository.create(snapshot)
        updated = replace(snapshot, current_step="recommendation")

        self.repository.save(updated)

        restored = self.repository.load(snapshot.session_id)
        self.assertEqual(restored.current_step, "recommendation")
        self.assertGreater(restored.updated_at_utc, snapshot.updated_at_utc)
        connection = sqlite3.connect(self.repository.database_path)
        try:
            row = connection.execute(
                "SELECT updated_at_utc, payload_json FROM sessions WHERE session_id = ?",
                (snapshot.session_id,),
            ).fetchone()
        finally:
            connection.close()
        payload = json.loads(row[1])
        self.assertEqual(row[0], restored.updated_at_utc)
        self.assertEqual(payload["updated_at_utc"], restored.updated_at_utc)

    def test_list_recent_is_ordered_and_limited(self):
        snapshots = (
            make_snapshot("session-old", updated_at_utc="2025-01-02T00:00:00Z"),
            make_snapshot("session-newest", updated_at_utc="2025-03-02T00:00:00Z"),
            make_snapshot("session-middle", updated_at_utc="2025-02-02T00:00:00Z"),
        )
        for snapshot in snapshots:
            self.repository.create(snapshot)

        recent = self.repository.list_recent(limit=2)

        self.assertEqual(
            tuple(item.session_id for item in recent),
            ("session-newest", "session-middle"),
        )
        self.assertEqual(recent[0].profile_names["printer"], "Test Printer")
        self.assertEqual(recent[0].status, "awaiting_results")

    def test_unknown_session_has_clear_error(self):
        with self.assertRaisesRegex(SessionNotFoundError, "missing-session"):
            self.repository.load("missing-session")

    def test_artifact_paths_must_stay_inside_session_root(self):
        with self.assertRaises(UnsafeArtifactPathError):
            self.repository.create(make_snapshot(artifact_paths=("../outside.gcode",)))

        absolute_path = self.root / "outside.gcode"
        with self.assertRaises(UnsafeArtifactPathError):
            self.repository.create(make_snapshot("session-absolute", artifact_paths=(str(absolute_path),)))

    def test_session_service_fingerprints_profiles_and_resumes_validated_state(self):
        service = SessionService(self.repository)

        created = service.create_session(make_selection(), module_id="ironing")

        self.assertEqual(created.profile_selection.source_paths["printer"], "profiles/Test Printer.json")
        self.assertEqual(len(created.profile_selection.source_hashes["printer"]), 64)
        expected_hash = hashlib.sha256(
            json.dumps(
                dict(created.profile_selection.printer.profile.raw),
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest()
        self.assertEqual(created.profile_selection.source_hashes["printer"], expected_hash)

        plan = create_initial_ironing_experiment(
            plan_id=f"{created.session_id}-plan-1",
            baseline_settings={"ironing_flow": "10", "ironing_speed": "30"},
            flow_values=(8, 10, 12),
            speed_values=(20, 30, 40),
        )
        updated = replace(created, current_step="results", plan=plan)
        service.save(updated)
        restored = service.resume(created.session_id)

        self.assertEqual(restored.plan.to_dict(), plan.to_dict())
        self.assertEqual(restored.current_step, "results")
        self.assertEqual(service.list_recent()[0].session_id, created.session_id)

    def test_service_rejects_results_without_an_associated_plan(self):
        service = SessionService(self.repository)
        created = service.create_session(make_selection(), module_id="ironing")
        invalid_results = ExperimentResults(
            plan_id="another-plan",
            assessments=(),
        )
        invalid = replace(created, results=invalid_results)

        with self.assertRaisesRegex(SessionStateError, "before an experiment plan"):
            service.save(invalid)


if __name__ == "__main__":
    unittest.main()
