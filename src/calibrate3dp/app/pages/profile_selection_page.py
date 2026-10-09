"""Three-profile selection view backed by the existing inheritance resolver."""

from __future__ import annotations

from typing import Any, Callable

from calibrate3dp.app.pages.setup_page import _selected_path
from calibrate3dp.app.services.profile_service import (
    ProfileChoice,
    ProfileImportError,
    ProfileService,
    ProfileServiceError,
)
from calibrate3dp.app.widgets.profile_card import (
    add_profile_card,
    update_profile_choices,
    update_profile_details,
)
from calibrate3dp.profiles import ProfileResolutionError, ResolvedProfile


PROFILE_ROLES = ("printer", "filament", "process")
IRONING_REQUIRED_SETTINGS = ("ironing_flow", "ironing_speed")


class ProfileSelectionPage:
    """Build profile cards, show per-setting provenance, and emit valid baselines."""

    def __init__(
        self,
        service: ProfileService,
        dpg: Any,
        *,
        on_continue: Callable[[Any], None] | None = None,
    ) -> None:
        self.service = service
        self.dpg = dpg
        self.on_continue = on_continue
        self.selected: dict[str, ProfileChoice | None] = {role: None for role in PROFILE_ROLES}
        self.selection: Any | None = None
        self._labels: dict[str, dict[str, ProfileChoice]] = {}

    def render(self) -> None:
        self.service.discover_profiles()
        dpg = self.dpg
        with dpg.group(tag="profile_selection_panel"):
            dpg.add_spacer(height=14)
            dpg.add_text("SELECT YOUR BASELINES", color=(92, 191, 178, 255))
            dpg.add_text(
                "Import presets from files or select profiles already found in Orca's local folders. "
                "Imported data stays read-only; effective values keep their source visible.",
                color=(165, 180, 195, 255),
                wrap=860,
            )
            dpg.add_spacer(height=10)
            dpg.add_input_text(
                label="Import scope",
                default_value=self.service.default_import_scope,
                tag="profile_import_scope",
                width=320,
                hint="Use a distinct scope for profiles with the same name",
            )
            with dpg.group(horizontal=True):
                for role in PROFILE_ROLES:
                    add_profile_card(
                        dpg,
                        role,
                        self.service.choices(role),
                        on_select=self._on_profile_selected,
                        on_import=self._on_import,
                    )
            dpg.add_spacer(height=8)
            dpg.add_text(
                "Continue is enabled only when all three profiles resolve and the process baseline "
                "contains ironing_flow and ironing_speed.",
                tag="profile_selection_requirement",
                color=(133, 149, 166, 255),
                wrap=850,
            )
            dpg.add_text(
                "Ironing baseline: select a process profile to read its imported values.",
                tag="profile_selection_baseline",
                color=(238, 244, 249, 255),
                wrap=850,
            )
            dpg.add_text("", tag="profile_selection_message", wrap=850)
            dpg.add_text("", tag="profile_discovery_issues", wrap=850,
                         color=(225, 180, 112, 255))
            dpg.add_button(
                label="Continue to module selection",
                tag="profile_continue_button",
                width=250,
                height=40,
                enabled=False,
                callback=self._on_continue,
            )
        self._refresh_label_maps()
        self._render_discovery_issues()
        self._update_continue_state()

    def refresh(self) -> None:
        """Refresh selector options after discovery or an import."""
        self._refresh_label_maps()
        for role in PROFILE_ROLES:
            choices = self.service.choices(role)
            selected = self.selected[role]
            if selected is not None:
                selected = next(
                    (choice for choice in choices if choice.label == selected.label),
                    None,
                )
            self.selected[role] = selected
            update_profile_choices(self.dpg, role, choices, selected)
        self._render_discovery_issues()
        self._update_continue_state()

    def _render_discovery_issues(self) -> None:
        issues = self.service.discovery_issues
        if not issues:
            self.dpg.set_value("profile_discovery_issues", "")
            return
        first = issues[0]
        self.dpg.set_value(
            "profile_discovery_issues",
            f"{len(issues)} local preset file(s) need attention. First: {first.message} "
            f"Recovery: {first.action}",
        )

    def _refresh_label_maps(self) -> None:
        self._labels = {
            role: {choice.label: choice for choice in self.service.choices(role)}
            for role in PROFILE_ROLES
        }

    def _on_profile_selected(self, sender: Any, app_data: Any, user_data: str) -> None:
        del sender
        role = user_data
        choice = self._labels.get(role, {}).get(str(app_data))
        self.selected[role] = choice
        if choice is None:
            update_profile_details(
                self.dpg,
                role,
                "Choose a profile to inspect its resolved values.",
                "Effective settings and their source will appear here.",
            )
        else:
            try:
                resolved = self.service.resolve_choice(choice)
            except ProfileResolutionError as exc:
                update_profile_details(
                    self.dpg,
                    role,
                    f"Cannot resolve inheritance: {exc}",
                    "The selected preset is blocked until its parent profile is imported or corrected.",
                )
            else:
                self._render_resolved_profile(role, choice, resolved)
        self._update_continue_state()

    def _render_resolved_profile(
        self, role: str, choice: ProfileChoice, resolved: ResolvedProfile
    ) -> None:
        digest = self.service.source_sha256(choice) or "not available"
        chain = " → ".join(profile.name for profile in resolved.chain)
        summary = (
            f"{resolved.profile.kind} · {resolved.profile.name}\n"
            f"Scope: {resolved.profile.scope}\n"
            f"Source: {resolved.profile.source}\n"
            f"SHA-256: {digest}\n"
            f"Inheritance: {chain or 'base preset'}"
        )
        setting_lines = ["EFFECTIVE SETTINGS  ·  VALUE SOURCE"]
        for name in sorted(resolved.settings, key=str.casefold):
            provenance = resolved.provenance[name]
            value = repr(resolved.settings[name])
            setting_lines.append(
                f"{name} = {value}  ←  {provenance.name} [{provenance.scope}]"
            )
        update_profile_details(self.dpg, role, summary, "\n".join(setting_lines))

    def _on_import(self, sender: Any, app_data: Any, user_data: Any) -> None:
        del sender
        if not isinstance(user_data, dict) or not user_data.get("dialog"):
            role = str(user_data)
            self.dpg.show_item(f"profile_import_dialog_{role}")
            return

        role = user_data["role"]
        source = _selected_path(app_data)
        if source is None:
            return
        try:
            scope = str(self.dpg.get_value("profile_import_scope")).strip()
            imported = self.service.import_source(source, scope=scope)
        except ProfileImportError as exc:
            self.dpg.set_value(
                "profile_selection_message",
                f"{exc.field}: {exc.message}  Recovery: {exc.action}",
            )
            return
        self.refresh()
        self.dpg.set_value(
            "profile_selection_message",
            f"Imported {len(imported.documents)} profile(s) into the local read-only catalog. "
            "No Orca preset files were changed.",
        )
        if self.selected.get(role) is None:
            self.dpg.set_value(f"profile_summary_{role}", "Select an imported profile from the list above.")

    def _update_continue_state(self) -> None:
        try:
            selection = self.service.build_selection(
                self.selected,
                required_settings=IRONING_REQUIRED_SETTINGS,
            )
        except (ProfileServiceError, ProfileResolutionError) as exc:
            self.selection = None
            self.dpg.configure_item("profile_continue_button", enabled=False)
            self.dpg.set_value("profile_selection_message", str(exc))
            process_choice = self.selected.get("process")
            if process_choice is None:
                baseline = "Ironing baseline: select a process profile to read its imported values."
            else:
                try:
                    process = self.service.resolve_choice(process_choice)
                    values = ", ".join(
                        f"{key}={process.settings.get(key, 'missing')}"
                        for key in IRONING_REQUIRED_SETTINGS
                    )
                    baseline = f"Ironing baseline from {process.profile.name}: {values}"
                except ProfileResolutionError:
                    baseline = "Ironing baseline blocked until this process profile resolves."
            self.dpg.set_value("profile_selection_baseline", baseline)
            return
        self.selection = selection
        self.dpg.configure_item("profile_continue_button", enabled=True)
        flow = selection.process.settings["ironing_flow"]
        speed = selection.process.settings["ironing_speed"]
        self.dpg.set_value(
            "profile_selection_baseline",
            f"Ironing baseline from {selection.process.profile.name}: "
            f"ironing_flow={flow} · ironing_speed={speed}",
        )
        self.dpg.set_value(
            "profile_selection_message",
            "All three profiles resolve. Ironing flow and speed are explicit in the selected process profile.",
        )

    def _on_continue(self, sender: Any, app_data: Any, user_data: Any = None) -> None:
        del sender, app_data, user_data
        if self.selection is None:
            self._update_continue_state()
            return
        if self.on_continue:
            self.on_continue(self.selection)
        else:
            self.dpg.set_value(
                "profile_selection_message",
                "ProfileSelection is ready. Module selection is the next workflow step.",
            )
