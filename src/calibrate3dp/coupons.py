"""Deterministic, original STL geometry for independent calibration candidates."""

from __future__ import annotations

from dataclasses import dataclass
import json
import math
from pathlib import Path
import re

from calibrate3dp.experiments import ExperimentPlan


class CouponGenerationError(ValueError):
    """Raised when candidate coupons cannot be generated without overwriting data."""


@dataclass(frozen=True)
class CouponSpec:
    """Dimensions for one flat top-surface ironing specimen, in millimeters."""

    length_mm: float = 30.0
    width_mm: float = 30.0
    height_mm: float = 4.0

    def __post_init__(self) -> None:
        for name, value in (
            ("length_mm", self.length_mm),
            ("width_mm", self.width_mm),
            ("height_mm", self.height_mm),
        ):
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise CouponGenerationError(f"{name} must be a finite positive number")
            if not math.isfinite(value) or value <= 0:
                raise CouponGenerationError(f"{name} must be a finite positive number")


@dataclass(frozen=True)
class CouponArtifact:
    candidate_id: str
    model_path: Path
    length_mm: float
    width_mm: float
    height_mm: float


def generate_ironing_coupons(
    plan: ExperimentPlan,
    output_dir: str | Path,
    *,
    spec: CouponSpec = CouponSpec(),
) -> tuple[CouponArtifact, ...]:
    """Write one identical closed rectangular coupon per ironing candidate.

    Each candidate gets a separate STL so its G-code is sliced with an explicit
    candidate profile and can be printed as a separate, labeled plate/job.
    Existing files are never overwritten.
    """
    if not isinstance(plan, ExperimentPlan) or plan.module_id != "ironing":
        raise CouponGenerationError("coupon generation requires an ironing ExperimentPlan")
    if not isinstance(spec, CouponSpec):
        raise CouponGenerationError("spec must be a CouponSpec")

    root = Path(output_dir)
    if root.exists():
        if not root.is_dir():
            raise CouponGenerationError(f"coupon output path is not a directory: {root}")
        if any(root.iterdir()):
            raise CouponGenerationError(f"coupon output directory must be empty: {root}")
    else:
        root.mkdir(parents=True, exist_ok=True)

    artifacts: list[CouponArtifact] = []
    for candidate in plan.candidates:
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]*", candidate.candidate_id):
            raise CouponGenerationError(
                f"candidate id {candidate.candidate_id!r} is not safe for a coupon filename"
            )
        model_path = root / f"coupon_{candidate.candidate_id}.stl"
        model_path.write_text(_ascii_box(spec), encoding="ascii", newline="\n")
        artifacts.append(CouponArtifact(
            candidate_id=candidate.candidate_id,
            model_path=model_path,
            length_mm=float(spec.length_mm),
            width_mm=float(spec.width_mm),
            height_mm=float(spec.height_mm),
        ))

    manifest = {
        "schema_version": 1,
        "plan_id": plan.plan_id,
        "module_id": plan.module_id,
        "geometry": {
            "kind": "rectangular-top-surface-coupon",
            "length_mm": float(spec.length_mm),
            "width_mm": float(spec.width_mm),
            "height_mm": float(spec.height_mm),
        },
        "candidates": [
            {"candidate_id": artifact.candidate_id, "model": artifact.model_path.name}
            for artifact in artifacts
        ],
    }
    (root / "coupon-map.json").write_text(
        json.dumps(manifest, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    return tuple(artifacts)


def _ascii_box(spec: CouponSpec) -> str:
    x = float(spec.length_mm)
    y = float(spec.width_mm)
    z = float(spec.height_mm)
    vertices = (
        (0.0, 0.0, 0.0), (x, 0.0, 0.0), (x, y, 0.0), (0.0, y, 0.0),
        (0.0, 0.0, z), (x, 0.0, z), (x, y, z), (0.0, y, z),
    )
    faces = (
        (0, 2, 1), (0, 3, 2), (4, 5, 6), (4, 6, 7),
        (0, 1, 5), (0, 5, 4), (1, 2, 6), (1, 6, 5),
        (2, 3, 7), (2, 7, 6), (3, 0, 4), (3, 4, 7),
    )
    lines = ["solid calibrate3dp_coupon"]
    for face in faces:
        a, b, c = (vertices[index] for index in face)
        normal = _unit_normal(a, b, c)
        lines.append(f"  facet normal {_fmt(normal[0])} {_fmt(normal[1])} {_fmt(normal[2])}")
        lines.append("    outer loop")
        for vertex in (a, b, c):
            lines.append(f"      vertex {_fmt(vertex[0])} {_fmt(vertex[1])} {_fmt(vertex[2])}")
        lines.extend(("    endloop", "  endfacet"))
    lines.append("endsolid calibrate3dp_coupon")
    return "\n".join(lines) + "\n"


def _unit_normal(a: tuple[float, float, float], b: tuple[float, float, float], c: tuple[float, float, float]) -> tuple[float, float, float]:
    u = tuple(b[index] - a[index] for index in range(3))
    v = tuple(c[index] - a[index] for index in range(3))
    cross = (
        u[1] * v[2] - u[2] * v[1],
        u[2] * v[0] - u[0] * v[2],
        u[0] * v[1] - u[1] * v[0],
    )
    magnitude = math.sqrt(sum(value * value for value in cross))
    return tuple(value / magnitude for value in cross)


def _fmt(value: float) -> str:
    return format(value, ".6g")
