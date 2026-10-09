"""Small deterministic geometry primitives for calibration plates.

The first connected-plate backend uses standard-library voxel construction so
the desktop application does not need a general CAD runtime dependency.
"""

from calibrate3dp.geometry.layout import (
    PlateBounds,
    PlateLayout,
    PlateLayoutError,
    PlateLayoutRequest,
    PlateSampleRequest,
    layout_plate,
)
from calibrate3dp.geometry.specimens import (
    GeometryConnection,
    GeometryValidation,
    PlateGeometry,
    PlateGeometryBackend,
    PlateObject,
    Point3,
    TriangleMesh,
)

__all__ = [
    "GeometryConnection",
    "GeometryValidation",
    "PlateBounds",
    "PlateGeometry",
    "PlateGeometryBackend",
    "PlateLayout",
    "PlateLayoutError",
    "PlateLayoutRequest",
    "PlateObject",
    "PlateSampleRequest",
    "Point3",
    "TriangleMesh",
    "layout_plate",
]
