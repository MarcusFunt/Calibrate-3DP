from __future__ import annotations

import unittest

from calibrate3dp.gcode_preflight import validate_gcode_preflight


MACHINE = {
    "bed_size": [100, 100],
    "printable_height": 100,
    "bed_exclude_area": ["40x40", "60x40", "60x60", "40x60"],
}
FILAMENT = {
    "nozzle_temperature_range_low": [180],
    "nozzle_temperature_range_high": [230],
    "bed_temperature_range_low": [0],
    "bed_temperature_range_high": [80],
}


class GcodePreflightTests(unittest.TestCase):
    def test_modal_linear_moves_relative_modes_and_temperature_limits(self):
        gcode = """G21
G90
M82
M109 S205
M190 S60
G92 X5 Y5 Z0 E0
G1 X10 Y10 Z0.2 F1200 E1
G91
M83
G1 X2 Y0 E0.5
"""
        report = validate_gcode_preflight(gcode, MACHINE, FILAMENT)
        self.assertTrue(report.passed)
        self.assertEqual(report.status, "subset_checked")
        self.assertEqual(report.movement_count, 2)
        self.assertEqual(report.extrusion_move_count, 2)
        self.assertEqual(report.temperature_commands[0]["target_c"], 205)
        self.assertTrue(report.temperature_sequence_verified)
        self.assertEqual(report.movement_bounds_mm["max_x"], 12)
        self.assertTrue(report.printable_area_checked)
        self.assertTrue(report.z_limit_checked)

    def test_travel_outside_bed_is_checked_even_without_extrusion(self):
        report = validate_gcode_preflight(
            "G21\nG90\nG92 X10 Y10 Z0 E0\nG0 X101 Y10 F9000\nM104 S200\nM140 S60\n",
            MACHINE,
            FILAMENT,
        )
        self.assertFalse(report.passed)
        self.assertTrue(any("leaves the configured printable XY area" in message for message in report.errors))
        self.assertEqual(report.travel_move_count, 1)

    def test_keep_out_crossing_fails_even_with_safe_endpoints(self):
        report = validate_gcode_preflight(
            "G21\nG90\nG92 X10 Y50 Z0 E0\nG1 X90 Y50 F1000 E5\nM104 S200\nM140 S60\n",
            MACHINE,
            FILAMENT,
        )
        self.assertTrue(any("crosses bed keep-out 1" in message for message in report.errors))

    def test_arcs_and_unknown_vendor_commands_are_not_assumed_safe(self):
        report = validate_gcode_preflight(
            "G21\nG90\nG92 X10 Y10 Z0 E0\nG2 X20 Y20 I5 J0 E1\nM1002 P1\nM104 S200\nM140 S60\n",
            MACHINE,
            FILAMENT,
        )
        self.assertIn("G2", report.unsupported_commands)
        self.assertIn("M1002", report.unsupported_commands)
        self.assertFalse(report.parser_coverage_complete)

    def test_missing_temperature_limits_remain_unverified_not_passed(self):
        report = validate_gcode_preflight(
            "G21\nG90\nG92 X5 Y5 Z0 E0\nG1 X6 Y6 Z1 E1\nM104 S210\nM140 S60\n",
            MACHINE,
            {},
        )
        self.assertFalse(report.passed)
        self.assertEqual(report.status, "unverified")
        self.assertTrue(any("temperature limits are unavailable" in item for item in report.unverified))

    def test_temperature_outside_profile_range_fails(self):
        report = validate_gcode_preflight(
            "G21\nG90\nG92 X5 Y5 Z0 E0\nG1 X6 Y6 Z1 E1\nM104 S260\nM140 S60\n",
            MACHINE,
            FILAMENT,
        )
        self.assertTrue(any("outside configured limits" in message for message in report.errors))

    def test_zero_temperature_targets_are_recorded_as_heater_shutdowns(self):
        report = validate_gcode_preflight(
            "G21\nG90\nM109 S205\nM190 S60\nG92 X5 Y5 Z0 E0\n"
            "G1 X6 Y6 Z1 E1\nM104 S0\nM140 S0\n",
            MACHINE,
            FILAMENT,
        )
        self.assertFalse(any("outside configured limits" in message for message in report.errors))
        shutdowns = [item for item in report.temperature_commands if item["target_c"] == 0]
        self.assertEqual([item["kind"] for item in shutdowns], ["nozzle", "bed"])
        self.assertTrue(all(item["heater_off"] for item in shutdowns))

    def test_zero_temperature_wait_does_not_satisfy_pre_extrusion_heat_sequence(self):
        report = validate_gcode_preflight(
            "G21\nG90\nG92 X5 Y5 Z0 E0\nG1 X6 Y6 Z1 E1\nM109 S0\nM190 S0\n",
            MACHINE,
            FILAMENT,
        )
        self.assertFalse(report.temperature_sequence_verified)
        self.assertTrue(any("blocking wait were not both observed before first extrusion" in item for item in report.unverified))

    def test_coordinate_reset_after_motion_is_not_treated_as_physical_position(self):
        report = validate_gcode_preflight(
            "G21\nG90\nM109 S205\nM190 S60\nG92 X5 Y5 Z0 E0\n"
            "G1 X90 Y10 Z0.2\nG92 X5\nG1 X25 Y10 E1\n",
            {"bed_size": [100, 100], "printable_height": 100},
            FILAMENT,
        )
        self.assertFalse(report.passed)
        self.assertFalse(report.parser_coverage_complete)
        self.assertTrue(any("coordinate reset" in item.lower() for item in report.unverified))

    def test_heater_shutdown_before_first_extrusion_invalidates_heat_sequence(self):
        report = validate_gcode_preflight(
            "G21\nG90\nM109 S205\nM190 S60\nM104 S0\nM140 S0\n"
            "G92 X5 Y5 Z0 E0\nG1 X6 Y6 Z1 E1\n",
            MACHINE,
            FILAMENT,
        )
        self.assertFalse(report.passed)
        self.assertFalse(report.temperature_sequence_verified)

    def test_missing_nozzle_limits_are_incomplete_even_when_nozzle_command_is_absent(self):
        report = validate_gcode_preflight(
            "G21\nG90\nM190 S60\nG92 X5 Y5 Z0 E0\nG1 X6 Y6 Z1 E1\n",
            MACHINE,
            {"bed_temperature_range_low": [0], "bed_temperature_range_high": [80]},
        )
        self.assertFalse(report.passed)
        self.assertFalse(report.parser_coverage_complete)
        self.assertTrue(any("nozzle temperature limits" in item.lower() for item in report.unverified))


if __name__ == "__main__":
    unittest.main()
