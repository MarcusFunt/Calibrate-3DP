"""Persistent, application-level preferences for the desktop workbench."""

from __future__ import annotations

from dataclasses import dataclass
import json
import os
from pathlib import Path
import tempfile
from typing import Any, Mapping


SETTINGS_SCHEMA_VERSION = 1


class SettingsError(ValueError):
    """Raised when saved application settings are malformed or unsupported."""


def default_config_directory() -> Path:
    """Return the conventional per-user configuration directory."""
    if os.name == "nt":
        base = os.environ.get("APPDATA") or os.environ.get("LOCALAPPDATA")
        root = Path(base).expanduser() if base else Path.home() / "AppData" / "Roaming"
    elif _is_macos():
        root = Path.home() / "Library" / "Application Support"
    else:
        base = os.environ.get("XDG_CONFIG_HOME")
        root = Path(base).expanduser() if base else Path.home() / ".config"
    return root / "Calibrate-3DP"


def default_workspace_root() -> Path:
    """Return the initial local session workspace directory."""
    if os.name == "nt":
        base = os.environ.get("LOCALAPPDATA") or os.environ.get("APPDATA")
        root = Path(base).expanduser() if base else Path.home() / "AppData" / "Local"
    elif _is_macos():
        root = Path.home() / "Library" / "Application Support"
    else:
        base = os.environ.get("XDG_DATA_HOME")
        root = Path(base).expanduser() if base else Path.home() / ".local" / "share"
    return root / "Calibrate-3DP" / "workspace"


def _is_macos() -> bool:
    import sys

    return sys.platform == "darwin"


def _path(value: Any, name: str, *, optional: bool = False) -> Path | None:
    if value is None and optional:
        return None
    if not isinstance(value, (str, Path)) or not str(value).strip():
        raise SettingsError(f"{name} must be a non-empty path")
    return Path(value).expanduser().resolve(strict=False)


@dataclass(frozen=True)
class AppSettings:
    """User-selected workspace, export, diagnostics, and Orca locations."""

    workspace_root: Path
    default_export_root: Path | None = None
    include_diagnostics_paths: bool = False
    include_diagnostics_profile_counts: bool = False
    orca_executable: Path | None = None
    orca_config_roots: tuple[Path, ...] | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "workspace_root", _path(self.workspace_root, "workspace_root"))
        object.__setattr__(
            self,
            "default_export_root",
            _path(self.default_export_root, "default_export_root", optional=True),
        )
        object.__setattr__(
            self,
            "orca_executable",
            _path(self.orca_executable, "orca_executable", optional=True),
        )
        if self.orca_config_roots is not None:
            roots = tuple(_path(root, "Orca config root") for root in self.orca_config_roots)
            unique = {os.path.normcase(str(root)): root for root in roots}
            object.__setattr__(self, "orca_config_roots", tuple(unique.values()))
        for name in ("include_diagnostics_paths", "include_diagnostics_profile_counts"):
            if type(getattr(self, name)) is not bool:
                raise SettingsError(f"{name} must be a boolean")

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": SETTINGS_SCHEMA_VERSION,
            "workspace_root": str(self.workspace_root),
            "default_export_root": str(self.default_export_root) if self.default_export_root else None,
            "include_diagnostics_paths": self.include_diagnostics_paths,
            "include_diagnostics_profile_counts": self.include_diagnostics_profile_counts,
            "orca_executable": str(self.orca_executable) if self.orca_executable else None,
            "orca_config_roots": (
                None if self.orca_config_roots is None
                else [str(root) for root in self.orca_config_roots]
            ),
        }

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "AppSettings":
        required = {
            "schema_version",
            "workspace_root",
            "default_export_root",
            "include_diagnostics_paths",
            "include_diagnostics_profile_counts",
            "orca_executable",
            "orca_config_roots",
        }
        if not isinstance(payload, Mapping) or set(payload) != required:
            raise SettingsError("settings file has an invalid shape")
        if type(payload["schema_version"]) is not int or payload["schema_version"] != SETTINGS_SCHEMA_VERSION:
            raise SettingsError("unsupported settings schema version")
        roots = payload["orca_config_roots"]
        if roots is not None and (
            not isinstance(roots, list)
            or any(not isinstance(root, str) for root in roots)
        ):
            raise SettingsError("orca_config_roots must be a path list or null")
        return cls(
            workspace_root=payload["workspace_root"],
            default_export_root=payload["default_export_root"],
            include_diagnostics_paths=payload["include_diagnostics_paths"],
            include_diagnostics_profile_counts=payload["include_diagnostics_profile_counts"],
            orca_executable=payload["orca_executable"],
            orca_config_roots=None if roots is None else tuple(roots),
        )


class AppSettingsService:
    """Load and atomically save application preferences as local JSON."""

    def __init__(
        self,
        settings_path: str | Path | None = None,
        *,
        persistent: bool = True,
        initial_settings: AppSettings | None = None,
    ) -> None:
        self.path = (
            Path(settings_path).expanduser().resolve(strict=False)
            if settings_path is not None
            else default_config_directory() / "settings.json" if persistent
            else None
        )
        self.was_loaded_from_disk = False
        self.load_error = ""
        self._settings = initial_settings or AppSettings(
            workspace_root=default_workspace_root(),
            default_export_root=default_workspace_root() / "exports",
        )
        if self.path is not None and self.path.is_file():
            try:
                raw = json.loads(self.path.read_text(encoding="utf-8"))
                if not isinstance(raw, Mapping):
                    raise SettingsError("settings file must contain a JSON object")
                self._settings = AppSettings.from_dict(raw)
                self.was_loaded_from_disk = True
            except (OSError, json.JSONDecodeError, SettingsError, TypeError) as exc:
                self.load_error = f"Settings could not be loaded: {exc}"

    @property
    def settings(self) -> AppSettings:
        return self._settings

    def save(self, settings: AppSettings) -> None:
        if not isinstance(settings, AppSettings):
            raise TypeError("settings must be an AppSettings instance")
        if self.path is not None:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            temporary_path: Path | None = None
            try:
                with tempfile.NamedTemporaryFile(
                    "w", encoding="utf-8", dir=self.path.parent,
                    prefix=f"{self.path.name}.", suffix=".tmp", delete=False,
                ) as temporary:
                    temporary_path = Path(temporary.name)
                    json.dump(settings.to_dict(), temporary, ensure_ascii=False, indent=2)
                    temporary.write("\n")
                    temporary.flush()
                    os.fsync(temporary.fileno())
                os.replace(temporary_path, self.path)
            except OSError:
                if temporary_path is not None:
                    temporary_path.unlink(missing_ok=True)
                raise
        self._settings = settings


__all__ = [
    "AppSettings",
    "AppSettingsService",
    "SETTINGS_SCHEMA_VERSION",
    "SettingsError",
    "default_config_directory",
    "default_workspace_root",
]
