"""Tests for headless Qt view models and the optional desktop shell."""

from __future__ import annotations

import importlib.util
import os
import unittest

from calibrate3dp.app.qt.view_models import (
    PrinterLibraryUnavailableError,
    PrinterLibraryViewModel,
    PrinterSummary,
)


class FakePrinterLibrary:
    def __init__(self, printers: tuple[PrinterSummary, ...]) -> None:
        self.printers = printers

    def list_saved_printers(self) -> tuple[PrinterSummary, ...]:
        return self.printers


class PlateLookupProbeRepository:
    def __init__(self) -> None:
        self.current_page = None
        self.page_during_lookup = None

    def get_run_by_plate_code(self, _code: str):
        self.page_during_lookup = self.current_page()
        raise LookupError("No saved plate matches that code.")


class PlateLookupProbeLibrary:
    def __init__(self) -> None:
        self.repository = PlateLookupProbeRepository()

    def list_runs(self):
        return ()

    def list_printers(self):
        return ()

    def list_materials(self):
        return ()


PRINTERS = (
    PrinterSummary("corexy", "Workshop CoreXY", "Voron 2.4", "0.4 mm"),
    PrinterSummary("mk4", "Studio Mk4", "Prusa MK4", "0.4 mm"),
    PrinterSummary("bench", "Bench Printer", "Bambu Lab A1", "0.4 mm"),
)


class PrinterLibraryViewModelTests(unittest.TestCase):
    def setUp(self) -> None:
        self.model = PrinterLibraryViewModel(FakePrinterLibrary(PRINTERS))

    def test_displays_fake_repository_printers_in_order(self) -> None:
        self.assertEqual(self.model.printers, PRINTERS)
        self.assertEqual(self.model.visible_printers(), PRINTERS)

    def test_search_matches_printer_name_or_model_and_keeps_selection(self) -> None:
        self.model.select("corexy")

        self.assertEqual(self.model.visible_printers("prusa"), (PRINTERS[1],))
        self.assertEqual(self.model.selected_printer, PRINTERS[0])
        self.assertEqual(self.model.visible_printers("no match"), ())
        self.assertEqual(self.model.selected_id, "corexy")

    def test_rejects_unknown_selection(self) -> None:
        with self.assertRaisesRegex(ValueError, "unknown printer id"):
            self.model.select("missing")

    def test_retains_a_readable_library_error_for_the_screen(self) -> None:
        class UnavailableLibrary:
            def list_saved_printers(self) -> tuple[PrinterSummary, ...]:
                raise PrinterLibraryUnavailableError("workspace database is unavailable")

        model = PrinterLibraryViewModel(UnavailableLibrary())

        self.assertEqual(model.printers, ())
        self.assertEqual(model.load_error, "workspace database is unavailable")


class QtOptionalDependencyTests(unittest.TestCase):
    def test_startup_module_imports_without_importing_py_side(self) -> None:
        import sys

        import calibrate3dp.app.__main__
        from calibrate3dp.app.qt import main as qt_main

        self.assertTrue(callable(qt_main.create_application))
        if importlib.util.find_spec("PySide6") is None:
            self.assertNotIn("PySide6", sys.modules)
            with self.assertRaises(qt_main.QtDependencyMissingError):
                qt_main.create_application([])


@unittest.skipUnless(importlib.util.find_spec("PySide6"), "PySide6 GUI extra is not installed")
class QtApplicationSmokeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        os.environ["QT_QPA_PLATFORM"] = "offscreen"
        from calibrate3dp.app.qt.main import create_application
        from calibrate3dp.app.qt.main_window import MainWindow
        from calibrate3dp.app.qt.navigation import AppPage
        from PySide6.QtWidgets import QBoxLayout

        cls.application = create_application([])
        cls.application.setQuitOnLastWindowClosed(False)
        cls.main_window_type = MainWindow
        cls.app_page = AppPage
        cls.QBoxLayout = QBoxLayout

    def test_application_starts_and_navigation_changes_page(self) -> None:
        window = self.main_window_type(FakePrinterLibrary(PRINTERS))
        self.assertEqual(window.windowTitle(), "Calibrate-3DP")
        page_enum = self.app_page
        self.assertIs(window.pages.currentWidget(), window.page_widgets[page_enum.PRINTER_LIBRARY])

        window.navigation.buttons[page_enum.RUNS_HISTORY].click()

        self.assertIs(window.pages.currentWidget(), window.page_widgets[page_enum.RUNS_HISTORY])
        self.assertTrue(window.navigation.buttons[page_enum.RUNS_HISTORY].isChecked())
        window.close()

    def test_printer_entries_render_and_search_filters_rows(self) -> None:
        window = self.main_window_type(FakePrinterLibrary(PRINTERS))
        page = window.page_widgets[self.app_page.PRINTER_LIBRARY]

        self.assertEqual(set(page.rows), {printer.printer_id for printer in PRINTERS})
        page.search.setText("Prusa")
        self.assertEqual(set(page.rows), {"mk4"})
        window.close()

    def test_open_printer_carries_the_selected_context(self) -> None:
        window = self.main_window_type(FakePrinterLibrary(PRINTERS))
        page = window.page_widgets[self.app_page.PRINTER_LIBRARY]
        page.rows["corexy"].click()
        page.open_button.click()

        self.assertIs(window.pages.currentWidget(), window.workspace_page)
        self.assertIn("Workshop CoreXY", window.workspace_page.printer_context.text())
        window.close()

    def test_global_plate_lookup_and_horizontal_navigation_share_the_current_context(self) -> None:
        window = self.main_window_type(FakePrinterLibrary(PRINTERS))
        try:
            self.assertIs(window.plate_code_entry, window.history_page.code_entry)
            self.assertIs(window.plate_lookup_button, window.history_page.lookup_button)
            self.assertIs(window.history_page.lookup_controls.parentWidget(), window.header)
            self.assertEqual(
                window.navigation.layout().direction(),
                self.QBoxLayout.Direction.LeftToRight,
            )

            window._open_printer_workspace("corexy")
            self.assertEqual(window.printer_context_value.text(), "Workshop CoreXY")

            window.plate_code_entry.setText("W4NT6B")
            window.plate_lookup_button.click()
            self.assertIs(window.pages.currentWidget(), window.history_page)
            self.assertIn("unavailable", window.history_page.lookup_result.toPlainText().lower())
        finally:
            window.close()

    def test_global_plate_lookup_shows_history_before_resolving_the_code(self) -> None:
        library = PlateLookupProbeLibrary()
        window = self.main_window_type(FakePrinterLibrary(PRINTERS), library_service=library)
        library.repository.current_page = lambda: window.pages.currentWidget()
        try:
            window.navigate_to(self.app_page.SETTINGS)
            window.plate_code_entry.setText("BADBAD")
            window.plate_lookup_button.click()

            self.assertIs(library.repository.page_during_lookup, window.history_page)
            self.assertIs(window.pages.currentWidget(), window.history_page)
            self.assertIn("No saved plate matches", window.history_page.lookup_result.toPlainText())
        finally:
            window.close()

    def test_printer_workspace_groups_existing_controls_in_contextual_tabs(self) -> None:
        window = self.main_window_type(FakePrinterLibrary(PRINTERS))
        try:
            workspace = window.workspace_page
            self.assertEqual(
                [workspace.tabs.tabText(index) for index in range(workspace.tabs.count())],
                ["Overview", "Experiments", "Printer and materials"],
            )
            self.assertIs(workspace.calibration_status_panel.parentWidget(), workspace.overview_tab)
            self.assertIs(workspace.run_table.parentWidget(), workspace.experiments_tab)
            self.assertIs(workspace.material_choice.parentWidget(), workspace.printer_materials_tab)
        finally:
            window.close()


if __name__ == "__main__":
    unittest.main()
