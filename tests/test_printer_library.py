from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from calibrate3dp.app.models import ProfileSelection
from calibrate3dp.app.services.experiment_service import ExperimentService
from calibrate3dp.app.services.library_service import LibraryService
from calibrate3dp.app.services.profile_service import ProfileService
from calibrate3dp.domain.experiment_config import (
    SavedExperimentConfiguration,
    default_layout_options,
)
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
    MissingRunArtifactError,
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
    @patch("calibrate3dp.storage.library_store.secrets.choice", side_effect=list("7K3P9D234567"))
    def test_allocate_plate_code_retries_an_existing_code(self, _choice):
        with tempfile.TemporaryDirectory() as temp:
            repository = LibraryRepository(SessionRepository(Path(temp)))
            printer, material, run = _records(Path(temp))
            repository.add_printer(printer)
            repository.add_material(material)
            repository.create_run(run)

            self.assertEqual(repository.allocate_plate_code(), "234567")

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
            version = connection.execute("PRAGMA user_version").fetchone()[0]
            self.assertEqual(connection.execute("SELECT session_id, payload_json FROM sessions").fetchone(), ("legacy-session", '{"schema_version":1}'))
            tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            connection.close()
            self.assertEqual(version, 4)
            self.assertTrue({
                "printers", "materials", "calibration_runs", "experiment_configs",
                "run_config_links", "run_assessment_revisions", "run_decisions", "run_exports",
            } <= tables)

    def test_schema_v3_config_migration_preserves_v1_and_accepts_v2_snapshots(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            repository = LibraryRepository(SessionRepository(root))
            _printer, _material, run = _records(root)
            selection = run.profiles.with_source_hashes({
                "printer": "a" * 64,
                "process": "b" * 64,
                "filament": "c" * 64,
            })
            plan = ExperimentService().create_initial("ironing", selection)
            old_options = default_layout_options()
            v1_keys = {
                "strategy", "rows", "columns", "geometry_backend", "geometry_backend_version",
                "margin_mm", "specimen_width_mm", "specimen_depth_mm", "specimen_height_mm",
                "gap_mm", "connector_width_mm", "connector_height_mm", "connector_gap_mm",
                "frame_width_mm", "voxel_mm", "label_pixel_mm", "code_pixel_mm",
            }
            legacy_options = {key: value for key, value in old_options.items() if key in v1_keys}
            legacy_options["geometry_backend"] = "stdlib-voxel"
            legacy = SavedExperimentConfiguration(
                "config-legacy", "experiment-legacy", 1,
                "printer-1", "material-1", selection, plan, legacy_options,
                "2026-10-10T12:00:00Z", schema_version=1,
            )
            repository.save_configuration(legacy)

            connection = sqlite3.connect(root / "sessions.sqlite3")
            connection.execute("PRAGMA foreign_keys = OFF")
            connection.execute("ALTER TABLE run_config_links RENAME TO run_config_links_v4")
            connection.execute("ALTER TABLE experiment_configs RENAME TO experiment_configs_v4")
            connection.execute(
                """CREATE TABLE experiment_configs (
                    config_id TEXT PRIMARY KEY,
                    experiment_id TEXT NOT NULL,
                    revision_no INTEGER NOT NULL CHECK (revision_no > 0),
                    schema_version INTEGER NOT NULL CHECK (schema_version = 1),
                    created_at_utc TEXT NOT NULL,
                    input_sha256 TEXT NOT NULL CHECK (length(input_sha256) = 64),
                    config_json TEXT NOT NULL,
                    UNIQUE (experiment_id, revision_no)
                )"""
            )
            connection.execute(
                """INSERT INTO experiment_configs
                   SELECT config_id, experiment_id, revision_no, schema_version,
                          created_at_utc, input_sha256, config_json
                   FROM experiment_configs_v4"""
            )
            connection.execute(
                """CREATE TABLE run_config_links (
                    run_id TEXT PRIMARY KEY REFERENCES calibration_runs(run_id),
                    config_id TEXT NOT NULL REFERENCES experiment_configs(config_id),
                    parent_run_id TEXT REFERENCES calibration_runs(run_id),
                    parent_assessment_revision_id TEXT,
                    parent_candidate_id TEXT,
                    relation_type TEXT NOT NULL CHECK (relation_type IN ('initial','refinement','confirmation')),
                    FOREIGN KEY (parent_run_id, parent_assessment_revision_id)
                        REFERENCES run_assessment_revisions(run_id, assessment_revision_id)
                )"""
            )
            connection.execute("DROP TABLE run_config_links_v4")
            connection.execute("DROP TABLE experiment_configs_v4")
            connection.execute("CREATE INDEX experiment_configs_revision_idx ON experiment_configs (experiment_id, revision_no DESC)")
            connection.execute("PRAGMA user_version = 3")
            connection.commit()
            connection.close()

            migrated = LibraryRepository(SessionRepository(root))
            self.assertEqual(migrated.get_configuration(legacy.config_id), legacy)
            v2 = replace(
                legacy,
                config_id="config-new-v2",
                experiment_id="experiment-new-v2",
                layout_options=default_layout_options(),
                schema_version=2,
            )
            migrated.save_configuration(v2)
            self.assertEqual(migrated.get_configuration(v2.config_id), v2)
            connection = sqlite3.connect(root / "sessions.sqlite3")
            self.assertEqual(connection.execute("PRAGMA user_version").fetchone()[0], 4)
            self.assertEqual(
                connection.execute("SELECT schema_version FROM experiment_configs ORDER BY schema_version").fetchall(),
                [(1,), (2,)],
            )
            connection.close()

    def test_v2_migration_preserves_populated_printer_material_and_run_rows(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            repository = LibraryRepository(SessionRepository(root))
            printer, material, run = _records(root)
            repository.add_printer(printer)
            repository.add_material(material)
            repository.create_run(run)
            connection = sqlite3.connect(root / "sessions.sqlite3")
            connection.execute("PRAGMA foreign_keys = OFF")
            for table in (
                "run_exports", "run_decisions", "run_assessment_revisions",
                "run_config_links", "experiment_configs",
            ):
                connection.execute(f"DROP TABLE {table}")
            connection.execute("PRAGMA user_version = 2")
            connection.commit()
            connection.close()

            migrated = LibraryRepository(SessionRepository(root))
            self.assertEqual(migrated.get_printer(printer.printer_id), printer)
            self.assertEqual(migrated.get_material(material.material_id), material)
            self.assertEqual(migrated.get_run(run.run_id), run)

    def test_schema_v3_migration_rolls_back_all_new_tables_on_failure(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            sessions = SessionRepository(root)
            connection = sqlite3.connect(root / "sessions.sqlite3")
            connection.execute("PRAGMA foreign_keys = OFF")
            for table in (
                "run_exports", "run_decisions", "run_assessment_revisions",
                "run_config_links", "experiment_configs",
            ):
                connection.execute(f"DROP TABLE {table}")
            connection.execute("PRAGMA user_version = 2")
            connection.execute("CREATE TABLE run_exports (conflicting_schema INTEGER)")
            connection.commit()
            connection.close()

            with self.assertRaises(sqlite3.OperationalError):
                SessionRepository(root)

            connection = sqlite3.connect(root / "sessions.sqlite3")
            version = connection.execute("PRAGMA user_version").fetchone()[0]
            tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            connection.close()
            self.assertEqual(version, 2)
            self.assertTrue(tables.isdisjoint({
                "experiment_configs", "run_config_links", "run_assessment_revisions", "run_decisions",
            }))
            self.assertTrue(sessions.database_path.exists())

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

    def test_configuration_revision_and_run_link_roundtrip(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            repository = LibraryRepository(SessionRepository(root))
            printer, material, _run = _records(root)
            repository.add_printer(printer)
            repository.add_material(material)
            selection = repository.get_printer(printer.printer_id)
            profile_selection = ProfileSelection(
                printer=selection.machine_profile.profile,
                process=selection.process_profile.profile,
                filament=material.filament_profile.profile,
                source_paths={
                    "printer": "printer.json", "process": "process.json", "filament": "filament.json",
                },
                source_hashes={"printer": "a" * 64, "process": "b" * 64, "filament": "c" * 64},
            )
            plan = ExperimentService().create_initial("ironing", profile_selection)
            configuration = SavedExperimentConfiguration(
                "config-1", "experiment-1", 1, printer.printer_id, material.material_id,
                profile_selection, plan, default_layout_options(), "2026-10-10T12:00:00Z",
            )
            repository.save_configuration(configuration)

            run = CalibrationRunRecord(
                "linked-run", "8K3P9D", printer.printer_id, material.material_id,
                "generating", "2026-10-10T12:01:00Z", plan, profile_selection,
                tuple({
                    "label": f"Sample-{label}", "candidate_id": candidate.candidate_id,
                    "settings": dict(candidate.overrides),
                } for label, candidate in zip("ABCDEFGHI", plan.candidates, strict=True)),
                {"state": "pending", "print_ready": False},
            )
            repository.create_run(run, configuration_id=configuration.config_id)

            self.assertEqual(repository.get_configuration(configuration.config_id), configuration)
            self.assertEqual(repository.get_configuration_for_run(run.run_id), configuration)
            with self.assertRaises(Exception):
                repository.save_configuration(configuration)

    def test_missing_or_corrupt_finalized_artifact_is_reported(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            repository = LibraryRepository(SessionRepository(root))
            printer, material, run = _records(root)
            repository.add_printer(printer)
            repository.add_material(material)
            repository.create_run(run)

            artifact_path = root / "runs" / run.run_id / "manifest.json"
            artifact_path.parent.mkdir(parents=True)
            artifact_path.write_text("original evidence\n", encoding="utf-8")
            original = artifact_path.read_bytes()
            artifact = ArtifactRecord(
                "runs/run-1/manifest.json", "application/json", len(original),
                hashlib.sha256(original).hexdigest(),
            )
            repository.finalize_run(
                run.run_id, status="settings_validated", artifacts=(artifact,), validation={}
            )

            artifact_path.unlink()
            with self.assertRaisesRegex(MissingRunArtifactError, "missing"):
                repository.artifact_path(artifact)

            artifact_path.write_text("changed evidence\n", encoding="utf-8")
            with self.assertRaisesRegex(MissingRunArtifactError, "hash does not match"):
                repository.artifact_path(artifact)


class LibraryServiceFreshnessTests(unittest.TestCase):
    def test_manual_import_and_inherited_sources_are_rechecked_after_restart(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            manual = root / "outside-orca-config"
            manual.mkdir()
            profile_root = root / "empty-orca-config"
            machine_path = manual / "machine.json"
            base_process_path = manual / "base-process.json"
            process_path = manual / "process.json"
            filament_path = manual / "filament.json"
            machine_path.write_text(json.dumps({
                "name": "Manual machine", "type": "machine", "nozzle_diameter": ["0.4"],
            }), encoding="utf-8")
            base_process_path.write_text(json.dumps({
                "name": "Manual base process", "type": "process",
                "ironing_flow": "5%", "ironing_speed": "5",
            }), encoding="utf-8")
            process_path.write_text(json.dumps({
                "name": "Manual process", "type": "process",
                "inherits": "Manual base process", "ironing_type": "top",
            }), encoding="utf-8")
            filament_path.write_text(json.dumps({
                "name": "Manual PLA", "type": "filament", "filament_type": ["PLA"],
            }), encoding="utf-8")

            profile_service = ProfileService(config_roots=(profile_root,))
            for path in (machine_path, base_process_path, process_path, filament_path):
                profile_service.import_source(path)
            repository = LibraryRepository(SessionRepository(root / "workspace"))
            library = LibraryService(repository, profile_service)
            printer = library.add_printer(
                display_name="Manual printer", model="Manual model", nozzle="0.4 mm",
                machine_choice=profile_service.choices("printer")[0],
                process_choice=next(item for item in profile_service.choices("process") if item.name == "Manual process"),
            )
            material = library.add_material(
                display_name="Manual PLA", nozzle_context="0.4 mm",
                filament_choice=profile_service.choices("filament")[0],
            )
            selection = library.resolve_selection(printer.printer_id, material.material_id)

            # Manual imports are intentionally outside the configured Orca tree.
            restarted_library = LibraryService(
                LibraryRepository(SessionRepository(root / "workspace")),
                ProfileService(config_roots=(profile_root,)),
            )
            self.assertEqual(
                restarted_library.current_source_hashes(selection),
                dict(selection.source_hashes),
            )

            base_process_path.write_text(json.dumps({
                "name": "Manual base process", "type": "process",
                "ironing_flow": "7%", "ironing_speed": "5",
            }), encoding="utf-8")
            changed_parent = restarted_library.current_source_hashes(selection)
            self.assertIsNone(changed_parent["process"])

            process_path.write_text(json.dumps({
                "name": "Manual process", "type": "process",
                "inherits": "Manual base process", "ironing_type": "top", "notes": "changed",
            }), encoding="utf-8")
            changed_leaf = restarted_library.current_source_hashes(selection)
            self.assertNotEqual(changed_leaf["process"], selection.source_hashes["process"])

            process_path.unlink()
            missing_leaf = restarted_library.current_source_hashes(selection)
            self.assertIsNone(missing_leaf["process"])


if __name__ == "__main__":
    unittest.main()
