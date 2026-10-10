"""Dependency-free 3MF packaging and G-code validation for grouped samples.

The module packages connected geometry as separate setting-bearing objects and
retains the earlier independent-coupon writer for focused adapter experiments.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
import math
from pathlib import Path
import re
from types import MappingProxyType
import zipfile
import xml.etree.ElementTree as ET
from typing import Any, Mapping

from calibrate3dp.experiments import ExperimentPlan
from calibrate3dp.geometry.layout import PlateLayoutError, require_layout_fits_printable_area
from calibrate3dp.geometry.contracts import GeometryValidation, PlateGeometry
from calibrate3dp.geometry.registry import GeometryBackendUnavailable, get_geometry_backend


class GroupedPlateError(ValueError):
    """Raised when a grouped plate cannot be compiled safely."""


def _validate_geometry_backend(geometry: PlateGeometry) -> GeometryValidation:
    try:
        backend = get_geometry_backend(geometry.backend_id, geometry.backend_version)
    except GeometryBackendUnavailable as exc:
        return GeometryValidation(False, (str(exc),))
    return backend.validate(geometry)


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
class GroupedPlateLayoutValidation:
    """Whether Orca kept every object's XY layout aligned to the plate mesh."""

    valid: bool
    messages: tuple[str, ...]
    xy_translation_mm: tuple[float, float] | None
    object_bounds_mm: Mapping[str, tuple[float, float, float, float]]

    def __post_init__(self) -> None:
        object.__setattr__(self, "object_bounds_mm", MappingProxyType(dict(self.object_bounds_mm)))

    def to_dict(self) -> dict[str, Any]:
        return {
            "valid": self.valid,
            "messages": list(self.messages),
            "xy_translation_mm": list(self.xy_translation_mm) if self.xy_translation_mm is not None else None,
            "object_bounds_mm": {name: list(bounds) for name, bounds in self.object_bounds_mm.items()},
        }


@dataclass(frozen=True)
class GroupedFeatureGcodeValidation:
    """Slicer evidence for CAD labels and the separate code plaque."""

    valid: bool
    messages: tuple[str, ...]
    first_layer_label_moves: Mapping[str, int]
    identifier_top_text_moves: int

    def __post_init__(self) -> None:
        object.__setattr__(self, "first_layer_label_moves", MappingProxyType(dict(self.first_layer_label_moves)))

    def to_dict(self) -> dict[str, Any]:
        return {
            "valid": self.valid,
            "messages": list(self.messages),
            "first_layer_label_moves": dict(self.first_layer_label_moves),
            "identifier_top_text_moves": self.identifier_top_text_moves,
        }


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
_GCODE_AXIS_WORD = re.compile(r"(?:^|\s)([EXYZ])([-+]?(?:\d+(?:\.\d*)?|\.\d+))(?=\s|$)", re.IGNORECASE)
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


def write_plate_geometry_3mf(
    geometry: PlateGeometry,
    *,
    destination: str | Path,
    module_id: str,
    plan_id: str,
    omitted_candidate_ids: set[str] | frozenset[str] = frozenset(),
) -> Path:
    """Package connected mesh objects while preserving Orca per-sample IDs.

    Mesh vertices use plate coordinates and each 3MF build item has an identity
    transform. Each sample object ID matches its model-settings object and part
    ID; frame and connector geometry stays in a separate object without sample
    ironing overrides.
    """
    if not isinstance(geometry, PlateGeometry) or not _validate_geometry_backend(geometry).valid:
        raise GroupedPlateError("connected plate geometry must pass backend validation")
    if not isinstance(module_id, str) or not module_id.strip() or not isinstance(plan_id, str) or not plan_id.strip():
        raise GroupedPlateError("module_id and plan_id must be non-empty strings")
    if isinstance(omitted_candidate_ids, (str, bytes)):
        raise GroupedPlateError("omitted_candidate_ids must be a set of candidate IDs")
    known_ids = {obj.candidate_id for obj in geometry.objects if obj.sample_label is not None}
    if not set(omitted_candidate_ids) <= known_ids:
        raise GroupedPlateError("omitted_candidate_ids contains an unknown candidate")
    destination_path = Path(destination).expanduser().resolve(strict=False)
    if destination_path.suffix.casefold() != ".3mf":
        raise GroupedPlateError("grouped plate destination must use the .3mf extension")
    if destination_path.exists():
        raise GroupedPlateError(f"grouped plate destination already exists: {destination_path}")

    ET.register_namespace("", _CORE_NS)
    model_settings = ET.Element("config")
    build_items: list[tuple[int, str]] = []
    manifest_samples: list[dict[str, Any]] = []
    for object_id, obj in enumerate(geometry.objects, start=1):
        object_config = ET.SubElement(model_settings, "object", {"id": str(object_id)})
        ET.SubElement(object_config, "metadata", {"key": "name", "value": obj.name})
        part = ET.SubElement(object_config, "part", {"id": str(object_id), "subtype": "normal_part"})
        if obj.sample_label is not None:
            applied = obj.candidate_id not in omitted_candidate_ids
            if applied:
                ET.SubElement(part, "metadata", {"key": "ironing_flow", "value": _flow_text(obj.settings.get("ironing_flow"))})
                ET.SubElement(part, "metadata", {"key": "ironing_speed", "value": str(obj.settings.get("ironing_speed"))})
            manifest_samples.append({
                "label": obj.name,
                "physical_label": obj.printed_marking,
                "candidate_id": obj.candidate_id,
                "settings": dict(obj.settings),
                "object_id": object_id,
                "override_written": applied,
            })
        build_items.append((object_id, obj.name))
    model_settings_xml = ET.tostring(model_settings, encoding="utf-8", xml_declaration=True)
    manifest = {
        "schema_version": 2 if geometry.backend_id == "build123d" else 1,
        "module_id": module_id,
        "plan_id": plan_id,
        "plate_code": geometry.layout.plate_code,
        "physical_labels_in_mesh": True,
        "physical_plate_code_in_mesh": True,
        "geometry": {
            "backend_id": geometry.backend_id,
            "backend_version": geometry.backend_version,
            "bounds_mm": {
                "min_x": geometry.layout.bounds.min_x,
                "min_y": geometry.layout.bounds.min_y,
                "min_z": geometry.layout.bounds.min_z,
                "max_x": geometry.layout.bounds.max_x,
                "max_y": geometry.layout.bounds.max_y,
                "max_z": geometry.layout.bounds.max_z,
            },
            "object_count": len(geometry.objects),
            "connection_count": len(geometry.connections),
            "object_bounds_mm": {
                obj.name: list(obj.mesh.bounds or ())
                for obj in geometry.objects
            },
            "provenance": dict(geometry.metadata),
        },
        "objects": [
            {
                "name": obj.name,
                "role": obj.role,
                "physical_marking": obj.printed_marking,
                "candidate_id": obj.candidate_id,
                "metadata": dict(obj.metadata),
            }
            for obj in geometry.objects
        ],
        "connections": [
            {
                "sample_label": item.sample_label,
                "source_name": item.source_name,
                "target_name": item.target_name,
                "contact_area_mm2": item.contact_area_mm2,
                "separation_type": item.separation_type,
            }
            for item in geometry.connections
        ],
        "samples": manifest_samples,
        "frame_object": next((obj.name for obj in geometry.objects if obj.sample_label is None), None),
    }
    destination_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with zipfile.ZipFile(destination_path, "x", compression=zipfile.ZIP_DEFLATED) as archive:
            archive.writestr("[Content_Types].xml", _CONTENT_TYPES)
            archive.writestr("_rels/.rels", _ROOT_RELS)
            with archive.open("3D/3dmodel.model", "w") as stream:
                _write_xml_stream(stream, _geometry_model_lines(geometry, build_items))
            archive.writestr("Metadata/model_settings.config", model_settings_xml)
            import json

            archive.writestr("Metadata/calibrate3dp-run.json", json.dumps(manifest, sort_keys=True, indent=2) + "\n")
    except Exception:
        destination_path.unlink(missing_ok=True)
        raise
    return destination_path


def _geometry_model_lines(geometry: PlateGeometry, build_items: list[tuple[int, str]]):
    yield '<?xml version="1.0" encoding="UTF-8"?>\n'
    yield f'<model xmlns="{_CORE_NS}" unit="millimeter" xml:lang="en-US" version="1.0">\n<resources>\n'
    for object_id, obj in enumerate(geometry.objects, start=1):
        yield f'<object id="{object_id}" type="model" name="{obj.name}"><mesh><vertices>\n'
        for point in obj.mesh.vertices:
            yield f'<vertex x="{point.x:g}" y="{point.y:g}" z="{point.z:g}"/>\n'
        yield '</vertices><triangles>\n'
        for first, second, third in obj.mesh.triangles:
            yield f'<triangle v1="{first}" v2="{second}" v3="{third}"/>\n'
        yield '</triangles></mesh></object>\n'
    yield '</resources><build>\n'
    for object_id, _name in build_items:
        yield (
            f'<item objectid="{object_id}" '
            'transform="1 0 0 0 1 0 0 0 1 0 0 0"/>\n'
        )
    yield '</build></model>\n'


def _write_xml_stream(stream, lines) -> None:
    chunk: list[bytes] = []
    length = 0
    for line in lines:
        encoded = line.encode("utf-8")
        chunk.append(encoded)
        length += len(encoded)
        if length >= 1024 * 1024:
            stream.write(b"".join(chunk))
            chunk.clear()
            length = 0
    if chunk:
        stream.write(b"".join(chunk))


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


def machine_printable_polygon(machine_settings: Mapping[str, Any]) -> tuple[tuple[float, float], ...]:
    """Return the selected Orca machine's printable XY polygon."""
    if not isinstance(machine_settings, Mapping):
        raise GroupedPlateError("selected machine profile has no resolved settings")
    return _printable_polygon(machine_settings)


def machine_keep_out_polygons(machine_settings: Mapping[str, Any]) -> tuple[tuple[tuple[float, float], ...], ...]:
    """Read explicit Orca bed-exclusion polygons; ignore only its 0x0 sentinel."""
    if not isinstance(machine_settings, Mapping):
        raise GroupedPlateError("selected machine profile has no resolved settings")
    raw = machine_settings.get("bed_exclude_area")
    if raw is None or raw == "" or raw == [] or raw == () or raw == ["0x0"] or raw == "0x0":
        return ()
    candidates: list[Any]
    if isinstance(raw, (list, tuple)) and raw and isinstance(raw[0], (list, tuple)) and raw[0] and isinstance(raw[0][0], (list, tuple)):
        candidates = list(raw)
    else:
        candidates = [raw]
    polygons: list[tuple[tuple[float, float], ...]] = []
    for candidate in candidates:
        if isinstance(candidate, (list, tuple)) and len(candidate) == 1 and str(candidate[0]).strip().casefold() == "0x0":
            continue
        point_count = len([item for item in candidate.split(",") if item.strip()]) if isinstance(candidate, str) else len(candidate) if isinstance(candidate, (list, tuple)) else 0
        if point_count < 3:
            raise GroupedPlateError("machine bed_exclude_area must contain at least three points")
        try:
            polygon = _printable_polygon({"printable_area": candidate})
        except GroupedPlateError as exc:
            raise GroupedPlateError("machine bed_exclude_area has an unsupported keep-out polygon") from exc
        if len(polygon) < 3:
            raise GroupedPlateError("machine bed_exclude_area must contain at least three points")
        polygons.append(polygon)
    return tuple(polygons)


def require_plate_geometry_fits_machine(geometry: PlateGeometry, machine_settings: Mapping[str, Any]) -> PlateBounds:
    """Validate all plate features against this machine's bed, keep-outs, and height."""
    if not isinstance(geometry, PlateGeometry) or not isinstance(machine_settings, Mapping):
        raise GroupedPlateError("connected plate and resolved machine settings are required")
    report = _validate_geometry_backend(geometry)
    if not report.valid:
        raise GroupedPlateError("connected plate geometry is invalid: " + "; ".join(report.messages))
    bounds = geometry.layout.bounds
    try:
        require_layout_fits_printable_area(
            geometry.layout,
            machine_printable_polygon(machine_settings),
            machine_keep_out_polygons(machine_settings),
        )
    except PlateLayoutError as exc:
        raise GroupedPlateError(str(exc)) from exc
    height = machine_settings.get("printable_height")
    if height is not None:
        try:
            height_mm = float(height[0] if isinstance(height, (list, tuple)) else height)
        except (TypeError, ValueError, IndexError) as exc:
            raise GroupedPlateError("machine printable_height is malformed") from exc
        if not math.isfinite(height_mm) or height_mm <= 0 or bounds.max_z > height_mm:
            raise GroupedPlateError(
                f"connected plate height {bounds.max_z:g} mm exceeds or cannot be checked against printable_height {height_mm:g} mm"
            )
    return PlateBounds(bounds.min_x, bounds.min_y, bounds.max_x, bounds.max_y, bounds.max_z)


def validate_grouped_plate_layout_gcode(
    gcode: str | Path,
    geometry: PlateGeometry,
    machine_settings: Mapping[str, Any],
    *,
    xy_tolerance_mm: float = 0.8,
) -> GroupedPlateLayoutValidation:
    """Check that Orca preserved each mesh's placement in one shared plate layout.

    Automatic object arrangement can preserve per-object settings while
    physically separating connected mesh parts. Compare positive XY extrusion
    bounds for every named object against the validated mesh bounds, allowing
    only one shared XY translation for the complete plate.
    """
    if not isinstance(geometry, PlateGeometry) or not isinstance(machine_settings, Mapping):
        raise GroupedPlateError("connected plate geometry and resolved machine settings are required")
    if (
        isinstance(xy_tolerance_mm, bool)
        or not isinstance(xy_tolerance_mm, (int, float))
        or not math.isfinite(xy_tolerance_mm)
        or xy_tolerance_mm < 0
    ):
        raise GroupedPlateError("xy_tolerance_mm must be finite and non-negative")
    report = _validate_geometry_backend(geometry)
    messages = list(report.messages)
    if not report.valid:
        return GroupedPlateLayoutValidation(False, tuple(messages), None, {})
    if isinstance(gcode, Path):
        try:
            text = gcode.read_text(encoding="utf-8", errors="replace")
        except OSError as exc:
            raise GroupedPlateError(f"G-code file could not be read: {exc}") from exc
    elif isinstance(gcode, str):
        text = gcode
    else:
        raise GroupedPlateError("gcode must be text or a Path")

    boxes: dict[str, list[float]] = {}
    current_object: str | None = None
    for line in text.splitlines():
        start = _OBJECT_START.match(line)
        if start:
            current_object = start.group(1).strip()
            boxes.setdefault(current_object, [math.inf, math.inf, -math.inf, -math.inf])
            continue
        if re.match(r"^;\s*stop printing object\b", line, re.IGNORECASE):
            current_object = None
            continue
        if current_object is None:
            continue
        command = line.partition(";")[0].lstrip()
        if not re.match(r"^G[0-3](?:\s|$)", command, re.IGNORECASE):
            continue
        words = {key.upper(): float(value) for key, value in _GCODE_AXIS_WORD.findall(command)}
        if words.get("E", 0.0) <= 0 or "X" not in words or "Y" not in words:
            continue
        box = boxes[current_object]
        box[0] = min(box[0], words["X"])
        box[1] = min(box[1], words["Y"])
        box[2] = max(box[2], words["X"])
        box[3] = max(box[3], words["Y"])

    expected = {item.name: item.mesh.bounds for item in geometry.objects}
    actual = {name: tuple(box) for name, box in boxes.items() if box[0] != math.inf}
    if set(actual) != set(expected):
        missing = sorted(set(expected) - set(actual))
        unexpected = sorted(set(actual) - set(expected))
        if missing:
            messages.append("G-code has no positive XY extrusion bounds for: " + ", ".join(missing))
        if unexpected:
            messages.append("G-code contains unrecognized object toolpaths: " + ", ".join(unexpected))

    translation: tuple[float, float] | None = None
    frame_mesh = expected.get("Plate-Frame")
    frame_toolpath = actual.get("Plate-Frame")
    if frame_mesh is not None and frame_toolpath is not None:
        raw_translation = (frame_toolpath[0] - frame_mesh[0], frame_toolpath[1] - frame_mesh[1])
        # Orca rounds toolpath coordinates, so subtracting otherwise identical
        # bounds can leave tiny floating-point noise in a nominal zero shift.
        translation = tuple(0.0 if abs(value) < 1e-6 else value for value in raw_translation)
        for name in sorted(set(expected) & set(actual)):
            mesh_bounds = expected[name]
            toolpath = actual[name]
            if mesh_bounds is None:
                messages.append(f"{name} mesh has no bounds for G-code placement validation")
                continue
            for axis, minimum_index, maximum_index, mesh_maximum_index, shift in (
                ("X", 0, 2, 3, translation[0]),
                ("Y", 1, 3, 4, translation[1]),
            ):
                if abs(toolpath[minimum_index] - (mesh_bounds[minimum_index] + shift)) > xy_tolerance_mm or abs(
                    toolpath[maximum_index] - (mesh_bounds[mesh_maximum_index] + shift)
                ) > xy_tolerance_mm:
                    messages.append(f"{name} G-code {axis} bounds do not preserve the shared mesh layout")
        try:
            require_layout_fits_printable_area(
                geometry.layout,
                machine_printable_polygon(machine_settings),
                machine_keep_out_polygons(machine_settings),
                translation_mm=translation,
            )
        except (GroupedPlateError, PlateLayoutError) as exc:
            messages.append("translated G-code plate layout is outside the selected machine area: " + str(exc))

    return GroupedPlateLayoutValidation(not messages, tuple(messages), translation, actual)


def validate_build123d_feature_toolpaths(
    gcode: str | Path,
    geometry: PlateGeometry,
    *,
    z_tolerance_mm: float = 0.3,
) -> GroupedFeatureGcodeValidation:
    """Require first-layer sample glyphs and top-side plaque text in sliced G-code.

    The proof is deliberately limited to feature presence and spatial placement.
    It does not infer readability, adhesion, successful bridging, or print safety.
    """
    if not isinstance(geometry, PlateGeometry) or geometry.backend_id != "build123d":
        return GroupedFeatureGcodeValidation(True, (), {}, 0)
    if isinstance(gcode, Path):
        try:
            text = gcode.read_text(encoding="utf-8", errors="replace")
        except OSError as exc:
            raise GroupedPlateError(f"G-code file could not be read: {exc}") from exc
    elif isinstance(gcode, str):
        text = gcode
    else:
        raise GroupedPlateError("gcode must be text or a Path")
    if not math.isfinite(z_tolerance_mm) or z_tolerance_mm <= 0:
        raise GroupedPlateError("z_tolerance_mm must be finite and positive")

    moves: dict[str, list[tuple[float, float, float]]] = {}
    current_object: str | None = None
    current_z = 0.0
    for line in text.splitlines():
        start = _OBJECT_START.match(line)
        if start:
            current_object = start.group(1).strip()
            moves.setdefault(current_object, [])
            continue
        if re.match(r"^;\s*stop printing object\b", line, re.IGNORECASE):
            current_object = None
            continue
        if current_object is None:
            continue
        command = line.partition(";")[0].strip()
        if not re.match(r"^G[0-3](?:\s|$)", command, re.IGNORECASE):
            continue
        words = {key.upper(): float(value) for key, value in _GCODE_AXIS_WORD.findall(command)}
        if "Z" in words:
            current_z = words["Z"]
        if words.get("E", 0.0) > 0 and "X" in words and "Y" in words:
            moves[current_object].append((words["X"], words["Y"], words.get("Z", current_z)))

    messages: list[str] = []
    expected_names = {item.name for item in geometry.objects}
    if set(moves) != expected_names:
        missing = sorted(expected_names - set(moves))
        unexpected = sorted(set(moves) - expected_names)
        if missing:
            messages.append("feature G-code is missing object sections: " + ", ".join(missing))
        if unexpected:
            messages.append("feature G-code contains unrecognized object sections: " + ", ".join(unexpected))

    def inside(x: float, y: float, bounds: Any) -> bool:
        return bounds[0] - 0.05 <= x <= bounds[2] + 0.05 and bounds[1] - 0.05 <= y <= bounds[3] + 0.05

    label_counts: dict[str, int] = {}
    for obj in geometry.objects:
        if obj.sample_label is None:
            continue
        points = moves.get(obj.name, ())
        if not points:
            messages.append(f"{obj.name} has no positive extrusion for its underside label")
            label_counts[obj.name] = 0
            continue
        first_z = min(point[2] for point in points)
        bounds = obj.metadata.get("text_bounds_mm")
        if not isinstance(bounds, (list, tuple)) or len(bounds) != 4:
            messages.append(f"{obj.name} has no measured underside glyph bounds")
            label_counts[obj.name] = 0
            continue
        count = sum(
            1 for x, y, z in points
            if z <= first_z + 0.05 and inside(x, y, bounds)
        )
        label_counts[obj.name] = count
        if count == 0:
            messages.append(f"{obj.name} has no first-layer extrusion inside its underside glyph bounds")

    identifier = next((item for item in geometry.objects if item.name == "Plate-Identifier"), None)
    identifier_moves = 0
    if identifier is None:
        messages.append("CAD geometry has no Plate-Identifier object")
    else:
        bounds = identifier.metadata.get("text_bounds_mm")
        top_z = identifier.metadata.get("text_top_z_mm")
        if not isinstance(bounds, (list, tuple)) or len(bounds) != 4 or not isinstance(top_z, (int, float)):
            messages.append("Plate-Identifier has no measured top-text bounds")
        else:
            identifier_moves = sum(
                1 for x, y, z in moves.get(identifier.name, ())
                if z >= float(top_z) - z_tolerance_mm and inside(x, y, bounds)
            )
            if identifier_moves == 0:
                messages.append("Plate-Identifier has no top-side text extrusion near its measured text bounds")
    return GroupedFeatureGcodeValidation(not messages, tuple(messages), label_counts, identifier_moves)


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
        comment = command.partition(";")[2].strip()
        words = {key.upper(): float(value) for key, value in _GCODE_WORD.findall(command)}
        if "F" in words and words["F"] > 0:
            current_feed_mm_s = words["F"] / 60.0
        if not re.match(r"^ironing(?:\s|$)", comment, re.IGNORECASE):
            # Orca can emit positive-E unretracts inside a TYPE:Ironing section.
            # Only count explicitly annotated ironing extrusion moves as flow
            # and speed evidence; section membership alone is insufficient.
            continue
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
