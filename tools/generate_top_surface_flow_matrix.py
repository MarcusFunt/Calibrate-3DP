#!/usr/bin/env python3
"""Build a labeled, parametric 3MF flow-ratio calibration matrix.

This standalone geometry/export command does NOT slice, register plate IDs in
Calibrate-3DP, or establish print readiness.
"""
from __future__ import annotations

import argparse
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))

from calibrate3dp.geometry.flow_ratio_matrix import (  # noqa: E402
    DEFAULT_MODIFIERS,
    TopSurfaceFlowMatrixSpec,
    export_top_surface_flow_matrix,
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--plate-code", required=True, help="Six uppercase alphanumeric characters; not added to the run database")
    parser.add_argument("--baseline-ratio", type=float, default=1.0, help="Imported filament_flow_ratio baseline")
    parser.add_argument("--modifiers", nargs="+", type=float, default=DEFAULT_MODIFIERS, help="Additive offsets to the filament baseline, 1-9 sorted values")
    parser.add_argument("--width", type=float, default=30.0, help="Coupon X dimension (mm)")
    parser.add_argument("--depth", type=float, default=25.0, help="Coupon Y dimension (mm)")
    parser.add_argument("--height", type=float, default=4.0, help="Coupon Z dimension (mm)")
    parser.add_argument("--bed-width", type=float, default=220.0, help="Rectangular printable bed width (mm)")
    parser.add_argument("--bed-depth", type=float, default=220.0, help="Rectangular printable bed depth (mm)")
    parser.add_argument("--output", type=Path, required=True, help="Output .3mf; a matching .json is generated")
    args = parser.parse_args(argv)
    try:
        spec = TopSurfaceFlowMatrixSpec(
            plate_code=args.plate_code,
            baseline_filament_flow_ratio=args.baseline_ratio,
            modifiers=tuple(args.modifiers),
            specimen_width_mm=args.width,
            specimen_depth_mm=args.depth,
            specimen_height_mm=args.height,
            printable_width_mm=args.bed_width,
            printable_depth_mm=args.bed_depth,
        )
        model_path, manifest_path = export_top_surface_flow_matrix(spec, args.output)
    except (ValueError, FileExistsError, OSError, RuntimeError, ImportError) as exc:
        parser.error(str(exc))
    print(f"Created: {model_path}")
    print(f"Manifest: {manifest_path}")
    for candidate in spec.candidates():
        print(
            f"  {candidate['sample']}: target filament_flow_ratio="
            f"{candidate['target_filament_flow_ratio']:.5f}, "
            f"object print_flow_ratio={candidate['object_print_flow_ratio']:.5f}"
        )
    print("NOT PRINT READY: Orca profile, toolpaths, physical plate and sample settings must be verified.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
