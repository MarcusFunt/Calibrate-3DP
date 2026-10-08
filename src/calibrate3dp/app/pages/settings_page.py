"""Application-level workspace, diagnostics, and Orca settings."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Callable

from calibrate3dp.app.services.settings_service import AppSettings, AppSettingsService


class SettingsPage:
    """Edit durable application preferences without changing Orca profiles."""

    def __init__(
        self,
        dpg: Any,
        settings_service: AppSettingsService,
        *,
        on_saved: Callable[[AppSettings], str | None] | None = None,
    ) -> None:
        self.dpg = dpg
        self.settings_service = settings_service
        self.on_saved = on_saved
        self.status = ""
        self._rendered = False

    def render(self) -> None:
        if self._rendered:
            self.refresh()
            return
        dpg = self.dpg
        settings = self.settings_service.settings
        with dpg.group(tag="page_settings", show=False):
            dpg.add_spacer(height=12)
            dpg.add_text("SETTINGS", color=(92, 191, 178, 255))
            dpg.add_spacer(height=7)
            dpg.add_text("Local application setup.", color=(238, 244, 249, 255))
            dpg.add_text(
                "Session files stay on this computer. Changing the workspace takes effect after restarting the application.",
                color=(165, 180, 195, 255), wrap=850,
            )
            dpg.add_spacer(height=14)
            dpg.add_input_text(
                label="Workspace root", tag="settings_workspace_root",
                default_value=str(settings.workspace_root), width=700,
            )
            dpg.add_button(label="Browse workspace folder", callback=self._show_workspace_dialog)
            dpg.add_input_text(
                label="Default export folder", tag="settings_export_root",
                default_value=str(settings.default_export_root or ""), width=700,
            )
            dpg.add_button(label="Browse export folder", callback=self._show_export_dialog)
            dpg.add_spacer(height=10)
            dpg.add_text("OFFLINE DIAGNOSTICS", color=(133, 149, 166, 255))
            dpg.add_checkbox(
                label="Include local executable and profile-folder paths",
                tag="settings_include_paths",
                default_value=settings.include_diagnostics_paths,
            )
            dpg.add_checkbox(
                label="Include counts of discovered profile types",
                tag="settings_include_profile_counts",
                default_value=settings.include_diagnostics_profile_counts,
            )
            dpg.add_spacer(height=10)
            dpg.add_text("ORCASLICER OVERRIDES", color=(133, 149, 166, 255))
            dpg.add_input_text(
                label="OrcaSlicer executable", tag="settings_orca_executable",
                default_value=str(settings.orca_executable or ""), width=700,
            )
            dpg.add_button(label="Browse Orca executable", callback=self._show_executable_dialog)
            roots_text = "" if settings.orca_config_roots is None else "\n".join(
                str(root) for root in settings.orca_config_roots
            )
            dpg.add_input_text(
                label="Profile folders, one path per line (blank uses automatic detection)",
                tag="settings_orca_config_roots", default_value=roots_text,
                width=700, height=88, multiline=True,
            )
            dpg.add_button(label="Add Orca profile folder", callback=self._show_config_dialog)
            dpg.add_spacer(height=12)
            dpg.add_button(label="Save settings", tag="settings_save", callback=self._on_save)
            dpg.add_text(self.settings_service.load_error, tag="settings_status", wrap=850,
                         color=(225, 180, 112, 255))

            dpg.add_file_dialog(
                tag="settings_workspace_dialog", show=False, modal=True,
                width=760, height=480, directory_selector=True,
                callback=self._on_workspace_selected,
            )
            dpg.add_file_dialog(
                tag="settings_export_dialog", show=False, modal=True,
                width=760, height=480, directory_selector=True,
                callback=self._on_export_selected,
            )
            dpg.add_file_dialog(
                tag="settings_config_dialog", show=False, modal=True,
                width=760, height=480, directory_selector=True,
                callback=self._on_config_selected,
            )
            with dpg.file_dialog(
                tag="settings_executable_dialog", show=False, modal=True,
                width=760, height=480, directory_selector=False,
                callback=self._on_executable_selected,
            ):
                dpg.add_file_extension(".*", custom_text="[Application executable]")
        self._rendered = True

    def refresh(self) -> None:
        if self._rendered:
            self._set("settings_status", self.status or self.settings_service.load_error)

    def save(self) -> AppSettings:
        """Validate current form values and persist them for the next launch."""
        if not self._rendered:
            raise RuntimeError("settings controls must be rendered before saving")
        raw_roots = self._value("settings_orca_config_roots")
        root_lines = [line.strip() for line in raw_roots.splitlines() if line.strip()]
        settings = AppSettings(
            workspace_root=self._value("settings_workspace_root").strip(),
            default_export_root=self._optional_path("settings_export_root"),
            include_diagnostics_paths=bool(self.dpg.get_value("settings_include_paths")),
            include_diagnostics_profile_counts=bool(
                self.dpg.get_value("settings_include_profile_counts")
            ),
            orca_executable=self._optional_path("settings_orca_executable"),
            orca_config_roots=tuple(Path(line) for line in root_lines) if root_lines else None,
        )
        self.settings_service.save(settings)
        self.status = "Settings saved. Restart the application to use a changed workspace root."
        if self.on_saved is not None:
            message = self.on_saved(settings)
            if message:
                self.status = message
        self.refresh()
        return settings

    def _on_save(self, sender: Any = None, app_data: Any = None) -> None:
        del sender, app_data
        try:
            self.save()
        except (OSError, TypeError, ValueError) as exc:
            self.status = f"Settings could not be saved: {exc}"
            self.refresh()

    def _show_workspace_dialog(self, *_: Any) -> None:
        self.dpg.show_item("settings_workspace_dialog")

    def _show_export_dialog(self, *_: Any) -> None:
        self.dpg.show_item("settings_export_dialog")

    def _show_config_dialog(self, *_: Any) -> None:
        self.dpg.show_item("settings_config_dialog")

    def _show_executable_dialog(self, *_: Any) -> None:
        self.dpg.show_item("settings_executable_dialog")

    def _on_workspace_selected(self, sender: Any, app_data: Any, user_data: Any = None) -> None:
        del sender, user_data
        path = _selected_path(app_data)
        if path:
            self.dpg.set_value("settings_workspace_root", path)

    def _on_export_selected(self, sender: Any, app_data: Any, user_data: Any = None) -> None:
        del sender, user_data
        path = _selected_path(app_data)
        if path:
            self.dpg.set_value("settings_export_root", path)

    def _on_executable_selected(self, sender: Any, app_data: Any, user_data: Any = None) -> None:
        del sender, user_data
        path = _selected_path(app_data)
        if path:
            self.dpg.set_value("settings_orca_executable", path)

    def _on_config_selected(self, sender: Any, app_data: Any, user_data: Any = None) -> None:
        del sender, user_data
        path = _selected_path(app_data)
        if not path:
            return
        current = self._value("settings_orca_config_roots")
        roots = [line.strip() for line in current.splitlines() if line.strip()]
        if path not in roots:
            roots.append(path)
        self.dpg.set_value("settings_orca_config_roots", "\n".join(roots))

    def _optional_path(self, tag: str) -> str | None:
        value = self._value(tag).strip()
        return value or None

    def _value(self, tag: str) -> str:
        value = self.dpg.get_value(tag)
        return value if isinstance(value, str) else ""

    def _set(self, tag: str, value: str) -> None:
        if self.dpg.does_item_exist(tag):
            self.dpg.set_value(tag, value)


def _selected_path(app_data: Any) -> str | None:
    if isinstance(app_data, dict):
        selected = app_data.get("file_path_name") or app_data.get("current_path")
        if selected:
            return str(selected)
    if isinstance(app_data, str):
        return app_data
    return None


__all__ = ["SettingsPage"]
