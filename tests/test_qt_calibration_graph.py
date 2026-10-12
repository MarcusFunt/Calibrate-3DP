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


class OverviewRepository(EmptyRepository):
    def __init__(self, runs=(), assessments=None, links=None) -> None:
        self.runs = {run.run_id: run for run in runs}
        self.assessments = dict(assessments or {})
        self.links = dict(links or {})

    def latest_assessment_revision(self, run_id: str):
        return self.assessments.get(run_id)

    def list_run_decisions(self, _run_id: str):
        return ()

    def get_run_config_link(self, run_id: str):
        return self.links.get(run_id)

    def get_run(self, run_id: str):
        return self.runs[run_id]


class OverviewLibrary(EmptyLibrary):
    def __init__(self, materials=(), runs=(), repository=None) -> None:
        super().__init__(materials)
        self.runs = tuple(runs)
        self.repository = repository or OverviewRepository(runs)

    def list_runs(self, *, printer_id: str):
        return tuple(run for run in self.runs if run.printer_id == printer_id)


def overview_material(material_id: str = "material-1", name: str = "PLA"):
    return SimpleNamespace(
        material_id=material_id,
        display_name=name,
        filament_profile=SimpleNamespace(
            profile=SimpleNamespace(profile=SimpleNamespace(name=name))
        ),
    )


def overview_run(
    run_id: str,
    plate_code: str,
    *,
    material_id: str = "material-1",
    status: str = "settings_validated",
    created_at_utc: str = "2026-10-10T12:00:00Z",
    printer_id: str = "printer-1",
):
    return SimpleNamespace(
        run_id=run_id,
        plate_code=plate_code,
        printer_id=printer_id,
        material_id=material_id,
        status=status,
        created_at_utc=created_at_utc,
    )


def overview_workspace(
    status: CalibrationStatus,
    can_start: bool,
    *,
    runs=(),
    assessments=None,
    links=None,
    materials=None,
):
    from calibrate3dp.app.qt.workflow_widgets import PrinterWorkspacePage

    states = SimpleNamespace(
        states_for=lambda *_args: (CalibrationState("ironing", status, can_start),)
    )
    library = OverviewLibrary(
        materials or (overview_material(),),
        runs,
        OverviewRepository(runs, assessments, links),
    )
    workspace = PrinterWorkspacePage(
        library,
        SimpleNamespace(calibration_state_service=states),
        calibration_state_service=states,
    )
    workspace.show_printer(SimpleNamespace(
        printer_id="printer-1", name="Workshop", model="Model", nozzle="0.4 mm"
    ))
    return workspace


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
            (CalibrationStatus.READY, True),
            (CalibrationStatus.IN_PROGRESS, False),
            (CalibrationStatus.NEEDS_REVIEW, True),
            (CalibrationStatus.ACCEPTED, True),
            (CalibrationStatus.STALE, False),
        )
        status_labels = {
            CalibrationStatus.UNTESTED: "Untested",
            CalibrationStatus.BLOCKED: "Blocked",
            CalibrationStatus.READY: "Can configure",
            CalibrationStatus.IN_PROGRESS: "In progress",
            CalibrationStatus.NEEDS_REVIEW: "Needs review",
            CalibrationStatus.ACCEPTED: "Accepted",
            CalibrationStatus.STALE: "Stale",
        }
        start_summaries = {
            CalibrationStatus.UNTESTED: "Prerequisites are met.",
            CalibrationStatus.BLOCKED: "Resolve the items below to configure this calibration.",
            CalibrationStatus.READY: "Prerequisites are met.",
            CalibrationStatus.IN_PROGRESS: "A calibration is already being generated.",
            CalibrationStatus.NEEDS_REVIEW: "Prerequisites are met.",
            CalibrationStatus.ACCEPTED: "Prerequisites are met.",
            CalibrationStatus.STALE: "Review the saved context before continuing.",
        }
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
                self.assertEqual(panel.status_label.text(), status_labels[status])
                self.assertEqual(panel.status_label.property("state"), status.value)
                self.assertEqual(panel.status_icon.state, status.value)
                self.assertEqual(panel.can_start_label.text(), start_summaries[status])
                self.assertEqual(
                    panel.dependency_nodes["printer_material"].state_label.text(),
                    "Not evaluated",
                )
                self.assertIn(f"Reason for {status.value}.", panel.reasons_label.text())
                self.assertIn("Review the physical sample by hand.", panel.reasons_label.text())

        self.assertIn("not yet available", panel.availability_label.text().lower())
        self.assertIn("not print-ready", panel.print_readiness_label.text().lower())
        self.assertNotIn("ready to print", panel.print_readiness_label.text().lower())

    def test_dependency_flow_chart_connects_evaluated_prerequisite_groups_to_calibration(self) -> None:
        from calibrate3dp.app.qt.calibration_status import CalibrationStatusPanel
        from calibrate3dp.calibration.dependencies import DependencyContext, DependencyEvaluator
        from calibrate3dp.calibration.ironing import IRONING_DEPENDENCY_GRAPH

        state = DependencyEvaluator(IRONING_DEPENDENCY_GRAPH).evaluate(
            DependencyContext({
                "printer_material_state": "resolved",
                "nozzle_context_state": "mismatch",
                "profile_context_state": "matches",
                "profile_hash_state.printer": "unchanged",
                "profile_hash_state.process": "unchanged",
                "profile_hash_state.filament": "changed",
                "slicer_state": "available",
            }),
            {},
        )[0]
        panel = CalibrationStatusPanel()
        panel.set_states((state,))

        self.assertEqual(panel.dependency_flow_chart.objectName(), "calibrationDependencyFlow")
        self.assertEqual(panel.flow_connector.input_count, 4)
        self.assertEqual(panel.dependency_nodes["printer_material"].state_label.text(), "Met")
        self.assertEqual(panel.dependency_nodes["nozzle"].state_label.text(), "Resolve")
        self.assertEqual(panel.dependency_nodes["profiles"].state_label.text(), "Resolve")
        self.assertEqual(panel.dependency_nodes["orca"].state_label.text(), "Met")
        self.assertEqual(panel.target_status_label.text(), "Blocked")
        self.assertEqual(panel.dependency_nodes["nozzle"].property("state"), "blocked")
        self.assertIn("nozzle", panel.reasons_label.text().lower())

        from calibrate3dp.app.qt.theme import application_stylesheet
        self.application.setStyleSheet(application_stylesheet())
        panel.resize(360, 680)
        panel.show()
        self.application.processEvents()
        self.assertLessEqual(panel.width(), 360)
        self.assertFalse(panel.grab().isNull())
        panel.close()

    def test_panel_translates_profile_prerequisites_and_keeps_technical_details_available(self) -> None:
        from calibrate3dp.app.qt.calibration_status import CalibrationStatusPanel

        raw_reason = (
            "filament-profile-hash-current: input/result 'profile_hash_state.filament' "
            "has value 'changed or unavailable'; expected equal to 'unchanged'"
        )
        panel = CalibrationStatusPanel()
        panel.set_states((CalibrationState(
            "ironing",
            CalibrationStatus.BLOCKED,
            False,
            reasons=(raw_reason,),
        ),))

        self.assertIn("filament profile", panel.reasons_label.text().lower())
        self.assertNotIn("profile_hash_state.filament", panel.reasons_label.text())
        self.assertTrue(panel.technical_details.isHidden())

        panel.details_toggle.click()

        self.assertFalse(panel.technical_details.isHidden())
        self.assertIn(raw_reason, panel.technical_details.text())

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

        self.assertTrue(workspace.generate_button.isEnabled())
        self.assertEqual(workspace.generate_button.text(), "Add material")
        self.assertIn("Select a saved material.", workspace.calibration_status_panel.reasons_label.text())
        self.assertEqual(states.calls[-1], ("printer-1", None))
        self.assertEqual(workspace.material_choice.currentText(), "No saved materials")
        self.assertFalse(workspace.material_choice.isEnabled())
        self.assertEqual(workspace.saved_configuration_choice.currentText(), "Select a material first")
        self.assertFalse(workspace.open_configuration_button.isEnabled())

        requests: list[str] = []
        workspace.add_material_requested.connect(requests.append)
        workspace.generate_button.click()
        self.assertEqual(requests, ["printer-1"])

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

        self.assertTrue(workspace.generate_button.isEnabled())
        self.assertEqual(workspace.generate_button.text(), "Open Settings")
        self.assertIn("OrcaSlicer is not available", workspace.calibration_status_panel.reasons_label.text())
        self.assertEqual(states.calls[-1], ("printer-1", "material-1"))
        settings_requests: list[bool] = []
        workspace.settings_requested.connect(lambda: settings_requests.append(True))
        workspace.generate_button.click()
        self.assertEqual(settings_requests, [True])

        previous_calls = len(states.calls)
        with patch.object(QThreadPool.globalInstance(), "start"):
            workspace._start_run_generation("configuration-1")
        self.assertGreater(len(states.calls), previous_calls)
        self.assertFalse(workspace.generate_button.isEnabled())

        previous_calls = len(states.calls)
        workspace._generation_failed("Test failure")
        self.assertGreater(len(states.calls), previous_calls)

    def test_overview_recommends_configuration_when_there_is_no_run_for_selected_material(self) -> None:
        workspace = overview_workspace(
            CalibrationStatus.UNTESTED,
            True,
            runs=(overview_run("old", "OLD234", material_id="material-2"),),
        )

        self.assertIn("Configure your first ironing plate", workspace.next_step_title.text())
        self.assertEqual(workspace.generate_button.text(), "Configure plate")
        self.assertTrue(workspace.generate_button.isEnabled())
        self.assertEqual(workspace.material_choice.currentText(), "PLA")
        self.assertNotIn("OLD234", workspace.next_step_description.text())

    def test_overview_routes_unassessed_validated_plate_to_assessment(self) -> None:
        from unittest.mock import patch

        workspace = overview_workspace(
            CalibrationStatus.NEEDS_REVIEW,
            True,
            runs=(overview_run("run-1", "ABC123"),),
        )

        self.assertEqual(workspace.next_step_title.text(), "Record results for plate ABC123")
        self.assertEqual(workspace.generate_button.text(), "Assess ABC123")
        self.assertTrue(workspace.generate_button.isEnabled())
        with patch("calibrate3dp.app.qt.workflow_widgets.ExperimentDetailsDialog") as dialog_type:
            workspace.generate_button.click()

        dialog_type.assert_called_once()
        self.assertEqual(dialog_type.call_args.kwargs["initial_tab"], "Assess")

    def test_overview_routes_failed_run_to_inspection_and_saved_assessment_to_decision(self) -> None:
        from unittest.mock import patch

        failed = overview_workspace(
            CalibrationStatus.READY,
            True,
            runs=(overview_run("failed", "BAD234", status="validation_failed"),),
        )
        self.assertEqual(failed.generate_button.text(), "Inspect BAD234")
        with patch("calibrate3dp.app.qt.workflow_widgets.ExperimentDetailsDialog") as inspect_dialog:
            failed.generate_button.click()
        self.assertEqual(inspect_dialog.call_args.kwargs["initial_tab"], "Inspect")

        assessed = overview_workspace(
            CalibrationStatus.READY,
            True,
            runs=(overview_run("assessed", "OK2345"),),
            assessments={"assessed": SimpleNamespace(assessment_revision_id="revision-1")},
        )
        self.assertEqual(assessed.generate_button.text(), "Review OK2345")
        with patch("calibrate3dp.app.qt.workflow_widgets.ExperimentDetailsDialog") as decision_dialog:
            assessed.generate_button.click()
        self.assertEqual(decision_dialog.call_args.kwargs["initial_tab"], "Decision")

    def test_overview_shows_four_recent_plates_and_clickable_parent_to_child_lineage(self) -> None:
        from PySide6.QtWidgets import QPushButton
        from unittest.mock import patch

        parent = overview_run("parent", "BASE23", created_at_utc="2026-10-08T12:00:00Z")
        child = overview_run("child", "CHLD45", created_at_utc="2026-10-10T12:00:00Z")
        other = overview_run(
            "other", "OTH678", material_id="material-2", created_at_utc="2026-10-09T12:00:00Z"
        )
        older = overview_run(
            "older", "OLD678", material_id="material-2", created_at_utc="2026-10-07T12:00:00Z"
        )
        oldest = overview_run(
            "oldest", "OLD123", material_id="material-2", created_at_utc="2026-10-06T12:00:00Z"
        )
        runs = (child, other, parent, older, oldest)
        links = {
            "parent": {"parent_run_id": None, "relation_type": "initial"},
            "child": {"parent_run_id": "parent", "relation_type": "refinement"},
        }
        workspace = overview_workspace(
            CalibrationStatus.NEEDS_REVIEW,
            True,
            runs=runs,
            links=links,
        )

        self.assertEqual(workspace.recent_plates_table.rowCount(), 4)
        self.assertEqual(workspace.recent_plates_table.item(0, 0).text(), "CHLD45")
        self.assertEqual(workspace.recent_plates_table.item(3, 0).text(), "OLD678")
        steps = workspace.lineage_card.findChildren(QPushButton, "plateLineageStep")
        self.assertEqual([button.text().splitlines()[0] for button in steps], ["BASE23", "CHLD45"])
        self.assertIn("Refinement", steps[-1].text())
        with patch("calibrate3dp.app.qt.workflow_widgets.ExperimentDetailsDialog") as dialog_type:
            steps[0].click()
        self.assertEqual(dialog_type.call_args.args[1], "parent")
        self.assertEqual(dialog_type.call_args.kwargs["initial_tab"], "Inspect")
        with patch("calibrate3dp.app.qt.workflow_widgets.ExperimentDetailsDialog") as recent_dialog:
            workspace.recent_plates_table.cellActivated.emit(0, 0)
        self.assertEqual(recent_dialog.call_args.args[1], "child")
        self.assertEqual(recent_dialog.call_args.kwargs["initial_tab"], "Inspect")

    def test_overview_routes_a_saved_in_progress_run_to_inspection(self) -> None:
        from unittest.mock import patch

        workspace = overview_workspace(
            CalibrationStatus.IN_PROGRESS,
            False,
            runs=(overview_run("running", "RUN123", status="generating"),),
        )

        self.assertEqual(workspace.generate_button.text(), "Inspect RUN123")
        with patch("calibrate3dp.app.qt.workflow_widgets.ExperimentDetailsDialog") as dialog_type:
            workspace.generate_button.click()
        self.assertEqual(dialog_type.call_args.kwargs["initial_tab"], "Inspect")

    def test_overview_explains_incomplete_or_cyclic_lineage_without_losing_loaded_plates(self) -> None:
        from PySide6.QtWidgets import QPushButton

        parent = overview_run("parent", "BASE23")
        child = overview_run("child", "CHLD45", created_at_utc="2026-10-11T12:00:00Z")
        missing_parent = overview_workspace(
            CalibrationStatus.NEEDS_REVIEW,
            True,
            runs=(child,),
            links={"child": {"parent_run_id": "missing", "relation_type": "refinement"}},
        )
        self.assertIn("parent plate could not be loaded", missing_parent.lineage_note.text())
        self.assertEqual(
            [button.text().splitlines()[0] for button in missing_parent.lineage_card.findChildren(QPushButton, "plateLineageStep")],
            ["CHLD45"],
        )

        cyclic = overview_workspace(
            CalibrationStatus.NEEDS_REVIEW,
            True,
            runs=(parent, child),
            links={
                "parent": {"parent_run_id": "child", "relation_type": "refinement"},
                "child": {"parent_run_id": "parent", "relation_type": "refinement"},
            },
        )
        self.assertIn("cycle", cyclic.lineage_note.text())
        self.assertEqual(
            [button.text().splitlines()[0] for button in cyclic.lineage_card.findChildren(QPushButton, "plateLineageStep")],
            ["BASE23", "CHLD45"],
        )

    def test_overview_routes_local_slicer_setup_to_settings(self) -> None:
        from calibrate3dp.app.qt.workflow_widgets import PrinterWorkspacePage

        states = SimpleNamespace(states_for=lambda *_args: (CalibrationState(
            "ironing",
            CalibrationStatus.BLOCKED,
            False,
            reasons=("OrcaSlicer is not available in local settings.",),
        ),))
        library = OverviewLibrary((overview_material(),))
        workspace = PrinterWorkspacePage(
            library,
            SimpleNamespace(calibration_state_service=states),
            calibration_state_service=states,
        )
        workspace.show_printer(SimpleNamespace(
            printer_id="printer-1", name="Workshop", model="Model", nozzle="0.4 mm"
        ))

        self.assertEqual(workspace.next_step_title.text(), "Verify OrcaSlicer to continue")
        self.assertEqual(workspace.generate_button.text(), "Open Settings")
        requests = []
        workspace.settings_requested.connect(lambda: requests.append(True))
        workspace.generate_button.click()
        self.assertEqual(requests, [True])


if __name__ == "__main__":
    unittest.main()
