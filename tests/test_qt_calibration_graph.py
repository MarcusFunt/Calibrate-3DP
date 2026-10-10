from __future__ import annotations

import os
from types import SimpleNamespace
import unittest

from calibrate3dp.calibration.state import CalibrationState, CalibrationStatus


class FakeCalibrationStateService:
    def __init__(self) -> None:
        self.calls: list[tuple[str | None, str | None]] = []

    def states_for(self, printer_id: str | None, material_id: str | None):
        self.calls.append((printer_id, material_id))
        if printer_id is None:
            return (CalibrationState(
                "ironing", CalibrationStatus.BLOCKED, False,
                reasons=("Select a saved printer.",),
            ),)
        if material_id is None:
            return (CalibrationState(
                "ironing", CalibrationStatus.BLOCKED, False,
                reasons=("Select a saved material.",),
            ),)
        return (CalibrationState(
            "ironing", CalibrationStatus.BLOCKED, False,
            reasons=("OrcaSlicer is not available in local settings.",),
        ),)


class EmptyRepository:
    def get_printer(self, _printer_id: str):
        raise LookupError("test printer details")

    def list_unlinked_initial_configurations(self, _printer_id: str, _material_id: str):
        return ()


class EmptyLibrary:
    repository = EmptyRepository()

    def __init__(self, materials=()) -> None:
        self.materials = tuple(materials)

    def list_materials(self):
        return self.materials

    def list_runs(self, *, printer_id: str):
        return ()


class QtCalibrationGraphTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        os.environ["QT_QPA_PLATFORM"] = "offscreen"
        from PySide6.QtWidgets import QApplication

        cls.application = QApplication.instance() or QApplication([])

    def test_panel_renders_every_lifecycle_state_reasons_and_readiness_boundary(self) -> None:
        from calibrate3dp.app.qt.calibration_status import CalibrationStatusPanel

        panel = CalibrationStatusPanel()
        states = (
            (CalibrationStatus.UNTESTED, True),
            (CalibrationStatus.BLOCKED, False),
            (CalibrationStatus.IN_PROGRESS, False),
            (CalibrationStatus.NEEDS_REVIEW, True),
            (CalibrationStatus.ACCEPTED, True),
            (CalibrationStatus.STALE, False),
        )
        for status, can_start in states:
            with self.subTest(status=status.value):
                panel.set_states((CalibrationState(
                    "ironing",
                    status,
                    can_start,
                    reasons=(f"Reason for {status.value}.",),
                    recommendations=("Review the physical sample by hand.",),
                ),))
                self.assertIn("Ironing", panel.workflow_label.text())
                self.assertEqual(panel.status_label.text(), status.value.replace("_", " ").title())
                self.assertEqual(
                    panel.can_start_label.text(),
                    f"Can start: {'yes' if can_start else 'no'}",
                )
                self.assertIn(f"Reason for {status.value}.", panel.reasons_label.text())
                self.assertIn("Review the physical sample by hand.", panel.reasons_label.text())

        self.assertIn("not yet available", panel.availability_label.text().lower())
        self.assertIn("not print-ready", panel.print_readiness_label.text().lower())
        self.assertNotIn("ready to print", panel.print_readiness_label.text().lower())

    def test_workspace_reports_missing_material_and_blocks_generation_when_context_is_blocked(self) -> None:
        from calibrate3dp.app.qt.workflow_widgets import PrinterWorkspacePage

        states = FakeCalibrationStateService()
        workspace = PrinterWorkspacePage(
            EmptyLibrary(),
            SimpleNamespace(calibration_state_service=states),
            calibration_state_service=states,
        )
        workspace.show_printer(SimpleNamespace(
            printer_id="printer-1", name="Workshop", model="Model", nozzle="0.4 mm"
        ))

        self.assertFalse(workspace.generate_button.isEnabled())
        self.assertIn("Select a saved material.", workspace.calibration_status_panel.reasons_label.text())
        self.assertEqual(states.calls[-1], ("printer-1", None))

    def test_workspace_refreshes_state_for_selected_material_and_generation_lifecycle(self) -> None:
        from PySide6.QtCore import QThreadPool
        from unittest.mock import patch
        from calibrate3dp.app.qt.workflow_widgets import PrinterWorkspacePage

        states = FakeCalibrationStateService()
        library = EmptyLibrary((SimpleNamespace(
            material_id="material-1",
            display_name="PLA",
            filament_profile=SimpleNamespace(profile=SimpleNamespace(profile=SimpleNamespace(name="PLA"))),
        ),))
        workspace = PrinterWorkspacePage(
            library,
            SimpleNamespace(calibration_state_service=states),
            calibration_state_service=states,
        )
        workspace.show_printer(SimpleNamespace(
            printer_id="printer-1", name="Workshop", model="Model", nozzle="0.4 mm"
        ))

        self.assertFalse(workspace.generate_button.isEnabled())
        self.assertIn("OrcaSlicer is not available", workspace.calibration_status_panel.reasons_label.text())
        self.assertEqual(states.calls[-1], ("printer-1", "material-1"))

        previous_calls = len(states.calls)
        with patch.object(QThreadPool.globalInstance(), "start"):
            workspace._start_run_generation("configuration-1")
        self.assertGreater(len(states.calls), previous_calls)
        self.assertFalse(workspace.generate_button.isEnabled())

        previous_calls = len(states.calls)
        workspace._generation_failed("Test failure")
        self.assertGreater(len(states.calls), previous_calls)


if __name__ == "__main__":
    unittest.main()
