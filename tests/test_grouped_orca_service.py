from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
import tempfile
import zipfile
import unittest

from calibrate3dp.app.services.grouped_orca_service import GroupedOrcaGenerationService
from calibrate3dp.app.services.library_service import LibraryService
from calibrate3dp.app.services.profile_service import ProfileService
from calibrate3dp.orca_cli import OrcaCliCapabilities, OrcaSliceResult
from calibrate3dp.storage.library_store import LibraryRepository
from calibrate3dp.storage.session_store import SessionRepository


_OPTIONS = frozenset({"--slice", "--arrange", "--orient", "--outputdir", "--datadir", "--load-settings", "--load-filaments"})


class FakeGroupedOrca:
    def __init__(self, *, sample_a_shift_mm: float = 0.0):
        self.sample_a_shift_mm = sample_a_shift_mm

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
            returncode=0,
            timed_out=False,
            stdout="fake stdout\n",
            stderr="one recorded warning\n",
            gcode_files=(path,),
            cancelled=False,
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

    def test_missing_orca_creates_a_failed_run_record_with_recovery_message(self):
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

            run = GroupedOrcaGenerationService(library, cli_provider=lambda: None).generate_ironing(
                printer.printer_id, material.material_id
            )

            self.assertEqual(run.status, "generation_failed")
            self.assertIn("Select an OrcaSlicer CLI", run.validation["messages"][0])
            self.assertEqual(repository.get_run_by_plate_code(run.plate_code).run_id, run.run_id)


if __name__ == "__main__":
    unittest.main()
