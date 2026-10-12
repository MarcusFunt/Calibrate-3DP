"""Qt-only application entry point and optional import contracts."""

from __future__ import annotations

from contextlib import redirect_stderr, redirect_stdout
import importlib.util
import io
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from calibrate3dp.app.__main__ import bundle_self_check, main


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

    @unittest.skipUnless(
        importlib.util.find_spec("PySide6")
        and importlib.util.find_spec("build123d")
        and importlib.util.find_spec("OCP"),
        "requires the GUI and CAD extras",
    )
    def test_bundle_self_check_exits_without_opening_the_application(self) -> None:
        repository_root = Path(__file__).resolve().parents[1]
        environment = os.environ.copy()
        environment["PYTHONPATH"] = str(repository_root / "src")
        environment["QT_QPA_PLATFORM"] = "offscreen"
        try:
            completed = subprocess.run(
                [
                    sys.executable,
                    "-c",
                    "from calibrate3dp.app.__main__ import main; "
                    "raise SystemExit(main(['--bundle-self-check']))",
                ],
                cwd=repository_root,
                env=environment,
                capture_output=True,
                text=True,
                check=False,
                timeout=15,
            )
        except subprocess.TimeoutExpired:
            self.fail("the bundle self-check entered the normal application event loop")

        self.assertEqual(completed.returncode, 0, completed.stderr)

    @unittest.skipUnless(
        importlib.util.find_spec("PySide6") and importlib.util.find_spec("build123d"),
        "requires the GUI and CAD extras",
    )
    def test_bundle_self_check_records_import_failure_for_ci(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            log_path = Path(directory) / "bundle-self-check.log"
            with patch.dict(os.environ, {"CALIBRATE3DP_BUNDLE_SELF_CHECK_LOG": str(log_path)}):
                original_import = __import__

                def fail_webengine_import(name, *args, **kwargs):
                    if name == "PySide6.QtWebEngineWidgets":
                        raise ImportError("synthetic WebEngine import failure")
                    return original_import(name, *args, **kwargs)

                with patch("builtins.__import__", side_effect=fail_webengine_import):
                    result = bundle_self_check()

            self.assertEqual(result, 10)
            self.assertIn("synthetic WebEngine import failure", log_path.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
