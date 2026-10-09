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

        cls.application = create_application([])
        cls.application.setQuitOnLastWindowClosed(False)
        cls.main_window_type = MainWindow
        cls.app_page = AppPage

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


if __name__ == "__main__":
    unittest.main()
