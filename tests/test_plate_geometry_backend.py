from __future__ import annotations

from dataclasses import replace
from pathlib import Path
import struct
from tempfile import TemporaryDirectory
import unittest

from calibrate3dp.geometry.layout import PlateLayoutRequest, PlateSampleRequest
from calibrate3dp.geometry.specimens import PlateGeometryBackend, TriangleMesh


def _request():
    samples = tuple(
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
    return PlateLayoutRequest(
        samples=samples,
        plate_code="7K3P9D",
        printable_polygon=((0, 0), (220, 0), (220, 220), (0, 220)),
    )


class PlateGeometryBackendTests(unittest.TestCase):
    def test_builds_nine_manifold_sample_objects_and_connected_marked_frame(self):
        backend = PlateGeometryBackend()
        geometry = backend.build(_request())

        self.assertEqual(geometry.backend_id, "stdlib-voxel")
        self.assertEqual(geometry.backend_version, "1")
        self.assertEqual(len(geometry.objects), 10)
        samples = {obj.sample_label: obj for obj in geometry.objects if obj.sample_label is not None}
        self.assertEqual(tuple(samples), tuple("ABCDEFGHI"))
        expected_flows = dict(zip("ABCDEFGHI", (12, 15, 18, 12, 15, 18, 12, 15, 18), strict=True))
        for label, obj in samples.items():
            self.assertEqual(obj.printed_marking, label)
            self.assertEqual(obj.candidate_id, f"candidate-{label}")
            self.assertEqual(obj.settings["ironing_flow"], expected_flows[label])
            self.assertTrue(obj.mesh.is_watertight(), label)
            self.assertGreater(len(obj.mesh.triangles), 12)
            placement = next(item for item in geometry.layout.sample_placements if item.label == label)
            self.assertLess(obj.mesh.bounds[1], placement.y_mm)
            links = [item for item in geometry.layout.connectors if item.sample_label == label]
            self.assertEqual(len(links), 8)
            self.assertTrue(all((item.width_mm, item.height_mm, item.contact_area_mm2) == (2.4, 0.8, 1.92) for item in links))
        frame = next(obj for obj in geometry.objects if obj.name == "Plate-Frame")
        self.assertEqual(frame.printed_marking, "7K3P9D")
        self.assertIsNone(frame.sample_label)
        self.assertTrue(frame.mesh.is_watertight())
        self.assertEqual(frame.mesh.bounds[1], geometry.layout.bounds.min_y)
        self.assertEqual(len(geometry.connections), 9)
        self.assertTrue(all(connection.target_name == "Plate-Frame" for connection in geometry.connections))
        self.assertTrue(all(connection.contact_area_mm2 >= 1.0 for connection in geometry.connections))
        self.assertTrue(backend.validate(geometry).valid)

    def test_meshes_and_layout_are_repeatable_for_identical_versioned_request(self):
        backend = PlateGeometryBackend()
        first = backend.build(_request())
        second = backend.build(_request())

        self.assertEqual(first.layout, second.layout)
        self.assertEqual(first.objects, second.objects)
        self.assertEqual(first.connections, second.connections)

    def test_rejects_a_non_manifold_mesh_instead_of_returning_valid_geometry(self):
        backend = PlateGeometryBackend()
        geometry = backend.build(_request())
        first = geometry.objects[0]
        broken_mesh = replace(first.mesh, triangles=first.mesh.triangles[:-1])
        broken_object = replace(first, mesh=broken_mesh)
        broken_geometry = replace(geometry, objects=(broken_object, *geometry.objects[1:]))

        report = backend.validate(broken_geometry)

        self.assertFalse(report.valid)
        self.assertTrue(any("watertight" in item.casefold() for item in report.messages))

    def test_rejects_a_watertight_replacement_mesh_missing_specimen_label_and_connectors(self):
        backend = PlateGeometryBackend()
        geometry = backend.build(_request())
        tetrahedron = TriangleMesh(
            vertices=((10, 10, 0), (10.4, 10, 0), (10, 10.4, 0), (10, 10, 0.4)),
            triangles=((0, 2, 1), (0, 1, 3), (0, 3, 2), (1, 2, 3)),
        )
        self.assertTrue(tetrahedron.is_watertight())
        broken = replace(geometry.objects[0], mesh=tetrahedron)
        report = backend.validate(replace(geometry, objects=(broken, *geometry.objects[1:])))

        self.assertFalse(report.valid)
        self.assertTrue(any("does not match" in item.casefold() for item in report.messages))

    def test_rejects_a_sample_mesh_reused_at_another_sample_placement(self):
        backend = PlateGeometryBackend()
        geometry = backend.build(_request())
        second = replace(geometry.objects[1], mesh=geometry.objects[0].mesh)
        report = backend.validate(replace(geometry, objects=(geometry.objects[0], second, *geometry.objects[2:])))

        self.assertFalse(report.valid)
        self.assertTrue(any("does not match" in item.casefold() for item in report.messages))

    def test_rejects_empty_or_degenerate_triangle_mesh(self):
        backend = PlateGeometryBackend()
        malformed = TriangleMesh(
            vertices=((0.0, 0.0, 0.0), (1.0, 0.0, 0.0), (2.0, 0.0, 0.0)),
            triangles=((0, 1, 2),),
        )

        self.assertFalse(malformed.is_watertight())
        with self.assertRaises(ValueError):
            malformed.require_watertight()

    def test_rejects_inconsistently_oriented_closed_mesh(self):
        tetrahedron = TriangleMesh(
            vertices=((0, 0, 0), (1, 0, 0), (0, 1, 0), (0, 0, 1)),
            triangles=((0, 2, 1), (0, 1, 3), (0, 3, 2), (1, 2, 3)),
        )
        self.assertTrue(tetrahedron.is_watertight())
        reversed_face = replace(tetrahedron, triangles=(*tetrahedron.triangles[:-1], (1, 3, 2)))
        self.assertFalse(reversed_face.is_watertight())

    def test_exports_a_watertight_sample_mesh_as_binary_stl(self):
        geometry = PlateGeometryBackend().build(_request())
        mesh = geometry.objects[0].mesh
        with TemporaryDirectory() as directory:
            path = mesh.write_binary_stl(Path(directory) / "sample-a.stl", solid_name="Sample-A")
            payload = path.read_bytes()

        self.assertEqual(len(payload), 84 + 50 * len(mesh.triangles))
        self.assertEqual(payload[80:84], struct.pack("<I", len(mesh.triangles)))
        self.assertIn(b"Sample-A", payload[:80])


if __name__ == "__main__":
    unittest.main()
