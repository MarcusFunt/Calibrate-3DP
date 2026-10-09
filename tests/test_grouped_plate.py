from __future__ import annotations

from pathlib import Path
import tempfile
import unittest
import xml.etree.ElementTree as ET
import zipfile

from calibrate3dp.grouped_plate import (
    GroupedPlateError,
    grouped_plate_bounds,
    require_grouped_plate_fits_machine,
    validate_grouped_ironing_gcode,
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
            f"G1 X5 Y5 E{nominal_per_percent * flow:.5f} F{speed * 60:g}",
            f"; stop printing object {label} id:1 copy 0",
        ))
    return "\n".join(lines), expected


class GroupedPlate3MFTests(unittest.TestCase):
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


class GroupedGcodeValidationTests(unittest.TestCase):
    def test_proves_expected_speed_and_proportional_flow_per_sample(self):
        plan = _plan()
        text, expected = _gcode(plan)
        result = validate_grouped_ironing_gcode(text, expected)
        self.assertTrue(result.valid, result.messages)
        self.assertEqual(set(result.samples), set(expected))
        self.assertAlmostEqual(result.samples["Sample-B"].observed_speed_mm_s[0], 15)
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
