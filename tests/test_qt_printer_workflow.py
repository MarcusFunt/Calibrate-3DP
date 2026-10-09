from __future__ import annotations

import importlib.util
import json
import os
from pathlib import Path
import tempfile
import unittest

from calibrate3dp.app.services.experiment_service import ExperimentService
from calibrate3dp.app.services.library_service import LibraryService
from calibrate3dp.app.services.profile_service import ProfileService
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

    def generate_ironing(self, printer_id: str, material_id: str, *, cancel_event=None):
        repository = self.library.repository
        selection = self.library.resolve_selection(printer_id, material_id)
        plan = ExperimentService().create_initial("ironing", selection)
        code = repository.allocate_plate_code()
        run = CalibrationRunRecord(
            run_id="qt-workflow-run",
            plate_code=code,
            printer_id=printer_id,
            material_id=material_id,
            status="generating",
            created_at_utc=utc_now(),
            plan=plan,
            profiles=selection,
            sample_map=tuple(
                {"label": f"Sample-{label}", "candidate_id": candidate.candidate_id, "settings": dict(candidate.overrides)}
                for label, candidate in zip("ABCDEFGHI", plan.candidates, strict=False)
            ),
            validation={"state": "pending", "print_ready": False},
        )
        repository.create_run(run)
        return repository.finalize_run(
            run.run_id, status="settings_validated", artifacts=(),
            validation={"state": "sample_settings_validated", "messages": [], "print_ready": False},
        )


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

        window = MainWindow(
            SavedPrinterLibrary(self.library),
            library_service=self.library,
            generation_service=SavingFakeGeneration(self.library),
        )
        self.addCleanup(window.close)
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
        window.workspace_page.generate_button.click()
        completed.exec()
        self.assertIn("not passed print-readiness checks", window.workspace_page.state.text())
        self.assertEqual(window.history_page.table.rowCount(), 1)

        run = self.repository.list_runs()[0]
        window.navigate_to(AppPage.RUNS_HISTORY)
        window.history_page.code_entry.setText(run.plate_code.lower())
        window.history_page._lookup()
        self.assertIn(f"Plate {run.plate_code}", window.history_page.lookup_result.toPlainText())
        self.assertIn("Sample-A", window.history_page.lookup_result.toPlainText())


if __name__ == "__main__":
    unittest.main()
