"""Measure cold build123d imports and a representative nine-coupon CAD build.

This opt-in benchmark writes an 11-object 3MF, one binary STL per object, and
a JSON report to a caller-selected evidence directory. It has no CI threshold;
the intended use is to compare a pinned environment and geometry revision.
"""

from __future__ import annotations

import argparse
import ctypes
import hashlib
import json
import os
from pathlib import Path
import platform
import statistics
import subprocess
import sys
import time
from datetime import datetime, timezone


def _peak_working_set_bytes() -> int:
    if os.name != "nt":
        raise RuntimeError("this benchmark currently measures process memory through Windows PSAPI")

    from ctypes import wintypes

    class ProcessMemoryCountersEx(ctypes.Structure):
        _fields_ = [
            ("cb", wintypes.DWORD),
            ("PageFaultCount", wintypes.DWORD),
            ("PeakWorkingSetSize", ctypes.c_size_t),
            ("WorkingSetSize", ctypes.c_size_t),
            ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
            ("QuotaPagedPoolUsage", ctypes.c_size_t),
            ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
            ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
            ("PagefileUsage", ctypes.c_size_t),
            ("PeakPagefileUsage", ctypes.c_size_t),
            ("PrivateUsage", ctypes.c_size_t),
        ]

    counters = ProcessMemoryCountersEx()
    counters.cb = ctypes.sizeof(counters)
    psapi = ctypes.WinDLL("psapi", use_last_error=True)
    psapi.GetProcessMemoryInfo.argtypes = (
        wintypes.HANDLE,
        ctypes.POINTER(ProcessMemoryCountersEx),
        wintypes.DWORD,
    )
    psapi.GetProcessMemoryInfo.restype = wintypes.BOOL
    handle = ctypes.windll.kernel32.GetCurrentProcess()
    if not psapi.GetProcessMemoryInfo(handle, ctypes.byref(counters), counters.cb):
        raise ctypes.WinError(ctypes.get_last_error())
    return int(counters.PeakWorkingSetSize)


def _cold_import_samples(count: int) -> list[dict[str, float]]:
    snippet = """
import json, time
start = time.perf_counter()
import build123d
build123d_seconds = time.perf_counter() - start
start = time.perf_counter()
from calibrate3dp.geometry.build123d_backend import Build123dPlateGeometryBackend
backend_seconds = time.perf_counter() - start
print(json.dumps({"build123d_import_seconds": build123d_seconds,
                  "backend_import_seconds": backend_seconds,
                  "combined_import_seconds": build123d_seconds + backend_seconds}))
"""
    samples = []
    for _ in range(count):
        result = subprocess.run(
            [sys.executable, "-c", snippet],
            check=True,
            capture_output=True,
            text=True,
        )
        samples.append(json.loads(result.stdout))
    return samples


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", required=True, type=Path, help="directory for retained benchmark artifacts")
    parser.add_argument("--cold-import-runs", type=int, default=3)
    args = parser.parse_args()
    if args.cold_import_runs < 1:
        parser.error("--cold-import-runs must be at least one")

    from calibrate3dp.geometry.build123d_backend import Build123dPlateGeometryBackend
    from calibrate3dp.geometry.layout import PlateLayoutRequest, PlateSampleRequest
    from calibrate3dp.grouped_plate import write_plate_geometry_3mf

    samples = tuple(
        PlateSampleRequest(
            label=label,
            candidate_id=f"benchmark-candidate-{label}",
            settings={"ironing_flow": f"{5 + index}%", "ironing_speed": 10 + index},
        )
        for index, label in enumerate("ABCDEFGHI")
    )
    request = PlateLayoutRequest(
        samples=samples,
        plate_code="R2F0I5",
        printable_polygon=((0, 0), (235, 0), (235, 235), (0, 235)),
        geometry_backend="build123d",
        geometry_backend_version="1",
    )

    root = args.output_dir.expanduser().resolve() / datetime.now(timezone.utc).strftime(
        "build123d-benchmark-%Y%m%dT%H%M%S%fZ"
    )
    artifacts = root / "artifacts"
    artifacts.mkdir(parents=True, exist_ok=False)
    import_samples = _cold_import_samples(args.cold_import_runs)
    backend = Build123dPlateGeometryBackend()
    started = time.perf_counter()
    geometry = backend.build(request)
    build_seconds = time.perf_counter() - started
    memory_after_build = _peak_working_set_bytes()

    started = time.perf_counter()
    stl_records = []
    for item in geometry.objects:
        path = item.mesh.write_binary_stl(artifacts / f"{item.name}.stl", solid_name=item.name)
        stl_records.append({
            "name": item.name,
            "triangle_count": len(item.mesh.triangles),
            "vertex_count": len(item.mesh.vertices),
            "size_bytes": path.stat().st_size,
            "sha256": _sha256(path),
        })
    stl_export_seconds = time.perf_counter() - started

    started = time.perf_counter()
    project = write_plate_geometry_3mf(
        geometry,
        destination=artifacts / "plate.3mf",
        module_id="ironing",
        plan_id="build123d-benchmark",
    )
    project_export_seconds = time.perf_counter() - started
    report = {
        "schema_version": 1,
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "python_version": platform.python_version(),
        "platform": platform.platform(),
        "backend": dict(geometry.metadata),
        "cold_import_runs": import_samples,
        "cold_import_median_seconds": statistics.median(
            item["combined_import_seconds"] for item in import_samples
        ),
        "geometry_build_seconds": build_seconds,
        "peak_working_set_bytes_after_geometry_build": memory_after_build,
        "stl_export_seconds": stl_export_seconds,
        "stl_object_count": len(stl_records),
        "stl_triangle_count": sum(item["triangle_count"] for item in stl_records),
        "stl_total_bytes": sum(item["size_bytes"] for item in stl_records),
        "stl_objects": stl_records,
        "3mf_export_seconds": project_export_seconds,
        "3mf_size_bytes": project.stat().st_size,
        "3mf_sha256": _sha256(project),
        "artifacts_directory": str(artifacts),
        "print_ready": False,
        "needs_physical_validation": True,
    }
    report_path = root / "benchmark.json"
    report_path.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps({"report": str(report_path), **report}, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
