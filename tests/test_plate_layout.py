from __future__ import annotations

import unittest

from calibrate3dp.geometry.layout import (
    PlateLayoutError,
    PlateLayoutRequest,
    PlateSampleRequest,
    layout_plate,
)


def _samples():
    return tuple(
        PlateSampleRequest(
            label=label,
            candidate_id=f"candidate-{label}",
            settings={"ironing_flow": flow, "ironing_speed": speed},
        )
        for label, flow, speed in zip(
            "ABCDEFGHI",
            (12, 15, 18, 12, 15, 18, 12, 15, 18),
            (10, 10, 10, 15, 15, 15, 20, 20, 20),
            strict=True,
        )
    )


def _request(**changes):
    values = {
        "samples": _samples(),
        "plate_code": "7K3P9D",
        "printable_polygon": ((0, 0), (220, 0), (220, 220), (0, 220)),
    }
    values.update(changes)
    return PlateLayoutRequest(**values)


class PlateLayoutTests(unittest.TestCase):
    def test_nine_samples_get_deterministic_a_to_i_positions_inside_plate_bounds(self):
        request = _request()

        layout = layout_plate(request)

        self.assertEqual(tuple(item.label for item in layout.sample_placements), tuple("ABCDEFGHI"))
        self.assertEqual(tuple(item.candidate_id for item in layout.sample_placements), tuple(f"candidate-{letter}" for letter in "ABCDEFGHI"))
        self.assertEqual(
            tuple((item.x_mm, item.y_mm) for item in layout.sample_placements[:3]),
            ((10.0, 10.0), (48.0, 10.0), (86.0, 10.0)),
        )
        self.assertLess(layout.bounds.min_x, layout.sample_placements[0].x_mm)
        self.assertLess(layout.bounds.min_y, layout.sample_placements[0].y_mm)
        self.assertGreater(layout.bounds.max_x, layout.sample_placements[-1].x_mm + 30.0)
        self.assertGreater(layout.bounds.max_y, layout.sample_placements[-1].y_mm + 30.0)
        self.assertEqual(layout.plate_code, "7K3P9D")

    def test_plate_fits_tight_rectangular_and_large_beds_and_irregular_printable_area(self):
        request = _request()
        layout = layout_plate(request)
        tight = (
            (layout.bounds.min_x, layout.bounds.min_y),
            (layout.bounds.max_x, layout.bounds.min_y),
            (layout.bounds.max_x, layout.bounds.max_y),
            (layout.bounds.min_x, layout.bounds.max_y),
        )

        exact_fit = layout_plate(_request(printable_polygon=tight))
        large_bed = layout_plate(_request(printable_polygon=((0, 0), (1000, 0), (1000, 1000), (0, 1000))))
        irregular = layout_plate(_request(printable_polygon=(
            (0, 0), (220, 0), (220, 220), (170, 220), (170, 180), (150, 180), (150, 220), (0, 220),
        )))

        self.assertEqual(exact_fit.bounds, layout.bounds)
        self.assertEqual(large_bed.sample_placements, layout.sample_placements)
        self.assertEqual(irregular.sample_placements, layout.sample_placements)

    def test_rejects_a_keep_out_crossing_a_sample_and_connector(self):
        with self.assertRaisesRegex(PlateLayoutError, "keep-out"):
            layout_plate(_request(keep_outs=(((38, 20), (44, 20), (44, 26), (38, 26)),)))

    def test_rejects_bed_too_small_for_full_plate_and_label_overhang(self):
        with self.assertRaisesRegex(PlateLayoutError, "printable area"):
            layout_plate(_request(printable_polygon=((0, 0), (100, 0), (100, 100), (0, 100))))

    def test_rejects_duplicate_or_out_of_order_sample_labels(self):
        samples = list(_samples())
        samples[1] = PlateSampleRequest("A", "candidate-B", {"ironing_flow": 15, "ironing_speed": 10})
        with self.assertRaisesRegex(PlateLayoutError, "labels"):
            layout_plate(_request(samples=tuple(samples)))

    def test_rejects_bad_plate_code_and_too_narrow_breakaway_connector(self):
        with self.assertRaisesRegex(PlateLayoutError, "six uppercase letters or digits"):
            layout_plate(_request(plate_code="bad-code"))
        with self.assertRaisesRegex(PlateLayoutError, "connector"):
            layout_plate(_request(connector_width_mm=0.4))

    def test_accepts_a_valid_code_with_six_digits(self):
        self.assertEqual(layout_plate(_request(plate_code="234567")).plate_code, "234567")

    def test_rejects_non_finite_dimensions_and_self_intersecting_bed_polygon(self):
        with self.assertRaisesRegex(PlateLayoutError, "finite positive"):
            layout_plate(_request(specimen_height_mm=float("nan")))
        with self.assertRaisesRegex(PlateLayoutError, "printable polygon"):
            layout_plate(_request(printable_polygon=((0, 0), (100, 100), (0, 100), (100, 0))))


if __name__ == "__main__":
    unittest.main()
