"""Viewport-free tests for desktop navigation and optional imports."""

from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys
import unittest

from calibrate3dp.app.window import AppShell, InvalidPageError


class AppShellTests(unittest.TestCase):
    def test_app_shell_starts_on_home(self):
        shell = AppShell()

        self.assertEqual(shell.current_page, "home")

    def test_navigation_switches_pages(self):
        shell = AppShell()

        shell.navigate("sessions")

        self.assertEqual(shell.current_page, "sessions")
        with self.assertRaises(InvalidPageError):
            shell.navigate("profile_editor")
        self.assertEqual(shell.current_page, "sessions")

    def test_core_import_does_not_load_dearpygui(self):
        repository_root = Path(__file__).resolve().parents[1]
        source_root = repository_root / "src"
        environment = os.environ.copy()
        environment["PYTHONPATH"] = str(source_root)
        script = """
import builtins
import sys

original_import = builtins.__import__
def without_gui(name, *args, **kwargs):
    if name == "dearpygui" or name.startswith("dearpygui."):
        raise ModuleNotFoundError("simulated installation without the GUI extra")
    return original_import(name, *args, **kwargs)

builtins.__import__ = without_gui
import calibrate3dp
import calibrate3dp.profiles
import calibrate3dp.app.__main__
assert not any(
    name == "dearpygui" or name.startswith("dearpygui.") for name in sys.modules
), "Dear PyGui was imported eagerly"
"""

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
