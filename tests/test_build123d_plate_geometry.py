from __future__ import annotations

import importlib.util
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
import xml.etree.ElementTree as ET
import zipfile

from calibrate3dp.geometry.layout import PlateLayoutRequest, PlateSampleRequest


HAS_BUILD123D = importlib.util.find_spec("build123d") is not None


def _request(*, count: int = 9, corner: str = "front_left") -> PlateLayoutRequest:
    return PlateLayoutRequest(
        samples=tuple(
            PlateSampleRequest(
                label=label,
                candidate_id=f"candidate-{label}",
                settings={"ironing_flow": 10 + index, "ironing_speed": 20 + index},
            )
            for index, label in enumerate("ABCDEFGHI"[:count])
        ),
        plate_code="R2F0I5",
        printable_polygon=((0, 0), (235, 0), (235, 235), (0, 235)),
        geometry_backend="build123d",
        geometry_backend_version="1",
        identifier_corner=corner,
    )


@unittest.skipUnless(HAS_BUILD123D, "install the optional cad extra to run build123d geometry tests")
class Build123dPlateGeometryTests(unittest.TestCase):
    def test_nine_samples_make_11_objects_with_pocketed_underside_text_and_corner_plaque(self):
        from calibrate3dp.geometry.build123d_backend import Build123dPlateGeometryBackend

        backend = Build123dPlateGeometryBackend()
        geometry = backend.build(_request())

        self.assertEqual(
            [item.name for item in geometry.objects],
            [*(f"Sample-{label}" for label in "ABCDEFGHI"), "Plate-Frame", "Plate-Identifier"],
        )
        self.assertTrue(backend.validate(geometry).valid, backend.validate(geometry).messages)
        samples = [item for item in geometry.objects if item.sample_label]
        self.assertEqual(len(samples), 9)
        for sample in samples:
            self.assertEqual(sample.role, "sample")
            self.assertEqual(sample.metadata["label_mode"], "underside-pocketed-emboss")
            self.assertEqual(sample.metadata["label_view_direction"], [0, 0, -1])
            self.assertEqual(sample.metadata["label_orientation_matrix"], [[1, 0, 0], [0, -1, 0], [0, 0, -1]])
            self.assertEqual(sample.mesh.bounds[2], 0)
            self.assertAlmostEqual(sample.mesh.bounds[5], 6.4, places=5)
            self.assertEqual(sample.metadata["solid_count"], 1)
            self.assertGreater(sample.metadata["pocket_depth_mm"], 0)
            self.assertGreater(sample.metadata["label_text_size_mm"], 0)

        frame = next(item for item in geometry.objects if item.name == "Plate-Frame")
        identifier = next(item for item in geometry.objects if item.name == "Plate-Identifier")
        self.assertEqual(frame.printed_marking, "")
        self.assertEqual(identifier.role, "identifier")
        self.assertEqual(identifier.printed_marking, "R2F0I5")
        self.assertEqual(identifier.metadata["corner"], "front_left")
        self.assertEqual(identifier.mesh.bounds[2], 0)
        self.assertGreater(identifier.mesh.bounds[5], identifier.metadata["thickness_mm"])
        self.assertEqual(identifier.metadata["solid_count"], 1)
        self.assertEqual(len(geometry.connections), 11)
        self.assertEqual(sum(link.source_name == "Plate-Identifier" for link in geometry.connections), 2)
        self.assertTrue(geometry.metadata["needs_physical_validation"])

    def test_confirmation_geometry_keeps_frame_and_identifier(self):
        from calibrate3dp.geometry.build123d_backend import Build123dPlateGeometryBackend

        geometry = Build123dPlateGeometryBackend().build(_request(count=1))

        self.assertEqual(
            [item.name for item in geometry.objects],
            ["Sample-A", "Plate-Frame", "Plate-Identifier"],
        )
        self.assertEqual(len(geometry.connections), 3)

    def test_all_identifier_corners_are_part_of_validated_layout(self):
        from calibrate3dp.geometry.build123d_backend import Build123dPlateGeometryBackend

        backend = Build123dPlateGeometryBackend()
        for corner in ("front_left", "front_right", "back_left", "back_right"):
            with self.subTest(corner=corner):
                geometry = backend.build(_request(count=1, corner=corner))
                identifier = geometry.objects[-1]
                self.assertEqual(identifier.metadata["corner"], corner)
                self.assertTrue(backend.validate(geometry).valid)

    def test_font_digest_is_checked_before_outline_generation(self):
        from calibrate3dp.geometry.build123d_backend import (
            Build123dPlateGeometryBackend,
            GeometryFontError,
        )

        backend = Build123dPlateGeometryBackend(expected_font_sha256="0" * 64)
        with self.assertRaisesRegex(GeometryFontError, "font.*SHA-256"):
            backend.build(_request(count=1))

    def test_3mf_preserves_eleven_named_objects_and_only_samples_get_overrides(self):
        from calibrate3dp.geometry.build123d_backend import Build123dPlateGeometryBackend
        from calibrate3dp.grouped_plate import write_plate_geometry_3mf

        geometry = Build123dPlateGeometryBackend().build(_request())
        with TemporaryDirectory() as temporary:
            path = Path(temporary) / "plate.3mf"
            write_plate_geometry_3mf(
                geometry,
                destination=path,
                module_id="ironing",
                plan_id="plan-cad",
            )

            with zipfile.ZipFile(path) as archive:
                manifest = json.loads(archive.read("Metadata/calibrate3dp-run.json"))
                settings = ET.fromstring(archive.read("Metadata/model_settings.config"))

        self.assertEqual(manifest["schema_version"], 2)
        self.assertEqual(manifest["geometry"]["object_count"], 11)
        self.assertEqual(manifest["geometry"]["provenance"]["font_sha256"], "f04d7c488ddf7d1fa99f2574efc3406ea4cbe17bb1af3a1ab960f84d0c96a172")
        self.assertEqual(manifest["objects"][-1]["name"], "Plate-Identifier")
        self.assertEqual(manifest["objects"][-1]["role"], "identifier")
        self.assertEqual(len(manifest["connections"]), 11)
        by_name = {
            item.find("metadata[@key='name']").attrib["value"]: item
            for item in settings.findall("object")
        }
        self.assertEqual(len(by_name), 11)
        self.assertIn("ironing_flow", [item.attrib.get("key") for item in by_name["Sample-A"].findall(".//metadata")])
        self.assertNotIn("ironing_flow", [item.attrib.get("key") for item in by_name["Plate-Frame"].findall(".//metadata")])
        self.assertNotIn("ironing_flow", [item.attrib.get("key") for item in by_name["Plate-Identifier"].findall(".//metadata")])

    def test_feature_toolpath_proof_requires_underside_first_layer_and_top_plaque_text(self):
        from calibrate3dp.geometry.build123d_backend import Build123dPlateGeometryBackend
        from calibrate3dp.grouped_plate import validate_build123d_feature_toolpaths

        geometry = Build123dPlateGeometryBackend().build(_request())

        def object_gcode(*, omit_sample_label: str | None = None, omit_plaque_top: bool = False) -> str:
            lines: list[str] = []
            for index, obj in enumerate(geometry.objects, start=1):
                lines.append(f"; printing object {obj.name} id: {index}")
                bounds = obj.mesh.bounds
                center = ((bounds[0] + bounds[3]) / 2, (bounds[1] + bounds[4]) / 2)
                if obj.sample_label is not None:
                    text_bounds = obj.metadata["text_bounds_mm"]
                    if obj.sample_label == omit_sample_label:
                        lines.append(f"G1 X{bounds[0] + 1:.3f} Y{bounds[1] + 1:.3f} Z0.2 E0.5 F1200")
                    else:
                        x = (text_bounds[0] + text_bounds[2]) / 2
                        y = (text_bounds[1] + text_bounds[3]) / 2
                        lines.append(f"G1 X{x:.3f} Y{y:.3f} Z0.2 E0.5 F1200")
                elif obj.name == "Plate-Identifier":
                    lines.append(f"G1 X{center[0]:.3f} Y{center[1]:.3f} Z0.2 E0.5 F1200")
                    if not omit_plaque_top:
                        text_bounds = obj.metadata["text_bounds_mm"]
                        x = (text_bounds[0] + text_bounds[2]) / 2
                        y = (text_bounds[1] + text_bounds[3]) / 2
                        lines.append(f"G1 X{x:.3f} Y{y:.3f} Z{obj.metadata['text_top_z_mm']:.3f} E0.5 F1200")
                else:
                    lines.append(f"G1 X{center[0]:.3f} Y{center[1]:.3f} Z0.2 E0.5 F1200")
                lines.append("; stop printing object")
            return "\n".join(lines)

        valid = validate_build123d_feature_toolpaths(object_gcode(), geometry)
        no_label = validate_build123d_feature_toolpaths(object_gcode(omit_sample_label="A"), geometry)
        no_code = validate_build123d_feature_toolpaths(object_gcode(omit_plaque_top=True), geometry)

        self.assertTrue(valid.valid, valid.messages)
        self.assertEqual(len(valid.first_layer_label_moves), 9)
        self.assertGreater(valid.identifier_top_text_moves, 0)
        self.assertFalse(no_label.valid)
        self.assertTrue(any("Sample-A" in message and "first-layer" in message for message in no_label.messages))
        self.assertFalse(no_code.valid)
        self.assertTrue(any("Plate-Identifier" in message and "top-side" in message for message in no_code.messages))


if __name__ == "__main__":
    unittest.main()
