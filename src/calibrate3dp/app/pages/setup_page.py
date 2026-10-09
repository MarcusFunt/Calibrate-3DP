"""Orca installation and local profile-root setup panel."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Callable

from calibrate3dp.app.services.profile_service import OrcaSetupState, ProfileService
from calibrate3dp.app.services.settings_service import AppSettings


class SetupPage:
    """Render setup state and keep recovery actions available offline."""

    def __init__(
        self,
        service: ProfileService,
        dpg: Any,
        *,
        on_profiles_changed: Callable[[], None] | None = None,
        diagnostics_options: Callable[[], AppSettings] | None = None,
    ) -> None:
        self.service = service
        self.dpg = dpg
        self.on_profiles_changed = on_profiles_changed
        self.diagnostics_options = diagnostics_options

    def render(self) -> None:
        dpg = self.dpg
        with dpg.child_window(width=-1, height=168, border=True, tag="orca_setup_panel"):
            with dpg.group(horizontal=True):
                dpg.add_text("ORCASLICER SETUP", color=(92, 191, 178, 255))
                dpg.add_spacer(width=10)
                dpg.add_text("Offline import remains available", color=(165, 180, 195, 255))
            dpg.add_spacer(height=7)
            dpg.add_text("", tag="orca_setup_executable", wrap=800)
            dpg.add_text("", tag="orca_setup_roots", wrap=800)
            dpg.add_text("", tag="orca_setup_version", wrap=800)
            dpg.add_text("", tag="orca_setup_compatibility", wrap=800,
                         color=(225, 180, 112, 255))
            dpg.add_text("", tag="orca_setup_error", wrap=800,
                         color=(235, 130, 125, 255))
            dpg.add_spacer(height=5)
            with dpg.group(horizontal=True):
                dpg.add_button(label="Browse Orca executable", callback=self._show_executable_dialog)
                dpg.add_button(label="Add profile folder", callback=self._show_config_dialog)
                dpg.add_button(label="Recheck Orca", callback=self._on_recheck)
                dpg.add_button(label="Export offline diagnostics", callback=self._show_diagnostics_dialog)

        self._add_dialogs()
        self._render_state(self.service.setup_state)

    def _add_dialogs(self) -> None:
        dpg = self.dpg
        with dpg.file_dialog(
            tag="orca_executable_dialog", show=False, modal=True,
            width=760, height=480, directory_selector=False,
            callback=self._on_executable_selected,
        ):
            dpg.add_file_extension(".*", custom_text="[Application executable]")

        dpg.add_file_dialog(
            tag="orca_config_root_dialog", show=False, modal=True,
            width=760, height=480, directory_selector=True,
            callback=self._on_config_root_selected,
        )

        with dpg.file_dialog(
            tag="orca_diagnostics_dialog", show=False, modal=True,
            width=760, height=480, directory_selector=False,
            default_filename="calibrate3dp-diagnostics.json",
            callback=self._on_diagnostics_destination,
        ):
            dpg.add_file_extension(".json", custom_text="[JSON diagnostics]")

    def _show_executable_dialog(self, *_: Any) -> None:
        self.dpg.show_item("orca_executable_dialog")

    def _show_config_dialog(self, *_: Any) -> None:
        self.dpg.show_item("orca_config_root_dialog")

    def _show_diagnostics_dialog(self, *_: Any) -> None:
        self.dpg.show_item("orca_diagnostics_dialog")

    def _on_executable_selected(self, sender: Any, app_data: Any, user_data: Any = None) -> None:
        del sender, user_data
        path = _selected_path(app_data)
        if path:
            self.service.set_executable(path)
            self._on_recheck()

    def _on_config_root_selected(self, sender: Any, app_data: Any, user_data: Any = None) -> None:
        del sender, user_data
        path = _selected_path(app_data)
        if path:
            roots = (*self.service.setup_state.config_roots, Path(path))
            self.service.set_config_roots(roots)
            self.service.discover_profiles()
            if self.on_profiles_changed:
                self.on_profiles_changed()
            self._render_state(self.service.setup_state)

    def _on_recheck(self, sender: Any = None, app_data: Any = None, user_data: Any = None) -> None:
        del sender, app_data, user_data
        self._render_state(self.service.check_setup())

    def _on_diagnostics_destination(
        self, sender: Any, app_data: Any, user_data: Any = None
    ) -> None:
        del sender, user_data
        destination = _selected_path(app_data)
        if not destination:
            return
        try:
            options = self.diagnostics_options() if self.diagnostics_options else None
            path = self.service.export_diagnostics(
                destination,
                include_paths=options.include_diagnostics_paths if options else True,
                include_profile_counts=(
                    options.include_diagnostics_profile_counts if options else True
                ),
            )
        except OSError as exc:
            self.dpg.set_value("orca_setup_error", f"Diagnostics could not be saved: {exc}")
        else:
            self.dpg.set_value("orca_setup_error", f"Offline diagnostics saved to {path}")

    def _render_state(self, state: OrcaSetupState) -> None:
        dpg = self.dpg
        dpg.set_value(
            "orca_setup_executable",
            f"Executable: {state.executable or 'not detected — browse to choose one'}",
        )
        roots = ", ".join(str(root) for root in state.config_roots) or "none detected"
        dpg.set_value("orca_setup_roots", f"Profile roots: {roots}")
        version = state.version_banner or "not checked"
        checked = state.last_checked_at_utc or "never"
        dpg.set_value("orca_setup_version", f"CLI: {state.cli_status}  ·  Version: {version}  ·  Last check: {checked}")
        dpg.set_value("orca_setup_compatibility", f"Compatibility: {state.compatibility_status}")
        dpg.set_value("orca_setup_error", state.error or "")


def _selected_path(app_data: Any) -> str | None:
    if isinstance(app_data, dict):
        selected = app_data.get("file_path_name") or app_data.get("current_path")
        if selected:
            return str(selected)
    if isinstance(app_data, str):
        return app_data
    return None
