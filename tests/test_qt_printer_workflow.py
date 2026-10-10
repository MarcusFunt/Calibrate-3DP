from __future__ import annotations

import importlib.util
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from calibrate3dp.app.services.experiment_service import ExperimentService
from calibrate3dp.app.services.experiment_configuration_service import ExperimentConfigurationService
from calibrate3dp.app.services.calibration_state_service import CalibrationStateService
from calibrate3dp.app.services.grouped_orca_service import GroupedOrcaGenerationService
from calibrate3dp.app.services.library_service import LibraryService
from calibrate3dp.app.services.profile_service import ProfileService
from calibrate3dp.calibration.dependencies import DependencyEvaluator
from calibrate3dp.calibration.ironing import IRONING_DEPENDENCY_GRAPH
from calibrate3dp.app.qt.view_models import PrinterSummary
from calibrate3dp.domain.records import CalibrationRunRecord, utc_now
from calibrate3dp.storage.library_store import LibraryRepository
from calibrate3dp.storage.session_store import SessionRepository


def _write_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value) + "\n", encoding="utf-8")


class SavedPrinterLibrary:
    def __init__(self, library: LibraryService) -> None:
        self.library = library

    def list_saved_printers(self):
        return tuple(
            PrinterSummary(record.printer_id, record.display_name, record.model, record.nozzle)
            for record in self.library.list_printers()
        )


class SavingFakeGeneration:
    def __init__(self, library: LibraryService) -> None:
        self.library = library
        self.configurations = ExperimentConfigurationService(library)
        self.calibration_state_service = CalibrationStateService(
            library,
            DependencyEvaluator(IRONING_DEPENDENCY_GRAPH),
            slicer_available=lambda: True,
        )

    def prepare_ironing_configuration(self, printer_id: str, material_id: str):
        return self.configurations.prepare_ironing(printer_id, material_id)

    def save_configuration(self, configuration):
        return self.configurations.save(configuration)

    def generate_from_configuration(self, config_id: str, *, cancel_event=None):
        repository = self.library.repository
        configuration = repository.get_configuration(config_id)
        plan = configuration.plan
        code = repository.allocate_plate_code()
        run = CalibrationRunRecord(
            run_id="qt-workflow-run",
            plate_code=code,
            printer_id=configuration.printer_id,
            material_id=configuration.material_id,
            status="generating",
            created_at_utc=utc_now(),
            plan=plan,
            profiles=configuration.profile_selection,
            sample_map=tuple(
                {"label": f"Sample-{label}", "candidate_id": candidate.candidate_id, "settings": dict(candidate.overrides)}
                for label, candidate in zip("ABCDEFGHI", plan.candidates, strict=True)
            ),
            validation={"state": "pending", "print_ready": False},
        )
        repository.create_run(run, configuration_id=configuration.config_id)
        return repository.finalize_run(
            run.run_id, status="settings_validated", artifacts=(),
            validation={
                "state": "sample_settings_validated",
                "messages": [],
                "geometry": {"valid": True, "physical_labels_in_mesh": True, "physical_plate_code_in_mesh": True, "object_count": 10},
                "print_ready": False,
                "print_readiness_reasons": [
                    "Physical readability and handling are not yet accepted.",
                    "Every emitted G-code movement is not checked against keep-outs.",
                    "Start/end code and temperature commands have not been validated.",
                ],
            },
        )

    def generate_ironing(self, printer_id: str, material_id: str, *, cancel_event=None):
        configuration = self.configurations.prepare_ironing(printer_id, material_id).configuration
        self.configurations.save(configuration)
        return self.generate_from_configuration(configuration.config_id, cancel_event=cancel_event)


@unittest.skipUnless(importlib.util.find_spec("PySide6"), "PySide6 GUI extra is not installed")
class QtPrinterWorkflowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        os.environ["QT_QPA_PLATFORM"] = "offscreen"
        from PySide6.QtWidgets import QApplication
        from calibrate3dp.app.qt.main import create_application

        cls.application = create_application([])
        cls.application.setQuitOnLastWindowClosed(False)
        cls.QApplication = QApplication

    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name)
        profiles_root = root / "orca" / "system"
        _write_json(profiles_root / "machine" / "machine.json", {
            "name": "Test machine", "type": "machine", "printer_model": "Test 3D Printer", "nozzle_diameter": ["0.4"],
            "printable_area": ["0x0", "220x0", "220x220", "0x220"],
        })
        _write_json(profiles_root / "process" / "process.json", {
            "name": "Test process", "type": "process", "ironing_type": "top", "ironing_flow": "5%", "ironing_speed": "5",
        })
        _write_json(profiles_root / "filament" / "pla.json", {
            "name": "Test PLA", "type": "filament", "filament_type": ["PLA"],
        })
        self.profile_service = ProfileService(executable=root / "orca.exe", config_roots=(profiles_root,))
        self.profile_service.discover_profiles()
        self.repository = LibraryRepository(SessionRepository(root / "workspace"))
        self.library = LibraryService(self.repository, self.profile_service)

    def test_add_printer_material_generate_and_look_up_from_qt(self) -> None:
        from PySide6.QtCore import QEventLoop, QTimer
        from calibrate3dp.app.qt.main_window import MainWindow
        from calibrate3dp.app.qt.workflow_widgets import AddMaterialDialog, AddPrinterDialog
        from calibrate3dp.app.qt.navigation import AppPage

        printer_dialog = AddPrinterDialog(self.library)
        printer_dialog.display_name.setText("Qt workshop")
        printer_dialog.model.setText("Test 3D Printer")
        printer_dialog.nozzle.setText("0.4 mm")
        printer_dialog._save()
        self.assertIsNotNone(printer_dialog.record)
        printer = printer_dialog.record

        material_dialog = AddMaterialDialog(self.library, printer)
        material_dialog.display_name.setText("Qt PLA")
        material_dialog._save()
        self.assertIsNotNone(material_dialog.record)

        generation = SavingFakeGeneration(self.library)
        window = MainWindow(
            SavedPrinterLibrary(self.library),
            library_service=self.library,
            generation_service=generation,
        )
        self.addCleanup(window.close)
        self.assertIs(
            window.workspace_page.calibration_state_service,
            generation.calibration_state_service,
        )
        library_page = window.library_page
        library_page.rows[printer.printer_id].click()
        library_page.open_button.click()
        self.assertIs(window.pages.currentWidget(), window.workspace_page)
        self.assertIn("Qt workshop", window.workspace_page.heading.text())
        self.assertEqual(window.workspace_page.material_choice.count(), 1)
        self.assertTrue(window.workspace_page.generate_button.isEnabled())

        completed = QEventLoop()
        window.workspace_page.run_finished.connect(completed.quit)
        QTimer.singleShot(5000, completed.quit)
        def accept_review():
            dialog = window.workspace_page.review_dialog
            self.assertIsNotNone(dialog)
            dialog._save_configuration()
            dialog._generate_saved_configuration()
        QTimer.singleShot(0, accept_review)
        window.workspace_page.generate_button.click()
        completed.exec()
        self.assertIn("Print-ready: no", window.workspace_page.state.text())
        self.assertIn("Geometry: validated", window.workspace_page.state.text())
        self.assertIn("A–I labels and plate code are in the mesh", window.workspace_page.state.text())
        self.assertIn("Physical readability and handling are not yet accepted.", window.workspace_page.state.text())
        self.assertIn("Every emitted G-code movement is not checked against keep-outs.", window.workspace_page.state.text())
        self.assertIn("Start/end code and temperature commands have not been validated.", window.workspace_page.state.text())
        self.assertEqual(window.history_page.table.rowCount(), 1)

        run = self.repository.list_runs()[0]
        window.navigate_to(AppPage.RUNS_HISTORY)
        window.history_page.code_entry.setText(run.plate_code.lower())
        from calibrate3dp.app.qt.experiment_detail import ExperimentDetailsDialog
        opened_details = []
        with patch.object(ExperimentDetailsDialog, "exec", lambda dialog: opened_details.append(dialog) or dialog.DialogCode.Rejected):
            window.history_page._lookup()
        self.assertIn(f"Plate {run.plate_code}", window.history_page.lookup_result.toPlainText())
        self.assertIn("Sample-A", window.history_page.lookup_result.toPlainText())
        self.assertIn("Remaining print-readiness checks", window.history_page.lookup_result.toPlainText())
        self.assertIn("Start/end code and temperature commands have not been validated.", window.history_page.lookup_result.toPlainText())
        self.assertEqual(len(opened_details), 1)
        self.assertEqual(opened_details[0].run.run_id, run.run_id)

    def test_configuration_dialog_previews_and_saves_the_reviewed_candidate_map(self) -> None:
        from calibrate3dp.app.qt.experiment_review import ExperimentConfigurationDialog
        from calibrate3dp.app.qt.workflow_widgets import AddMaterialDialog, AddPrinterDialog

        printer_dialog = AddPrinterDialog(self.library)
        printer_dialog.display_name.setText("Review printer")
        printer_dialog.model.setText("Test 3D Printer")
        printer_dialog.nozzle.setText("0.4 mm")
        printer_dialog._save()
        printer = printer_dialog.record
        material_dialog = AddMaterialDialog(self.library, printer)
        material_dialog.display_name.setText("Review PLA")
        material_dialog._save()
        material = material_dialog.record
        generation = GroupedOrcaGenerationService(self.library, cli_provider=lambda: None)
        review = generation.prepare_ironing_configuration(printer.printer_id, material.material_id)
        dialog = ExperimentConfigurationDialog(review, generation)

        self.assertEqual(dialog.candidate_table.rowCount(), 9)
        self.assertEqual(dialog.layout_preview.plate_layout.sample_placements, review.layout.sample_placements)
        self.assertIn("allocated at generation", dialog.code_status.text().lower())
        dialog.flow_values.setText("7%, 10%, 13%")
        dialog._refresh_preview()
        self.assertEqual(dialog.candidate_table.item(0, 1).text(), "7%")
        dialog._save_configuration()

        self.assertIsNotNone(dialog.configuration)
        self.assertEqual(
            self.repository.get_configuration(dialog.configuration.config_id),
            dialog.configuration,
        )
        self.assertEqual(self.repository.list_runs(), ())

        dialog.flow_values.setText("8%, 11%, 14%")
        self.assertTrue(dialog.save_button.isEnabled())
        self.assertFalse(dialog.generate_button.isEnabled())
        dialog._save_configuration()
        saved_flow = next(
            item.values for item in dialog.configuration.plan.dimensions
            if item.key == "ironing_flow"
        )
        self.assertEqual(saved_flow, ("8%", "11%", "14%"))
        self.assertEqual(dialog.configuration.revision_no, 2)

    def test_saved_configuration_draft_can_be_reopened_after_repository_restart(self) -> None:
        from calibrate3dp.app.qt.experiment_review import ExperimentConfigurationDialog
        from calibrate3dp.app.qt.main_window import MainWindow
        from calibrate3dp.app.qt.workflow_widgets import AddMaterialDialog, AddPrinterDialog

        printer_dialog = AddPrinterDialog(self.library)
        printer_dialog.display_name.setText("Resume printer")
        printer_dialog.model.setText("Test 3D Printer")
        printer_dialog.nozzle.setText("0.4 mm")
        printer_dialog._save()
        printer = printer_dialog.record
        material_dialog = AddMaterialDialog(self.library, printer)
        material_dialog.display_name.setText("Resume PLA")
        material_dialog._save()
        material = material_dialog.record
        saved = ExperimentConfigurationService(self.library).prepare_ironing(
            printer.printer_id, material.material_id
        ).configuration
        saved = ExperimentConfigurationService(self.library).save(saved)

        # Simulate a fresh application process with new repository/service objects.
        repository = LibraryRepository(SessionRepository(Path(self.temp.name) / "workspace"))
        library = LibraryService(repository, self.profile_service)
        window = MainWindow(
            SavedPrinterLibrary(library),
            library_service=library,
            generation_service=SavingFakeGeneration(library),
        )
        self.addCleanup(window.close)
        window.library_page.rows[printer.printer_id].click()
        window.library_page.open_button.click()
        workspace = window.workspace_page
        self.assertEqual(workspace.saved_configuration_choice.count(), 1)

        started: list[str] = []
        workspace._start_run_generation = started.append
        opened: list[object] = []

        def accept_saved_draft(dialog):
            opened.append(dialog._saved_configuration)
            dialog._generate_saved_configuration()
            return dialog.DialogCode.Accepted

        with patch.object(ExperimentConfigurationDialog, "exec", accept_saved_draft):
            workspace.open_configuration_button.click()

        self.assertEqual(opened, [saved])
        self.assertEqual(started, [saved.config_id])


if __name__ == "__main__":
    unittest.main()
