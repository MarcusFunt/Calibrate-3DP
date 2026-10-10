"""Shared request builder for grouped ironing preview and generation."""

from __future__ import annotations

from typing import Any, Mapping

from calibrate3dp.domain.experiment_config import default_layout_options
from calibrate3dp.experiments import ExperimentPlan
from calibrate3dp.geometry.layout import PlateLayoutRequest, PlateSampleRequest
from calibrate3dp.grouped_plate import machine_keep_out_polygons, machine_printable_polygon


_GEOMETRY_OPTION_KEYS = (
    "margin_mm", "specimen_width_mm", "specimen_depth_mm", "specimen_height_mm",
    "gap_mm", "connector_width_mm", "connector_height_mm", "connector_gap_mm",
    "frame_width_mm", "voxel_mm", "label_pixel_mm", "code_pixel_mm",
)


def grouped_ironing_layout_request(
    plan: ExperimentPlan,
    plate_code: str,
    machine_settings: Mapping[str, Any],
    layout_options: Mapping[str, Any] | None = None,
) -> PlateLayoutRequest:
    """Build the exact validated layout request used by preview and mesh generation."""
    if not 1 <= len(plan.candidates) <= 9:
        raise ValueError("connected grouped plate generation supports one to nine candidates")
    options = dict(default_layout_options() if layout_options is None else layout_options)
    if options.get("strategy") != "connected-grid" or options.get("rows") != 3 or options.get("columns") != 3:
        raise ValueError("unsupported grouped ironing layout strategy")
    return PlateLayoutRequest(
        samples=tuple(
            PlateSampleRequest(
                label=label,
                candidate_id=candidate.candidate_id,
                settings=candidate.overrides,
            )
            for label, candidate in zip("ABCDEFGHI"[:len(plan.candidates)], plan.candidates, strict=True)
        ),
        plate_code=plate_code,
        printable_polygon=machine_printable_polygon(machine_settings),
        keep_outs=machine_keep_out_polygons(machine_settings),
        **{key: options[key] for key in _GEOMETRY_OPTION_KEYS},
    )
