"""Validated, deterministic layout for connected calibration sample plates."""

from __future__ import annotations

from dataclasses import dataclass, replace
import math
from types import MappingProxyType
from typing import Any, Mapping


class PlateLayoutError(ValueError):
    """Raised when the selected bed or request cannot fit a safe layout."""


@dataclass(frozen=True)
class PlateSampleRequest:
    label: str
    candidate_id: str
    settings: Mapping[str, Any]

    def __post_init__(self) -> None:
        if not isinstance(self.settings, Mapping):
            raise PlateLayoutError("each sample must have a settings mapping")
        object.__setattr__(self, "settings", MappingProxyType(dict(self.settings)))


@dataclass(frozen=True)
class Rect2D:
    min_x: float
    min_y: float
    max_x: float
    max_y: float

    @property
    def corners(self) -> tuple[tuple[float, float], ...]:
        return (
            (self.min_x, self.min_y), (self.max_x, self.min_y),
            (self.max_x, self.max_y), (self.min_x, self.max_y),
        )


@dataclass(frozen=True)
class SamplePlacement:
    label: str
    candidate_id: str
    row: int
    column: int
    x_mm: float
    y_mm: float
    width_mm: float
    depth_mm: float
    height_mm: float


@dataclass(frozen=True)
class PlateBounds:
    min_x: float
    min_y: float
    min_z: float
    max_x: float
    max_y: float
    max_z: float


@dataclass(frozen=True)
class ConnectorPlacement:
    sample_label: str
    rectangle: Rect2D
    orientation: str
    width_mm: float
    height_mm: float
    contact_area_mm2: float


@dataclass(frozen=True)
class PlateLayout:
    plate_code: str
    sample_placements: tuple[SamplePlacement, ...]
    frame_rails: tuple[Rect2D, ...]
    connectors: tuple[ConnectorPlacement, ...]
    label_regions: tuple[tuple[str, Rect2D], ...]
    code_support_region: Rect2D
    code_regions: tuple[tuple[str, Rect2D], ...]
    bounds: PlateBounds
    voxel_mm: float
    findings: tuple[str, ...]
    identifier_region: Rect2D | None = None
    identifier_corner: str | None = None
    identifier_tabs: tuple[Rect2D, ...] = ()

    @property
    def named_feature_regions(self) -> tuple[tuple[str, Rect2D], ...]:
        bodies = tuple(
            (f"Sample-{p.label}", Rect2D(p.x_mm, p.y_mm, p.x_mm + p.width_mm, p.y_mm + p.depth_mm))
            for p in self.sample_placements
        )
        rails = tuple(
            (f"Plate-Frame rail {index + 1}", region)
            for index, region in enumerate(self.frame_rails)
        )
        connectors = tuple(
            (f"Sample-{item.sample_label} connector {item.orientation}", item.rectangle)
            for item in self.connectors
        )
        labels = tuple(
            (f"Sample-{label} label area", region)
            for label, region in self.label_regions
        )
        if self.identifier_region is not None:
            return (
                *bodies,
                *rails,
                *connectors,
                *labels,
                ("Plate-Identifier plaque", self.identifier_region),
                *(
                    (f"Plate-Identifier attachment tab {index + 1}", region)
                    for index, region in enumerate(self.identifier_tabs)
                ),
            )
        return (
            *bodies,
            *rails,
            *connectors,
            *labels,
            ("Plate-Frame code support", self.code_support_region),
            *(
                (f"Plate-Frame code glyph {character}", region)
                for character, region in self.code_regions
            ),
        )

    @property
    def feature_regions(self) -> tuple[Rect2D, ...]:
        return tuple(region for _name, region in self.named_feature_regions)


@dataclass(frozen=True)
class PlateLayoutRequest:
    samples: tuple[PlateSampleRequest, ...]
    plate_code: str
    printable_polygon: tuple[tuple[float, float], ...]
    keep_outs: tuple[tuple[tuple[float, float], ...], ...] = ()
    margin_mm: float = 4.8
    specimen_width_mm: float = 30.0
    specimen_depth_mm: float = 30.0
    specimen_height_mm: float = 6.4
    gap_mm: float = 8.0
    connector_width_mm: float = 2.4
    connector_height_mm: float = 0.8
    connector_gap_mm: float = 2.8
    frame_width_mm: float = 2.4
    voxel_mm: float = 0.4
    label_pixel_mm: float = 0.8
    code_pixel_mm: float = 0.8
    geometry_backend: str = "stdlib-voxel"
    geometry_backend_version: str = "1"
    identifier_corner: str = "front_left"
    label_pocket_width_mm: float = 12.0
    label_pocket_depth_mm: float = 8.0
    label_pocket_depth_z_mm: float = 0.8
    label_text_size_mm: float = 5.2
    identifier_width_mm: float = 42.0
    identifier_depth_mm: float = 14.0
    identifier_thickness_mm: float = 1.6
    identifier_text_size_mm: float = 4.5
    identifier_relief_mm: float = 0.5
    identifier_tab_width_mm: float = 1.2
    mesh_linear_tolerance_mm: float = 0.05
    mesh_angular_tolerance_rad: float = 0.1
    vertex_weld_tolerance_mm: float = 1e-7
    recipe_id: str = "ironing.flat_coupon"
    recipe_version: str = "2"
    layout_version: str = "2"

    def __post_init__(self) -> None:
        object.__setattr__(self, "samples", tuple(self.samples))
        object.__setattr__(self, "printable_polygon", _points(self.printable_polygon))
        object.__setattr__(self, "keep_outs", tuple(_points(polygon) for polygon in self.keep_outs))


def require_layout_fits_printable_area(
    layout: PlateLayout,
    printable_polygon: tuple[tuple[float, float], ...],
    keep_outs: tuple[tuple[tuple[float, float], ...], ...] = (),
    *,
    translation_mm: tuple[float, float] = (0.0, 0.0),
) -> None:
    """Fail closed if any plate feature crosses a bed edge or keep-out."""
    if not isinstance(layout, PlateLayout):
        raise PlateLayoutError("connected plate layout is required")
    polygon = _points(printable_polygon)
    _validate_polygon(polygon, "printable polygon")
    obstacles = tuple(_points(points) for points in keep_outs)
    for keepout in obstacles:
        _validate_polygon(keepout, "keep-out polygon")
    try:
        dx, dy = (float(value) for value in translation_mm)
    except (TypeError, ValueError) as exc:
        raise PlateLayoutError("layout translation must contain two finite numbers") from exc
    if not math.isfinite(dx) or not math.isfinite(dy):
        raise PlateLayoutError("layout translation must contain two finite numbers")
    for feature_name, feature in layout.named_feature_regions:
        translated = Rect2D(
            feature.min_x + dx, feature.min_y + dy,
            feature.max_x + dx, feature.max_y + dy,
        )
        if not _rect_inside_polygon(translated, polygon):
            raise PlateLayoutError(
                f"connected plate feature {feature_name} exceeds the selected printable area"
            )
        if any(_rect_intersects_polygon(translated, keepout) for keepout in obstacles):
            raise PlateLayoutError(f"connected plate feature {feature_name} intersects a keep-out")


def layout_plate(request: PlateLayoutRequest) -> PlateLayout:
    """Place one to nine labeled samples, a code rail, and breakaway tabs."""
    if not isinstance(request, PlateLayoutRequest):
        raise PlateLayoutError("request must be a PlateLayoutRequest")
    labels = tuple("ABCDEFGHI"[:len(request.samples)])
    if not 1 <= len(request.samples) <= 9 or tuple(item.label for item in request.samples) != labels:
        raise PlateLayoutError("sample labels must be one to nine ordered labels starting with A")
    if any(not isinstance(item.candidate_id, str) or not item.candidate_id.strip() for item in request.samples):
        raise PlateLayoutError("sample candidate IDs must be unique non-empty strings")
    if len({item.candidate_id for item in request.samples}) != len(request.samples):
        raise PlateLayoutError("sample candidate IDs must be unique non-empty strings")
    if not isinstance(request.plate_code, str) or len(request.plate_code) != 6 or not request.plate_code.isascii() or not request.plate_code.isalnum() or not all(character in "ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789" for character in request.plate_code):
        raise PlateLayoutError("plate code must contain six uppercase letters or digits")
    _validate_polygon(request.printable_polygon, "printable polygon")
    for polygon in request.keep_outs:
        _validate_polygon(polygon, "keep-out polygon")

    positive_dimensions = (
        "margin_mm", "specimen_width_mm", "specimen_depth_mm", "specimen_height_mm",
        "gap_mm", "connector_width_mm", "connector_height_mm", "connector_gap_mm",
        "frame_width_mm", "voxel_mm", "label_pixel_mm", "code_pixel_mm",
    )
    for name in positive_dimensions:
        value = getattr(request, name)
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value <= 0:
            raise PlateLayoutError(f"{name} must be finite positive")
        units = value / request.voxel_mm
        if abs(units - round(units)) > 1e-8:
            raise PlateLayoutError(f"{name} must be an exact multiple of voxel_mm")
    for sample in request.samples:
        for setting in ("ironing_flow", "ironing_speed"):
            value = sample.settings.get(setting)
            try:
                number = float(str(value).removesuffix("%").strip())
            except (TypeError, ValueError) as exc:
                raise PlateLayoutError(f"sample {sample.label} {setting} must be finite positive") from exc
            if not math.isfinite(number) or number <= 0:
                raise PlateLayoutError(f"sample {sample.label} {setting} must be finite positive")
    if request.connector_width_mm < 0.8 or request.connector_height_mm < 0.4:
        raise PlateLayoutError("connector minimum is 0.8 mm wide and 0.4 mm high")
    if request.connector_gap_mm * 2 + request.frame_width_mm > request.gap_mm + 1e-8:
        raise PlateLayoutError("connector and frame widths do not fit the sample gap")

    columns = min(3, len(request.samples))
    rows = (len(request.samples) + columns - 1) // columns
    pitch_x = request.specimen_width_mm + request.gap_mm
    pitch_y = request.specimen_depth_mm + request.gap_mm
    start_x = request.margin_mm + request.frame_width_mm + request.connector_gap_mm
    start_y = start_x
    placements = tuple(
        SamplePlacement(
            label=sample.label,
            candidate_id=sample.candidate_id,
            row=index // columns,
            column=index % columns,
            x_mm=start_x + (index % columns) * pitch_x,
            y_mm=start_y + (index // columns) * pitch_y,
            width_mm=request.specimen_width_mm,
            depth_mm=request.specimen_depth_mm,
            height_mm=request.specimen_height_mm,
        )
        for index, sample in enumerate(request.samples)
    )
    x_edges = (
        request.margin_mm,
        *(start_x + column * pitch_x + request.specimen_width_mm + request.connector_gap_mm for column in range(columns - 1)),
        start_x + (columns - 1) * pitch_x + request.specimen_width_mm + request.connector_gap_mm,
    )
    y_edges = (
        request.margin_mm,
        *(start_y + row * pitch_y + request.specimen_depth_mm + request.connector_gap_mm for row in range(rows - 1)),
        start_y + (rows - 1) * pitch_y + request.specimen_depth_mm + request.connector_gap_mm,
    )
    # Outside rails align to the margin; internal rails sit in each sample gap.
    vertical_rails = tuple(
        Rect2D(
            x_edges[index],
            request.margin_mm,
            x_edges[index] + request.frame_width_mm,
            _outer_edge(start_y, request.specimen_depth_mm, pitch_y, request.connector_gap_mm, request.frame_width_mm, rows),
        )
        for index in range(columns + 1)
    )
    # Explicit horizontal bands use the same x extent as the complete frame.
    min_x = request.margin_mm
    max_x = _outer_edge(start_x, request.specimen_width_mm, pitch_x, request.connector_gap_mm, request.frame_width_mm, columns)
    min_y = request.margin_mm
    max_y = _outer_edge(start_y, request.specimen_depth_mm, pitch_y, request.connector_gap_mm, request.frame_width_mm, rows)
    horizontal_rails = tuple(
        Rect2D(
            min_x,
            y_edges[index],
            max_x,
            y_edges[index] + request.frame_width_mm,
        )
        for index in range(rows + 1)
    )
    # All rail boundaries are derived from the layout's intended 2.8 mm tab gap.
    # For a row/column, connect both sides and both front/back edges using the
    # same cross-section and two separated attachment points per side.
    connectors: list[ConnectorPlacement] = []
    row_rail_spans = tuple((rail.min_y, rail.max_y) for rail in horizontal_rails)
    col_rail_spans = tuple((rail.min_x, rail.max_x) for rail in vertical_rails)
    for sample in placements:
        left_gap = (col_rail_spans[sample.column][1], sample.x_mm)
        right_gap = (sample.x_mm + sample.width_mm, col_rail_spans[sample.column + 1][0])
        front_gap = (row_rail_spans[sample.row][1], sample.y_mm)
        back_gap = (sample.y_mm + sample.depth_mm, row_rail_spans[sample.row + 1][0])
        for side, (low, high) in (("left", left_gap), ("right", right_gap)):
            if high <= low:
                raise PlateLayoutError("connector geometry has no clearance between sample and frame")
            for offset in (4.0, request.specimen_depth_mm - 6.4):
                rect = Rect2D(low, sample.y_mm + offset, high, sample.y_mm + offset + request.connector_width_mm)
                connectors.append(_connector(sample.label, rect, side, request))
        for side, (low, high) in (("front", front_gap), ("back", back_gap)):
            if high <= low:
                raise PlateLayoutError("connector geometry has no clearance between sample and frame")
            for offset in (4.0, request.specimen_width_mm - 6.4):
                rect = Rect2D(sample.x_mm + offset, low, sample.x_mm + offset + request.connector_width_mm, high)
                connectors.append(_connector(sample.label, rect, side, request))

    label_regions = tuple(
        (
            p.label,
            Rect2D(
                round((p.x_mm + (p.width_mm - 3.2) / 2) / request.voxel_mm) * request.voxel_mm,
                p.y_mm - request.voxel_mm,
                round((p.x_mm + (p.width_mm - 3.2) / 2) / request.voxel_mm) * request.voxel_mm + 3.2,
                p.y_mm,
            ),
        )
        for p in placements
    )
    # The code is embossed on the front vertical face of the first rail. The
    # pixel glyphs span 58 grid cells at the default 0.8 mm pixel size.
    glyph_cells = 6 * 8 + 5 * 2
    code_width = glyph_cells * request.voxel_mm
    code_start = round(((min_x + max_x - code_width) / 2) / request.voxel_mm) * request.voxel_mm
    code_support = Rect2D(
        code_start - 4 * request.voxel_mm,
        min_y - request.voxel_mm,
        code_start + code_width + 4 * request.voxel_mm,
        min_y,
    )
    code_regions = tuple(
        (character, Rect2D(code_start + index * 10 * request.voxel_mm, min_y - 2 * request.voxel_mm,
                           code_start + index * 10 * request.voxel_mm + 8 * request.voxel_mm, min_y - request.voxel_mm))
        for index, character in enumerate(request.plate_code)
    )
    bounds = PlateBounds(
        min_x=min_x,
        min_y=min_y - 2 * request.voxel_mm,
        min_z=0.0,
        max_x=max_x,
        max_y=max_y,
        max_z=max(request.specimen_height_mm, request.connector_height_mm + 0.4),
    )
    layout = PlateLayout(
        plate_code=request.plate_code,
        sample_placements=placements,
        frame_rails=(*vertical_rails, *horizontal_rails),
        connectors=tuple(connectors),
        label_regions=label_regions,
        code_support_region=code_support,
        code_regions=code_regions,
        bounds=bounds,
        voxel_mm=request.voxel_mm,
        findings=(f"{len(labels)} labeled sample object(s); connectors terminate at the shared frame surface.",),
    )
    if request.geometry_backend == "build123d" and request.geometry_backend_version == "1":
        layout = _build123d_identifier_layout(layout, request)
    elif (request.geometry_backend, request.geometry_backend_version) != ("stdlib-voxel", "1"):
        raise PlateLayoutError("unsupported geometry backend or version")
    require_layout_fits_printable_area(layout, request.printable_polygon, request.keep_outs)
    return layout


def _build123d_identifier_layout(layout: PlateLayout, request: PlateLayoutRequest) -> PlateLayout:
    if (request.recipe_id, request.recipe_version, request.layout_version) != (
        "ironing.flat_coupon", "2", "2"
    ):
        raise PlateLayoutError("unsupported build123d recipe or layout version")
    corners = {"front_left", "front_right", "back_left", "back_right"}
    if request.identifier_corner not in corners:
        raise PlateLayoutError("identifier_corner must be front_left, front_right, back_left, or back_right")
    for name in (
        "label_pocket_width_mm", "label_pocket_depth_mm", "label_pocket_depth_z_mm",
        "label_text_size_mm", "identifier_width_mm", "identifier_depth_mm",
        "identifier_thickness_mm", "identifier_text_size_mm", "identifier_relief_mm",
        "identifier_tab_width_mm", "mesh_linear_tolerance_mm",
        "mesh_angular_tolerance_rad", "vertex_weld_tolerance_mm",
    ):
        value = getattr(request, name)
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value <= 0:
            raise PlateLayoutError(f"{name} must be finite positive")
    if request.label_pocket_width_mm + 4.0 > request.specimen_width_mm or request.label_pocket_depth_mm + 4.0 > request.specimen_depth_mm:
        raise PlateLayoutError("underside label pocket must leave at least 2 mm of specimen land on every side")
    frame_span = layout.bounds.max_x - layout.bounds.min_x
    effective_plaque_width = min(request.identifier_width_mm, frame_span - 2 * request.frame_width_mm)
    if effective_plaque_width < 20.0:
        raise PlateLayoutError("connected frame is too narrow for a readable identifier plaque")
    if request.identifier_tab_width_mm > effective_plaque_width / 3:
        raise PlateLayoutError("identifier attachment tabs are too wide for the plaque")

    # Reserve room on the front side by moving the complete specimen grid
    # inward. Back-side plaque placement uses the existing positive margin.
    dy = (
        request.identifier_depth_mm + request.frame_width_mm
        if request.identifier_corner.startswith("front_") else 0.0
    )

    def moved(rectangle: Rect2D) -> Rect2D:
        return Rect2D(rectangle.min_x, rectangle.min_y + dy, rectangle.max_x, rectangle.max_y + dy)

    placements = tuple(replace(item, y_mm=item.y_mm + dy) for item in layout.sample_placements)
    rails = tuple(moved(item) for item in layout.frame_rails)
    connectors = tuple(replace(item, rectangle=moved(item.rectangle)) for item in layout.connectors)
    min_x = min(item.min_x for item in rails)
    max_x = max(item.max_x for item in rails)
    min_y = min(item.min_y for item in rails)
    max_y = max(item.max_y for item in rails)
    if request.identifier_corner.endswith("left"):
        plaque_min_x = min_x + request.frame_width_mm
    else:
        plaque_min_x = max_x - request.frame_width_mm - effective_plaque_width
    plaque_max_x = plaque_min_x + effective_plaque_width
    if request.identifier_corner.startswith("front_"):
        plaque_max_y = min_y - request.frame_width_mm
        plaque_min_y = plaque_max_y - request.identifier_depth_mm
        tab_min_y, tab_max_y = plaque_max_y, min_y
    else:
        plaque_min_y = max_y + request.frame_width_mm
        plaque_max_y = plaque_min_y + request.identifier_depth_mm
        tab_min_y, tab_max_y = max_y, plaque_min_y
    identifier = Rect2D(plaque_min_x, plaque_min_y, plaque_max_x, plaque_max_y)
    offset = effective_plaque_width / 3
    tabs = tuple(
        Rect2D(
            plaque_min_x + index * offset + (offset - request.identifier_tab_width_mm) / 2,
            tab_min_y,
            plaque_min_x + index * offset + (offset + request.identifier_tab_width_mm) / 2,
            tab_max_y,
        )
        for index in (0, 2)
    )
    label_regions = tuple(
        (
            placement.label,
            Rect2D(
                placement.x_mm + (placement.width_mm - request.label_pocket_width_mm) / 2,
                placement.y_mm + (placement.depth_mm - request.label_pocket_depth_mm) / 2,
                placement.x_mm + (placement.width_mm + request.label_pocket_width_mm) / 2,
                placement.y_mm + (placement.depth_mm + request.label_pocket_depth_mm) / 2,
            ),
        )
        for placement in placements
    )
    updated_bounds = PlateBounds(
        min_x=min(min_x, identifier.min_x),
        min_y=min(min_y - dy, identifier.min_y),
        min_z=0.0,
        max_x=max(max_x, identifier.max_x),
        max_y=max(max_y, identifier.max_y),
        max_z=max(layout.bounds.max_z, request.identifier_thickness_mm + request.identifier_relief_mm),
    )
    updated = replace(
        layout,
        sample_placements=placements,
        frame_rails=rails,
        connectors=connectors,
        label_regions=label_regions,
        code_regions=(),
        code_support_region=identifier,
        bounds=updated_bounds,
        identifier_region=identifier,
        identifier_corner=request.identifier_corner,
        identifier_tabs=tabs,
        findings=(*layout.findings, "A separate top-labeled corner plaque is connected to the frame by two declared breakaway tabs."),
    )
    return updated


def _outer_edge(start: float, dimension: float, pitch: float, clearance: float, rail_width: float, count: int) -> float:
    return start + (count - 1) * pitch + dimension + clearance + rail_width


def _connector(label: str, rect: Rect2D, side: str, request: PlateLayoutRequest) -> ConnectorPlacement:
    return ConnectorPlacement(
        sample_label=label,
        rectangle=rect,
        orientation=side,
        width_mm=request.connector_width_mm,
        height_mm=request.connector_height_mm,
        contact_area_mm2=request.connector_width_mm * request.connector_height_mm,
    )


def _points(polygon: Any) -> tuple[tuple[float, float], ...]:
    try:
        points = tuple((float(point[0]), float(point[1])) for point in polygon)
    except (TypeError, ValueError, IndexError) as exc:
        raise PlateLayoutError("printable polygon and keep-outs need numeric coordinate pairs") from exc
    return points


def _validate_polygon(polygon: tuple[tuple[float, float], ...], name: str) -> None:
    if len(polygon) < 3 or any(not all(math.isfinite(value) for value in point) for point in polygon):
        raise PlateLayoutError(f"{name} must have at least three finite points")
    if len(set(polygon)) < 3 or abs(_signed_area(polygon)) <= 1e-8:
        raise PlateLayoutError(f"{name} must enclose a non-zero area")
    count = len(polygon)
    for first in range(count):
        a, b = polygon[first], polygon[(first + 1) % count]
        if math.dist(a, b) <= 1e-8:
            raise PlateLayoutError(f"{name} cannot contain zero-length edges")
        for second in range(first + 1, count):
            if second in {first, (first + 1) % count} or (second + 1) % count in {first, (first + 1) % count}:
                continue
            c, d = polygon[second], polygon[(second + 1) % count]
            if _segments_intersect(a, b, c, d):
                raise PlateLayoutError(f"{name} cannot self-intersect")


def _signed_area(points: tuple[tuple[float, float], ...]) -> float:
    return sum(a[0] * b[1] - b[0] * a[1] for a, b in zip(points, (*points[1:], points[0]), strict=True)) / 2


def _orientation(a: tuple[float, float], b: tuple[float, float], c: tuple[float, float]) -> float:
    return (b[0] - a[0]) * (c[1] - a[1]) - (b[1] - a[1]) * (c[0] - a[0])


def _on_segment(point: tuple[float, float], start: tuple[float, float], end: tuple[float, float]) -> bool:
    return abs(_orientation(start, end, point)) <= 1e-8 and min(start[0], end[0]) - 1e-8 <= point[0] <= max(start[0], end[0]) + 1e-8 and min(start[1], end[1]) - 1e-8 <= point[1] <= max(start[1], end[1]) + 1e-8


def _segments_intersect(a: tuple[float, float], b: tuple[float, float], c: tuple[float, float], d: tuple[float, float]) -> bool:
    ab_c, ab_d = _orientation(a, b, c), _orientation(a, b, d)
    cd_a, cd_b = _orientation(c, d, a), _orientation(c, d, b)
    if ((ab_c > 1e-8 and ab_d < -1e-8) or (ab_c < -1e-8 and ab_d > 1e-8)) and ((cd_a > 1e-8 and cd_b < -1e-8) or (cd_a < -1e-8 and cd_b > 1e-8)):
        return True
    return (
        (abs(ab_c) <= 1e-8 and _on_segment(c, a, b)) or
        (abs(ab_d) <= 1e-8 and _on_segment(d, a, b)) or
        (abs(cd_a) <= 1e-8 and _on_segment(a, c, d)) or
        (abs(cd_b) <= 1e-8 and _on_segment(b, c, d))
    )


def _point_in_polygon(point: tuple[float, float], polygon: tuple[tuple[float, float], ...]) -> bool:
    x, y = point
    inside = False
    previous = polygon[-1]
    for current in polygon:
        if _on_segment(point, previous, current):
            return True
        if (previous[1] > y) != (current[1] > y):
            crossing_x = (current[0] - previous[0]) * (y - previous[1]) / (current[1] - previous[1]) + previous[0]
            if x < crossing_x:
                inside = not inside
        previous = current
    return inside


def _rect_inside_polygon(rect: Rect2D, polygon: tuple[tuple[float, float], ...]) -> bool:
    corners = rect.corners
    if not all(_point_in_polygon(corner, polygon) for corner in corners):
        return False
    if any(rect.min_x + 1e-8 < x < rect.max_x - 1e-8 and rect.min_y + 1e-8 < y < rect.max_y - 1e-8 for x, y in polygon):
        return False
    edges = tuple(zip(corners, (*corners[1:], corners[0]), strict=True))
    polygon_edges = tuple(zip(polygon, (*polygon[1:], polygon[0]), strict=True))
    # A concave boundary crossing a feature is not caught by corner tests alone;
    # proper crossings catch it while allowing edges that share the bed border.
    return not any(_proper_segments_intersect(a, b, c, d) for a, b in edges for c, d in polygon_edges)


def _proper_segments_intersect(a: tuple[float, float], b: tuple[float, float], c: tuple[float, float], d: tuple[float, float]) -> bool:
    first, second = _orientation(a, b, c), _orientation(a, b, d)
    third, fourth = _orientation(c, d, a), _orientation(c, d, b)
    return ((first > 1e-8 and second < -1e-8) or (first < -1e-8 and second > 1e-8)) and ((third > 1e-8 and fourth < -1e-8) or (third < -1e-8 and fourth > 1e-8))


def _rect_intersects_polygon(rect: Rect2D, polygon: tuple[tuple[float, float], ...]) -> bool:
    corners = rect.corners
    if any(_point_in_polygon(corner, polygon) for corner in corners):
        return True
    if any(rect.min_x - 1e-8 <= x <= rect.max_x + 1e-8 and rect.min_y - 1e-8 <= y <= rect.max_y + 1e-8 for x, y in polygon):
        return True
    edges = tuple(zip(corners, (*corners[1:], corners[0]), strict=True))
    polygon_edges = tuple(zip(polygon, (*polygon[1:], polygon[0]), strict=True))
    return any(_segments_intersect(a, b, c, d) for a, b in edges for c, d in polygon_edges)
