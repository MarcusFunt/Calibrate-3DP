"""Explicit, versioned geometry backend selection with no implicit fallback."""

from __future__ import annotations

from importlib import metadata, util

from calibrate3dp.geometry.contracts import GeometryBackend


VOXEL_BACKEND_ID = "stdlib-voxel"
VOXEL_BACKEND_VERSION = "1"
BUILD123D_BACKEND_ID = "build123d"
BUILD123D_BACKEND_VERSION = "1"
BUILD123D_RUNTIME_VERSION = "0.13.0"
OCP_RUNTIME_VERSION = "8.0.1.1.0"


class GeometryBackendUnavailable(RuntimeError):
    """Raised when a requested, versioned geometry backend cannot be used."""


def is_geometry_backend_available(backend_id: str, backend_version: str) -> bool:
    if (backend_id, backend_version) == (VOXEL_BACKEND_ID, VOXEL_BACKEND_VERSION):
        return True
    if (backend_id, backend_version) != (BUILD123D_BACKEND_ID, BUILD123D_BACKEND_VERSION):
        return False
    if util.find_spec("build123d") is None or util.find_spec("OCP") is None:
        return False
    try:
        return (
            metadata.version("build123d") == BUILD123D_RUNTIME_VERSION
            and metadata.version("cadquery-ocp-novtk") == OCP_RUNTIME_VERSION
        )
    except metadata.PackageNotFoundError:
        return False


def get_geometry_backend(backend_id: str, backend_version: str) -> GeometryBackend:
    """Return the exact requested backend or explain why it is unavailable."""
    identity = (backend_id, backend_version)
    if identity == (VOXEL_BACKEND_ID, VOXEL_BACKEND_VERSION):
        from calibrate3dp.geometry.specimens import PlateGeometryBackend

        return PlateGeometryBackend()
    if identity != (BUILD123D_BACKEND_ID, BUILD123D_BACKEND_VERSION):
        raise GeometryBackendUnavailable(
            f"geometry backend {backend_id!r}@{backend_version!r} is unsupported"
        )
    if not is_geometry_backend_available(*identity):
        raise GeometryBackendUnavailable(
            "CAD backend unavailable: install the optional 'cad' extra with the pinned build123d and OCCT versions"
        )
    from calibrate3dp.geometry.build123d_backend import Build123dPlateGeometryBackend

    return Build123dPlateGeometryBackend()
