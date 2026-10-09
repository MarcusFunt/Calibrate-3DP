"""Qt-only application entry point and optional import contracts."""

from __future__ import annotations

from contextlib import redirect_stderr, redirect_stdout
import importlib.util
import io
import os
from pathlib import Path
import subprocess
import sys
import unittest

from calibrate3dp.app.__main__ import main


class ApplicationEntryPointTests(unittest.TestCase):
    def test_legacy_dpg_switch_is_rejected(self) -> None:
        error = io.StringIO()
        with redirect_stderr(error), self.assertRaises(SystemExit) as raised:
            main(["--legacy-dpg"])

        self.assertEqual(raised.exception.code, 2)
        self.assertIn("--legacy-dpg was removed", error.getvalue())

    def test_help_does_not_advertise_a_legacy_gui(self) -> None:
        output = io.StringIO()
        with redirect_stdout(output), self.assertRaises(SystemExit) as raised:
            main(["--help"])

        self.assertEqual(raised.exception.code, 0)
        self.assertNotIn("Dear PyGui", output.getvalue())
        self.assertNotIn("legacy-dpg", output.getvalue())

    @unittest.skipIf(importlib.util.find_spec("PySide6"), "requires a headless environment without PySide6")
    def test_default_launch_reports_missing_qt_extra_cleanly(self) -> None:
        error = io.StringIO()
        with redirect_stderr(error):
            result = main([])

        self.assertEqual(result, 2)
        self.assertIn("install it with", error.getvalue())

    def test_package_imports_without_loading_a_gui_runtime(self) -> None:
        repository_root = Path(__file__).resolve().parents[1]
        source_root = repository_root / "src"
        script = """
import builtins
import sys

original_import = builtins.__import__
def without_gui(name, *args, **kwargs):
    if name == "dearpygui" or name.startswith("dearpygui."):
        raise AssertionError("Dear PyGui was imported")
    if name == "PySide6" or name.startswith("PySide6."):
        raise AssertionError("PySide6 was imported eagerly")
    return original_import(name, *args, **kwargs)

builtins.__import__ = without_gui
import calibrate3dp
import calibrate3dp.app.__main__
import calibrate3dp.app.qt.main
assert not any(name == "dearpygui" or name.startswith("dearpygui.") for name in sys.modules)
assert not any(name == "PySide6" or name.startswith("PySide6.") for name in sys.modules)
"""
        environment = os.environ.copy()
        environment["PYTHONPATH"] = str(source_root)

        completed = subprocess.run(
            [sys.executable, "-c", script],
            cwd=repository_root,
            env=environment,
            capture_output=True,
            text=True,
            check=False,
        )

        self.assertEqual(completed.returncode, 0, completed.stderr)


if __name__ == "__main__":
    unittest.main()
