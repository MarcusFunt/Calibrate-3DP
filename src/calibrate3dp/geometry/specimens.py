"""Dependency-free watertight voxel meshes for the connected calibration plate."""

from __future__ import annotations

import math
from typing import Any

from calibrate3dp.geometry.layout import PlateLayout, PlateLayoutError, PlateLayoutRequest, layout_plate
from calibrate3dp.geometry.contracts import (
    GeometryConnection,
    GeometryValidation,
    PlateGeometry,
    PlateObject,
    Point3,
    TriangleMesh,
)


class PlateGeometryBackend:
    """Build separate sample meshes plus one shared frame, using 0.4 mm voxels."""

    backend_id = "stdlib-voxel"
    backend_version = "1"

    def build(self, request: PlateLayoutRequest) -> PlateGeometry:
        layout = layout_plate(request)
        objects: list[PlateObject] = []
        cell = layout.voxel_mm
        for placement, sample in zip(layout.sample_placements, request.samples, strict=True):
            occupied: set[tuple[int, int, int]] = set()
            _add_box(
                occupied, cell,
                placement.x_mm, placement.y_mm, 0,
                placement.x_mm + placement.width_mm, placement.y_mm + placement.depth_mm, placement.height_mm,
            )
            _add_sample_label(occupied, cell, placement, sample.label)
            for connector in (item for item in layout.connectors if item.sample_label == sample.label):
                _add_box(
                    occupied, cell,
                    connector.rectangle.min_x, connector.rectangle.min_y, 0.4,
                    connector.rectangle.max_x, connector.rectangle.max_y, 0.4 + connector.height_mm,
                )
            objects.append(PlateObject(
                name=f"Sample-{sample.label}",
                mesh=_mesh_from_voxels(occupied, cell),
                sample_label=sample.label,
                candidate_id=sample.candidate_id,
                settings=sample.settings,
                printed_marking=sample.label,
            ))

        frame_voxels: set[tuple[int, int, int]] = set()
        for rail in layout.frame_rails:
            _add_box(frame_voxels, cell, rail.min_x, rail.min_y, 0, rail.max_x, rail.max_y, 1.2)
        for character, region in layout.code_regions:
            _add_code_support(frame_voxels, cell, layout.code_support_region)
            _add_glyph(frame_voxels, cell, region.min_x, region.min_y, 0.4, character, request.code_pixel_mm)
        objects.append(PlateObject(
            name="Plate-Frame",
            mesh=_mesh_from_voxels(frame_voxels, cell),
            sample_label=None,
            candidate_id=None,
            settings={},
            printed_marking=layout.plate_code,
        ))
        connections = tuple(
            GeometryConnection(
                sample_label=sample.label,
                target_name="Plate-Frame",
                contact_area_mm2=sum(
                    connector.contact_area_mm2
                    for connector in layout.connectors
                    if connector.sample_label == sample.label
                ),
            )
            for sample in request.samples
        )
        geometry = PlateGeometry(
            layout=layout,
            backend_id=self.backend_id,
            backend_version=self.backend_version,
            objects=tuple(objects),
            connections=connections,
        )
        report = self.validate(geometry)
        if not report.valid:
            raise PlateLayoutError("generated geometry failed validation: " + "; ".join(report.messages))
        return geometry

    def validate(self, geometry: PlateGeometry) -> GeometryValidation:
        messages: list[str] = []
        if not isinstance(geometry, PlateGeometry):
            return GeometryValidation(False, ("plate geometry artifact is missing",))
        if geometry.backend_id != self.backend_id or geometry.backend_version != self.backend_version:
            messages.append("geometry backend identity is unsupported")
        sample_objects = [obj for obj in geometry.objects if obj.sample_label is not None]
        labels = tuple(obj.sample_label for obj in sample_objects)
        expected_labels = tuple("ABCDEFGHI"[:len(sample_objects)])
        if not 1 <= len(sample_objects) <= 9 or labels != expected_labels:
            messages.append("geometry must preserve one to nine ordered sample objects starting at A")
        expected_names = tuple(f"Sample-{label}" for label in expected_labels) + ("Plate-Frame",)
        if tuple(obj.name for obj in geometry.objects) != expected_names:
            messages.append("geometry object names and ordering do not match the samples and shared frame")
        placement_labels = tuple(item.label for item in geometry.layout.sample_placements)
        if placement_labels != expected_labels:
            messages.append("layout must preserve the ordered sample placements")
        if len({obj.candidate_id for obj in sample_objects}) != len(sample_objects):
            messages.append("sample object candidate mapping is missing or ambiguous")
        placements = {item.label: item for item in geometry.layout.sample_placements}
        samples_by_label = {obj.sample_label: obj for obj in sample_objects}
        for obj in geometry.objects:
            if not obj.mesh.is_watertight():
                messages.append(f"{obj.name} mesh is not watertight")
            if obj.sample_label is not None:
                if obj.name != f"Sample-{obj.sample_label}" or obj.printed_marking != obj.sample_label:
                    messages.append(f"{obj.name} label geometry mapping is invalid")
                if not obj.candidate_id or not obj.settings:
                    messages.append(f"{obj.name} has no candidate settings mapping")
                placement = placements.get(obj.sample_label)
                if placement is None or placement.candidate_id != obj.candidate_id:
                    messages.append(f"{obj.name} candidate does not match its layout placement")
            elif obj.name != "Plate-Frame" or obj.printed_marking != geometry.layout.plate_code:
                messages.append("plate frame has no matching physical code mapping")
        if len([obj for obj in geometry.objects if obj.name == "Plate-Frame"]) != 1:
            messages.append("geometry must contain exactly one shared frame object")
        expected_connections = tuple(
            GeometryConnection(
                sample_label=label,
                target_name="Plate-Frame",
                contact_area_mm2=sum(item.contact_area_mm2 for item in geometry.layout.connectors if item.sample_label == label),
            )
            for label in expected_labels
        )
        if geometry.connections != expected_connections or any(
            not math.isfinite(item.contact_area_mm2) or item.contact_area_mm2 < 1.0
            for item in geometry.connections
        ):
            messages.append("sample-to-frame breakaway connections are invalid")
        if placement_labels == expected_labels:
            for label in expected_labels:
                sample = samples_by_label.get(label)
                placement = placements[label]
                if sample is None:
                    continue
                expected_mesh = _expected_sample_mesh(geometry.layout, placement, label)
                if sample.mesh != expected_mesh:
                    messages.append(f"{sample.name} mesh does not match the labeled specimen and connector layout")
            frame_objects = [obj for obj in geometry.objects if obj.name == "Plate-Frame"]
            if len(frame_objects) == 1:
                expected_frame = _expected_frame_mesh(geometry.layout)
                if frame_objects[0].mesh != expected_frame:
                    messages.append("Plate-Frame mesh does not match the rail and printed-code layout")
        bounds = geometry.layout.bounds
        for obj in geometry.objects:
            obj_bounds = obj.mesh.bounds
            if obj_bounds is None:
                messages.append(f"{obj.name} mesh is empty")
            elif (
                obj_bounds[0] < bounds.min_x - 1e-6 or obj_bounds[1] < bounds.min_y - 1e-6 or obj_bounds[2] < bounds.min_z - 1e-6
                or obj_bounds[3] > bounds.max_x + 1e-6 or obj_bounds[4] > bounds.max_y + 1e-6 or obj_bounds[5] > bounds.max_z + 1e-6
            ):
                messages.append(f"{obj.name} mesh exceeds validated plate bounds")
        return GeometryValidation(not messages, tuple(messages))


def _expected_sample_mesh(layout: PlateLayout, placement: Any, label: str) -> TriangleMesh:
    occupied: set[tuple[int, int, int]] = set()
    cell = layout.voxel_mm
    _add_box(
        occupied, cell,
        placement.x_mm, placement.y_mm, 0,
        placement.x_mm + placement.width_mm, placement.y_mm + placement.depth_mm, placement.height_mm,
    )
    _add_sample_label(occupied, cell, placement, label)
    for connector in (item for item in layout.connectors if item.sample_label == label):
        _add_box(
            occupied, cell,
            connector.rectangle.min_x, connector.rectangle.min_y, 0.4,
            connector.rectangle.max_x, connector.rectangle.max_y, 0.4 + connector.height_mm,
        )
    return _mesh_from_voxels(occupied, cell)


def _expected_frame_mesh(layout: PlateLayout) -> TriangleMesh:
    occupied: set[tuple[int, int, int]] = set()
    cell = layout.voxel_mm
    for rail in layout.frame_rails:
        _add_box(occupied, cell, rail.min_x, rail.min_y, 0, rail.max_x, rail.max_y, 1.2)
    for character, region in layout.code_regions:
        _add_code_support(occupied, cell, layout.code_support_region)
        _add_glyph(
            occupied, cell, region.min_x, region.min_y, 0.4,
            character, (region.max_x - region.min_x) / 4,
        )
    return _mesh_from_voxels(occupied, cell)


def _grid(value: float, cell: float) -> int:
    units = value / cell
    rounded = round(units)
    if abs(units - rounded) > 1e-7:
        raise ValueError(f"geometry coordinate {value!r} is not aligned to voxel {cell!r}")
    return int(rounded)


def _add_box(
    occupied: set[tuple[int, int, int]], cell: float,
    min_x: float, min_y: float, min_z: float,
    max_x: float, max_y: float, max_z: float,
) -> None:
    x0, y0, z0 = _grid(min_x, cell), _grid(min_y, cell), _grid(min_z, cell)
    x1, y1, z1 = _grid(max_x, cell), _grid(max_y, cell), _grid(max_z, cell)
    occupied.update((x, y, z) for x in range(x0, x1) for y in range(y0, y1) for z in range(z0, z1))


_GLYPHS: dict[str, tuple[str, ...]] = {
    "0": ("1111", "1001", "1001", "1001", "1001", "1111"),
    "1": ("0010", "0110", "0010", "0010", "0010", "0111"),
    "2": ("1111", "0001", "0010", "0100", "1000", "1111"),
    "3": ("1111", "0001", "0110", "0001", "0001", "1111"),
    "4": ("1001", "1001", "1111", "0001", "0001", "0001"),
    "5": ("1111", "1000", "1110", "0001", "0001", "1110"),
    "6": ("0111", "1000", "1110", "1001", "1001", "0110"),
    "7": ("1111", "0001", "0010", "0100", "0100", "0100"),
    "8": ("0110", "1001", "0110", "1001", "1001", "0110"),
    "9": ("0110", "1001", "1001", "0111", "0001", "1110"),
    "A": ("0110", "1001", "1001", "1111", "1001", "1001"),
    "B": ("1110", "1001", "1110", "1001", "1001", "1110"),
    "C": ("0111", "1000", "1000", "1000", "1000", "0111"),
    "D": ("1110", "1001", "1001", "1001", "1001", "1110"),
    "E": ("1111", "1000", "1110", "1000", "1000", "1111"),
    "F": ("1111", "1000", "1110", "1000", "1000", "1000"),
    "G": ("0111", "1000", "1000", "1011", "1001", "0111"),
    "H": ("1001", "1001", "1111", "1001", "1001", "1001"),
    "I": ("1111", "0110", "0010", "0010", "0110", "1111"),
    "J": ("0011", "0001", "0001", "0001", "1001", "0110"),
    "K": ("1001", "1010", "1100", "1100", "1010", "1001"),
    "L": ("1000", "1000", "1000", "1000", "1000", "1111"),
    "M": ("1001", "1111", "1111", "1001", "1001", "1001"),
    "N": ("1001", "1101", "1101", "1011", "1011", "1001"),
    "O": ("0110", "1001", "1001", "1001", "1001", "0110"),
    "P": ("1110", "1001", "1001", "1110", "1000", "1000"),
    "Q": ("0110", "1001", "1001", "1011", "0110", "0001"),
    "R": ("1110", "1001", "1001", "1110", "1010", "1001"),
    "S": ("0111", "1000", "0110", "0001", "0001", "1110"),
    "T": ("1111", "0110", "0010", "0010", "0010", "0010"),
    "U": ("1001", "1001", "1001", "1001", "1001", "0110"),
    "V": ("1001", "1001", "1001", "1001", "0110", "0110"),
    "W": ("1001", "1001", "1001", "1111", "1111", "1001"),
    "X": ("1001", "1001", "0110", "0110", "1001", "1001"),
    "Y": ("1001", "1001", "0110", "0010", "0010", "0010"),
    "Z": ("1111", "0001", "0010", "0100", "1000", "1111"),
}


def _add_glyph(
    occupied: set[tuple[int, int, int]], cell: float,
    min_x: float, front_y: float, base_z: float, character: str, pixel_mm: float,
) -> None:
    rows = _GLYPHS.get(character)
    if rows is None:
        raise ValueError(f"no printable bitmap glyph for {character!r}")
    scale = _grid(pixel_mm, cell)
    x0, y0, z0 = _grid(min_x, cell), _grid(front_y, cell), _grid(base_z, cell)
    pixels = {(row, column) for row, pattern in enumerate(rows) for column, filled in enumerate(pattern) if filled == "1"}
    # A voxel union whose pixels meet only at corners is non-manifold. Bridge
    # diagonal-only neighbors with one deterministic orthogonal pixel first.
    while True:
        bridge: tuple[int, int] | None = None
        for row, column in sorted(pixels):
            for next_row, next_column in ((row + 1, column + 1), (row + 1, column - 1)):
                if (next_row, next_column) in pixels and 0 <= next_column < 4 and next_row < len(rows):
                    if (row, next_column) not in pixels and (next_row, column) not in pixels:
                        bridge = (row, next_column)
                        break
            if bridge is not None:
                break
        if bridge is None:
            break
        pixels.add(bridge)
    for row, column in pixels:
        for dx in range(scale):
            for dz in range(scale):
                occupied.add((x0 + column * scale + dx, y0, z0 + (len(rows) - row - 1) * scale + dz))


def _add_sample_label(occupied: set[tuple[int, int, int]], cell: float, placement: Any, label: str) -> None:
    width = 3.2
    _add_glyph(
        occupied, cell,
        round((placement.x_mm + (placement.width_mm - width) / 2) / cell) * cell,
        placement.y_mm - cell,
        0.8,
        label,
        0.8,
    )


def _add_code_support(occupied: set[tuple[int, int, int]], cell: float, region: Any) -> None:
    # The thin support plaque makes every raised bitmap glyph physically tied
    # into the shared front rail while leaving a legible raised relief.
    _add_box(occupied, cell, region.min_x, region.min_y, 0.4, region.max_x, region.max_y, 5.2)


def _mesh_from_voxels(occupied: set[tuple[int, int, int]], cell: float) -> TriangleMesh:
    if not occupied:
        return TriangleMesh((), ())
    vertex_ids: dict[tuple[int, int, int], int] = {}
    vertices: list[Point3] = []
    triangles: list[tuple[int, int, int]] = []

    def vertex_id(point: tuple[int, int, int]) -> int:
        index = vertex_ids.get(point)
        if index is None:
            index = len(vertices)
            vertex_ids[point] = index
            vertices.append(Point3(point[0] * cell, point[1] * cell, point[2] * cell))
        return index

    faces = (
        ((-1, 0, 0), lambda x, y, z: ((x, y, z), (x, y, z + 1), (x, y + 1, z + 1), (x, y + 1, z))),
        ((1, 0, 0), lambda x, y, z: ((x + 1, y, z), (x + 1, y + 1, z), (x + 1, y + 1, z + 1), (x + 1, y, z + 1))),
        ((0, -1, 0), lambda x, y, z: ((x, y, z), (x + 1, y, z), (x + 1, y, z + 1), (x, y, z + 1))),
        ((0, 1, 0), lambda x, y, z: ((x, y + 1, z), (x, y + 1, z + 1), (x + 1, y + 1, z + 1), (x + 1, y + 1, z))),
        ((0, 0, -1), lambda x, y, z: ((x, y, z), (x, y + 1, z), (x + 1, y + 1, z), (x + 1, y, z))),
        ((0, 0, 1), lambda x, y, z: ((x, y, z + 1), (x + 1, y, z + 1), (x + 1, y + 1, z + 1), (x, y + 1, z + 1))),
    )
    for x, y, z in sorted(occupied):
        for (dx, dy, dz), corners_for_face in faces:
            if (x + dx, y + dy, z + dz) in occupied:
                continue
            corners = corners_for_face(x, y, z)
            quad = tuple(vertex_id(point) for point in corners)
            triangles.extend(((quad[0], quad[1], quad[2]), (quad[0], quad[2], quad[3])))
    return TriangleMesh(tuple(vertices), tuple(triangles))
