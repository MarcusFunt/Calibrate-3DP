from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest

from calibrate3dp.app.models import ProfileSelection
from calibrate3dp.domain.records import (
    ArtifactRecord,
    CalibrationRunRecord,
    MaterialRecord,
    PrinterRecord,
    ProfileSnapshot,
)
from calibrate3dp.experiments import ExperimentCandidate, ExperimentPlan, SweepDimension
from calibrate3dp.profiles import ProfileDocument, ResolvedProfile
from calibrate3dp.storage.library_store import (
    DuplicatePlateCodeError,
    LibraryRepository,
    RunStateError,
)
from calibrate3dp.storage.session_store import SessionRepository


def _resolved(name: str, kind: str, settings: dict, *, scope="library"):
    raw = {"name": name, "type": kind, **settings, "unknown_project_key": "preserved"}
    document = ProfileDocument(name, kind, scope, raw, f"{name}.json")
    return ResolvedProfile(document, raw, {key: document for key in raw}, (document,))


def _records(tmp: Path):
    machine = ProfileSnapshot.capture(_resolved("Test machine", "machine", {"printer_model": "Test printer", "nozzle_diameter": ["0.4"]}))
    process = ProfileSnapshot.capture(_resolved("Test process", "process", {"ironing_type": "top", "ironing_flow": "5%", "ironing_speed": "5"}))
    filament = ProfileSnapshot.capture(_resolved("Test PLA", "filament", {"filament_type": ["PLA"]}))
    now = datetime.now(timezone.utc).isoformat()
    printer = PrinterRecord("printer-1", "Bench printer", "Test printer", "0.4 mm", machine, process, now)
    material = MaterialRecord("material-1", "Generic PLA", "0.4 mm", filament, None, now)
    selection = ProfileSelection(machine.profile, filament.profile, process.profile)
    plan = ExperimentPlan(
        "plan-1", "ironing",
        {"ironing_flow": "5%", "ironing_speed": "5", "ironing_type": "top"},
        (SweepDimension("ironing_flow", ("12%",), "Flow"), SweepDimension("ironing_speed", (10,), "Speed")),
        (ExperimentCandidate("sample-1", {"ironing_flow": "12%", "ironing_speed": 10}),),
    )
    run = CalibrationRunRecord(
        "run-1", "7K3P9D", printer.printer_id, material.material_id, "generating", now,
        plan, selection,
        ({"label": "Sample-A", "candidate_id": "sample-1", "settings": {"ironing_flow": "12%", "ironing_speed": 10}},),
        {"validated": False},
    )
    return printer, material, run


class ProfileSnapshotTests(unittest.TestCase):
    def test_resolved_profile_roundtrip_preserves_raw_keys_and_provenance(self):
        original = ProfileSnapshot.capture(_resolved("Test process", "process", {"ironing_flow": "5%"}), "a" * 64)
        restored = ProfileSnapshot.from_dict(original.to_dict())
        self.assertEqual(restored.source_sha256, "a" * 64)
        self.assertEqual(restored.profile.profile.raw["unknown_project_key"], "preserved")
        self.assertEqual(restored.profile.settings["ironing_flow"], "5%")
        self.assertEqual(restored.profile.provenance["ironing_flow"].name, "Test process")

    def test_printer_material_and_run_payloads_are_versioned(self):
        with tempfile.TemporaryDirectory() as temp:
            printer, material, run = _records(Path(temp))
            self.assertEqual(PrinterRecord.from_dict(printer.to_dict()), printer)
            self.assertEqual(MaterialRecord.from_dict(material.to_dict()), material)
            restored = CalibrationRunRecord.from_dict(run.to_dict())
            self.assertEqual(restored.plan.to_dict(), run.plan.to_dict())
            self.assertEqual(restored.plate_code, "7K3P9D")
            self.assertEqual(restored.sample_map[0]["settings"]["ironing_flow"], "12%")


class LibraryRepositoryTests(unittest.TestCase):
    def test_migrates_session_schema_and_preserves_existing_session_rows(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            db_path = root / "sessions.sqlite3"
            connection = sqlite3.connect(db_path)
            connection.executescript("""
                CREATE TABLE sessions (
                    session_id TEXT PRIMARY KEY, module_id TEXT NOT NULL, status TEXT NOT NULL,
                    created_at_utc TEXT NOT NULL, updated_at_utc TEXT NOT NULL,
                    profile_names_json TEXT NOT NULL, profile_selection_path TEXT NOT NULL,
                    payload_json TEXT NOT NULL, archived INTEGER NOT NULL DEFAULT 0 CHECK (archived IN (0, 1))
                );
                CREATE INDEX sessions_recent_idx ON sessions (archived, updated_at_utc DESC, session_id ASC);
                PRAGMA user_version = 1;
                INSERT INTO sessions VALUES (
                    'legacy-session', 'ironing', 'complete', '2026-10-01T00:00:00Z', '2026-10-01T00:00:00Z',
                    '{}', 'profiles/profile-selection.json', '{"schema_version":1}', 0
                );
            """)
            connection.commit()
            connection.close()

            sessions = SessionRepository(root)
            LibraryRepository(sessions)
            connection = sqlite3.connect(db_path)
            self.assertEqual(connection.execute("PRAGMA user_version").fetchone()[0], 2)
            self.assertEqual(connection.execute("SELECT session_id, payload_json FROM sessions").fetchone(), ("legacy-session", '{"schema_version":1}'))
            tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            connection.close()
            self.assertTrue({"printers", "materials", "calibration_runs"} <= tables)

    def test_printers_materials_runs_and_plate_code_lookup_persist(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            sessions = SessionRepository(root)
            repository = LibraryRepository(sessions)
            printer, material, run = _records(root)
            repository.add_printer(printer)
            repository.add_material(material)
            repository.create_run(run)
            self.assertEqual(repository.get_printer(printer.printer_id), printer)
            self.assertEqual(repository.get_material(material.material_id), material)
            self.assertEqual(repository.get_run_by_plate_code("7k3p9d"), run)
            self.assertEqual(repository.list_runs()[0], run)

    def test_unique_plate_code_and_finalized_plan_immutability(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            repository = LibraryRepository(SessionRepository(root))
            printer, material, run = _records(root)
            repository.add_printer(printer)
            repository.add_material(material)
            repository.create_run(run)
            duplicate = CalibrationRunRecord(
                "run-2", run.plate_code, run.printer_id, run.material_id, "generating", run.created_at_utc,
                run.plan, run.profiles, run.sample_map, run.validation,
            )
            with self.assertRaises(DuplicatePlateCodeError):
                repository.create_run(duplicate)

            artifact_path = root / "runs" / run.run_id / "manifest.json"
            artifact_path.parent.mkdir(parents=True)
            artifact_path.write_text("{}\n", encoding="utf-8")
            artifact = ArtifactRecord("runs/run-1/manifest.json", "application/json", artifact_path.stat().st_size, hashlib.sha256(artifact_path.read_bytes()).hexdigest())
            finalized = repository.finalize_run(
                run.run_id, status="settings_validated", artifacts=(artifact,), validation={"validated": True}
            )
            self.assertEqual(finalized.status, "settings_validated")
            self.assertEqual(finalized.plan.to_dict(), run.plan.to_dict())
            with self.assertRaises(RunStateError):
                repository.finalize_run(run.run_id, status="validation_failed", artifacts=(), validation={})


if __name__ == "__main__":
    unittest.main()
