import unittest

from calibrate3dp import profiles


class ProfileExportTests(unittest.TestCase):
    def test_clone_changes_only_name_and_explicitly_patched_settings(self):
        raw = {
            "type": "process",
            "name": "0.20mm Standard",
            "inherits": "Common Process",
            "compatible_printers": ["DIY CoreXY 0.4"],
            "ironing_flow": "8",
            "unknown_future_field": {"retain": [1, 2]},
        }
        source = profiles.ProfileDocument(
            name="0.20mm Standard",
            kind="process",
            scope="vendor",
            raw=raw,
            source="vendor/0.20mm Standard.json",
        )
        clone_function = getattr(profiles, "clone_profile_with_patch", None)
        self.assertTrue(callable(clone_function), "profile patch export helper is not implemented")

        exported = clone_function(
            source,
            new_name="0.20mm Standard - calibrated",
            patch={"ironing_flow": "11", "ironing_speed": "18"},
        )

        self.assertEqual(
            exported,
            {
                "type": "process",
                "name": "0.20mm Standard - calibrated",
                "inherits": "Common Process",
                "compatible_printers": ["DIY CoreXY 0.4"],
                "ironing_flow": "11",
                "ironing_speed": "18",
                "unknown_future_field": {"retain": [1, 2]},
            },
        )
        self.assertEqual(raw["name"], "0.20mm Standard")
        self.assertEqual(raw["ironing_flow"], "8")

    def test_profile_patch_cannot_overwrite_identity_or_inheritance(self):
        source = profiles.ProfileDocument(
            name="Process",
            kind="process",
            scope="vendor",
            raw={"type": "process", "name": "Process", "inherits": "Base"},
            source="process.json",
        )
        clone_function = getattr(profiles, "clone_profile_with_patch", None)
        self.assertTrue(callable(clone_function), "profile patch export helper is not implemented")

        for key in ("name", "type", "inherits"):
            with self.subTest(key=key):
                with self.assertRaises(profiles.InvalidProfilePatchError):
                    clone_function(source, new_name="Calibrated", patch={key: "changed"})

    def test_profile_patch_rejects_invalid_name_and_non_json_values(self):
        source = profiles.ProfileDocument(
            name="Process",
            kind="process",
            scope="vendor",
            raw={"type": "process", "name": "Process"},
            source="process.json",
        )
        clone_function = getattr(profiles, "clone_profile_with_patch", None)
        self.assertTrue(callable(clone_function), "profile patch export helper is not implemented")

        with self.assertRaises(profiles.InvalidProfilePatchError):
            clone_function(source, new_name="  ", patch={"ironing_flow": "10"})
        with self.assertRaises(profiles.InvalidProfilePatchError):
            clone_function(source, new_name="Process", patch={"ironing_flow": "10"})
        with self.assertRaises(profiles.InvalidProfilePatchError):
            clone_function(source, new_name="Calibrated", patch={"ironing_flow": object()})


if __name__ == "__main__":
    unittest.main()
