import hashlib
import json
from pathlib import Path
import tempfile
import unittest
import zipfile

from calibrate3dp.profiles import ProfileCatalog


class OrcaProfileAdapterTests(unittest.TestCase):
    def api(self):
        from calibrate3dp import orca_profiles
        return orca_profiles

    def write_bundle(self, path: Path, entries: dict[str, str]) -> None:
        with zipfile.ZipFile(path, "w") as archive:
            for name, contents in entries.items():
                archive.writestr(name, contents)

    def test_builds_fully_resolved_cli_profile_with_candidate_patch(self):
        api = self.api()
        parent = api.ProfileDocument(
            name="Base", kind="process", scope="system",
            raw={"type": "process", "name": "Base", "ironing_speed": "30", "inherited_unknown": [1, 2]},
            source="base.json",
        )
        child = api.ProfileDocument(
            name="Fine", kind="process", scope="user",
            raw={"type": "process", "name": "Fine", "inherits": "Base", "ironing_flow": "10%", "child_unknown": {"keep": True}},
            source="fine.json",
        )
        resolved = ProfileCatalog([parent, child], parent_scopes={"user": ("system",)}).resolve("process", "user", "Fine")

        candidate = api.OrcaProfileAdapter().to_cli_profile(
            resolved,
            name="Fine calibration candidate",
            settings_patch={"ironing_flow": "15%"},
        )

        self.assertEqual(candidate["name"], "Fine calibration candidate")
        self.assertNotIn("inherits", candidate)
        self.assertEqual(candidate["ironing_speed"], "30")
        self.assertEqual(candidate["ironing_flow"], "15%")
        self.assertEqual(candidate["inherited_unknown"], [1, 2])
        self.assertEqual(candidate["child_unknown"], {"keep": True})
        self.assertEqual(child.raw["inherits"], "Base")
        self.assertEqual(child.raw["ironing_flow"], "10%")

    def test_cli_profile_patch_cannot_change_profile_identity(self):
        api = self.api()
        source = api.ProfileDocument(
            name="Fine", kind="process", scope="user",
            raw={"type": "process", "name": "Fine", "ironing_flow": "10%"},
            source="fine.json",
        )
        resolved = ProfileCatalog([source]).resolve("process", "user", "Fine")

        with self.assertRaises(api.InvalidProfilePatchError):
            api.OrcaProfileAdapter().to_cli_profile(
                resolved,
                name="Candidate",
                settings_patch={"type": "filament"},
            )

    def test_imports_printer_bundle_and_resolves_profiles_without_extraction(self):
        api = self.api()
        parent = {"type": "process", "name": "Base", "ironing_flow": "10"}
        child = {
            "type": "process",
            "name": "Fine",
            "inherits": "Base",
            "ironing_speed": "24",
            "future_orca_setting": {"retained": True},
        }
        filament = {"type": "filament", "name": "PLA", "temperature": ["210"]}
        printer = {"type": "machine", "name": "Printer", "bed_shape": [[0, 0], [220, 220]]}
        index = {"name": "Vendor", "process_list": [{"name": "Base"}]}
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "printer.orca_printer"
            self.write_bundle(path, {
                "process/Base.json": json.dumps(parent),
                "process/Fine.json": json.dumps(child),
                "filament/PLA.json": json.dumps(filament),
                "machine/Printer.json": json.dumps(printer),
                "Vendor.json": json.dumps(index),
            })
            before_hash = hashlib.sha256(path.read_bytes()).hexdigest()
            loaded = api.OrcaProfileAdapter().load(path, scope="my-printer")
            catalog = ProfileCatalog(loaded.documents)
            resolved = catalog.resolve("process", "my-printer", "Fine")

            self.assertEqual(loaded.adapter_version, 1)
            self.assertEqual(loaded.source_sha256, before_hash)
            self.assertEqual(loaded.scope, "my-printer")
            self.assertEqual({doc.kind for doc in loaded.documents}, {"process", "filament", "machine"})
            self.assertEqual(len(loaded.documents), 4)
            self.assertEqual(resolved.settings["ironing_flow"], "10")
            self.assertEqual(resolved.settings["ironing_speed"], "24")
            self.assertEqual(resolved.settings["future_orca_setting"], {"retained": True})
            self.assertTrue(all(doc.source.startswith(f"{path}!") for doc in loaded.documents))
            self.assertFalse((Path(directory) / "process").exists())

    def test_accepts_documented_orca_bundle_suffixes_and_generic_zip(self):
        api = self.api()
        profile = json.dumps({"type": "filament", "name": "PLA", "temperature": ["210"]})
        with tempfile.TemporaryDirectory() as directory:
            for suffix in (".orca_filaments", ".orca_filament", ".orca_bundle", ".zip"):
                with self.subTest(suffix=suffix):
                    path = Path(directory) / f"profiles{suffix}"
                    self.write_bundle(path, {"filament/PLA.json": profile})
                    loaded = api.OrcaProfileAdapter().load(path, scope="user")
                    self.assertEqual([doc.name for doc in loaded.documents], ["PLA"])
                    self.assertEqual(loaded.format, "zip-profile-bundle")

    def test_imports_a_single_profile_json_with_a_content_hash(self):
        api = self.api()
        payload = {"type": "process", "name": "Fine", "ironing_flow": "10"}
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "fine.json"
            path.write_text(json.dumps(payload), encoding="utf-8")
            expected_hash = hashlib.sha256(path.read_bytes()).hexdigest()
            loaded = api.OrcaProfileAdapter().load(path, scope="user-processes")

        self.assertEqual(loaded.format, "json-profile")
        self.assertEqual(loaded.source_sha256, expected_hash)
        self.assertEqual(loaded.documents[0].raw, payload)

    def test_imports_single_json_profile_with_utf8_bom(self):
        api = self.api()
        payload = {"type": "process", "name": "Fine", "ironing_flow": "10%"}
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "fine.json"
            path.write_bytes(bytes.fromhex("efbbbf") + json.dumps(payload).encode("utf-8"))
            loaded = api.OrcaProfileAdapter().load(path, scope="user")

        self.assertEqual(loaded.documents[0].raw, payload)

    def test_rejects_invalid_utf8_in_single_profile_json_with_adapter_error(self):
        api = self.api()
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "invalid-utf8.json"
            path.write_bytes(bytes.fromhex("ff"))
            with self.assertRaises(api.InvalidProfileBundleError):
                api.OrcaProfileAdapter().load(path, scope="user")

    def test_rejects_unrelated_archive_without_profile_json(self):
        api = self.api()
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "project.zip"
            self.write_bundle(path, {"3D/3dmodel.model": "<model />", "notes.txt": "not presets"})
            with self.assertRaises(api.InvalidProfileBundleError):
                api.OrcaProfileAdapter().load(path, scope="project")

    def test_bounds_archive_member_reads_even_when_size_metadata_understates_content(self):
        api = self.api()
        adapter = api.OrcaProfileAdapter()
        adapter.max_member_bytes = 8

        class OversizedMember:
            requested_size = None

            def __enter__(self):
                return self

            def __exit__(self, *args):
                return None

            def read(self, size):
                self.requested_size = size
                return b"x" * (size + 1)

        member = OversizedMember()

        class ArchiveStub:
            def open(self, entry, mode):
                return member

        entry = zipfile.ZipInfo("process/oversized.json")
        entry.file_size = 1
        with self.assertRaises(api.InvalidProfileBundleError):
            adapter._read_profile_entries(
                ArchiveStub(), Path("bundle.orca_printer"), "test", [entry]
            )
        self.assertEqual(member.requested_size, adapter.max_member_bytes + 1)

    def test_rejects_malformed_json_in_a_profile_directory(self):
        api = self.api()
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "broken.orca_printer"
            self.write_bundle(path, {"process/Broken.json": "{not valid json"})
            with self.assertRaises(api.InvalidProfileBundleError):
                api.OrcaProfileAdapter().load(path, scope="broken")

    def test_rejects_duplicate_profile_identity_in_one_bundle(self):
        api = self.api()
        profile = json.dumps({"type": "process", "name": "Fine"})
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "duplicate.zip"
            self.write_bundle(path, {"process/a.json": profile, "process/b.json": profile})
            with self.assertRaises(api.DuplicateProfileError):
                api.OrcaProfileAdapter().load(path, scope="same-scope")

    def test_rejects_path_traversal_entries(self):
        api = self.api()
        profile = json.dumps({"type": "process", "name": "Fine"})
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "unsafe.zip"
            self.write_bundle(path, {"../process/Fine.json": profile})
            with self.assertRaises(api.InvalidProfileBundleError):
                api.OrcaProfileAdapter().load(path, scope="unsafe")

    def test_rejects_unknown_or_unhashable_profile_types_cleanly(self):
        api = self.api()
        with tempfile.TemporaryDirectory() as directory:
            archive_path = Path(directory) / "invalid-type.orca_printer"
            self.write_bundle(archive_path, {
                "process/Invalid.json": json.dumps({"type": [], "name": "Invalid"}),
            })
            with self.assertRaises(api.InvalidProfileBundleError):
                api.OrcaProfileAdapter().load(archive_path, scope="invalid")

            json_path = Path(directory) / "unknown.json"
            json_path.write_text(json.dumps({"type": "unknown", "name": "Nope"}), encoding="utf-8")
            with self.assertRaises(api.InvalidProfileBundleError):
                api.OrcaProfileAdapter().load(json_path, scope="invalid")

    def test_rejects_unsupported_or_unreadable_profile_sources(self):
        api = self.api()
        with tempfile.TemporaryDirectory() as directory:
            unsupported = Path(directory) / "profiles.txt"
            unsupported.write_text("{}", encoding="utf-8")
            with self.assertRaises(api.InvalidProfileBundleError):
                api.OrcaProfileAdapter().load(unsupported, scope="user")

            malformed = Path(directory) / "broken.orca_printer"
            malformed.write_text("not a zip archive", encoding="utf-8")
            with self.assertRaises(api.InvalidProfileBundleError):
                api.OrcaProfileAdapter().load(malformed, scope="user")


if __name__ == "__main__":
    unittest.main()
