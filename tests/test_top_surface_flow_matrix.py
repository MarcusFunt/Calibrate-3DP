"""Tests for the standalone, versioned top-surface flow matrix recipe."""
from __future__ import annotations

from pathlib import Path
from tempfile import TemporaryDirectory
import json
import unittest
import xml.etree.ElementTree as ET
import zipfile

from calibrate3dp.geometry.flow_ratio_matrix import (
    RECIPE_ID,
    TopSurfaceFlowMatrixSpec,
    build_top_surface_flow_matrix,
    export_top_surface_flow_matrix,
)
from calibrate3dp.geometry.layout import PlateLayoutError
from calibrate3dp.geometry.registry import is_geometry_backend_available
from calibrate3dp.grouped_plate import GroupedPlateError, write_plate_geometry_3mf


class TopSurfaceFlowMatrixSpecTests(unittest.TestCase):
    def test_default_nine_sorted_additive_candidates(self):
        spec = TopSurfaceFlowMatrixSpec(plate_code="FLOW01", baseline_filament_flow_ratio=0.98)
        candidates = spec.candidates()
        self.assertEqual(len(candidates), 9)
        self.assertEqual([c["sample"] for c in candidates], [f"Sample-{c}" for c in "ABCDEFGHI"])
        self.assertAlmostEqual(candidates[0]["target_filament_flow_ratio"], 0.94)
        self.assertAlmostEqual(candidates[-1]["target_filament_flow_ratio"], 1.02)
        self.assertAlmostEqual(candidates[0]["object_print_flow_ratio"], 0.94 / 0.98)
        self.assertAlmostEqual(candidates[4]["object_print_flow_ratio"], 1.0)
        self.assertEqual(spec.to_dict()["plate_code"], "FLOW01")

    def test_reject_invalid_input(self):
        invalid = [
            {"plate_code": "bad"},
            {"plate_code": "FLOW01", "modifiers": ()},
            {"plate_code": "FLOW01", "modifiers": tuple(i * .01 for i in range(10))},
            {"plate_code": "FLOW01", "modifiers": (0.01, -0.01)},
            {"plate_code": "FLOW01", "modifiers": (0.01, 0.01)},
            {"plate_code": "FLOW01", "modifiers": (float("nan"),)},
            {"plate_code": "FLOW01", "baseline_filament_flow_ratio": 0.0},
            {"plate_code": "FLOW01", "baseline_filament_flow_ratio": True},
            {"plate_code": "FLOW01", "specimen_width_mm": 10},
            {"plate_code": "FLOW01", "printable_width_mm": float("inf")},
            {"plate_code": "FLOW01", "baseline_filament_flow_ratio": 1.49, "modifiers": (0.04,)},
        ]
        for kwargs in invalid:
            with self.subTest(kwargs=kwargs), self.assertRaises(ValueError):
                TopSurfaceFlowMatrixSpec(**kwargs)


@unittest.skipUnless(is_geometry_backend_available("build123d", "1"), "pinned cad extra unavailable")
class TopSurfaceFlowMatrixCadTests(unittest.TestCase):
    def setUp(self):
        self.spec = TopSurfaceFlowMatrixSpec(
            plate_code="FLOW01", baseline_filament_flow_ratio=0.98,
            modifiers=(-0.02, 0.0, 0.02),
        )

    def test_geometry_is_connected_labeled_exact_and_non_ironing(self):
        geometry = build_top_surface_flow_matrix(self.spec)
        self.assertEqual([item.name for item in geometry.objects], [
            "Sample-A", "Sample-B", "Sample-C", "Plate-Frame", "Plate-Identifier"
        ])
        self.assertEqual(geometry.metadata["recipe_id"], RECIPE_ID)
        self.assertFalse(geometry.metadata["print_ready"])
        self.assertTrue(geometry.metadata["needs_physical_validation"])
        self.assertEqual(geometry.metadata["flow_ratio_matrix"]["slicer_requirements"]["ironing_type"], "no ironing")
        self.assertEqual(len(geometry.connections), 5)
        self.assertEqual(geometry.objects[-1].printed_marking, "FLOW01")
        for shape, candidate in zip(geometry.objects[:3], self.spec.candidates(), strict=True):
            self.assertEqual(shape.metadata["label_mode"], "underside-pocketed-emboss")
            self.assertEqual(shape.settings, {"print_flow_ratio": candidate["object_print_flow_ratio"]})
            self.assertEqual(shape.mesh.bounds[2], 0.0)
            self.assertEqual(shape.mesh.bounds[5], self.spec.specimen_height_mm)
            self.assertEqual(shape.metadata["solid_count"], 1)
        self.assertEqual(geometry.layout.sample_placements[0].width_mm, 30.0)
        self.assertEqual(geometry.layout.sample_placements[0].depth_mm, 25.0)

    def test_3mf_contains_per_object_multiplier_not_ironing_settings(self):
        geometry = build_top_surface_flow_matrix(self.spec)
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "plate.3mf"
            write_plate_geometry_3mf(
                geometry, destination=path, module_id="flow_ratio", plan_id="demo-flow"
            )
            with zipfile.ZipFile(path) as archive:
                root = ET.fromstring(archive.read("Metadata/model_settings.config"))
                manifest = json.loads(archive.read("Metadata/calibrate3dp-run.json"))
            parts = [obj.find("part") for obj in root.findall("object")]
            for index, part in enumerate(parts[:3]):
                attrs = {item.get("key"): item.get("value") for item in part.findall("metadata")}
                self.assertEqual(set(attrs), {"print_flow_ratio"})
                self.assertAlmostEqual(float(attrs["print_flow_ratio"]), self.spec.candidates()[index]["object_print_flow_ratio"])
                self.assertEqual(root.findall("object")[index].get("id"), part.get("id"))
            for part in parts[3:]:
                self.assertFalse(part.findall("metadata"))
            self.assertEqual(manifest["module_id"], "flow_ratio")
            self.assertEqual(manifest["geometry"]["provenance"]["recipe_id"], RECIPE_ID)
            self.assertEqual(len(manifest["samples"]), 3)

    def test_missing_per_object_override_is_not_silently_filled(self):
        geometry = build_top_surface_flow_matrix(self.spec)
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "no-b.3mf"
            write_plate_geometry_3mf(
                geometry, destination=path, module_id="flow_ratio", plan_id="negative",
                omitted_candidate_ids={"flow-02"},
            )
            with zipfile.ZipFile(path) as archive:
                root = ET.fromstring(archive.read("Metadata/model_settings.config"))
            parts = [obj.find("part") for obj in root.findall("object")]
            self.assertEqual(len(parts[1].findall("metadata")), 0)
            self.assertEqual(len(parts[0].findall("metadata")), 1)

    def test_fail_closed_for_wrong_module_and_small_bed(self):
        geometry = build_top_surface_flow_matrix(self.spec)
        with TemporaryDirectory() as tmp:
            with self.assertRaises(GroupedPlateError):
                write_plate_geometry_3mf(
                    geometry, destination=Path(tmp) / "wrong.3mf", module_id="ironing", plan_id="x"
                )
        with self.assertRaises(PlateLayoutError):
            build_top_surface_flow_matrix(TopSurfaceFlowMatrixSpec(
                plate_code="FLOW02", printable_width_mm=40.0, printable_depth_mm=40.0
            ))

    def test_export_writes_manifest_and_never_overwrites(self):
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "flow.3mf"
            created, sidecar = export_top_surface_flow_matrix(self.spec, path)
            self.assertEqual(created, path)
            document = json.loads(sidecar.read_text(encoding="utf-8"))
            self.assertFalse(document["qualification"]["print_ready"])
            self.assertFalse(document["qualification"]["run_registered"])
            self.assertEqual(document["candidate_map"][1]["target_filament_flow_ratio"], 0.98)
            self.assertEqual(len(document["artifact_sha256"]), 64)
            with self.assertRaises(FileExistsError):
                export_top_surface_flow_matrix(self.spec, path)


class TopSurfaceFlowGcodeTests(unittest.TestCase):
    def _gcode(self, a_extrusion: float = 0.9, b_extrusion: float = 1.1) -> str:
        return (
            "G90\nM83\n"
            "; printing object Sample-A id:1 copy 0\n"
            ";TYPE:Top surface\n"
            "G1 X0 Y0 F1200\n"
            f"G1 X10 Y0 E{a_extrusion} ; infill\n"
            "; stop printing object Sample-A id:1\n"
            "; printing object Sample-B id:2 copy 0\n"
            ";TYPE:Top surface\n"
            "G1 X20 Y0 F1200\n"
            f"G1 X30 Y0 E{b_extrusion} ; infill\n"
            "; stop printing object Sample-B id:2\n"
        )

    def test_accepts_proportional_relative_top_surface_extrusion(self):
        from calibrate3dp.calibration.flow_ratio_gcode import validate_top_surface_flow_gcode

        expected = {"Sample-A": {"print_flow_ratio": 0.9}, "Sample-B": {"print_flow_ratio": 1.1}}
        result = validate_top_surface_flow_gcode(self._gcode(), expected)
        self.assertTrue(result.valid, result.messages)
        self.assertAlmostEqual(result.samples["Sample-A"].normalized_extrusion_per_mm, .1)
        self.assertEqual(result.samples["Sample-B"].positive_top_surface_moves, 1)

    def test_rejects_ignored_override_or_missing_top_surface(self):
        from calibrate3dp.calibration.flow_ratio_gcode import validate_top_surface_flow_gcode

        expected = {"Sample-A": {"print_flow_ratio": 0.9}, "Sample-B": {"print_flow_ratio": 1.1}}
        ignored_override = validate_top_surface_flow_gcode(self._gcode(b_extrusion=0.9), expected)
        self.assertFalse(ignored_override.valid)
        self.assertTrue(any("Sample-B" in msg for msg in ignored_override.messages))
        no_top = validate_top_surface_flow_gcode(
            self._gcode().replace(";TYPE:Top surface", ";TYPE:Inner wall"), expected
        )
        self.assertFalse(no_top.valid)

    def test_rejects_unverified_extrusion_motion_mode(self):
        from calibrate3dp.calibration.flow_ratio_gcode import validate_top_surface_flow_gcode

        expected = {"Sample-A": {"print_flow_ratio": 0.9}, "Sample-B": {"print_flow_ratio": 1.1}}
        bad = validate_top_surface_flow_gcode(self._gcode().replace("M83", "M82"), expected)
        self.assertFalse(bad.valid)
        self.assertTrue(any("M82" in message for message in bad.messages))



if __name__ == "__main__":
    unittest.main()
