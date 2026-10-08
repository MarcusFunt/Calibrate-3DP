import importlib
import json
import tempfile
import unittest
from pathlib import Path

try:
    profile_api = importlib.import_module("calibrate3dp.profiles")
    profile_import_error = None
except ImportError as error:
    profile_api = None
    profile_import_error = error


class ProfileResolverTests(unittest.TestCase):
    def api(self):
        self.assertIsNotNone(
            profile_api,
            f"profile resolver API is not implemented yet: {profile_import_error}",
        )
        return profile_api

    def doc(self, api, name, raw, *, scope="vendor", kind="process", source=None):
        return api.ProfileDocument(
            name=name,
            kind=kind,
            scope=scope,
            raw=raw,
            source=source or f"{scope}/{name}.json",
        )

    def test_child_overrides_parent_and_preserves_inherited_unknown_values(self):
        api = self.api()
        parent = self.doc(
            api,
            "Base Quality",
            {
                "type": "process",
                "name": "Base Quality",
                "ironing_flow": "10",
                "support_threshold": "40",
                "vendor_extension": {"future_option": True},
                "wall_sequence": ["inner", "outer"],
            },
        )
        child_raw = {
            "type": "process",
            "name": "My Quality",
            "inherits": "Base Quality",
            "ironing_flow": "12",
            "top_shell_layers": "5",
            "wall_sequence": ["outer", "inner"],
        }
        child = self.doc(api, "My Quality", child_raw)

        resolved = api.ProfileCatalog([parent, child]).resolve("process", "vendor", "My Quality")

        self.assertEqual(
            resolved.settings,
            {
                "type": "process",
                "name": "My Quality",
                "ironing_flow": "12",
                "support_threshold": "40",
                "vendor_extension": {"future_option": True},
                "wall_sequence": ["outer", "inner"],
                "top_shell_layers": "5",
            },
        )
        self.assertEqual(resolved.provenance["ironing_flow"].name, "My Quality")
        self.assertEqual(resolved.provenance["support_threshold"].name, "Base Quality")
        self.assertEqual(resolved.provenance["vendor_extension"].source, "vendor/Base Quality.json")
        self.assertEqual([item.name for item in resolved.chain], ["Base Quality", "My Quality"])
        self.assertEqual(child.raw, child_raw)

    def test_parent_can_be_found_in_an_explicitly_allowed_parent_scope(self):
        api = self.api()
        system_base = self.doc(
            api,
            "Shared Base",
            {"type": "process", "name": "Shared Base", "layer_height": "0.2"},
            scope="system",
        )
        vendor_child = self.doc(
            api,
            "Vendor Fine",
            {"type": "process", "name": "Vendor Fine", "inherits": "Shared Base", "ironing_flow": "9"},
            scope="vendor",
        )

        resolved = api.ProfileCatalog(
            [system_base, vendor_child], parent_scopes={"vendor": ("system",)}
        ).resolve("process", "vendor", "Vendor Fine")

        self.assertEqual(resolved.settings["layer_height"], "0.2")
        self.assertEqual(resolved.settings["ironing_flow"], "9")
        self.assertEqual([item.scope for item in resolved.chain], ["system", "vendor"])

    def test_missing_parent_is_reported_instead_of_using_a_default(self):
        api = self.api()
        child = self.doc(api, "Orphan", {"type": "process", "name": "Orphan", "inherits": "Missing Base"})

        with self.assertRaisesRegex(api.MissingParentError, "Missing Base"):
            api.ProfileCatalog([child]).resolve("process", "vendor", "Orphan")

    def test_inheritance_cycle_reports_the_cycle(self):
        api = self.api()
        first = self.doc(api, "First", {"type": "process", "name": "First", "inherits": "Second"})
        second = self.doc(api, "Second", {"type": "process", "name": "Second", "inherits": "First"})

        with self.assertRaises(api.InheritanceCycleError) as caught:
            api.ProfileCatalog([first, second]).resolve("process", "vendor", "First")
        self.assertIn("First", str(caught.exception))
        self.assertIn("Second", str(caught.exception))

    def test_duplicate_profile_identity_is_rejected(self):
        api = self.api()
        first = self.doc(api, "Same Name", {"type": "process", "name": "Same Name"})
        duplicate = self.doc(
            api, "Same Name", {"type": "process", "name": "Same Name", "ironing_flow": "11"}
        )

        with self.assertRaisesRegex(api.DuplicateProfileError, "Same Name"):
            api.ProfileCatalog([first, duplicate])

    def test_parent_lookup_does_not_cross_profile_types(self):
        api = self.api()
        filament = self.doc(
            api,
            "Base",
            {"type": "filament", "name": "Base", "temperature": "205"},
            kind="filament",
        )
        process = self.doc(api, "Child", {"type": "process", "name": "Child", "inherits": "Base"})

        with self.assertRaisesRegex(api.MissingParentError, "Base"):
            api.ProfileCatalog([filament, process]).resolve("process", "vendor", "Child")

    def test_non_unique_selected_profile_name_is_reported(self):
        api = self.api()
        left = self.doc(api, "Quality", {"type": "process", "name": "Quality"}, scope="vendor-a")
        right = self.doc(api, "Quality", {"type": "process", "name": "Quality"}, scope="vendor-b")

        with self.assertRaisesRegex(api.AmbiguousProfileError, "Quality"):
            api.ProfileCatalog([left, right]).resolve("process", None, "Quality")

    def test_json_file_import_preserves_orca_metadata_and_unknown_fields(self):
        api = self.api()
        loader = getattr(api.ProfileDocument, "from_json_file", None)
        self.assertTrue(callable(loader), "ProfileDocument must expose a JSON file loader")
        raw = {
            "type": "process",
            "name": "Fine 0.16",
            "inherits": "Base",
            "ironing_flow": "10",
            "future_orca_setting": {"value": 7},
        }
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "fine.json"
            path.write_text(json.dumps(raw), encoding="utf-8")
            document = loader(path, scope="vendor")

        self.assertEqual(document.kind, "process")
        self.assertEqual(document.name, "Fine 0.16")
        self.assertEqual(document.scope, "vendor")
        self.assertEqual(document.source, str(path))
        self.assertEqual(document.raw, raw)

    def test_json_file_import_rejects_non_profile_json(self):
        api = self.api()
        loader = getattr(api.ProfileDocument, "from_json_file", None)
        self.assertTrue(callable(loader), "ProfileDocument must expose a JSON file loader")
        invalid_documents = ("[]", "{\"name\": \"No type\"}", "{\"type\": \"process\", \"name\": 4}")

        with tempfile.TemporaryDirectory() as directory:
            for index, contents in enumerate(invalid_documents):
                with self.subTest(contents=contents):
                    path = Path(directory) / f"bad-{index}.json"
                    path.write_text(contents, encoding="utf-8")
                    with self.assertRaises(api.InvalidProfileDocumentError):
                        loader(path, scope="vendor")

    def test_profile_document_rejects_non_string_identity_fields_cleanly(self):
        api = self.api()
        raw = {"type": "process", "name": "Quality"}

        for field in ("name", "kind", "scope", "source"):
            with self.subTest(field=field):
                values = {"name": "Quality", "kind": "process", "scope": "vendor", "raw": raw, "source": "p.json"}
                values[field] = 4
                with self.assertRaises(api.ProfileResolutionError):
                    api.ProfileDocument(**values)

    def test_parent_scope_configuration_rejects_a_single_string(self):
        api = self.api()

        with self.assertRaises(api.ProfileResolutionError):
            api.ProfileCatalog(parent_scopes={"vendor": "system"})


if __name__ == "__main__":
    unittest.main()
