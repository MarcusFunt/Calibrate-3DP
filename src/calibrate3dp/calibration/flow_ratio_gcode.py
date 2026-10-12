"""Fail-closed relative flow verification for identical top-surface coupons.

The G-code proof is a narrow Orca dialect with labeled object sections,
TYPE:Top surface, G90 XY and M83 relative extrusion. It is not a general
machine-safety or material-flow calibration certification.
"""
from __future__ import annotations

from dataclasses import dataclass
import math
from pathlib import Path
import re
from typing import Any, Mapping

_OBJECT_START = re.compile(r"^;\s*printing object\s+(.+?)\s+id:", re.IGNORECASE)
_STOP_OBJECT = re.compile(r"^;\s*stop printing object\b", re.IGNORECASE)
_AXIS_WORD = re.compile(r"(?:^|\s)([XYE])([-+]?(?:\d+(?:\.\d*)?|\.\d+))(?=\s|$)", re.IGNORECASE)
_MOVE = re.compile(r"^G0?[01](?:\s|$)", re.IGNORECASE)


@dataclass(frozen=True)
class FlowRatioSampleEvidence:
    extrusion_mm: float
    path_length_mm: float
    positive_top_surface_moves: int
    extrusion_per_mm: float
    normalized_extrusion_per_mm: float


@dataclass(frozen=True)
class FlowRatioGcodeValidation:
    valid: bool
    messages: tuple[str, ...]
    samples: Mapping[str, FlowRatioSampleEvidence]


def validate_top_surface_flow_gcode(
    gcode: str | Path,
    expected_by_sample: Mapping[str, Mapping[str, Any]],
    *,
    relative_tolerance: float = 0.02,
    min_path_length_mm: float = 5.0,
) -> FlowRatioGcodeValidation:
    """Compare TOP SURFACE positive extrusion / extruded XY path across objects.

    All samples must have the same geometry and same slicer settings apart
    from their per-object print_flow_ratio. This validator does not prove
    toolpath equivalence; the corresponding geometry/layout gates are required.
    """
    if not expected_by_sample or not isinstance(expected_by_sample, Mapping):
        raise ValueError("expected_by_sample must be a nonempty mapping")
    if not isinstance(relative_tolerance, (int, float)) or not 0 < relative_tolerance < 1:
        raise ValueError("relative_tolerance must be between zero and one")
    if not isinstance(min_path_length_mm, (int, float)) or min_path_length_mm <= 0:
        raise ValueError("min_path_length_mm must be positive")
    if isinstance(gcode, Path):
        text = gcode.read_text(encoding="utf-8", errors="replace")
    elif isinstance(gcode, str):
        text = gcode
    else:
        raise ValueError("gcode must be a string or Path")
    expected: dict[str, float] = {}
    for sample_name, settings in expected_by_sample.items():
        if not isinstance(sample_name, str) or not sample_name.strip() or not isinstance(settings, Mapping):
            raise ValueError("each sample must have a name and settings mapping")
        value = settings.get("print_flow_ratio")
        try:
            flow = float(value)
        except (ValueError, TypeError) as exc:
            raise ValueError(f"{sample_name} lacks a valid print_flow_ratio") from exc
        if isinstance(value, bool) or not math.isfinite(flow) or not 0.5 <= flow <= 1.5:
            raise ValueError(f"{sample_name} print_flow_ratio must be finite and between 0.5 and 1.5")
        expected[sample_name] = flow

    positive_e = {name: 0.0 for name in expected}
    distances = {name: 0.0 for name in expected}
    counts = {name: 0 for name in expected}
    relative_extrusion = False
    absolute_xy = False
    top_surface = False
    current_object: str | None = None
    xy: tuple[float, float] | None = None
    messages: list[str] = []
    unsupported_modes = set()

    for raw_line in text.splitlines():
        line = raw_line.strip()
        command = line.partition(";")[0].strip()
        cmd = command.split(maxsplit=1)[0].upper() if command else ""
        if cmd == "M83":
            relative_extrusion = True
        elif cmd == "M82":
            relative_extrusion = False
        elif cmd == "G90":
            absolute_xy = True
        elif cmd == "G91":
            absolute_xy = False
        object_start = _OBJECT_START.match(line)
        if object_start:
            current_object = object_start.group(1).strip()
            top_surface = False
        elif _STOP_OBJECT.match(line):
            current_object = None
            top_surface = False
        elif line.casefold().startswith(";type:"):
            top_surface = line.split(":", 1)[1].strip().casefold() == "top surface"

        if not _MOVE.match(command):
            continue
        words = {key.upper(): float(value) for key, value in _AXIS_WORD.findall(command)}
        if not words:
            continue
        if not absolute_xy and ("X" in words or "Y" in words):
            unsupported_modes.add("G91 or missing G90 absolute XY")
            continue
        previous_xy = xy
        if "X" in words or "Y" in words:
            if previous_xy is None:
                # Cannot derive a length from the first absolute endpoint.
                xy = (words.get("X", 0.0), words.get("Y", 0.0))
            else:
                xy = (words.get("X", previous_xy[0]), words.get("Y", previous_xy[1]))
        if current_object not in expected or not top_surface or "E" not in words:
            continue
        if not relative_extrusion:
            unsupported_modes.add("M82 or missing M83 relative extrusion")
            continue
        # Ignore retract/unretract and XY travel; only extruding XY moves count.
        if words["E"] <= 0 or previous_xy is None or xy is None:
            continue
        length = math.dist(previous_xy, xy)
        if length <= 1e-5:
            continue
        positive_e[current_object] += words["E"]
        distances[current_object] += length
        counts[current_object] += 1

    for mode in sorted(unsupported_modes):
        messages.append(f"unsupported G-code motion context: {mode}")

    evidence: dict[str, FlowRatioSampleEvidence] = {}
    normalized = []
    for name, flow in expected.items():
        length = distances[name]
        e_per_mm = positive_e[name] / length if length else 0.0
        divided = e_per_mm / flow
        evidence[name] = FlowRatioSampleEvidence(
            extrusion_mm=positive_e[name],
            path_length_mm=length,
            positive_top_surface_moves=counts[name],
            extrusion_per_mm=e_per_mm,
            normalized_extrusion_per_mm=divided,
        )
        if length < min_path_length_mm or counts[name] <= 0:
            messages.append(f"{name} lacks sufficient identified top-surface extrusion path")
        else:
            normalized.append(divided)
    if len(normalized) == len(expected):
        reference = sorted(normalized)[len(normalized) // 2]
        if reference <= 0:
            messages.append("normalized top-surface extrusion is zero")
        for name, values in evidence.items():
            if values.normalized_extrusion_per_mm <= 0:
                continue
            delta = abs(values.normalized_extrusion_per_mm - reference) / reference if reference else math.inf
            if delta > relative_tolerance:
                messages.append(
                    f"{name} top-surface extrusion differs by {delta:.2%} from normalized reference"
                )
    return FlowRatioGcodeValidation(not messages, tuple(messages), evidence)
