"""Focused diagnostics and discovery tests for Orca CLI setup."""

from __future__ import annotations

import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from calibrate3dp.app.services import profile_service
from calibrate3dp.app.services.profile_service import ProfileService
from calibrate3dp.orca_cli import OrcaCliError, parse_orca_help


class OrcaCliDiscoveryTests(unittest.TestCase):
    def test_empty_help_explains_that_a_console_executable_is_required(self):
        with self.assertRaisesRegex(OrcaCliError, "CLI/console executable"):
            parse_orca_help("\n  \t")

    def test_discovery_prefers_the_console_command_on_path(self):
        with tempfile.TemporaryDirectory() as directory:
            console = Path(directory) / "orca-slicer-console"
            console.write_text("", encoding="utf-8")

            def find_command(command: str) -> str | None:
                return str(console) if command == "orca-slicer-console" else None

            with (
                patch.dict(os.environ, {"ORCA_SLICER_EXE": ""}),
                patch.object(profile_service.shutil, "which", side_effect=find_command),
            ):
                service = ProfileService()

        self.assertEqual(service.setup_state.executable, console)


if __name__ == "__main__":
    unittest.main()
