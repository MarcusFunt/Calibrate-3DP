from __future__ import annotations

import hashlib
import importlib.util
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest


HAS_BUILD123D = importlib.util.find_spec("build123d") is not None


@unittest.skipUnless(HAS_BUILD123D, "install the optional cad extra to run build123d geometry tests")
class Build123dSmokeTests(unittest.TestCase):
    def test_pinned_font_asset_is_present_and_hash_checked(self):
        font_path = (
            Path(__file__).resolve().parents[1]
            / "src"
            / "calibrate3dp"
            / "assets"
            / "fonts"
            / "IBMPlexMono-SemiBold.ttf"
        )

        self.assertTrue(font_path.is_file(), "the explicit outline font asset is required")
        self.assertEqual(
            hashlib.sha256(font_path.read_bytes()).hexdigest(),
            "f04d7c488ddf7d1fa99f2574efc3406ea4cbe17bb1af3a1ab960f84d0c96a172",
        )

    def test_pinned_font_outlines_every_plate_code_glyph_and_rejects_unknowns(self):
        from calibrate3dp.geometry.build123d_backend import (
            Build123dPlateGeometryBackend,
            GeometryFontError,
        )

        backend = Build123dPlateGeometryBackend()
        outline = backend._text("ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-", 5.0)
        bounds = outline.bounding_box()
        self.assertGreater(bounds.max.X, bounds.min.X)
        self.assertGreater(bounds.max.Y, bounds.min.Y)
        with self.assertRaisesRegex(GeometryFontError, "unsupported geometry text glyph"):
            backend._text("R2F*", 5.0)

    def test_explicit_text_pocket_relief_tessellation_and_stl_export(self):
        from build123d import Align, Axis, Box, FontStyle, Text, export_stl, extrude

        font_path = (
            Path(__file__).resolve().parents[1]
            / "src"
            / "calibrate3dp"
            / "assets"
            / "fonts"
            / "IBMPlexMono-SemiBold.ttf"
        )
        body = Box(30, 30, 6.4, align=(Align.MIN, Align.MIN, Align.MIN))
        pocket_depth = 0.8
        pocket = Box(10, 9, pocket_depth, align=(Align.MIN, Align.MIN, Align.MIN)).translate((10, 10.5, 0))
        outline = Text("R2F", 7, font_path=str(font_path), font_style=FontStyle.REGULAR)
        relief = extrude(outline, amount=0.9).rotate(Axis.X, 180).translate((15, 15, 0.9))

        sample = (body - pocket) + relief
        self.assertTrue(sample.is_valid)
        self.assertEqual(len(sample.solids()), 1)
        self.assertAlmostEqual(sample.bounding_box().min.Z, 0.0, places=6)
        self.assertAlmostEqual(sample.bounding_box().max.Z, 6.4, places=6)
        vertices, triangles = sample.tessellate(0.05, 0.1)
        self.assertGreater(len(vertices), 0)
        self.assertGreater(len(triangles), 0)

        with TemporaryDirectory() as directory:
            destination = Path(directory) / "underside-label.stl"
            self.assertTrue(export_stl(sample, str(destination), tolerance=0.05, angular_tolerance=0.1))
            self.assertGreater(destination.stat().st_size, 84)


if __name__ == "__main__":
    unittest.main()
