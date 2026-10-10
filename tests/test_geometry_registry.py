from __future__ import annotations

from unittest.mock import patch
import unittest

from calibrate3dp.geometry.contracts import (
    GeometryBackend,
    GeometryConnection,
    GeometryValidation,
    PlateGeometry,
    PlateObject,
    Point3,
    TriangleMesh,
)
from calibrate3dp.geometry.registry import (
    GeometryBackendUnavailable,
    get_geometry_backend,
    is_geometry_backend_available,
)
from calibrate3dp.geometry.specimens import PlateGeometryBackend


class GeometryBackendRegistryTests(unittest.TestCase):
    def test_shared_contracts_keep_legacy_import_identity(self):
        from calibrate3dp.geometry import specimens

        self.assertIs(specimens.Point3, Point3)
        self.assertIs(specimens.TriangleMesh, TriangleMesh)
        self.assertIs(specimens.PlateObject, PlateObject)
        self.assertIs(specimens.GeometryConnection, GeometryConnection)
        self.assertIs(specimens.GeometryValidation, GeometryValidation)
        self.assertIs(specimens.PlateGeometry, PlateGeometry)
        self.assertTrue(getattr(GeometryBackend, "_is_protocol", False))

    def test_legacy_backend_requires_an_explicit_supported_version(self):
        backend = get_geometry_backend("stdlib-voxel", "1")

        self.assertIsInstance(backend, PlateGeometryBackend)
        self.assertEqual(backend.backend_id, "stdlib-voxel")
        self.assertEqual(backend.backend_version, "1")

    def test_unknown_backend_and_version_are_rejected_without_guessing(self):
        with self.assertRaisesRegex(GeometryBackendUnavailable, "unsupported"):
            get_geometry_backend("unknown", "1")
        with self.assertRaisesRegex(GeometryBackendUnavailable, "unsupported"):
            get_geometry_backend("stdlib-voxel", "2")

    def test_build123d_is_selected_only_when_the_pinned_runtime_is_available(self):
        if not is_geometry_backend_available("build123d", "1"):
            self.skipTest("install the optional cad extra to exercise build123d dispatch")

        backend = get_geometry_backend("build123d", "1")

        self.assertEqual(backend.backend_id, "build123d")
        self.assertEqual(backend.backend_version, "1")

    def test_missing_build123d_is_reported_and_never_falls_back_to_voxels(self):
        with patch("calibrate3dp.geometry.registry.util.find_spec", return_value=None):
            self.assertFalse(is_geometry_backend_available("build123d", "1"))
            with self.assertRaisesRegex(GeometryBackendUnavailable, "CAD backend unavailable"):
                get_geometry_backend("build123d", "1")


if __name__ == "__main__":
    unittest.main()
