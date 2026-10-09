"""Small, dependency-free 3MF plates and G-code checks for grouped ironing samples.

This module is an Orca integration path for flat coupons, not the final V1
connected plate geometry. Every mesh is deliberately a separate object so Orca
can apply settings to the corresponding model-settings part.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
import math
from pathlib import Path
import re
import zipfile
import xml.etree.ElementTree as ET
from typing import Any, Mapping

from calibrate3dp.experiments import ExperimentPlan


class GroupedPlateError(ValueError):
    """Raised when a grouped plate cannot be compiled safely."""


@dataclass(frozen=True)
class SampleGcodeEvidence:
    """Observed ironing toolpath measurements for one named sample object."""

    positive_extrusion_mm: float
    extrusion_per_flow_percent: float
    observed_speed_mm_s: tuple[float, ...]
    ironing_extrusion_moves: int


@dataclass(frozen=True)
class GroupedGcodeValidation:
    """Outcome and per-sample evidence from grouped ironing output."""

    valid: bool
    messages: tuple[str, ...]
    samples: Mapping[str, SampleGcodeEvidence]


@dataclass(frozen=True)
class PlateBounds:
    min_x: float
    min_y: float
    max_x: float
    max_y: float
    max_z: float

    def to_dict(self) -> dict[str, float]:
        return {
            "min_x": self.min_x, "min_y": self.min_y,
            "max_x": self.max_x, "max_y": self.max_y, "max_z": self.max_z,
        }


_CORE_NS = "http://schemas.microsoft.com/3dmanufacturing/core/2015/02"
_OBJECT_START = re.compile(r"^;\s*printing object\s+(.+?)\s+id:", re.IGNORECASE)
_GCODE_WORD = re.compile(r"(?:^|\s)([EF])([-+]?(?:\d+(?:\.\d*)?|\.\d+))(?=\s|$)", re.IGNORECASE)
_VERTICES = (
    (0, 0, 0), (30, 0, 0), (30, 30, 0), (0, 30, 0),
    (0, 0, 4), (30, 0, 4), (30, 30, 4), (0, 30, 4),
)
_TRIANGLES = (
    (0, 2, 1), (0, 3, 2), (4, 5, 6), (4, 6, 7),
    (0, 1, 5), (0, 5, 4), (1, 2, 6), (1, 6, 5),
    (2, 3, 7), (2, 7, 6), (3, 0, 4), (3, 4, 7),
)
_CONTENT_TYPES = b'''<?xml version="1.0" encoding="UTF-8"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>
<Default Extension="model" ContentType="application/vnd.ms-package.3dmanufacturing-3dmodel+xml"/>
<Default Extension="config" ContentType="application/xml"/>
<Default Extension="json" ContentType="application/json"/>
</Types>'''
_ROOT_RELS = b'''<?xml version="1.0" encoding="UTF-8"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
<Relationship Target="/3D/3dmodel.model" Id="rel-1" Type="http://schemas.microsoft.com/3dmanufacturing/2013/01/3dmodel"/>
</Relationships>'''


def write_grouped_plate_3mf(
    plan: ExperimentPlan,
    *,
    plate_code: str,
    destination: str | Path,
    omitted_candidate_ids: set[str] | frozenset[str] = frozenset(),
) -> Path:
    """Write up to nine same-size coupons with per-object ironing metadata.

    The object ID and nested model-settings part ID are intentionally identical:
    real Orca 2.3.0 applied overrides only when that resource mapping matched.
    ``omitted_candidate_ids`` exists for negative integration fixtures.
    """
    if not isinstance(plan, ExperimentPlan) or plan.module_id != "ironing":
        raise GroupedPlateError("grouped plate generation requires an ironing experiment plan")
    if not isinstance(plate_code, str) or not re.fullmatch(r"[A-Z0-9]{6}", plate_code):
        raise GroupedPlateError("plate_code must contain exactly six uppercase letters or digits")
    if not 1 <= len(plan.candidates) <= 9:
        raise GroupedPlateError("grouped ironing plates support one to nine samples")
    if isinstance(omitted_candidate_ids, (str, bytes)):
        raise GroupedPlateError("omitted_candidate_ids must be a set of candidate IDs")
    known_ids = {candidate.candidate_id for candidate in plan.candidates}
    if not set(omitted_candidate_ids) <= known_ids:
        raise GroupedPlateError("omitted_candidate_ids contains an unknown candidate")
    destination_path = Path(destination).expanduser().resolve(strict=False)
    if destination_path.suffix.lower() != ".3mf":
        raise GroupedPlateError("grouped plate destination must use the .3mf extension")
    if destination_path.exists():
        raise GroupedPlateError(f"grouped plate destination already exists: {destination_path}")

    ET.register_namespace("", _CORE_NS)
    model = ET.Element(
        f"{{{_CORE_NS}}}model",
        {"unit": "millimeter", "{http://www.w3.org/XML/1998/namespace}lang": "en-US", "version": "1.0"},
    )
    resources = ET.SubElement(model, f"{{{_CORE_NS}}}resources")
    labels = "ABCDEFGHI"
    rows = (len(plan.candidates) + 2) // 3
    for index, _candidate in enumerate(plan.candidates, start=1):
        label = labels[index - 1]
        obj = ET.SubElement(
            resources,
            f"{{{_CORE_NS}}}object",
            {"id": str(index), "type": "model", "name": f"Sample-{label}"},
        )
        mesh = ET.SubElement(obj, f"{{{_CORE_NS}}}mesh")
        vertices = ET.SubElement(mesh, f"{{{_CORE_NS}}}vertices")
        for x, y, z in _VERTICES:
            ET.SubElement(vertices, f"{{{_CORE_NS}}}vertex", {"x": str(x), "y": str(y), "z": str(z)})
        triangles = ET.SubElement(mesh, f"{{{_CORE_NS}}}triangles")
        for v1, v2, v3 in _TRIANGLES:
            ET.SubElement(
                triangles,
                f"{{{_CORE_NS}}}triangle",
                {"v1": str(v1), "v2": str(v2), "v3": str(v3)},
            )

    build = ET.SubElement(model, f"{{{_CORE_NS}}}build")
    config = ET.Element("config")
    manifest_samples: list[dict[str, Any]] = []
    for index, candidate in enumerate(plan.candidates, start=1):
        label = labels[index - 1]
        row, column = divmod(index - 1, 3)
        transform = f"1 0 0 0 1 0 0 0 1 {5 + column * 38} {5 + row * 38} 0"
        ET.SubElement(build, f"{{{_CORE_NS}}}item", {"objectid": str(index), "transform": transform})

        object_config = ET.SubElement(config, "object", {"id": str(index)})
        ET.SubElement(object_config, "metadata", {"key": "name", "value": f"Sample-{label}"})
        part = ET.SubElement(object_config, "part", {"id": str(index), "subtype": "normal_part"})
        applied = candidate.candidate_id not in omitted_candidate_ids
        if applied:
            flow = candidate.overrides.get("ironing_flow")
            speed = candidate.overrides.get("ironing_speed")
            if flow is None or speed is None:
                raise GroupedPlateError(f"candidate {candidate.candidate_id!r} lacks ironing flow or speed")
            ET.SubElement(part, "metadata", {"key": "ironing_flow", "value": _flow_text(flow)})
            ET.SubElement(part, "metadata", {"key": "ironing_speed", "value": str(speed)})
        manifest_samples.append({
            "label": f"Sample-{label}",
            "candidate_id": candidate.candidate_id,
            "settings": dict(candidate.overrides),
            "override_written": applied,
        })

    model_xml = ET.tostring(model, encoding="utf-8", xml_declaration=True)
    config_xml = ET.tostring(config, encoding="utf-8", xml_declaration=True)
    bounds = grouped_plate_bounds(plan)
    run_manifest = {
        "schema_version": 1,
        "plate_code": plate_code,
        "module_id": plan.module_id,
        "plan_id": plan.plan_id,
        "layout": {"rows": rows, "columns": min(3, len(plan.candidates)), "coupon_mm": [30, 30, 4], "spacing_mm": 8, "bounds_mm": bounds.to_dict()},
        "samples": manifest_samples,
        "physical_labels_in_mesh": False,
    }
    destination_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with zipfile.ZipFile(destination_path, "x", compression=zipfile.ZIP_DEFLATED) as archive:
            archive.writestr("[Content_Types].xml", _CONTENT_TYPES)
            archive.writestr("_rels/.rels", _ROOT_RELS)
            archive.writestr("3D/3dmodel.model", model_xml)
            archive.writestr("Metadata/model_settings.config", config_xml)
            import json

            archive.writestr("Metadata/calibrate3dp-run.json", json.dumps(run_manifest, sort_keys=True, indent=2) + "\n")
    except Exception:
        destination_path.unlink(missing_ok=True)
        raise
    return destination_path


def grouped_plate_bounds(plan: ExperimentPlan) -> PlateBounds:
    """Return the exact XY/Z extent used by the simple grouped coupon layout."""
    if not isinstance(plan, ExperimentPlan) or not 1 <= len(plan.candidates) <= 9:
        raise GroupedPlateError("plate bounds require an ironing plan with one to nine samples")
    columns = min(3, len(plan.candidates))
    rows = (len(plan.candidates) + 2) // 3
    return PlateBounds(5.0, 5.0, 5.0 + (columns - 1) * 38 + 30, 5.0 + (rows - 1) * 38 + 30, 4.0)


def require_grouped_plate_fits_machine(plan: ExperimentPlan, machine_settings: Mapping[str, Any]) -> PlateBounds:
    """Block grouped coupon slicing when the selected machine cannot fit it."""
    if not isinstance(machine_settings, Mapping):
        raise GroupedPlateError("selected machine profile has no resolved settings")
    bounds = grouped_plate_bounds(plan)
    bed_polygon = _printable_polygon(machine_settings)
    points = (
        (bounds.min_x, bounds.min_y), (bounds.max_x, bounds.min_y),
        (bounds.max_x, bounds.max_y), (bounds.min_x, bounds.max_y),
    )
    for start, end in zip(points, (*points[1:], points[0]), strict=True):
        distance = math.dist(start, end)
        steps = max(1, math.ceil(distance / 5.0))
        for index in range(steps + 1):
            ratio = index / steps
            point = (start[0] + ratio * (end[0] - start[0]), start[1] + ratio * (end[1] - start[1]))
            if not _point_in_polygon(point, bed_polygon):
                raise GroupedPlateError(
                    f"grouped plate XY bounds {bounds.min_x:g}..{bounds.max_x:g} × "
                    f"{bounds.min_y:g}..{bounds.max_y:g} mm exceed the selected machine's printable area"
                )
    height = machine_settings.get("printable_height")
    if height is not None:
        try:
            height_mm = float(height[0] if isinstance(height, (list, tuple)) else height)
        except (TypeError, ValueError, IndexError) as exc:
            raise GroupedPlateError("machine printable_height is malformed") from exc
        if not math.isfinite(height_mm) or bounds.max_z > height_mm:
            raise GroupedPlateError(
                f"grouped plate height {bounds.max_z:g} mm exceeds printable_height {height_mm:g} mm"
            )
    return bounds


def validate_grouped_ironing_gcode(
    gcode: str | Path,
    expected_by_sample: Mapping[str, Mapping[str, Any]],
    *,
    flow_tolerance: float = 0.05,
    speed_tolerance_mm_s: float = 0.1,
) -> GroupedGcodeValidation:
    """Prove per-object ironing speed and relative flow in actual G-code.

    Flow is validated from positive extrusion per requested percentage across
    identical coupons. Speed is validated from feed rates on those same ironing
    extrusion moves. A missing or ignored override therefore fails closed.
    """
    if not isinstance(expected_by_sample, Mapping) or not expected_by_sample:
        raise GroupedPlateError("expected_by_sample must map sample names to settings")
    if isinstance(gcode, Path):
        try:
            text = gcode.read_text(encoding="utf-8", errors="replace")
        except OSError as exc:
            raise GroupedPlateError(f"G-code file could not be read: {exc}") from exc
    elif isinstance(gcode, str):
        text = gcode
    else:
        raise GroupedPlateError("gcode must be text or a Path")
    if not math.isfinite(flow_tolerance) or not 0 <= flow_tolerance < 1:
        raise GroupedPlateError("flow_tolerance must be between zero and one")
    if not math.isfinite(speed_tolerance_mm_s) or speed_tolerance_mm_s < 0:
        raise GroupedPlateError("speed_tolerance_mm_s must be non-negative")

    totals = {name: 0.0 for name in expected_by_sample}
    speeds: dict[str, set[float]] = {name: set() for name in expected_by_sample}
    move_counts = {name: 0 for name in expected_by_sample}
    current_object: str | None = None
    ironing = False
    current_feed_mm_s: float | None = None
    for line in text.splitlines():
        start = _OBJECT_START.match(line)
        if start:
            current_object = start.group(1).strip()
            ironing = False
            continue
        if re.match(r"^;\s*stop printing object\b", line, re.IGNORECASE):
            current_object = None
            ironing = False
            continue
        if line.startswith(";TYPE:"):
            ironing = line[len(";TYPE:"):].strip().casefold() == "ironing"
            continue
        if current_object not in expected_by_sample or not ironing:
            continue
        command = line.lstrip()
        if not re.match(r"^G[0-3](?:\s|$)", command, re.IGNORECASE):
            continue
        words = {key.upper(): float(value) for key, value in _GCODE_WORD.findall(command)}
        if "F" in words and words["F"] > 0:
            current_feed_mm_s = words["F"] / 60.0
        extrusion = words.get("E", 0.0)
        if extrusion <= 0:
            continue
        totals[current_object] += extrusion
        move_counts[current_object] += 1
        if current_feed_mm_s is not None:
            speeds[current_object].add(current_feed_mm_s)

    messages: list[str] = []
    evidence: dict[str, SampleGcodeEvidence] = {}
    normalized: list[float] = []
    for name, settings in expected_by_sample.items():
        if not isinstance(name, str) or not name.strip() or not isinstance(settings, Mapping):
            raise GroupedPlateError("each sample name and settings mapping must be valid")
        expected_flow = _as_positive_float(settings.get("ironing_flow"), name, "ironing_flow")
        expected_speed = _as_positive_float(settings.get("ironing_speed"), name, "ironing_speed")
        sample_speeds = tuple(sorted(speeds[name]))
        if move_counts[name] == 0 or totals[name] <= 0:
            messages.append(f"{name} has no positive-extrusion ironing toolpath.")
        if not sample_speeds:
            messages.append(f"{name} has no observed ironing extrusion speed.")
        elif any(abs(speed - expected_speed) > speed_tolerance_mm_s for speed in sample_speeds):
            rendered = ", ".join(f"{speed:g}" for speed in sample_speeds)
            messages.append(f"{name} ironing speed mismatch: expected {expected_speed:g} mm/s, observed {rendered} mm/s.")
        per_flow = totals[name] / expected_flow if expected_flow else 0.0
        if totals[name] > 0:
            normalized.append(per_flow)
        evidence[name] = SampleGcodeEvidence(
            positive_extrusion_mm=totals[name],
            extrusion_per_flow_percent=per_flow,
            observed_speed_mm_s=sample_speeds,
            ironing_extrusion_moves=move_counts[name],
        )

    mean = sum(normalized) / len(normalized) if normalized else 0.0
    if mean <= 0 or len(normalized) != len(expected_by_sample):
        messages.append("Could not compare flow across every expected sample.")
    elif any(abs(value - mean) / mean > flow_tolerance for value in normalized):
        for name, sample in evidence.items():
            if sample.positive_extrusion_mm > 0:
                deviation = abs(sample.extrusion_per_flow_percent - mean) / mean
                if deviation > flow_tolerance:
                    messages.append(
                        f"{name} ironing flow mismatch: normalized extrusion differs by {deviation:.1%}."
                    )
    return GroupedGcodeValidation(not messages, tuple(messages), evidence)


def _flow_text(value: Any) -> str:
    rendered = str(value).strip()
    if rendered.endswith("%"):
        rendered = rendered[:-1].strip()
    _as_positive_float(rendered, "sample", "ironing_flow")
    return f"{rendered}%"


def _printable_polygon(machine_settings: Mapping[str, Any]) -> tuple[tuple[float, float], ...]:
    raw = machine_settings.get("printable_area")
    if isinstance(raw, str):
        values = [item.strip() for item in raw.split(",") if item.strip()]
    elif isinstance(raw, (list, tuple)):
        values = list(raw)
    else:
        values = []
    points: list[tuple[float, float]] = []
    for item in values:
        if isinstance(item, (list, tuple)) and len(item) >= 2:
            pair = (item[0], item[1])
        elif isinstance(item, str) and re.fullmatch(r"\s*[-+\d.]+\s*[xX]\s*[-+\d.]+\s*", item):
            pair = re.split(r"[xX]", item.strip())
        else:
            raise GroupedPlateError("machine printable_area contains an unsupported coordinate")
        try:
            point = float(pair[0]), float(pair[1])
        except (TypeError, ValueError) as exc:
            raise GroupedPlateError("machine printable_area contains a non-numeric coordinate") from exc
        if not all(math.isfinite(value) for value in point):
            raise GroupedPlateError("machine printable_area contains a non-finite coordinate")
        points.append(point)
    if len(points) >= 3:
        return tuple(points)

    bed_size = machine_settings.get("bed_size")
    if isinstance(bed_size, (list, tuple)) and len(bed_size) >= 2:
        try:
            width, depth = float(bed_size[0]), float(bed_size[1])
        except (TypeError, ValueError) as exc:
            raise GroupedPlateError("machine bed_size is malformed") from exc
        if math.isfinite(width) and math.isfinite(depth) and width > 0 and depth > 0:
            return ((0.0, 0.0), (width, 0.0), (width, depth), (0.0, depth))
    raise GroupedPlateError("machine profile must define a valid printable_area or bed_size")


def _point_in_polygon(point: tuple[float, float], polygon: tuple[tuple[float, float], ...]) -> bool:
    x, y = point
    inside = False
    previous = polygon[-1]
    for current in polygon:
        x1, y1 = previous
        x2, y2 = current
        cross = (x - x1) * (y2 - y1) - (y - y1) * (x2 - x1)
        if abs(cross) <= 1e-8 and min(x1, x2) - 1e-8 <= x <= max(x1, x2) + 1e-8 and min(y1, y2) - 1e-8 <= y <= max(y1, y2) + 1e-8:
            return True
        if (y1 > y) != (y2 > y):
            crossing_x = (x2 - x1) * (y - y1) / (y2 - y1) + x1
            if x < crossing_x:
                inside = not inside
        previous = current
    return inside


def _as_positive_float(value: Any, sample: str, setting: str) -> float:
    rendered = str(value).strip()
    if setting == "ironing_flow":
        rendered = rendered.removesuffix("%").strip()
    try:
        result = float(Decimal(rendered))
    except (InvalidOperation, ValueError, TypeError) as exc:
        raise GroupedPlateError(f"{sample} {setting} must be a finite positive number") from exc
    if not math.isfinite(result) or result <= 0:
        raise GroupedPlateError(f"{sample} {setting} must be a finite positive number")
    return result
