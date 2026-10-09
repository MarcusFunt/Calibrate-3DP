from __future__ import annotations

from pathlib import Path
import tempfile
import unittest
import xml.etree.ElementTree as ET
import zipfile
from calibrate3dp.geometry.layout import PlateLayoutRequest, PlateSampleRequest
from calibrate3dp.geometry.specimens import PlateGeometryBackend

from calibrate3dp.grouped_plate import (
    GroupedPlateError,
    grouped_plate_bounds,
    machine_keep_out_polygons,
    machine_printable_polygon,
    require_grouped_plate_fits_machine,
    require_plate_geometry_fits_machine,
    validate_grouped_ironing_gcode,
    validate_grouped_plate_layout_gcode,
    write_plate_geometry_3mf,
    write_grouped_plate_3mf,
)
from calibrate3dp.ironing import create_initial_ironing_experiment


_CORE = "http://schemas.microsoft.com/3dmanufacturing/core/2015/02"


def _plan():
    return create_initial_ironing_experiment(
        plan_id="grouped-test",
        baseline_settings={"ironing_type": "top", "ironing_flow": "5%", "ironing_speed": "5"},
        flow_values=(12, 15, 18),
        speed_values=(10, 15, 20),
    )


def _expected_by_label(plan):
    labels = "ABCDEFGHI"
    return {
        f"Sample-{label}": candidate.overrides
        for label, candidate in zip(labels, plan.candidates, strict=True)
    }


def _gcode(plan, *, wrong_speed: str | None = None):
    expected = _expected_by_label(plan)
    lines = []
    nominal_per_percent = 0.1
    for label, values in expected.items():
        flow = float(str(values["ironing_flow"]).rstrip("%"))
        speed = float(values["ironing_speed"])
        if label == "Sample-B" and wrong_speed is not None:
            speed = float(wrong_speed)
        lines.extend((
            f"; printing object {label} id:1 copy 0",
            ";TYPE:Ironing",
            "G1 E5 F2400 ; unretract",
            f"G1 X5 Y5 E{nominal_per_percent * flow:.5f} F{speed * 60:g} ; ironing",
            f"; stop printing object {label} id:1 copy 0",
        ))
    return "\n".join(lines), expected


class GroupedPlate3MFTests(unittest.TestCase):
    def test_resolves_printable_area_and_explicit_bed_keep_out_polygon(self):
        settings = {
            "printable_area": ["0x0", "220x0", "220x220", "0x220"],
            "bed_exclude_area": ["180x0", "220x0", "220x40", "180x40"],
            "bed_size": [220, 220],
        }
        self.assertEqual(machine_printable_polygon(settings), ((0, 0), (220, 0), (220, 220), (0, 220)))
        self.assertEqual(machine_keep_out_polygons(settings), (((180, 0), (220, 0), (220, 40), (180, 40)),))
        self.assertEqual(machine_keep_out_polygons({"bed_exclude_area": ["0x0"], "bed_size": [220, 220]}), ())
        with self.assertRaisesRegex(GroupedPlateError, "at least three points"):
            machine_keep_out_polygons({"bed_exclude_area": ["0x0", "220x0"], "bed_size": [220, 220]})

    def test_layout_bounds_are_checked_against_machine_printable_area(self):
        plan = _plan()
        bounds = grouped_plate_bounds(plan)
        self.assertEqual((bounds.min_x, bounds.min_y, bounds.max_x, bounds.max_y), (5, 5, 111, 111))
        accepted = require_grouped_plate_fits_machine(
            plan,
            {"printable_area": ["0x0", "220x0", "220x220", "0x220"], "printable_height": "250"},
        )
        self.assertEqual(accepted, bounds)
        with self.assertRaisesRegex(GroupedPlateError, "printable area"):
            require_grouped_plate_fits_machine(
                plan,
                {"printable_area": ["0x0", "100x0", "100x100", "0x100"]},
            )
        with self.assertRaisesRegex(GroupedPlateError, "must define"):
            require_grouped_plate_fits_machine(plan, {})

    def test_each_sample_part_id_matches_its_3mf_object_and_settings(self):
        plan = _plan()
        with tempfile.TemporaryDirectory() as temp:
            path = write_grouped_plate_3mf(plan, plate_code="7K3P9D", destination=Path(temp) / "plate.3mf")

            with zipfile.ZipFile(path) as archive:
                model = ET.fromstring(archive.read("3D/3dmodel.model"))
                config = ET.fromstring(archive.read("Metadata/model_settings.config"))

            objects = model.findall(f".//{{{_CORE}}}resources/{{{_CORE}}}object")
            self.assertEqual(len(objects), 9)
            by_name = {obj.attrib["name"]: obj.attrib["id"] for obj in objects}
            config_objects = {obj.attrib["id"]: obj for obj in config.findall("object")}
            self.assertEqual(len(config_objects), 9)
            for label, candidate in zip("ABCDEFGHI", plan.candidates, strict=True):
                name = f"Sample-{label}"
                object_id = by_name[name]
                obj_config = config_objects[object_id]
                part = obj_config.find("part")
                self.assertEqual(part.attrib["id"], object_id)
                metadata = {item.attrib["key"]: item.attrib["value"] for item in part.findall("metadata")}
                expected_flow = str(candidate.overrides["ironing_flow"])
                if not expected_flow.endswith("%"):
                    expected_flow += "%"
                self.assertEqual(metadata["ironing_flow"], expected_flow)
                self.assertEqual(metadata["ironing_speed"], str(candidate.overrides["ironing_speed"]))

    def test_explicitly_omitted_candidate_has_no_part_override(self):
        plan = _plan()
        target = plan.candidates[4].candidate_id
        with tempfile.TemporaryDirectory() as temp:
            path = write_grouped_plate_3mf(
                plan,
                plate_code="7K3P9D",
                destination=Path(temp) / "plate.3mf",
                omitted_candidate_ids={target},
            )
            with zipfile.ZipFile(path) as archive:
                config = ET.fromstring(archive.read("Metadata/model_settings.config"))
            sample = config.findall("object")[4]
            self.assertEqual(sample.findall("part/metadata"), [])

    def test_geometry_3mf_keeps_nine_objects_mapped_and_physical_markings_recorded(self):
        samples = tuple(
            PlateSampleRequest(label, candidate.candidate_id, candidate.overrides)
            for label, candidate in zip("ABCDEFGHI", _plan().candidates, strict=True)
        )
        geometry = PlateGeometryBackend().build(PlateLayoutRequest(
            samples=samples,
            plate_code="7K3P9D",
            printable_polygon=((0, 0), (220, 0), (220, 220), (0, 220)),
        ))
        with tempfile.TemporaryDirectory() as temp:
            path = write_plate_geometry_3mf(
                geometry, destination=Path(temp) / "connected.3mf", module_id="ironing", plan_id="grouped-test"
            )
            with zipfile.ZipFile(path) as archive:
                model = ET.fromstring(archive.read("3D/3dmodel.model"))
                config = ET.fromstring(archive.read("Metadata/model_settings.config"))
                manifest = __import__("json").loads(archive.read("Metadata/calibrate3dp-run.json"))

        objects = model.findall(f".//{{{_CORE}}}resources/{{{_CORE}}}object")
        by_name = {item.attrib["name"]: item.attrib["id"] for item in objects}
        self.assertEqual(len(objects), 10)
        self.assertIn("Plate-Frame", by_name)
        config_by_id = {item.attrib["id"]: item for item in config.findall("object")}
        for label in "ABCDEFGHI":
            object_id = by_name[f"Sample-{label}"]
            part = config_by_id[object_id].find("part")
            self.assertEqual(part.attrib["id"], object_id)
            self.assertEqual({item.attrib["key"] for item in part.findall("metadata")}, {"ironing_flow", "ironing_speed"})
        self.assertTrue(manifest["physical_labels_in_mesh"])
        self.assertTrue(manifest["physical_plate_code_in_mesh"])
        self.assertEqual(manifest["geometry"]["backend_id"], "stdlib-voxel")
        self.assertEqual(len(manifest["samples"]), 9)


class GroupedGcodeValidationTests(unittest.TestCase):
    def test_gcode_layout_requires_all_objects_to_keep_one_shared_xy_translation(self):
        samples = tuple(
            PlateSampleRequest(label, candidate.candidate_id, candidate.overrides)
            for label, candidate in zip("ABCDEFGHI", _plan().candidates, strict=True)
        )
        geometry = PlateGeometryBackend().build(PlateLayoutRequest(
            samples=samples,
            plate_code="7K3P9D",
            printable_polygon=((0, 0), (220, 0), (220, 220), (0, 220)),
        ))
        machine = {
            "printable_area": ["0x0", "220x0", "220x220", "0x220"],
            "printable_height": ["250"],
            "bed_exclude_area": ["0x0"],
        }

        def bounds_gcode(shift_sample_a: float) -> str:
            lines = []
            for obj in geometry.objects:
                bounds = obj.mesh.bounds
                shift = shift_sample_a if obj.name == "Sample-A" else 0.0
                lines.extend((
                    f"; printing object {obj.name} id:1 copy 0",
                    f"G1 X{bounds[0] + shift:g} Y{bounds[1]:g} E0.01 ; perimeter",
                    f"G1 X{bounds[3] + shift:g} Y{bounds[4]:g} E0.01 ; perimeter",
                    f"; stop printing object {obj.name} id:1 copy 0",
                ))
            return "\n".join(lines)

        valid = validate_grouped_plate_layout_gcode(bounds_gcode(0), geometry, machine)
        rearranged = validate_grouped_plate_layout_gcode(bounds_gcode(30), geometry, machine)

        self.assertTrue(valid.valid, valid.messages)
        self.assertEqual(valid.xy_translation_mm, (0.0, 0.0))
        self.assertFalse(rearranged.valid)
        self.assertTrue(any("Sample-A G-code X bounds" in item for item in rearranged.messages))

    def test_connected_geometry_machine_fit_rechecks_xy_bed_and_keepouts(self):
        samples = tuple(
            PlateSampleRequest(label, candidate.candidate_id, candidate.overrides)
            for label, candidate in zip("ABCDEFGHI", _plan().candidates, strict=True)
        )
        geometry = PlateGeometryBackend().build(PlateLayoutRequest(
            samples=samples,
            plate_code="7K3P9D",
            printable_polygon=((0, 0), (220, 0), (220, 220), (0, 220)),
        ))

        with self.assertRaisesRegex(GroupedPlateError, "printable area"):
            require_plate_geometry_fits_machine(geometry, {
                "printable_area": ["0x0", "20x0", "20x20", "0x20"],
                "printable_height": ["250"],
            })
        with self.assertRaisesRegex(GroupedPlateError, "keep-out"):
            require_plate_geometry_fits_machine(geometry, {
                "printable_area": ["0x0", "220x0", "220x220", "0x220"],
                "bed_exclude_area": ["18x8", "25x8", "25x25", "18x25"],
                "printable_height": ["250"],
            })

    def test_proves_expected_speed_and_proportional_flow_per_sample(self):
        plan = _plan()
        text, expected = _gcode(plan)
        result = validate_grouped_ironing_gcode(text, expected)
        self.assertTrue(result.valid, result.messages)
        self.assertEqual(set(result.samples), set(expected))
        self.assertAlmostEqual(result.samples["Sample-B"].observed_speed_mm_s[0], 15)
        self.assertAlmostEqual(result.samples["Sample-A"].positive_extrusion_mm, 1.2)
        self.assertAlmostEqual(result.samples["Sample-C"].extrusion_per_flow_percent, 0.1)

    def test_rejects_a_sample_that_used_the_base_speed(self):
        plan = _plan()
        text, expected = _gcode(plan, wrong_speed="5")
        result = validate_grouped_ironing_gcode(text, expected)
        self.assertFalse(result.valid)
        self.assertTrue(any("Sample-B" in message and "speed" in message.lower() for message in result.messages))

    def test_rejects_missing_object_toolpath(self):
        plan = _plan()
        text, expected = _gcode(plan)
        text = "\n".join(line for line in text.splitlines() if "Sample-B" not in line)
        result = validate_grouped_ironing_gcode(text, expected)
        self.assertFalse(result.valid)
        self.assertTrue(any("Sample-B" in message for message in result.messages))


if __name__ == "__main__":
    unittest.main()
