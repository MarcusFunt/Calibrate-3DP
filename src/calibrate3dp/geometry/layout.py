"""Validated, deterministic layout for a connected nine-sample plate."""

from __future__ import annotations

from dataclasses import dataclass
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

    @property
    def feature_regions(self) -> tuple[Rect2D, ...]:
        bodies = tuple(
            Rect2D(p.x_mm, p.y_mm, p.x_mm + p.width_mm, p.y_mm + p.depth_mm)
            for p in self.sample_placements
        )
        return (
            *bodies,
            *self.frame_rails,
            *(connector.rectangle for connector in self.connectors),
            *(region for _label, region in self.label_regions),
            self.code_support_region,
            *(region for _character, region in self.code_regions),
        )


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
    for feature in layout.feature_regions:
        translated = Rect2D(
            feature.min_x + dx, feature.min_y + dy,
            feature.max_x + dx, feature.max_y + dy,
        )
        if not _rect_inside_polygon(translated, polygon):
            raise PlateLayoutError("connected plate exceeds the selected printable area")
        if any(_rect_intersects_polygon(translated, keepout) for keepout in obstacles):
            raise PlateLayoutError("connected plate intersects a keep-out")


def layout_plate(request: PlateLayoutRequest) -> PlateLayout:
    """Place nine labeled samples, a code rail, and identical breakaway tabs."""
    if not isinstance(request, PlateLayoutRequest):
        raise PlateLayoutError("request must be a PlateLayoutRequest")
    if len(request.samples) != 9 or tuple(item.label for item in request.samples) != tuple("ABCDEFGHI"):
        raise PlateLayoutError("sample labels must be the nine ordered labels A through I")
    if any(not isinstance(item.candidate_id, str) or not item.candidate_id.strip() for item in request.samples):
        raise PlateLayoutError("sample candidate IDs must be unique non-empty strings")
    if len({item.candidate_id for item in request.samples}) != 9:
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

    pitch_x = request.specimen_width_mm + request.gap_mm
    pitch_y = request.specimen_depth_mm + request.gap_mm
    start_x = request.margin_mm + request.frame_width_mm + request.connector_gap_mm
    start_y = start_x
    placements = tuple(
        SamplePlacement(
            label=sample.label,
            candidate_id=sample.candidate_id,
            row=index // 3,
            column=index % 3,
            x_mm=start_x + (index % 3) * pitch_x,
            y_mm=start_y + (index // 3) * pitch_y,
            width_mm=request.specimen_width_mm,
            depth_mm=request.specimen_depth_mm,
            height_mm=request.specimen_height_mm,
        )
        for index, sample in enumerate(request.samples)
    )
    edge_coordinates = (
        request.margin_mm,
        *(start_x + column * pitch_x + request.specimen_width_mm + request.connector_gap_mm for column in range(2)),
        start_x + 2 * pitch_x + request.specimen_width_mm + request.connector_gap_mm,
    )
    # The first and last rails align to the outer margin; internal rails occupy
    # the center of each 8 mm gap, leaving equal-length 2.8 mm breakaway tabs.
    vertical_rails = tuple(
        Rect2D(
            (request.margin_mm if index == 0 else edge_coordinates[index]),
            request.margin_mm,
            (request.margin_mm + request.frame_width_mm if index == 0 else edge_coordinates[index] + request.frame_width_mm),
            _outer_edge(start_y, request.specimen_depth_mm, pitch_y, request.connector_gap_mm, request.frame_width_mm),
        )
        for index in range(4)
    )
    # Explicit horizontal bands use the same x extent as the complete frame.
    min_x = request.margin_mm
    max_x = _outer_edge(start_x, request.specimen_width_mm, pitch_x, request.connector_gap_mm, request.frame_width_mm)
    min_y = request.margin_mm
    max_y = _outer_edge(start_y, request.specimen_depth_mm, pitch_y, request.connector_gap_mm, request.frame_width_mm)
    horizontal_rails = tuple(
        Rect2D(
            min_x,
            (request.margin_mm if index == 0 else edge_coordinates[index]),
            max_x,
            (request.margin_mm + request.frame_width_mm if index == 0 else edge_coordinates[index] + request.frame_width_mm),
        )
        for index in range(4)
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
        findings=("Nine independent sample objects; connectors terminate at the shared frame surface.",),
    )
    require_layout_fits_printable_area(layout, request.printable_polygon, request.keep_outs)
    return layout


def _outer_edge(start: float, dimension: float, pitch: float, clearance: float, rail_width: float) -> float:
    return start + 2 * pitch + dimension + clearance + rail_width


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
