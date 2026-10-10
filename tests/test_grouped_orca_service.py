from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
import tempfile
import zipfile
import unittest

from calibrate3dp.app.services.experiment_configuration_service import ExperimentConfigurationService
from calibrate3dp.app.services.grouped_orca_service import CalibrationBlockedError, GroupedOrcaGenerationError, GroupedOrcaGenerationService
from calibrate3dp.app.services.library_service import LibraryService
from calibrate3dp.app.services.profile_service import ProfileService
from calibrate3dp.app.services.assessment_service import AssessmentService
from calibrate3dp.app.services.run_decision_service import RunDecisionService
from calibrate3dp.domain.assessment import PrintAttestation
from calibrate3dp.experiments import CandidateAssessment, ExperimentResults
from calibrate3dp.orca_cli import OrcaCliCapabilities, OrcaSliceResult
from calibrate3dp.storage.library_store import LibraryRepository
from calibrate3dp.storage.session_store import SessionRepository


_OPTIONS = frozenset({"--slice", "--arrange", "--orient", "--outputdir", "--datadir", "--load-settings", "--load-filaments"})


class FakeGroupedOrca:
    def __init__(self, *, sample_a_shift_mm: float = 0.0, returncode: int = 0, cancelled: bool = False):
        self.sample_a_shift_mm = sample_a_shift_mm
        self.returncode = returncode
        self.cancelled = cancelled

    def probe(self, *, timeout_seconds=30):
        return OrcaCliCapabilities(Path("orca-test.exe"), "OrcaSlicer-test", _OPTIONS, 0)

    def run_slice(self, *, model_path, machine_process_profiles, filament_profiles, output_dir, data_dir, timeout_seconds, cancel_event=None):
        with zipfile.ZipFile(model_path) as archive:
            manifest = json.loads(archive.read("Metadata/calibrate3dp-run.json"))
        lines = []
        samples = {sample["label"]: sample for sample in manifest["samples"]}
        for name, bounds in manifest["geometry"]["object_bounds_mm"].items():
            shift_x = self.sample_a_shift_mm if name == "Sample-A" else 0.0
            lines.extend((
                f"; printing object {name} id:1 copy 0",
                f"G1 X{bounds[0] + shift_x:g} Y{bounds[1]:g} E0.01 ; geometry",
                f"G1 X{bounds[3] + shift_x:g} Y{bounds[4]:g} E0.01 ; geometry",
            ))
            sample = samples.get(name)
            if sample is not None:
                settings = sample["settings"]
                flow = float(str(settings["ironing_flow"]).rstrip("%"))
                speed = float(settings["ironing_speed"])
                center_x = (bounds[0] + bounds[3]) / 2 + shift_x
                center_y = (bounds[1] + bounds[4]) / 2
                lines.extend((
                    ";TYPE:Ironing",
                    f"G1 F{speed * 60:g}",
                    f"G1 X{center_x:g} Y{center_y:g} E{flow * 0.1:.5f} ; ironing",
                ))
            lines.append(f"; stop printing object {name} id:1 copy 0")
        path = Path(output_dir) / "plate_1.gcode"
        path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        now = datetime.now(timezone.utc).isoformat()
        return OrcaSliceResult(
            argv=("orca-test.exe", "--slice", str(model_path)),
            started_at=now,
            finished_at=now,
            returncode=self.returncode,
            timed_out=False,
            stdout="fake stdout\n",
            stderr="one recorded warning\n",
            gcode_files=(path,),
            cancelled=self.cancelled,
        )


def _write_profile(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


def _create_test_library(root: Path):
    profile_root = root / "orca" / "system"
    _write_profile(profile_root / "machine" / "machine.json", {
        "name": "Test Printer 0.4 nozzle", "type": "machine", "printer_model": "Test Printer",
        "nozzle_diameter": ["0.4"], "printable_area": ["0x0", "220x0", "220x220", "0x220"],
    })
    _write_profile(profile_root / "process" / "process.json", {
        "name": "Test Quality", "type": "process", "ironing_type": "no",
        "ironing_flow": "5%", "ironing_speed": "5", "layer_height": "0.2",
    })
    _write_profile(profile_root / "filament" / "pla.json", {
        "name": "Test PLA", "type": "filament", "filament_type": ["PLA"],
    })
    profile_service = ProfileService(
        executable=root / "missing-orca.exe", config_roots=(profile_root,)
    )
    profile_service.discover_profiles()
    repository = LibraryRepository(SessionRepository(root / "workspace"))
    library = LibraryService(repository, profile_service)
    printer = library.add_printer(
        display_name="Workshop printer", model="Test Printer", nozzle="0.4 mm",
        machine_choice=profile_service.choices("printer")[0],
        process_choice=profile_service.choices("process")[0],
    )
    material = library.add_material(
        display_name="Workshop PLA", nozzle_context="0.4mm",
        filament_choice=profile_service.choices("filament")[0],
    )
    return repository, library, printer, material


class GroupedOrcaGenerationServiceTests(unittest.TestCase):
    def test_linked_one_candidate_confirmation_generates_a_labeled_single_sample_plate(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            repository, library, printer, material = _create_test_library(root)
            configurations = ExperimentConfigurationService(library)
            initial = configurations.prepare_ironing(printer.printer_id, material.material_id).configuration
            configurations.save(initial)
            generation = GroupedOrcaGenerationService(library, cli_provider=FakeGroupedOrca)
            parent = generation.generate_from_configuration(initial.config_id)
            selected = parent.plan.candidates[4].candidate_id
            results = ExperimentResults(
                parent.plan.plan_id,
                tuple(CandidateAssessment(item.candidate_id, verdict="pass" if item.candidate_id == selected else "fail") for item in parent.plan.candidates),
                selected_candidate_id=selected,
            )
            assessment = AssessmentService(repository).save(
                parent.run_id, expected_revision=0, results=results,
                attestation=PrintAttestation(False, False, synthetic=True),
            )
            decision = RunDecisionService(repository).evaluate(parent.run_id, assessment.assessment_revision_id)
            confirmation = RunDecisionService(repository).create_followup(decision.decision_id, kind="confirmation")

            child = generation.generate_from_configuration(confirmation.config_id)

            self.assertEqual(child.status, "settings_validated")
            self.assertEqual(len(child.plan.candidates), 1)
            self.assertEqual(len(child.sample_map), 1)
            self.assertEqual(child.sample_map[0]["label"], "Sample-A")
            self.assertEqual(child.sample_map[0]["candidate_id"], child.plan.candidates[0].candidate_id)
            self.assertTrue(child.validation["geometry"]["valid"])
            self.assertEqual(child.validation["geometry"]["sample_count"], 1)
            self.assertEqual(child.validation["geometry"]["object_count"], 2)
            self.assertEqual(repository.get_run_config_link(child.run_id)["relation_type"], "confirmation")

    def test_review_uses_real_layout_without_allocating_a_plate_code(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            repository, library, printer, material = _create_test_library(root)
            configurations = ExperimentConfigurationService(library)

            review = configurations.prepare_ironing(printer.printer_id, material.material_id)

            self.assertEqual(len(review.configuration.plan.candidates), 9)
            self.assertEqual(
                [item.candidate_id for item in review.experiment_review.plate_map],
                [item.candidate_id for item in review.configuration.plan.candidates],
            )
            self.assertEqual(
                [item.specimen_label for item in review.experiment_review.plate_map],
                [f"Sample-{label}" for label in "ABCDEFGHI"],
            )
            self.assertEqual(review.layout.plate_code, "XXXXXX")
            self.assertEqual(
                [item.label for item in review.layout.sample_placements], list("ABCDEFGHI")
            )
            self.assertEqual(repository.list_runs(), ())

    def test_saved_configuration_generates_and_reopens_the_exact_linked_revision(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            repository, library, printer, material = _create_test_library(root)
            configurations = ExperimentConfigurationService(library)
            generation = GroupedOrcaGenerationService(library, cli_provider=FakeGroupedOrca)
            review = configurations.prepare_ironing(printer.printer_id, material.material_id)
            configuration = review.configuration
            configurations.save(configuration)

            run = generation.generate_from_configuration(configuration.config_id)

            self.assertEqual(run.status, "settings_validated")
            self.assertEqual(repository.get_configuration_for_run(run.run_id), configuration)
            self.assertEqual(repository.get_run_config_link(run.run_id)["config_id"], configuration.config_id)
            self.assertEqual(run.plan.to_dict(), configuration.plan.to_dict())
            self.assertEqual(run.profiles.to_dict(), configuration.profile_selection.to_dict())
            self.assertEqual(run.validation["dependency_snapshot"]["schema_version"], 1)
            self.assertEqual(run.validation["dependency_snapshot"]["calibration_id"], "ironing")
            geometry_artifact = next(
                item for item in run.artifacts if item.relative_path.endswith("geometry/geometry.json")
            )
            geometry = json.loads(repository.artifact_path(geometry_artifact).read_text(encoding="utf-8"))
            expected_placements = [
                {
                    "label": item.label,
                    "candidate_id": item.candidate_id,
                    "row": item.row,
                    "column": item.column,
                    "x_mm": item.x_mm,
                    "y_mm": item.y_mm,
                    "width_mm": item.width_mm,
                    "depth_mm": item.depth_mm,
                    "height_mm": item.height_mm,
                }
                for item in review.layout.sample_placements
            ]
            self.assertEqual(geometry["sample_placements"], expected_placements)

    def test_changed_source_profile_hash_blocks_generation_before_allocating_a_run(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            repository, library, printer, material = _create_test_library(root)
            configurations = ExperimentConfigurationService(library)
            configuration = configurations.prepare_ironing(
                printer.printer_id, material.material_id
            ).configuration
            configurations.save(configuration)
            process_source = Path(configuration.profile_selection.source_paths["process"])
            process_source.write_text(
                json.dumps({
                    "name": "Test Quality", "type": "process", "ironing_type": "no",
                    "ironing_flow": "6%", "ironing_speed": "5", "layer_height": "0.2",
                }),
                encoding="utf-8",
            )
            generation = GroupedOrcaGenerationService(library, cli_provider=FakeGroupedOrca)

            with self.assertRaises(CalibrationBlockedError) as blocked:
                generation.generate_from_configuration(configuration.config_id)

            self.assertTrue(any("profile_hash_state.process" in item for item in blocked.exception.reasons))
            self.assertEqual(repository.list_runs(), ())
            self.assertEqual(list(repository.runs_root.iterdir()), [])

    def test_import_save_generate_and_reopen_grouped_run(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            repository, library, printer, material = _create_test_library(root)
            service = GroupedOrcaGenerationService(library, cli_provider=FakeGroupedOrca)

            run = service.generate_ironing(printer.printer_id, material.material_id)

            self.assertEqual(run.status, "settings_validated")
            self.assertFalse(run.validation["print_ready"])
            self.assertEqual(len(run.sample_map), 9)
            self.assertTrue(all(item["physical_label_present"] is True for item in run.sample_map))
            self.assertTrue(all(item["physical_plate_code_present"] is True for item in run.sample_map))
            self.assertTrue(run.validation["geometry"]["valid"])
            self.assertEqual(run.validation["geometry"]["object_count"], 10)
            self.assertEqual(run.validation["state"], "sample_settings_validated")
            self.assertIn("dependency_snapshot", run.validation)
            self.assertEqual(run.validation["dependency_snapshot"]["schema_version"], 1)
            self.assertTrue(run.validation["sliced_layout"]["valid"])
            self.assertEqual(run.validation["sliced_layout"]["xy_translation_mm"], [0.0, 0.0])
            self.assertTrue(run.validation["geometry_bounds"]["within_bounds"])
            self.assertEqual(run.validation["orca"]["identity_status"], "unresolved")
            self.assertFalse(run.validation["orca"]["identity"]["support_claim"])
            self.assertTrue(any(item.relative_path.endswith("manifest.json") for item in run.artifacts))
            self.assertTrue(any(item.relative_path.endswith("plate_1.gcode") for item in run.artifacts))
            stl_artifacts = [item for item in run.artifacts if item.relative_path.endswith(".stl")]
            self.assertEqual(len(stl_artifacts), 10)
            self.assertTrue(all(item.media_type == "model/stl" for item in stl_artifacts))
            self.assertTrue(any(item.relative_path.endswith("geometry.json") for item in run.artifacts))
            for artifact in run.artifacts:
                self.assertTrue(repository.artifact_path(artifact).is_file())
            self.assertEqual(repository.get_run_by_plate_code(run.plate_code).run_id, run.run_id)

            reopened = LibraryRepository(SessionRepository(root / "workspace"))
            loaded = reopened.get_run(run.run_id)
            self.assertEqual(loaded.plan.to_dict(), run.plan.to_dict())
            self.assertEqual(loaded.profiles.source_hashes, run.profiles.source_hashes)

    def test_rearranged_object_fails_generation_and_is_preserved_in_run(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            repository, library, printer, material = _create_test_library(root)
            service = GroupedOrcaGenerationService(
                library,
                cli_provider=lambda: FakeGroupedOrca(sample_a_shift_mm=30.0),
            )

            run = service.generate_ironing(printer.printer_id, material.material_id)

            self.assertEqual(run.status, "validation_failed")
            self.assertEqual(run.validation["state"], "plate_layout_failed")
            self.assertFalse(run.validation["sliced_layout"]["valid"])
            self.assertFalse(run.validation["print_ready"])
            self.assertTrue(any("Sample-A G-code X bounds" in message for message in run.validation["messages"]))
            self.assertEqual(repository.get_run_by_plate_code(run.plate_code).run_id, run.run_id)

    def test_missing_orca_blocks_before_allocating_a_run_or_artifacts(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            profile_root = root / "orca"
            _write_profile(profile_root / "machine" / "machine.json", {"name": "M", "type": "machine", "nozzle_diameter": ["0.4"], "bed_size": [220, 220]})
            _write_profile(profile_root / "process" / "process.json", {"name": "P", "type": "process", "ironing_flow": "5%", "ironing_speed": "5"})
            _write_profile(profile_root / "filament" / "filament.json", {"name": "F", "type": "filament"})
            profiles = ProfileService(executable=root / "missing.exe", config_roots=(profile_root,))
            profiles.discover_profiles()
            repository = LibraryRepository(SessionRepository(root / "workspace"))
            library = LibraryService(repository, profiles)
            printer = library.add_printer(
                display_name="P1", model="M", nozzle="0.4mm",
                machine_choice=profiles.choices("printer")[0], process_choice=profiles.choices("process")[0],
            )
            material = library.add_material(
                display_name="PLA", nozzle_context="0.4 mm", filament_choice=profiles.choices("filament")[0],
            )

            generation = GroupedOrcaGenerationService(library, cli_provider=lambda: None)
            with self.assertRaises(CalibrationBlockedError) as blocked:
                generation.generate_ironing(printer.printer_id, material.material_id)

            self.assertTrue(any("slicer" in item.lower() for item in blocked.exception.reasons))
            self.assertEqual(repository.list_runs(), ())
            self.assertEqual(list(repository.runs_root.iterdir()), [])

    def test_cancelled_slice_is_saved_as_not_ready(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            repository, library, printer, material = _create_test_library(root)
            service = GroupedOrcaGenerationService(
                library, cli_provider=lambda: FakeGroupedOrca(cancelled=True)
            )

            run = service.generate_ironing(printer.printer_id, material.material_id)

            self.assertEqual(run.status, "cancelled")
            self.assertEqual(run.validation["state"], "cancelled")
            self.assertFalse(run.validation["print_ready"])
            self.assertIn("dependency_snapshot", run.validation)
            self.assertEqual(repository.get_run_by_plate_code(run.plate_code).run_id, run.run_id)

    def test_failed_slice_is_saved_as_not_ready(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            repository, library, printer, material = _create_test_library(root)
            service = GroupedOrcaGenerationService(
                library, cli_provider=lambda: FakeGroupedOrca(returncode=2)
            )

            run = service.generate_ironing(printer.printer_id, material.material_id)

            self.assertEqual(run.status, "generation_failed")
            self.assertEqual(run.validation["state"], "orca_failed")
            self.assertFalse(run.validation["print_ready"])
            self.assertIn("dependency_snapshot", run.validation)
            self.assertEqual(repository.get_run_by_plate_code(run.plate_code).run_id, run.run_id)


if __name__ == "__main__":
    unittest.main()
