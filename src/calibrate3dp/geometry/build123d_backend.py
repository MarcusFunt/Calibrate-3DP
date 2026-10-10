"""Pinned build123d recipes for labeled connected ironing plates.

This module is imported only after explicit backend selection. The standard
library voxel backend remains available for immutable schema-v1 runs.
"""

from __future__ import annotations

import hashlib
import math
from pathlib import Path
import platform
import sys
from typing import Any, Iterable

from build123d import Align, Axis, Box, FontStyle, Text, extrude
from importlib import metadata

from calibrate3dp.geometry.contracts import (
    GeometryConnection,
    GeometryValidation,
    PlateGeometry,
    PlateObject,
    Point3,
    TriangleMesh,
)
from calibrate3dp.geometry.layout import (
    PlateLayoutError,
    PlateLayoutRequest,
    Rect2D,
    layout_plate,
)
from calibrate3dp.geometry.font_asset import FONT_ASSET_ID, FONT_RELATIVE_PATH, FONT_SHA256, FONT_STYLE
from calibrate3dp.geometry.specs import GeometryRecipeSpec


BUILD123D_BACKEND_ID = "build123d"
BUILD123D_BACKEND_VERSION = "1"
BUILD123D_RUNTIME_VERSION = "0.13.0"
OCP_RUNTIME_VERSION = "8.0.1.1.0"
FONT_PATH = Path(__file__).resolve().parents[1] / FONT_RELATIVE_PATH
MESH_LINEAR_TOLERANCE_MM = 0.05
MESH_ANGULAR_TOLERANCE_RAD = 0.1
VERTEX_WELD_TOLERANCE_MM = 1e-7
_SUPPORTED_GLYPHS = frozenset("ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-")


class GeometryFontError(PlateLayoutError):
    """Raised when the pinned font is missing, altered, or cannot render text."""


class Build123dPlateGeometryBackend:
    backend_id = BUILD123D_BACKEND_ID
    backend_version = BUILD123D_BACKEND_VERSION

    def __init__(
        self,
        *,
        font_path: str | Path = FONT_PATH,
        expected_font_sha256: str = FONT_SHA256,
    ) -> None:
        self.font_path = Path(font_path).resolve(strict=False)
        self.expected_font_sha256 = expected_font_sha256

    def build(self, request: PlateLayoutRequest) -> PlateGeometry:
        if (request.geometry_backend, request.geometry_backend_version) != (self.backend_id, self.backend_version):
            raise PlateLayoutError("build123d backend requires build123d@1 layout inputs")
        font_sha256 = self._require_font()
        recipe_spec = GeometryRecipeSpec.from_layout_options({
            "geometry_backend": request.geometry_backend,
            "geometry_backend_version": request.geometry_backend_version,
            "identifier_corner": request.identifier_corner,
            "label_pocket_width_mm": request.label_pocket_width_mm,
            "label_pocket_depth_mm": request.label_pocket_depth_mm,
            "label_pocket_depth_z_mm": request.label_pocket_depth_z_mm,
            "label_text_size_mm": request.label_text_size_mm,
            "identifier_width_mm": request.identifier_width_mm,
            "identifier_depth_mm": request.identifier_depth_mm,
            "identifier_thickness_mm": request.identifier_thickness_mm,
            "identifier_text_size_mm": request.identifier_text_size_mm,
            "identifier_relief_mm": request.identifier_relief_mm,
            "identifier_tab_width_mm": request.identifier_tab_width_mm,
            "mesh_linear_tolerance_mm": request.mesh_linear_tolerance_mm,
            "mesh_angular_tolerance_rad": request.mesh_angular_tolerance_rad,
            "vertex_weld_tolerance_mm": request.vertex_weld_tolerance_mm,
            "recipe_id": request.recipe_id,
            "recipe_version": request.recipe_version,
            "layout_version": request.layout_version,
            "font_asset_id": FONT_ASSET_ID,
            "font_relative_path": FONT_RELATIVE_PATH,
            "font_sha256": font_sha256,
            "font_style": FONT_STYLE,
        })
        layout = layout_plate(request)
        sample_objects: list[PlateObject] = []
        for placement, sample in zip(layout.sample_placements, request.samples, strict=True):
            shape = Box(
                placement.width_mm,
                placement.depth_mm,
                placement.height_mm,
                align=(Align.MIN, Align.MIN, Align.MIN),
            ).translate((placement.x_mm, placement.y_mm, 0))
            sample_connectors = [
                item for item in layout.connectors if item.sample_label == sample.label
            ]
            for connector in sample_connectors:
                shape = shape + self._box_for_region(
                    connector.rectangle,
                    0.4,
                    0.4 + connector.height_mm,
                )
            pocket_region = dict(layout.label_regions)[sample.label]
            pocket = self._box_for_region(
                pocket_region,
                0.0,
                request.label_pocket_depth_z_mm,
            )
            shape = shape - pocket
            text_solid, text_bounds = self._underside_label(
                sample.label,
                center=(
                    (pocket_region.min_x + pocket_region.max_x) / 2,
                    (pocket_region.min_y + pocket_region.max_y) / 2,
                ),
                pocket_depth_mm=request.label_pocket_depth_z_mm,
                size_mm=request.label_text_size_mm,
            )
            shape = shape + text_solid
            _require_one_valid_solid(shape, f"Sample-{sample.label}")
            mesh = self._mesh(shape, f"Sample-{sample.label}", recipe_spec.tessellation)
            sample_objects.append(PlateObject(
                name=f"Sample-{sample.label}",
                mesh=mesh,
                sample_label=sample.label,
                candidate_id=sample.candidate_id,
                settings=sample.settings,
                printed_marking=sample.label,
                role="sample",
                metadata={
                    "label_mode": "underside-pocketed-emboss",
                    "label_text": sample.label,
                    "label_position_mm": [pocket_region.min_x, pocket_region.min_y],
                    "pocket_bounds_mm": [pocket_region.min_x, pocket_region.min_y, pocket_region.max_x, pocket_region.max_y],
                    "text_bounds_mm": list(text_bounds),
                    "label_orientation": "readable-from-below; non-mirrored",
                    "label_orientation_matrix": [[1, 0, 0], [0, -1, 0], [0, 0, -1]],
                    "label_view_direction": [0, 0, -1],
                    "font_asset_id": FONT_ASSET_ID,
                    "font_relative_path": FONT_RELATIVE_PATH,
                    "font_sha256": font_sha256,
                    "font_style": FONT_STYLE,
                    "label_text_size_mm": request.label_text_size_mm,
                    "pocket_depth_mm": request.label_pocket_depth_z_mm,
                    "pocket_width_mm": request.label_pocket_width_mm,
                    "pocket_depth_xy_mm": request.label_pocket_depth_mm,
                    "label_bottom_z_mm": 0.0,
                    "label_top_z_mm": request.label_pocket_depth_z_mm + 0.1,
                    "solid_count": len(shape.solids()),
                },
            ))

        frame_shape = self._union(
            self._box_for_region(rail, 0.0, 1.2)
            for rail in layout.frame_rails
        )
        _require_one_valid_solid(frame_shape, "Plate-Frame")
        frame_mesh = self._mesh(frame_shape, "Plate-Frame", recipe_spec.tessellation)
        frame = PlateObject(
            "Plate-Frame", frame_mesh, None, None, {}, "", role="frame",
            metadata={"solid_count": len(frame_shape.solids()), "code_on_frame": False},
        )

        identifier_shape, identifier_text_bounds = self._build_identifier(request, layout, font_sha256)
        _require_one_valid_solid(identifier_shape, "Plate-Identifier")
        identifier_mesh = self._mesh(identifier_shape, "Plate-Identifier", recipe_spec.tessellation)
        identifier = PlateObject(
            "Plate-Identifier", identifier_mesh, None, None, {}, layout.plate_code,
            role="identifier",
            metadata={
                "corner": layout.identifier_corner,
                "text": layout.plate_code,
                "text_side": "top",
                "text_bounds_mm": list(identifier_text_bounds),
                "plaque_bounds_mm": [
                    layout.identifier_region.min_x,
                    layout.identifier_region.min_y,
                    layout.identifier_region.max_x,
                    layout.identifier_region.max_y,
                ],
                "thickness_mm": request.identifier_thickness_mm,
                "relief_mm": request.identifier_relief_mm,
                "text_top_z_mm": request.identifier_thickness_mm + request.identifier_relief_mm,
                "font_asset_id": FONT_ASSET_ID,
                "font_relative_path": FONT_RELATIVE_PATH,
                "font_sha256": font_sha256,
                "font_style": FONT_STYLE,
                "text_size_mm": request.identifier_text_size_mm,
                "solid_count": len(identifier_shape.solids()),
            },
        )
        connections = tuple(
            GeometryConnection(
                sample_label=sample.label,
                target_name="Plate-Frame",
                contact_area_mm2=sum(
                    item.contact_area_mm2
                    for item in layout.connectors
                    if item.sample_label == sample.label
                ),
                source_name=f"Sample-{sample.label}",
            )
            for sample in request.samples
        ) + tuple(
            GeometryConnection(
                sample_label=None,
                target_name="Plate-Frame",
                contact_area_mm2=request.identifier_tab_width_mm * min(request.identifier_thickness_mm, 1.2),
                source_name="Plate-Identifier",
            )
            for _ in layout.identifier_tabs
        )
        geometry = PlateGeometry(
            layout=layout,
            backend_id=self.backend_id,
            backend_version=self.backend_version,
            objects=(*sample_objects, frame, identifier),
            connections=connections,
            metadata={
                "recipe_id": recipe_spec.recipe_id,
                "recipe_version": recipe_spec.recipe_version,
                "layout_version": recipe_spec.layout_version,
                "backend_id": self.backend_id,
                "backend_version": self.backend_version,
                "build123d_version": metadata.version("build123d"),
                "ocp_distribution": "cadquery-ocp-novtk",
                "ocp_version": metadata.version("cadquery-ocp-novtk"),
                "python_version": platform.python_version(),
                "platform": platform.platform(),
                "font_asset_id": FONT_ASSET_ID,
                "font_relative_path": FONT_RELATIVE_PATH,
                "font_sha256": font_sha256,
                "mesh_linear_tolerance_mm": recipe_spec.tessellation.linear_tolerance_mm,
                "mesh_angular_tolerance_rad": recipe_spec.tessellation.angular_tolerance_rad,
                "vertex_weld_tolerance_mm": recipe_spec.tessellation.vertex_weld_tolerance_mm,
                "recipe_spec": recipe_spec.to_dict(),
                "identifier_corner": layout.identifier_corner,
                "label_mode": recipe_spec.label.mode,
                "needs_physical_validation": True,
                "print_ready": False,
            },
        )
        report = self.validate(geometry)
        if not report.valid:
            raise PlateLayoutError("generated build123d geometry failed validation: " + "; ".join(report.messages))
        return geometry

    def validate(self, geometry: PlateGeometry) -> GeometryValidation:
        messages: list[str] = []
        if not isinstance(geometry, PlateGeometry):
            return GeometryValidation(False, ("plate geometry artifact is missing",))
        if (geometry.backend_id, geometry.backend_version) != (self.backend_id, self.backend_version):
            messages.append("geometry backend identity is unsupported")
        samples = [item for item in geometry.objects if item.sample_label is not None]
        labels = tuple(item.sample_label for item in samples)
        expected_labels = tuple("ABCDEFGHI"[:len(samples)])
        expected_names = tuple(f"Sample-{label}" for label in expected_labels) + ("Plate-Frame", "Plate-Identifier")
        if not 1 <= len(samples) <= 9 or labels != expected_labels:
            messages.append("geometry must preserve one to nine ordered sample objects starting at A")
        if tuple(item.name for item in geometry.objects) != expected_names:
            messages.append("geometry must contain ordered sample, frame, and identifier objects")
        placements = {item.label: item for item in geometry.layout.sample_placements}
        for sample in samples:
            placement = placements.get(sample.sample_label)
            if (
                sample.name != f"Sample-{sample.sample_label}"
                or sample.printed_marking != sample.sample_label
                or sample.role != "sample"
                or not sample.candidate_id
                or not sample.settings
                or placement is None
                or placement.candidate_id != sample.candidate_id
            ):
                messages.append(f"{sample.name} sample identity or settings mapping is invalid")
            if sample.metadata.get("label_mode") != "underside-pocketed-emboss":
                messages.append(f"{sample.name} has no declared underside label geometry")
            if sample.metadata.get("font_sha256") != self.expected_font_sha256:
                messages.append(f"{sample.name} font identity does not match the pinned asset")
        frame = [item for item in geometry.objects if item.name == "Plate-Frame"]
        identifiers = [item for item in geometry.objects if item.name == "Plate-Identifier"]
        if len(frame) != 1 or frame[0].role != "frame" or frame[0].printed_marking:
            messages.append("geometry must have one unmarked frame object")
        if len(identifiers) != 1:
            messages.append("geometry must have exactly one separate Plate-Identifier object")
        elif (
            identifiers[0].role != "identifier"
            or identifiers[0].printed_marking != geometry.layout.plate_code
            or identifiers[0].metadata.get("corner") != geometry.layout.identifier_corner
            or identifiers[0].metadata.get("text_side") != "top"
        ):
            messages.append("identifier plaque code, corner, or top-side mapping is invalid")
        bounds = geometry.layout.bounds
        for obj in geometry.objects:
            if not obj.mesh.is_watertight():
                messages.append(f"{obj.name} mesh is not watertight")
            mesh_bounds = obj.mesh.bounds
            if mesh_bounds is None:
                messages.append(f"{obj.name} mesh is empty")
            elif mesh_bounds[2] < -1e-6:
                messages.append(f"{obj.name} has geometry below the bed plane")
            elif (
                mesh_bounds[0] < bounds.min_x - 1e-5
                or mesh_bounds[1] < bounds.min_y - 1e-5
                or mesh_bounds[2] < bounds.min_z - 1e-5
                or mesh_bounds[3] > bounds.max_x + 1e-5
                or mesh_bounds[4] > bounds.max_y + 1e-5
                or mesh_bounds[5] > bounds.max_z + 1e-5
            ):
                messages.append(f"{obj.name} mesh exceeds the complete validated plate bounds")
        if len(geometry.connections) != len(samples) + 2:
            messages.append("sample and identifier breakaway connection records are incomplete")
        else:
            for label in expected_labels:
                if not any(
                    item.sample_label == label
                    and item.source_name == f"Sample-{label}"
                    and item.target_name == "Plate-Frame"
                    and math.isfinite(item.contact_area_mm2)
                    and item.contact_area_mm2 > 0
                    for item in geometry.connections
                ):
                    messages.append(f"Sample-{label} has no declared frame connection")
            plaque_links = [item for item in geometry.connections if item.source_name == "Plate-Identifier"]
            if len(plaque_links) != 2 or any(
                item.target_name != "Plate-Frame" or item.sample_label is not None
                or not math.isfinite(item.contact_area_mm2) or item.contact_area_mm2 <= 0
                for item in plaque_links
            ):
                messages.append("identifier plaque must have two declared frame links")
        if geometry.metadata.get("needs_physical_validation") is not True or geometry.metadata.get("print_ready") is not False:
            messages.append("CAD geometry must preserve physical validation and print readiness warnings")
        return GeometryValidation(not messages, tuple(messages))

    def _require_font(self) -> str:
        if not self.font_path.is_file():
            raise GeometryFontError(f"pinned geometry font is missing: {self.font_path}")
        digest = _sha256(self.font_path)
        if digest != self.expected_font_sha256:
            raise GeometryFontError(
                f"geometry font SHA-256 mismatch for {self.font_path.name}: expected {self.expected_font_sha256}, found {digest}"
            )
        return digest

    def _text(self, text: str, size_mm: float):
        unsupported = sorted(set(text) - _SUPPORTED_GLYPHS)
        if unsupported:
            raise GeometryFontError("unsupported geometry text glyph(s): " + ", ".join(unsupported))
        try:
            return Text(text, size_mm, font_path=str(self.font_path), font_style=FontStyle.REGULAR)
        except Exception as exc:
            raise GeometryFontError(f"pinned font could not outline {text!r}: {exc}") from exc

    def _underside_label(self, label: str, *, center: tuple[float, float], pocket_depth_mm: float, size_mm: float):
        outline = self._text(label, size_mm)
        bounds = outline.bounding_box()
        center_x = (bounds.min.X + bounds.max.X) / 2
        center_y = (bounds.min.Y + bounds.max.Y) / 2
        overlap = min(0.1, pocket_depth_mm / 4)
        relief = extrude(outline, amount=pocket_depth_mm + overlap)
        relief_solid = (
            relief.translate((-center_x, -center_y, 0))
            .rotate(Axis.X, 180)
            .translate((center[0], center[1], pocket_depth_mm + overlap))
        )
        text_bounds = (
            center[0] - (bounds.max.X - bounds.min.X) / 2,
            center[1] - (bounds.max.Y - bounds.min.Y) / 2,
            center[0] + (bounds.max.X - bounds.min.X) / 2,
            center[1] + (bounds.max.Y - bounds.min.Y) / 2,
        )
        return relief_solid, text_bounds

    def _build_identifier(self, request: PlateLayoutRequest, layout, font_sha256: str):
        region = layout.identifier_region
        if region is None:
            raise PlateLayoutError("build123d layout has no identifier region")
        shape = self._box_for_region(region, 0.0, request.identifier_thickness_mm)
        for tab in layout.identifier_tabs:
            shape = shape + self._box_for_region(tab, 0.0, request.identifier_thickness_mm)
        outline = self._text(layout.plate_code, request.identifier_text_size_mm)
        bounds = outline.bounding_box()
        width = bounds.max.X - bounds.min.X
        height = bounds.max.Y - bounds.min.Y
        if width > region.max_x - region.min_x or height > region.max_y - region.min_y:
            raise GeometryFontError("plate identifier code does not fit the declared plaque dimensions")
        center_x = (region.min_x + region.max_x) / 2
        center_y = (region.min_y + region.max_y) / 2
        text_bounds = (
            center_x - width / 2,
            center_y - height / 2,
            center_x + width / 2,
            center_y + height / 2,
        )
        text = outline.translate((center_x - (bounds.min.X + bounds.max.X) / 2,
                                  center_y - (bounds.min.Y + bounds.max.Y) / 2,
                                  request.identifier_thickness_mm - 0.1))
        raised = extrude(text, amount=request.identifier_relief_mm + 0.1)
        return shape + raised, text_bounds

    def _mesh(self, shape, name: str, tessellation) -> TriangleMesh:
        vertices, triangles = shape.tessellate(
            tessellation.linear_tolerance_mm,
            tessellation.angular_tolerance_rad,
        )
        coordinate_to_id: dict[tuple[int, int, int], int] = {}
        points: list[Point3] = []
        indexed: list[tuple[int, int, int]] = []
        for triangle in triangles:
            indices: list[int] = []
            for raw in triangle:
                vertex = vertices[raw]
                values = (float(vertex.X), float(vertex.Y), float(vertex.Z))
                key = tuple(round(value / tessellation.vertex_weld_tolerance_mm) for value in values)
                if key not in coordinate_to_id:
                    coordinate_to_id[key] = len(points)
                    points.append(Point3(*(round(value, 7) for value in values)))
                indices.append(coordinate_to_id[key])
            if len(set(indices)) != 3:
                raise PlateLayoutError(f"{name} tessellation contains a degenerate triangle after vertex welding")
            indexed.append(tuple(indices))
        mesh = TriangleMesh(tuple(points), tuple(indexed))
        if not mesh.is_watertight():
            raise PlateLayoutError(f"{name} tessellation is not a closed oriented mesh")
        return mesh

    @staticmethod
    def _box_for_region(region: Rect2D, min_z: float, max_z: float):
        return Box(
            region.max_x - region.min_x,
            region.max_y - region.min_y,
            max_z - min_z,
            align=(Align.MIN, Align.MIN, Align.MIN),
        ).translate((region.min_x, region.min_y, min_z))

    @staticmethod
    def _union(shapes: Iterable[Any]):
        iterator = iter(shapes)
        try:
            result = next(iterator)
        except StopIteration as exc:
            raise PlateLayoutError("cannot union an empty shape set") from exc
        for item in iterator:
            result = result + item
        return result


def _require_one_valid_solid(shape, name: str) -> None:
    if not shape.is_valid:
        raise PlateLayoutError(f"{name} CAD shape is invalid")
    if len(shape.solids()) != 1:
        raise PlateLayoutError(f"{name} must be one connected solid; found {len(shape.solids())}")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()
