"""Bounded modal G-code preflight; it is not a hardware safety certification."""

from __future__ import annotations

from dataclasses import dataclass
import math
from pathlib import Path
import re
from typing import Any, Mapping

from calibrate3dp.grouped_plate import machine_keep_out_polygons, machine_printable_polygon


_WORD = re.compile(r"([A-Z])([+-]?(?:\d+(?:\.\d*)?|\.\d+))", re.IGNORECASE)
_SUPPORTED_SIMPLE = {
    "G4", "G17", "G18", "G19", "G21", "G90", "G91",
    "M82", "M83", "M104", "M106", "M107", "M109", "M117", "M118",
    "M140", "M190", "M204", "M205", "M220", "M221", "M400", "M73",
}


@dataclass(frozen=True)
class GcodePreflightReport:
    status: str
    passed: bool
    parser_coverage_complete: bool
    commands_observed: tuple[str, ...]
    unsupported_commands: tuple[str, ...]
    errors: tuple[str, ...]
    unverified: tuple[str, ...]
    movement_count: int
    extrusion_move_count: int
    travel_move_count: int
    temperature_commands: tuple[Mapping[str, Any], ...]
    temperature_sequence_verified: bool
    movement_bounds_mm: Mapping[str, float] | None
    printable_area_checked: bool
    keep_out_count: int
    z_limit_checked: bool

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": 1,
            "status": self.status,
            "passed": self.passed,
            "parser_coverage_complete": self.parser_coverage_complete,
            "commands_observed": list(self.commands_observed),
            "unsupported_commands": list(self.unsupported_commands),
            "errors": list(self.errors),
            "unverified": list(self.unverified),
            "movement_count": self.movement_count,
            "extrusion_move_count": self.extrusion_move_count,
            "travel_move_count": self.travel_move_count,
            "temperature_commands": [dict(item) for item in self.temperature_commands],
            "temperature_sequence_verified": self.temperature_sequence_verified,
            "movement_bounds_mm": dict(self.movement_bounds_mm) if self.movement_bounds_mm else None,
            "printable_area_checked": self.printable_area_checked,
            "keep_out_count": self.keep_out_count,
            "z_limit_checked": self.z_limit_checked,
        }


def validate_gcode_preflight(
    gcode: str | Path,
    machine_settings: Mapping[str, Any],
    filament_settings: Mapping[str, Any] | None = None,
    process_settings: Mapping[str, Any] | None = None,
) -> GcodePreflightReport:
    """Interpret a limited linear-motion subset and report every coverage gap."""
    if isinstance(gcode, Path):
        text = gcode.read_text(encoding="utf-8", errors="replace")
    elif isinstance(gcode, str):
        text = gcode
    else:
        raise TypeError("gcode must be text or a Path")
    if not isinstance(machine_settings, Mapping):
        raise TypeError("machine_settings must be a mapping")
    filament_settings = filament_settings or {}
    process_settings = process_settings or {}
    errors: list[str] = []
    unverified: list[str] = []
    unsupported: set[str] = set()
    observed: set[str] = set()
    temperature_commands: list[Mapping[str, Any]] = []
    printable = None
    keepouts = ()
    try:
        printable = machine_printable_polygon(machine_settings)
        keepouts = machine_keep_out_polygons(machine_settings)
    except (TypeError, ValueError):
        unverified.append("Printable XY polygon or keep-out data is unavailable or unsupported.")
    z_limit = _numeric_setting(machine_settings, ("printable_height", "max_print_height", "printer_max_z"))
    if z_limit is None:
        unverified.append("Machine Z limit is unavailable.")

    xyz_absolute = True
    e_absolute = True
    units_mm: bool | None = None
    current: dict[str, float | None] = {"X": None, "Y": None, "Z": None, "E": 0.0}
    motion_mode: str | None = None
    movement_count = extrusion_count = travel_count = 0
    first_extrusion_line: int | None = None
    temperature_waits = {"nozzle": False, "bed": False}
    bounds = {"min_x": math.inf, "min_y": math.inf, "min_z": math.inf, "max_x": -math.inf, "max_y": -math.inf, "max_z": -math.inf}
    max_temperature: dict[str, float | None] = {"nozzle": _numeric_setting(filament_settings, ("nozzle_temperature_range_high", "hotend_temp_max")), "bed": _numeric_setting(filament_settings, ("bed_temperature_range_high", "bed_temp_max"))}
    min_temperature: dict[str, float | None] = {"nozzle": _numeric_setting(filament_settings, ("nozzle_temperature_range_low", "hotend_temp_min")), "bed": _numeric_setting(filament_settings, ("bed_temperature_range_low", "bed_temp_min"))}
    for settings in (machine_settings, process_settings):
        for kind, keys in (
            ("nozzle", ("nozzle_temperature_range_low", "hotend_temp_min")),
            ("bed", ("bed_temperature_range_low", "bed_temp_min")),
        ):
            value = _numeric_setting(settings, keys)
            if value is not None and (min_temperature[kind] is None or value > min_temperature[kind]):
                min_temperature[kind] = value
        for kind, keys in (
            ("nozzle", ("nozzle_temperature_range_high", "hotend_temp_max")),
            ("bed", ("bed_temperature_range_high", "bed_temp_max")),
        ):
            value = _numeric_setting(settings, keys)
            if value is not None and (max_temperature[kind] is None or value < max_temperature[kind]):
                max_temperature[kind] = value

    for kind in ("nozzle", "bed"):
        if min_temperature[kind] is None or max_temperature[kind] is None:
            unverified.append(f"{kind.capitalize()} temperature limits are unavailable or incomplete.")

    for line_no, original in enumerate(text.splitlines(), start=1):
        line = original.partition(";")[0].strip().upper()
        if not line:
            continue
        line = line.split("*", 1)[0].strip()
        words = {letter: float(value) for letter, value in _WORD.findall(line)}
        command_match = re.search(r"\b([GMT])\s*(\d+(?:\.\d+)?)", line)
        if command_match is None:
            if any(key in words for key in "XYZEF") and motion_mode in {"G0", "G1"}:
                command = motion_mode
            else:
                unverified.append(f"Line {line_no}: commandless parameter block was not interpreted.")
                continue
        else:
            command = f"{command_match.group(1)}{command_match.group(2)}"
            observed.add(command)
        if command.startswith("T"):
            unsupported.add(command)
            continue
        if command in {"G2", "G3", "G2.0", "G3.0", "G28", "G20", "G29", "G30"}:
            unsupported.add(command)
            if command in {"G2", "G3", "G2.0", "G3.0"}:
                errors.append(f"Line {line_no}: arc move {command} is unsupported; its full path envelope is unknown.")
            elif command == "G20":
                errors.append(f"Line {line_no}: inch units are unsupported; millimeter assumptions would be unsafe.")
            else:
                unverified.append(f"Line {line_no}: homing/probing command {command} has machine-specific motion that is not modeled.")
            continue
        if command not in _SUPPORTED_SIMPLE and command not in {"G0", "G1", "G92"}:
            unsupported.add(command)
            continue
        if command == "G90":
            xyz_absolute = True
            continue
        if command == "G91":
            xyz_absolute = False
            continue
        if command == "G21":
            units_mm = True
            continue
        if command == "M82":
            e_absolute = True
            continue
        if command == "M83":
            e_absolute = False
            continue
        if command == "G92":
            if movement_count and any(axis in words for axis in "XYZ"):
                unverified.append(
                    f"Line {line_no}: G92 XYZ coordinate reset after motion is not modeled; movement bounds are incomplete."
                )
            for axis in "XYZE":
                if axis in words:
                    current[axis] = words[axis]
            continue
        if command in {"M104", "M109", "M140", "M190"}:
            kind = "bed" if command in {"M140", "M190"} else "nozzle"
            target = words.get("S", words.get("R"))
            if target is None:
                unverified.append(f"Line {line_no}: {command} has no parsed target temperature.")
                continue
            blocking_wait = command in {"M109", "M190"}
            heater_off = target == 0
            temperature_commands.append({"line": line_no, "command": command, "kind": kind, "target_c": target, "blocking_wait": blocking_wait, "heater_off": heater_off})
            if blocking_wait and not heater_off:
                temperature_waits[kind] = True
            if target < 0:
                errors.append(f"Line {line_no}: negative {kind} temperature target {target:g}°C.")
            elif heater_off:
                # Orca emits zero setpoints to switch heaters off at the end of a job.
                # That control command is not a material printing temperature.
                continue
            lower, upper = min_temperature[kind], max_temperature[kind]
            if lower is None or upper is None:
                unverified.append(f"{kind.capitalize()} temperature limits are unavailable for an observed {kind} target.")
            elif target < lower or target > upper:
                errors.append(f"Line {line_no}: {kind} target {target:g}°C is outside configured limits {lower:g}..{upper:g}°C.")
            continue
        if command not in {"G0", "G1"}:
            continue
        motion_mode = command
        if units_mm is not True:
            if units_mm is None:
                unverified.append(f"Line {line_no}: movement units were not explicitly set to millimeters with G21.")
            else:
                errors.append(f"Line {line_no}: movement units are not millimeters.")
            continue
        target = dict(current)
        moved = False
        for axis in "XYZ":
            if axis not in words:
                continue
            moved = True
            if xyz_absolute:
                target[axis] = words[axis]
            elif current[axis] is not None:
                target[axis] = current[axis] + words[axis]
            else:
                target[axis] = None
        previous_e = current["E"]
        next_e = words.get("E", previous_e)
        if "E" in words and not e_absolute:
            next_e = (previous_e or 0.0) + words["E"]
        delta_e = (next_e or 0.0) - (previous_e or 0.0)
        current["E"] = next_e
        if not moved:
            if delta_e > 1e-8:
                extrusion_count += 1
                if first_extrusion_line is None:
                    first_extrusion_line = line_no
                if any(current[axis] is None for axis in "XYZ"):
                    unverified.append(f"Line {line_no}: stationary extrusion occurs at an unresolved XYZ position.")
                else:
                    point = (float(current["X"]), float(current["Y"]))
                    z_value = float(current["Z"])
                    if printable is not None and not _inside(point, printable):
                        errors.append(f"Line {line_no}: stationary extrusion is outside the printable XY area.")
                    if any(_inside(point, polygon) for polygon in keepouts):
                        errors.append(f"Line {line_no}: stationary extrusion is inside a bed keep-out.")
                    if z_value < -1e-6 or (z_limit is not None and z_value > z_limit):
                        errors.append(f"Line {line_no}: stationary extrusion is outside the configured Z range.")
            continue
        movement_count += 1
        if delta_e > 1e-8:
            extrusion_count += 1
            if first_extrusion_line is None:
                first_extrusion_line = line_no
        else:
            travel_count += 1
        if any(target[axis] is None for axis in "XYZ") or any(current[axis] is None for axis in "XYZ"):
            unverified.append(f"Line {line_no}: movement envelope has an unresolved starting or ending XYZ position.")
            current.update({axis: target[axis] for axis in "XYZ"})
            continue
        start = tuple(float(current[axis]) for axis in "XY")
        end = tuple(float(target[axis]) for axis in "XY")
        start_z, end_z = float(current["Z"]), float(target["Z"])
        if printable is not None:
            if not _inside(start, printable) or not _inside(end, printable) or _crosses_polygon(start, end, printable, allow_endpoint_contacts=True):
                errors.append(f"Line {line_no}: {command} movement leaves the configured printable XY area.")
        for polygon_index, polygon in enumerate(keepouts, start=1):
            if _inside(start, polygon) or _inside(end, polygon) or _crosses_polygon(start, end, polygon, allow_endpoint_contacts=False):
                errors.append(f"Line {line_no}: {command} movement enters or crosses bed keep-out {polygon_index}.")
        if start_z < -1e-6 or end_z < -1e-6 or (z_limit is not None and max(start_z, end_z) > z_limit):
            errors.append(f"Line {line_no}: Z movement is outside the configured 0..{z_limit if z_limit is not None else 'unknown'} mm range.")
        for x, y, z in ((start[0], start[1], start_z), (end[0], end[1], end_z)):
            bounds["min_x"] = min(bounds["min_x"], x)
            bounds["max_x"] = max(bounds["max_x"], x)
            bounds["min_y"] = min(bounds["min_y"], y)
            bounds["max_y"] = max(bounds["max_y"], y)
            bounds["min_z"] = min(bounds["min_z"], z)
            bounds["max_z"] = max(bounds["max_z"], z)
        current.update({axis: target[axis] for axis in "XYZ"})

    if not temperature_commands:
        unverified.append("No nozzle or bed setpoint command was recognized.")
    required_temperature_kinds = {"nozzle", "bed"}
    temperature_sequence_verified = first_extrusion_line is not None
    if first_extrusion_line is not None:
        for kind in sorted(required_temperature_kinds):
            preceding = [
                item for item in temperature_commands
                if item["kind"] == kind and item["line"] < first_extrusion_line
            ]
            last_target = preceding[-1] if preceding else None
            targeted = last_target is not None and not last_target["heater_off"]
            waited = targeted and bool(last_target["blocking_wait"])
            if not targeted or not waited:
                temperature_sequence_verified = False
                unverified.append(f"A {kind} target and blocking wait were not both observed before first extrusion.")
    if movement_count == 0:
        unverified.append("No supported linear movement was recognized.")
    if printable is None:
        unverified.append("XY movement bounds were not checked against a machine polygon.")
    if z_limit is None:
        unverified.append("Z movement bounds were not checked against a machine limit.")
    if unsupported:
        unverified.append("Unsupported commands prevent complete motion and setup coverage: " + ", ".join(sorted(unsupported)))
    bounds_result = None if not math.isfinite(bounds["min_x"]) else bounds
    passed = not errors and not unsupported and not unverified
    status = "failed" if errors else "unsupported" if unsupported else "unverified" if unverified else "subset_checked"
    return GcodePreflightReport(
        status, passed, not unsupported and not unverified, tuple(sorted(observed)), tuple(sorted(unsupported)),
        tuple(dict.fromkeys(errors)), tuple(dict.fromkeys(unverified)), movement_count, extrusion_count,
        travel_count, tuple(temperature_commands), temperature_sequence_verified, bounds_result,
        printable is not None, len(keepouts), z_limit is not None,
    )


def _numeric_setting(settings: Mapping[str, Any], keys: tuple[str, ...]) -> float | None:
    for key in keys:
        value = settings.get(key)
        if isinstance(value, (list, tuple)):
            value = value[0] if value else None
        try:
            number = float(str(value).removesuffix("°C").strip())
        except (TypeError, ValueError):
            continue
        if math.isfinite(number) and number >= 0:
            return number
    return None


def _inside(point: tuple[float, float], polygon: tuple[tuple[float, float], ...]) -> bool:
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


def _crosses_polygon(
    start: tuple[float, float], end: tuple[float, float], polygon: tuple[tuple[float, float], ...], *, allow_endpoint_contacts: bool
) -> bool:
    previous = polygon[-1]
    for current in polygon:
        if _segments_intersect(start, end, previous, current):
            if allow_endpoint_contacts and (start in {previous, current} or end in {previous, current}):
                previous = current
                continue
            return True
        previous = current
    return False


def _segments_intersect(a, b, c, d) -> bool:
    def orient(p, q, r):
        return (q[0] - p[0]) * (r[1] - p[1]) - (q[1] - p[1]) * (r[0] - p[0])

    def on_segment(p, q, r):
        return abs(orient(p, q, r)) <= 1e-8 and min(p[0], q[0]) - 1e-8 <= r[0] <= max(p[0], q[0]) + 1e-8 and min(p[1], q[1]) - 1e-8 <= r[1] <= max(p[1], q[1]) + 1e-8

    o1, o2, o3, o4 = orient(a, b, c), orient(a, b, d), orient(c, d, a), orient(c, d, b)
    if ((o1 > 1e-8 and o2 < -1e-8) or (o1 < -1e-8 and o2 > 1e-8)) and ((o3 > 1e-8 and o4 < -1e-8) or (o3 < -1e-8 and o4 > 1e-8)):
        return True
    return on_segment(a, b, c) or on_segment(a, b, d) or on_segment(c, d, a) or on_segment(c, d, b)
