"""Typed, immutable specifications for versioned CAD geometry recipes."""

from __future__ import annotations

from dataclasses import asdict, dataclass
import math
from typing import Any, Mapping

from calibrate3dp.geometry.font_asset import FONT_ASSET_ID, FONT_RELATIVE_PATH, FONT_SHA256, FONT_STYLE


@dataclass(frozen=True)
class UndersideLabelSpec:
    mode: str
    pocket_width_mm: float
    pocket_depth_mm: float
    pocket_depth_z_mm: float
    text_size_mm: float
    font_asset_id: str
    font_relative_path: str
    font_sha256: str
    font_style: str


@dataclass(frozen=True)
class IdentifierPlaqueSpec:
    corner: str
    width_mm: float
    depth_mm: float
    thickness_mm: float
    text_size_mm: float
    relief_mm: float
    tab_width_mm: float
    font_asset_id: str
    font_relative_path: str
    font_sha256: str
    font_style: str


@dataclass(frozen=True)
class TessellationSpec:
    linear_tolerance_mm: float
    angular_tolerance_rad: float
    vertex_weld_tolerance_mm: float


@dataclass(frozen=True)
class GeometryRecipeSpec:
    recipe_id: str
    recipe_version: str
    layout_version: str
    backend_id: str
    backend_version: str
    label: UndersideLabelSpec
    identifier: IdentifierPlaqueSpec
    tessellation: TessellationSpec

    @classmethod
    def from_layout_options(cls, values: Mapping[str, Any]) -> "GeometryRecipeSpec":
        if not isinstance(values, Mapping):
            raise ValueError("geometry recipe inputs must be a mapping")
        font_values = (
            values.get("font_asset_id"),
            values.get("font_relative_path"),
            values.get("font_sha256"),
            values.get("font_style"),
        )
        expected_font = (FONT_ASSET_ID, FONT_RELATIVE_PATH, FONT_SHA256, FONT_STYLE)
        if font_values != expected_font:
            raise ValueError("geometry recipe font identity does not match the pinned asset")
        label = UndersideLabelSpec(
            mode="underside-pocketed-emboss",
            pocket_width_mm=_positive(values["label_pocket_width_mm"], "label pocket width"),
            pocket_depth_mm=_positive(values["label_pocket_depth_mm"], "label pocket depth"),
            pocket_depth_z_mm=_positive(values["label_pocket_depth_z_mm"], "label pocket Z depth"),
            text_size_mm=_positive(values["label_text_size_mm"], "label text size"),
            font_asset_id=font_values[0],
            font_relative_path=font_values[1],
            font_sha256=font_values[2],
            font_style=font_values[3],
        )
        identifier = IdentifierPlaqueSpec(
            corner=str(values["identifier_corner"]),
            width_mm=_positive(values["identifier_width_mm"], "identifier width"),
            depth_mm=_positive(values["identifier_depth_mm"], "identifier depth"),
            thickness_mm=_positive(values["identifier_thickness_mm"], "identifier thickness"),
            text_size_mm=_positive(values["identifier_text_size_mm"], "identifier text size"),
            relief_mm=_positive(values["identifier_relief_mm"], "identifier relief"),
            tab_width_mm=_positive(values["identifier_tab_width_mm"], "identifier tab width"),
            font_asset_id=font_values[0],
            font_relative_path=font_values[1],
            font_sha256=font_values[2],
            font_style=font_values[3],
        )
        if identifier.corner not in {"front_left", "front_right", "back_left", "back_right"}:
            raise ValueError("identifier corner must be one of the four plate corners")
        tessellation = TessellationSpec(
            linear_tolerance_mm=_positive(values["mesh_linear_tolerance_mm"], "mesh linear tolerance"),
            angular_tolerance_rad=_positive(values["mesh_angular_tolerance_rad"], "mesh angular tolerance"),
            vertex_weld_tolerance_mm=_positive(values["vertex_weld_tolerance_mm"], "vertex weld tolerance"),
        )
        return cls(
            recipe_id=str(values["recipe_id"]),
            recipe_version=str(values["recipe_version"]),
            layout_version=str(values["layout_version"]),
            backend_id=str(values["geometry_backend"]),
            backend_version=str(values["geometry_backend_version"]),
            label=label,
            identifier=identifier,
            tessellation=tessellation,
        )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _positive(value: Any, name: str) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{name} must be finite and positive") from exc
    if isinstance(value, bool) or not math.isfinite(number) or number <= 0:
        raise ValueError(f"{name} must be finite and positive")
    return number
