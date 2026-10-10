"""Backend-neutral mesh, plate, and geometry validation contracts."""

from __future__ import annotations

from dataclasses import dataclass, field
import math
from pathlib import Path
import struct
from types import MappingProxyType
from typing import Any, Mapping, Protocol, TYPE_CHECKING

if TYPE_CHECKING:
    from calibrate3dp.geometry.layout import PlateLayout, PlateLayoutRequest


@dataclass(frozen=True, order=True)
class Point3:
    x: float
    y: float
    z: float


@dataclass(frozen=True)
class TriangleMesh:
    """Immutable triangle mesh. Tuple vertices are accepted for small fixtures."""

    vertices: tuple[Point3, ...]
    triangles: tuple[tuple[int, int, int], ...]

    def __post_init__(self) -> None:
        converted = tuple(
            vertex if isinstance(vertex, Point3) else Point3(float(vertex[0]), float(vertex[1]), float(vertex[2]))
            for vertex in self.vertices
        )
        triangles = tuple(tuple(int(index) for index in triangle) for triangle in self.triangles)
        object.__setattr__(self, "vertices", converted)
        object.__setattr__(self, "triangles", triangles)

    @property
    def bounds(self) -> tuple[float, float, float, float, float, float] | None:
        if not self.vertices:
            return None
        return (
            min(point.x for point in self.vertices), min(point.y for point in self.vertices), min(point.z for point in self.vertices),
            max(point.x for point in self.vertices), max(point.y for point in self.vertices), max(point.z for point in self.vertices),
        )

    def is_watertight(self) -> bool:
        if not self.vertices or not self.triangles or any(
            not math.isfinite(value)
            for point in self.vertices
            for value in (point.x, point.y, point.z)
        ):
            return False
        edge_counts: dict[tuple[int, int], int] = {}
        edge_directions: dict[tuple[int, int], int] = {}
        for triangle in self.triangles:
            if len(triangle) != 3 or len(set(triangle)) != 3 or any(
                index < 0 or index >= len(self.vertices) for index in triangle
            ):
                return False
            a, b, c = (self.vertices[index] for index in triangle)
            ab = (b.x - a.x, b.y - a.y, b.z - a.z)
            ac = (c.x - a.x, c.y - a.y, c.z - a.z)
            cross = (
                ab[1] * ac[2] - ab[2] * ac[1],
                ab[2] * ac[0] - ab[0] * ac[2],
                ab[0] * ac[1] - ab[1] * ac[0],
            )
            if sum(value * value for value in cross) <= 1e-20:
                return False
            for start, end in ((triangle[0], triangle[1]), (triangle[1], triangle[2]), (triangle[2], triangle[0])):
                edge = (start, end) if start < end else (end, start)
                edge_counts[edge] = edge_counts.get(edge, 0) + 1
                edge_directions[edge] = edge_directions.get(edge, 0) + (1 if start < end else -1)
        return bool(edge_counts) and all(
            count == 2 and edge_directions[edge] == 0
            for edge, count in edge_counts.items()
        )

    def require_watertight(self) -> None:
        if not self.is_watertight():
            raise ValueError("triangle mesh is empty, degenerate, or not watertight")

    def write_binary_stl(self, destination: str | Path, *, solid_name: str = "Calibrate-3DP") -> Path:
        """Write a binary STL without an external mesh or CAD package."""
        self.require_watertight()
        path = Path(destination).expanduser().resolve(strict=False)
        if path.suffix.casefold() != ".stl":
            raise ValueError("STL destination must use the .stl extension")
        if path.exists():
            raise FileExistsError(path)
        header = solid_name.encode("ascii", errors="replace")[:80].ljust(80, b" ")
        path.parent.mkdir(parents=True, exist_ok=True)
        try:
            with path.open("xb") as stream:
                stream.write(header)
                stream.write(struct.pack("<I", len(self.triangles)))
                for first, second, third in self.triangles:
                    a, b, c = (self.vertices[index] for index in (first, second, third))
                    ab = (b.x - a.x, b.y - a.y, b.z - a.z)
                    ac = (c.x - a.x, c.y - a.y, c.z - a.z)
                    normal = (
                        ab[1] * ac[2] - ab[2] * ac[1],
                        ab[2] * ac[0] - ab[0] * ac[2],
                        ab[0] * ac[1] - ab[1] * ac[0],
                    )
                    length = math.sqrt(sum(value * value for value in normal))
                    if length:
                        normal = tuple(value / length for value in normal)
                    stream.write(struct.pack(
                        "<12fH",
                        *normal,
                        a.x, a.y, a.z, b.x, b.y, b.z, c.x, c.y, c.z,
                        0,
                    ))
        except Exception:
            path.unlink(missing_ok=True)
            raise
        return path


@dataclass(frozen=True)
class PlateObject:
    name: str
    mesh: TriangleMesh
    sample_label: str | None
    candidate_id: str | None
    settings: Mapping[str, Any]
    printed_marking: str
    role: str = "object"
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "settings", MappingProxyType(dict(self.settings)))
        object.__setattr__(self, "metadata", MappingProxyType(dict(self.metadata)))


@dataclass(frozen=True)
class GeometryConnection:
    sample_label: str | None
    target_name: str
    contact_area_mm2: float
    source_name: str | None = None
    separation_type: str = "breakaway-tab"


@dataclass(frozen=True)
class GeometryValidation:
    valid: bool
    messages: tuple[str, ...]


@dataclass(frozen=True)
class PlateGeometry:
    layout: PlateLayout
    backend_id: str
    backend_version: str
    objects: tuple[PlateObject, ...]
    connections: tuple[GeometryConnection, ...]
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "metadata", MappingProxyType(dict(self.metadata)))


class GeometryBackend(Protocol):
    backend_id: str
    backend_version: str

    def build(self, request: PlateLayoutRequest) -> PlateGeometry: ...

    def validate(self, geometry: PlateGeometry) -> GeometryValidation: ...
