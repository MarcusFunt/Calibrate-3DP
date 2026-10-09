"""Headless contracts for Orca setup and profile selection UI services."""

from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest

from calibrate3dp.orca_cli import OrcaCliCapabilities
from calibrate3dp.app.services.profile_service import (
    ProfileImportError,
    MissingRequiredProfileSettingError,
    ProfileService,
)
from calibrate3dp.profiles import AmbiguousProfileError, MissingParentError


class ProfileSelectionServiceTests(unittest.TestCase):
    def test_missing_orca_shows_browse_and_diagnostics_actions(self):
        service = ProfileService(
            executable=None,
            config_roots=(),
            executable_detector=lambda: None,
        )

        state = service.setup_state

        self.assertIsNone(state.executable)
        self.assertIn("browse_executable", state.actions)
        self.assertIn("export_diagnostics", state.actions)
        self.assertIn("recheck", state.actions)
        self.assertIn("not verified", state.compatibility_status.casefold())

    def test_recheck_records_cli_banner_without_claiming_profile_compatibility(self):
        class FakeCli:
            def __init__(self, executable):
                self.executable = Path(executable)

            def probe(self, *, timeout_seconds):
                del timeout_seconds
                return OrcaCliCapabilities(
                    executable=self.executable,
                    version_banner="OrcaSlicer-test",
                    options=frozenset(),
                    help_returncode=0,
                )

        with tempfile.TemporaryDirectory() as directory:
            executable = Path(directory) / "orca"
            executable.write_text("test executable", encoding="utf-8")
            service = ProfileService(
                executable=executable,
                config_roots=(),
                cli_factory=FakeCli,
            )

            state = service.check_setup()

        self.assertEqual(state.version_banner, "OrcaSlicer-test")
        self.assertIsNotNone(state.last_checked_at_utc)
        self.assertIn("not verified", state.compatibility_status.casefold())

    def test_unsupported_bundle_has_actionable_import_error(self):
        service = ProfileService(config_roots=())
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "profiles.tar"
            path.write_bytes(b"archive")

            with self.assertRaises(ProfileImportError) as caught:
                service.import_source(path)

        self.assertIn("supported", caught.exception.message)
        self.assertIn(".json", caught.exception.action)

    def test_diagnostics_export_omits_profile_setting_values(self):
        service = ProfileService(config_roots=())
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self._import(
                service,
                root,
                "private.json",
                {"type": "process", "name": "Private", "private_setting": "do not export"},
            )
            destination = root / "diagnostics.json"

            written = service.export_diagnostics(destination)
            payload = written.read_text(encoding="utf-8")

        self.assertIn('"count_by_kind"', payload)
        self.assertNotIn("private_setting", payload)
        self.assertNotIn("do not export", payload)

    def test_discovery_reads_only_profile_directories(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "OrcaSlicer"
            process = root / "user" / "default" / "process" / "Fine.json"
            process.parent.mkdir(parents=True)
            process.write_text(
                json.dumps({"type": "process", "name": "Fine", "ironing_flow": "10%"}),
                encoding="utf-8",
            )
            (root / "user" / "default" / "preferences.json").write_text(
                json.dumps({"type": "process", "name": "Not a preset"}),
                encoding="utf-8",
            )
            service = ProfileService(config_roots=(root,))

            choices = service.discover_profiles()

        self.assertEqual([choice.name for choice in choices], ["Fine"])
        self.assertTrue(choices[0].scope.startswith("orca:"))

    def test_malformed_json_shows_field_level_error(self):
        service = ProfileService(config_roots=())
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "broken.json"
            path.write_text('{"type": "process",\n', encoding="utf-8")

            with self.assertRaises(ProfileImportError) as caught:
                service.import_source(path, scope="local-import")

        self.assertEqual(caught.exception.field, "profile JSON")
        self.assertIn("line 2", caught.exception.message)
        self.assertIn("column", caught.exception.message)

    def test_missing_parent_blocks_profile_selection(self):
        service = ProfileService(config_roots=())
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self._import(service, root, "printer.json", {"type": "machine", "name": "Printer"})
            self._import(service, root, "filament.json", {"type": "filament", "name": "PLA"})
            self._import(
                service,
                root,
                "process.json",
                {
                    "type": "process",
                    "name": "Fine",
                    "inherits": "Missing base",
                    "ironing_flow": "10%",
                    "ironing_speed": "30",
                },
            )

            choices = {
                "printer": service.choices("printer")[0],
                "filament": service.choices("filament")[0],
                "process": service.choices("process")[0],
            }
            with self.assertRaises(MissingParentError):
                service.build_selection(
                    choices,
                    required_settings=("ironing_flow", "ironing_speed"),
                )

    def test_missing_required_module_setting_blocks_profile_selection(self):
        service = ProfileService(config_roots=())
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self._import(service, root, "printer.json", {"type": "machine", "name": "Printer"})
            self._import(service, root, "filament.json", {"type": "filament", "name": "PLA"})
            self._import(
                service,
                root,
                "process.json",
                {"type": "process", "name": "Fine", "ironing_flow": "10%"},
            )
            choices = {
                "printer": service.choices("printer")[0],
                "filament": service.choices("filament")[0],
                "process": service.choices("process")[0],
            }

            with self.assertRaisesRegex(MissingRequiredProfileSettingError, "ironing_speed"):
                service.build_selection(
                    choices,
                    required_settings=("ironing_flow", "ironing_speed"),
                )

    def test_ambiguous_name_requires_scope(self):
        service = ProfileService(config_roots=())
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for scope, filename in (("vendor-a", "a.json"), ("vendor-b", "b.json")):
                path = root / filename
                path.write_text(
                    json.dumps({"type": "process", "name": "Fine", "ironing_flow": "10%"}),
                    encoding="utf-8",
                )
                service.import_source(path, scope=scope)

        with self.assertRaises(AmbiguousProfileError):
            service.resolve("process", None, "Fine")

    def test_unique_cross_scope_parent_is_resolved_with_provenance(self):
        service = ProfileService(config_roots=())
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self._import(
                service,
                root,
                "base.json",
                {"type": "process", "name": "Base", "ironing_flow": "10%"},
                scope="orca:system",
            )
            self._import(
                service,
                root,
                "fine.json",
                {"type": "process", "name": "Fine", "inherits": "Base", "ironing_speed": "30"},
                scope="orca:user",
            )

            resolved = service.resolve("process", "orca:user", "Fine")

        self.assertEqual(resolved.settings["ironing_flow"], "10%")
        self.assertEqual(resolved.provenance["ironing_flow"].scope, "orca:system")

    def test_ambiguous_cross_scope_parent_blocks_resolution(self):
        service = ProfileService(config_roots=())
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for scope, filename, value in (
                ("vendor-a", "base-a.json", "10%"),
                ("vendor-b", "base-b.json", "12%"),
            ):
                self._import(
                    service,
                    root,
                    filename,
                    {"type": "process", "name": "Base", "ironing_flow": value},
                    scope=scope,
                )
            self._import(
                service,
                root,
                "fine.json",
                {"type": "process", "name": "Fine", "inherits": "Base", "ironing_speed": "30"},
                scope="user",
            )

        with self.assertRaisesRegex(AmbiguousProfileError, "multiple scopes"):
            service.resolve("process", "user", "Fine")

    def test_effective_values_retain_per_setting_provenance(self):
        service = ProfileService(config_roots=())
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self._import(
                service,
                root,
                "base.json",
                {
                    "type": "process",
                    "name": "Base",
                    "ironing_flow": "10%",
                    "ironing_speed": "30",
                    "wall_loops": "3",
                },
                scope="vendor",
            )
            self._import(
                service,
                root,
                "fine.json",
                {
                    "type": "process",
                    "name": "Fine",
                    "inherits": "Base",
                    "ironing_speed": "28",
                },
                scope="vendor",
            )

            resolved = service.resolve("process", "vendor", "Fine")

        self.assertEqual(resolved.settings["ironing_flow"], "10%")
        self.assertEqual(resolved.settings["ironing_speed"], "28")
        self.assertEqual(resolved.provenance["ironing_flow"].name, "Base")
        self.assertEqual(resolved.provenance["ironing_speed"].name, "Fine")
        self.assertEqual([profile.name for profile in resolved.chain], ["Base", "Fine"])

    def test_valid_selection_retains_selected_source_hashes(self):
        service = ProfileService(config_roots=())
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self._import(service, root, "printer.json", {"type": "machine", "name": "Printer"})
            self._import(service, root, "filament.json", {"type": "filament", "name": "PLA"})
            self._import(
                service,
                root,
                "process.json",
                {
                    "type": "process",
                    "name": "Fine",
                    "ironing_flow": "10%",
                    "ironing_speed": "30",
                },
            )
            selection = service.build_selection(
                {
                    "printer": service.choices("printer")[0],
                    "filament": service.choices("filament")[0],
                    "process": service.choices("process")[0],
                },
                required_settings=("ironing_flow", "ironing_speed"),
            )

        self.assertEqual(set(selection.source_paths), {"printer", "filament", "process"})
        self.assertEqual(set(selection.source_hashes), {"printer", "filament", "process"})
        self.assertEqual(len(selection.compatibility_warnings), 1)

    @staticmethod
    def _import(
        service: ProfileService,
        root: Path,
        filename: str,
        payload: dict[str, object],
        *,
        scope: str = "local-import",
    ) -> None:
        path = root / filename
        path.write_text(json.dumps(payload), encoding="utf-8")
        service.import_source(path, scope=scope)


if __name__ == "__main__":
    unittest.main()
