"""Parametric top-surface flow-ratio comparison plate.

This is a standalone model-generation recipe, not a qualified calibration run.
It reuses the pinned Build123d connected-plate backend, physical sample labels,
identifier plaque, and Orca-specific per-object 3MF metadata writer.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from decimal import Decimal
import hashlib
import json
import math
from pathlib import Path
from typing import Any

from calibrate3dp.domain.experiment_config import default_layout_options
from calibrate3dp.geometry.contracts import PlateGeometry
from calibrate3dp.geometry.layout import PlateLayoutRequest, PlateSampleRequest
from calibrate3dp.grouped_plate import write_plate_geometry_3mf


DEFAULT_MODIFIERS = tuple(float(Decimal(step) / Decimal(100)) for step in range(-4, 5))
RECIPE_ID = "flow_ratio.top_surface_matrix"
RECIPE_VERSION = "1"


@dataclass(frozen=True)
class TopSurfaceFlowMatrixSpec:
    """Absolute filament flow targets = baseline + additive modifier.

    Orca's object-level print_flow_ratio is a MULTIPLIER, not the intended
    filament_flow_ratio value.  We therefore write
    print_flow_ratio = (baseline + modifier) / baseline, leaving the user's
    source filament preset untouched. A validated print must verify that Orca
    honors this per-object override and that other flow factors are controlled.
    """

    plate_code: str
    baseline_filament_flow_ratio: float = 1.0
    modifiers: tuple[float, ...] = DEFAULT_MODIFIERS
    specimen_width_mm: float = 30.0
    specimen_depth_mm: float = 25.0
    specimen_height_mm: float = 4.0
    printable_width_mm: float = 220.0
    printable_depth_mm: float = 220.0

    def __post_init__(self) -> None:
        if not isinstance(self.plate_code, str) or len(self.plate_code) != 6 or not all(
            c in "ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789" for c in self.plate_code
        ):
            raise ValueError("plate_code must contain exactly six uppercase letters/digits")
        for name in (
            "baseline_filament_flow_ratio", "specimen_width_mm",
            "specimen_depth_mm", "specimen_height_mm",
            "printable_width_mm", "printable_depth_mm",
        ):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, (float, int)) or not math.isfinite(value) or value <= 0:
                raise ValueError(f"{name} must be a finite positive number")
        if not 0.5 <= self.baseline_filament_flow_ratio <= 1.5:
            raise ValueError("baseline_filament_flow_ratio must be between 0.5 and 1.5")
        if self.specimen_width_mm < 20 or self.specimen_depth_mm < 16 or self.specimen_height_mm < 2:
            raise ValueError("coupons must be large enough for underside labels and flat top inspection")
        try:
            modifiers = tuple(self.modifiers)
        except TypeError as exc:
            raise ValueError("modifiers must be a sequence") from exc
        if not 1 <= len(modifiers) <= 9:
            raise ValueError("flow matrix supports one to nine labeled coupons")
        if any(isinstance(v, bool) or not isinstance(v, (float, int)) or not math.isfinite(v) for v in modifiers):
            raise ValueError("modifiers must contain finite numbers")
        if any(b <= a for a, b in zip(modifiers, modifiers[1:])):
            raise ValueError("modifiers must be strictly increasing without duplicates")
        if any(not 0.5 <= self.baseline_filament_flow_ratio + v <= 1.5 for v in modifiers):
            raise ValueError("resulting filament flow targets must remain between 0.5 and 1.5")
        object.__setattr__(self, "modifiers", modifiers)

    def candidates(self) -> tuple[dict[str, Any], ...]:
        baseline = Decimal(str(self.baseline_filament_flow_ratio))
        return tuple(
            {
                "sample": f"Sample-{chr(65 + index)}",
                "candidate_id": f"flow-{index + 1:02d}",
                "modifier": float(Decimal(str(offset))),
                "target_filament_flow_ratio": float(baseline + Decimal(str(offset))),
                "object_print_flow_ratio": float((baseline + Decimal(str(offset))) / baseline),
            }
            for index, offset in enumerate(self.modifiers)
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "plate_code": self.plate_code,
            "baseline_filament_flow_ratio": self.baseline_filament_flow_ratio,
            "modifiers": list(self.modifiers),
            "specimen_width_mm": self.specimen_width_mm,
            "specimen_depth_mm": self.specimen_depth_mm,
            "specimen_height_mm": self.specimen_height_mm,
            "printable_width_mm": self.printable_width_mm,
            "printable_depth_mm": self.printable_depth_mm,
        }


def build_top_surface_flow_matrix(spec: TopSurfaceFlowMatrixSpec) -> PlateGeometry:
    """Build one connected, labeled, exact-dimension Build123d CAD plate."""
    if not isinstance(spec, TopSurfaceFlowMatrixSpec):
        raise TypeError("spec must be TopSurfaceFlowMatrixSpec")
    # A versioned identity ensures this cannot masquerade as an ironing plate.
    options = default_layout_options()
    options.update({
        "recipe_id": RECIPE_ID,
        "recipe_version": RECIPE_VERSION,
        "specimen_width_mm": spec.specimen_width_mm,
        "specimen_depth_mm": spec.specimen_depth_mm,
        "specimen_height_mm": spec.specimen_height_mm,
    })
    candidates = spec.candidates()
    request = PlateLayoutRequest(
        samples=tuple(
            PlateSampleRequest(
                label=chr(65 + index),
                candidate_id=candidate["candidate_id"],
                settings={"print_flow_ratio": candidate["object_print_flow_ratio"]},
            )
            for index, candidate in enumerate(candidates)
        ),
        plate_code=spec.plate_code,
        printable_polygon=(
            (0.0, 0.0),
            (spec.printable_width_mm, 0.0),
            (spec.printable_width_mm, spec.printable_depth_mm),
            (0.0, spec.printable_depth_mm),
        ),
        **{
            key: options[key]
            for key in PlateLayoutRequest.__dataclass_fields__
            if key not in {"samples", "plate_code", "printable_polygon", "keep_outs"} and key in options
        },
    )
    from calibrate3dp.geometry.registry import get_geometry_backend

    geometry = get_geometry_backend("build123d", "1").build(request)
    metadata = {
        **geometry.metadata,
        "flow_ratio_matrix": {
            "schema_version": 1,
            "spec": spec.to_dict(),
            "candidates": list(candidates),
            "calibration_type": "filament_flow_ratio",
            "candidate_application": "per-object print_flow_ratio multiplier",
            "slicer_requirements": {
                "ironing_type": "no ironing",
                "print_flow_ratio": "per-object override; process-level default must be 1",
                "top_shell_layers": "at least 3, with a fully closed top surface",
                "top_surface_pattern": "constant across candidates; monotonic or supported equivalent",
                "filament_flow_ratio": spec.baseline_filament_flow_ratio,
                "all_other_flow_multipliers": "fixed across candidates",
            },
            "manual_review": [
                "top-surface gaps and pinholes",
                "ridges and overfilled top-surface lines",
                "smoothness and uniformity",
                "winner or tie and printed physical evidence",
            ],
            "limitations": [
                "These are ordinary solid-top coupons, not special slicer-generated Archimedean chord toolpaths.",
                "G-code per-object flow propagation is not established by successful CAD or 3MF export.",
                "This standalone plate is not registered in the application's run history.",
            ],
        },
        "print_ready": False,
        "needs_physical_validation": True,
    }
    return replace(geometry, metadata=metadata)


def export_top_surface_flow_matrix(spec: TopSurfaceFlowMatrixSpec, destination: str | Path) -> tuple[Path, Path]:
    """Write a 3MF and sidecar JSON; refuse to overwrite either artifact."""
    destination = Path(destination).expanduser().resolve(strict=False)
    if destination.suffix.lower() != ".3mf":
        raise ValueError("destination must end with .3mf")
    sidecar = destination.with_suffix(".json")
    if destination.exists() or sidecar.exists():
        raise FileExistsError("refusing to overwrite an existing 3MF or JSON manifest")
    geometry = build_top_surface_flow_matrix(spec)
    destination.parent.mkdir(parents=True, exist_ok=True)
    try:
        write_plate_geometry_3mf(
            geometry,
            destination=destination,
            module_id="flow_ratio",
            plan_id=f"standalone-{spec.plate_code}",
        )
        payload = {
            "schema_version": 1,
            "recipe_id": RECIPE_ID,
            "recipe_version": RECIPE_VERSION,
            "geometry_backend": "build123d@1",
            "plate_code": spec.plate_code,
            "spec": spec.to_dict(),
            "candidate_map": list(spec.candidates()),
            "geometry": dict(geometry.metadata),
            "qualification": {
                "settings_propagation_verified": False,
                "physical_validation": False,
                "run_registered": False,
                "print_ready": False,
            },
            "artifact_sha256": hashlib.sha256(destination.read_bytes()).hexdigest(),
        }
        with sidecar.open("x", encoding="utf-8") as stream:
            json.dump(payload, stream, indent=2, sort_keys=True, allow_nan=False)
            stream.write("\n")
    except Exception:
        destination.unlink(missing_ok=True)
        sidecar.unlink(missing_ok=True)
        raise
    return destination, sidecar
