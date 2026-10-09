"""Module chooser that exposes only planners and geometry adapters in place."""

from __future__ import annotations

from typing import Any, Callable

from calibrate3dp.app.models import ProfileSelection
from calibrate3dp.app.services.experiment_service import (
    ExperimentService,
    ExperimentServiceError,
)


class ModulePage:
    """Show available and planned modules after profile selection."""

    def __init__(
        self,
        service: ExperimentService,
        dpg: Any,
        *,
        on_plan_created: Callable[[Any, ProfileSelection], None],
    ) -> None:
        self.service = service
        self.dpg = dpg
        self.on_plan_created = on_plan_created
        self.profiles: ProfileSelection | None = None

    def render(self) -> None:
        dpg = self.dpg
        with dpg.group(tag="module_selection_panel", show=False):
            dpg.add_spacer(height=15)
            dpg.add_text("CHOOSE A CALIBRATION MODULE", color=(92, 191, 178, 255))
            dpg.add_text(
                "The selected profiles are the baseline for every candidate. Future modules remain "
                "disabled until their planner and specimen geometry are implemented.",
                color=(165, 180, 195, 255),
                wrap=850,
            )
            dpg.add_text("", tag="module_profile_summary", wrap=850)
            dpg.add_spacer(height=10)
            with dpg.group(horizontal=True):
                for module in self.service.modules:
                    with dpg.child_window(width=278, height=188, border=True):
                        dpg.add_text(
                            module.label,
                            color=(238, 244, 249, 255) if module.available else (165, 180, 195, 255),
                        )
                        dpg.add_spacer(height=7)
                        dpg.add_text(module.description, wrap=246, color=(165, 180, 195, 255))
                        dpg.add_spacer(height=8)
                        if module.available:
                            dpg.add_text("AVAILABLE", color=(92, 191, 178, 255))
                            dpg.add_button(
                                label="Review initial 3 × 3 grid",
                                width=-1,
                                callback=self._on_module_selected,
                                user_data=module.module_id,
                            )
                        else:
                            dpg.add_text("PLANNED · NOT AVAILABLE", color=(133, 149, 166, 255))
                            dpg.add_text(module.availability_reason or "", wrap=246,
                                         color=(133, 149, 166, 255))
            dpg.add_text("", tag="module_selection_error", wrap=850,
                         color=(235, 130, 125, 255))
            dpg.add_button(label="Back to profile selection", callback=self._on_back)

    def set_profiles(self, profiles: ProfileSelection) -> None:
        self.profiles = profiles
        self.dpg.set_value(
            "module_profile_summary",
            "Baseline: "
            f"{profiles.printer.profile.name} · {profiles.filament.profile.name} · "
            f"{profiles.process.profile.name}",
        )
        self.dpg.set_value("module_selection_error", "")

    def _on_module_selected(self, sender: Any, app_data: Any, user_data: str) -> None:
        del sender, app_data
        if self.profiles is None:
            self.dpg.set_value("module_selection_error", "Select all three baseline profiles first.")
            return
        try:
            plan = self.service.create_initial(user_data, self.profiles)
        except ExperimentServiceError as exc:
            self.dpg.set_value("module_selection_error", str(exc))
            return
        self.dpg.set_value("module_selection_error", "")
        self.on_plan_created(plan, self.profiles)

    def _on_back(self, sender: Any, app_data: Any, user_data: Any = None) -> None:
        del sender, app_data, user_data
        self.dpg.configure_item("module_selection_panel", show=False)
        self.dpg.configure_item("profile_selection_panel", show=True)
