"""Dear PyGui application shell and first-pass workbench pages."""

from __future__ import annotations

from dataclasses import dataclass
from importlib import import_module
from typing import Any, Mapping

from calibrate3dp.experiments import ExperimentPlan
from calibrate3dp.app.pages.profile_selection_page import ProfileSelectionPage
from calibrate3dp.app.pages.experiment_review_page import ExperimentReviewPage
from calibrate3dp.app.pages.module_page import ModulePage
from calibrate3dp.app.pages.setup_page import SetupPage
from calibrate3dp.app.services.experiment_service import ExperimentService
from calibrate3dp.app.services.profile_service import ProfileService


PAGE_LABELS: Mapping[str, str] = {
    "home": "Home",
    "new_calibration": "New Calibration",
    "sessions": "Sessions",
    "settings": "Settings",
}


class InvalidPageError(ValueError):
    """Raised when navigation targets a page the application does not expose."""


class GuiDependencyMissingError(RuntimeError):
    """Raised when a user starts the GUI without installing the GUI extra."""


@dataclass
class RouteState:
    """Small navigation model that can be used without creating a viewport."""

    current_page: str = "home"

    def __post_init__(self) -> None:
        if self.current_page not in PAGE_LABELS:
            raise InvalidPageError(f"unknown application page: {self.current_page!r}")

    def navigate(self, page: str) -> None:
        if page not in PAGE_LABELS:
            raise InvalidPageError(f"unknown application page: {page!r}")
        self.current_page = page


def load_dearpygui() -> Any:
    """Import Dear PyGui only when the desktop app is actually launched."""
    try:
        return import_module("dearpygui.dearpygui")
    except ModuleNotFoundError as exc:
        if exc.name != "dearpygui":
            raise
        raise GuiDependencyMissingError(
            "the desktop interface is an optional dependency; install it with "
            "`python -m pip install 'calibrate-3dp[gui]'`"
        ) from exc


class AppShell:
    """Single-viewport application frame with four top-level destinations."""

    def __init__(
        self,
        services: Mapping[str, object] | None = None,
        *,
        dpg_module: Any | None = None,
    ) -> None:
        self.services = dict(services or {})
        self.profile_service = self.services.get("profile_service") or ProfileService()
        self.experiment_service = self.services.get("experiment_service") or ExperimentService()
        self.profile_selection: Any | None = None
        self.experiment_plan: ExperimentPlan | None = None
        self._profile_selection_page: ProfileSelectionPage | None = None
        self._module_page: ModulePage | None = None
        self._experiment_review_page: ExperimentReviewPage | None = None
        self.routes = RouteState()
        self._dpg = dpg_module
        self._context_created = False
        self._nav_idle_theme: Any = None
        self._nav_active_theme: Any = None

    @property
    def current_page(self) -> str:
        return self.routes.current_page

    def navigate(self, page: str) -> None:
        """Change pages and update the visible desktop view when it exists."""
        self.routes.navigate(page)
        if self._dpg is None or not self._dpg.does_item_exist("page_frame"):
            return

        self._dpg.set_value("route_heading", PAGE_LABELS[page].upper())
        for page_key in PAGE_LABELS:
            self._dpg.configure_item(
                f"page_{page_key}", show=(page_key == self.current_page)
            )
            self._dpg.bind_item_theme(
                f"nav_{page_key}",
                self._nav_active_theme
                if page_key == self.current_page
                else self._nav_idle_theme,
            )

    def run(self) -> int:
        """Create the native viewport, dispatch callbacks, and clean up."""
        if self._dpg is None:
            self._dpg = load_dearpygui()
        dpg = self._dpg

        try:
            dpg.create_context()
            self._context_created = True
            dpg.configure_app(manual_callback_management=True)
            dpg.create_viewport(
                title="Calibrate-3DP | Calibration Workbench",
                width=1240,
                height=820,
            )
            dpg.set_viewport_min_width(1040)
            dpg.set_viewport_min_height(680)
            self._build_layout()
            dpg.setup_dearpygui()
            dpg.show_viewport()

            while dpg.is_dearpygui_running():
                callbacks = dpg.get_callback_queue()
                if callbacks:
                    dpg.run_callbacks(callbacks)
                dpg.render_dearpygui_frame()
        finally:
            if self._context_created:
                dpg.destroy_context()
                self._context_created = False

        return 0

    def _build_layout(self) -> None:
        dpg = self._dpg
        self._create_themes()
        dpg.bind_theme(self._nav_idle_theme)

        with dpg.window(
            label="Calibrate-3DP",
            tag="root_window",
            no_title_bar=True,
            no_move=True,
            no_resize=True,
            no_collapse=True,
            no_scrollbar=True,
        ):
            with dpg.group(horizontal=True):
                self._build_sidebar()
                self._build_workspace()
        dpg.set_primary_window("root_window", True)

    def _create_themes(self) -> None:
        dpg = self._dpg
        with dpg.theme() as app_theme:
            with dpg.theme_component(dpg.mvAll):
                dpg.add_theme_color(
                    dpg.mvThemeCol_WindowBg, (12, 18, 27, 255), category=dpg.mvThemeCat_Core
                )
                dpg.add_theme_color(
                    dpg.mvThemeCol_ChildBg, (18, 27, 39, 255), category=dpg.mvThemeCat_Core
                )
                dpg.add_theme_color(
                    dpg.mvThemeCol_Text, (232, 239, 246, 255), category=dpg.mvThemeCat_Core
                )
                dpg.add_theme_color(
                    dpg.mvThemeCol_TextDisabled, (133, 149, 166, 255), category=dpg.mvThemeCat_Core
                )
                dpg.add_theme_color(
                    dpg.mvThemeCol_Border, (43, 58, 75, 255), category=dpg.mvThemeCat_Core
                )
                dpg.add_theme_color(
                    dpg.mvThemeCol_FrameBg, (25, 37, 52, 255), category=dpg.mvThemeCat_Core
                )
                dpg.add_theme_color(
                    dpg.mvThemeCol_Button, (27, 42, 58, 255), category=dpg.mvThemeCat_Core
                )
                dpg.add_theme_color(
                    dpg.mvThemeCol_ButtonHovered, (38, 58, 77, 255), category=dpg.mvThemeCat_Core
                )
                dpg.add_theme_color(
                    dpg.mvThemeCol_ButtonActive, (46, 71, 90, 255), category=dpg.mvThemeCat_Core
                )
                dpg.add_theme_color(
                    dpg.mvThemeCol_Separator, (43, 58, 75, 255), category=dpg.mvThemeCat_Core
                )
                dpg.add_theme_style(
                    dpg.mvStyleVar_WindowPadding, x=18, y=16, category=dpg.mvThemeCat_Core
                )
                dpg.add_theme_style(
                    dpg.mvStyleVar_FramePadding, x=12, y=9, category=dpg.mvThemeCat_Core
                )
                dpg.add_theme_style(
                    dpg.mvStyleVar_FrameRounding, x=7, y=7, category=dpg.mvThemeCat_Core
                )
                dpg.add_theme_style(
                    dpg.mvStyleVar_ItemSpacing, x=10, y=10, category=dpg.mvThemeCat_Core
                )

        with dpg.theme() as self._nav_idle_theme:
            with dpg.theme_component(dpg.mvButton):
                dpg.add_theme_color(
                    dpg.mvThemeCol_Button, (18, 27, 39, 255), category=dpg.mvThemeCat_Core
                )
                dpg.add_theme_color(
                    dpg.mvThemeCol_ButtonHovered, (31, 46, 62, 255), category=dpg.mvThemeCat_Core
                )

        with dpg.theme() as self._nav_active_theme:
            with dpg.theme_component(dpg.mvButton):
                dpg.add_theme_color(
                    dpg.mvThemeCol_Button, (31, 77, 80, 255), category=dpg.mvThemeCat_Core
                )
                dpg.add_theme_color(
                    dpg.mvThemeCol_ButtonHovered, (39, 91, 92, 255), category=dpg.mvThemeCat_Core
                )
                dpg.add_theme_color(
                    dpg.mvThemeCol_ButtonActive, (46, 105, 103, 255), category=dpg.mvThemeCat_Core
                )

        with dpg.theme() as self._primary_button_theme:
            with dpg.theme_component(dpg.mvButton):
                dpg.add_theme_color(
                    dpg.mvThemeCol_Button, (37, 131, 119, 255), category=dpg.mvThemeCat_Core
                )
                dpg.add_theme_color(
                    dpg.mvThemeCol_ButtonHovered, (47, 151, 137, 255), category=dpg.mvThemeCat_Core
                )
                dpg.add_theme_color(
                    dpg.mvThemeCol_ButtonActive, (32, 112, 104, 255), category=dpg.mvThemeCat_Core
                )

        dpg.bind_theme(app_theme)

    def _build_sidebar(self) -> None:
        dpg = self._dpg
        with dpg.child_window(
            tag="sidebar", width=220, autosize_y=True, border=False, no_scrollbar=True
        ):
            dpg.add_spacer(height=6)
            dpg.add_text("CALIBRATE", color=(226, 239, 246, 255))
            dpg.add_text("3DP  /  WORKBENCH", color=(92, 191, 178, 255))
            dpg.add_spacer(height=18)
            dpg.add_separator()
            dpg.add_spacer(height=14)
            dpg.add_text("WORKSPACE", color=(133, 149, 166, 255))
            dpg.add_spacer(height=6)

            for page_key, label in PAGE_LABELS.items():
                dpg.add_button(
                    label=label,
                    tag=f"nav_{page_key}",
                    width=184,
                    height=40,
                    callback=self._on_navigation,
                    user_data=page_key,
                )

            dpg.add_spacer(height=18)
            dpg.add_separator()
            dpg.add_spacer(height=14)
            dpg.add_text("LOCAL MODE", color=(92, 191, 178, 255))
            dpg.add_text("No cloud account required.", color=(133, 149, 166, 255))
            dpg.add_text("Printing stays in OrcaSlicer.", color=(133, 149, 166, 255))

        for page_key in PAGE_LABELS:
            dpg.bind_item_theme(
                f"nav_{page_key}",
                self._nav_active_theme
                if page_key == self.current_page
                else self._nav_idle_theme,
            )

    def _build_workspace(self) -> None:
        dpg = self._dpg
        with dpg.child_window(
            tag="workspace", width=-1, autosize_y=True, border=False, no_scrollbar=True
        ):
            with dpg.group(horizontal=True):
                dpg.add_text("CALIBRATION WORKSPACE", color=(133, 149, 166, 255))
                dpg.add_spacer(width=24)
                dpg.add_text("HOME", tag="route_heading", color=(92, 191, 178, 255))

            dpg.add_separator()
            with dpg.child_window(
                tag="page_frame", width=-1, height=-1, border=False, no_scrollbar=False
            ):
                self._build_home_page()
                self._build_new_calibration_page()
                self._build_sessions_page()
                self._build_settings_page()

    def _build_home_page(self) -> None:
        dpg = self._dpg
        with dpg.group(tag="page_home"):
            dpg.add_spacer(height=12)
            dpg.add_text("LOCAL-FIRST PRINT CALIBRATION", color=(92, 191, 178, 255))
            dpg.add_spacer(height=7)
            dpg.add_text("Make each test easier to trust.", color=(238, 244, 249, 255))
            dpg.add_text(
                "Plan controlled calibration experiments from your OrcaSlicer profiles, "
                "then compare printed results at your own pace.",
                color=(165, 180, 195, 255),
                wrap=760,
            )
            dpg.add_spacer(height=18)

            with dpg.group(horizontal=True):
                with dpg.child_window(width=420, height=174, border=True):
                    dpg.add_text("FIRST WORKFLOW", color=(133, 149, 166, 255))
                    dpg.add_spacer(height=7)
                    dpg.add_text("Ironing Finish", color=(238, 244, 249, 255))
                    dpg.add_text(
                        "Compare ironing flow and speed across a controlled candidate grid.",
                        color=(165, 180, 195, 255),
                        wrap=365,
                    )
                    dpg.add_spacer(height=12)
                    button = dpg.add_button(
                        label="View calibration setup",
                        width=205,
                        height=38,
                        callback=self._on_navigation,
                        user_data="new_calibration",
                    )
                    dpg.bind_item_theme(button, self._primary_button_theme)

                dpg.add_spacer(width=6)
                with dpg.child_window(width=320, height=174, border=True):
                    dpg.add_text("WORKBENCH STATUS", color=(133, 149, 166, 255))
                    dpg.add_spacer(height=8)
                    dpg.add_text("Calibration core ready", color=(92, 191, 178, 255))
                    dpg.add_text(
                        "Profile resolution, candidate planning, Orca CLI slicing, and "
                        "manual assessment are implemented in the core.",
                        color=(165, 180, 195, 255),
                        wrap=275,
                    )
                    dpg.add_spacer(height=8)
                    dpg.add_text(
                        "Desktop session workflow is being connected.",
                        color=(133, 149, 166, 255),
                        wrap=275,
                    )

            dpg.add_spacer(height=24)
            dpg.add_text("ALREADY IN THE CALIBRATION CORE", color=(133, 149, 166, 255))
            dpg.add_spacer(height=8)
            with dpg.group(horizontal=True):
                self._add_capability_card(
                    "01  PROFILE BASELINES",
                    "Resolve inherited Orca settings and retain the source of each value.",
                    width=236,
                )
                self._add_capability_card(
                    "02  CANDIDATE GRIDS",
                    "Build deterministic ironing sweeps and refine from manual results.",
                    width=236,
                )
                self._add_capability_card(
                    "03  ORCA CLI JOBS",
                    "Slice isolated candidate jobs and save their logs and artifacts.",
                    width=236,
                )

            dpg.add_spacer(height=20)
            with dpg.child_window(width=-1, height=72, border=True):
                dpg.add_text("WHAT THIS FIRST UI SLICE INCLUDES", color=(92, 191, 178, 255))
                dpg.add_text(
                    "Desktop navigation, Orca setup, read-only profile import, and inherited-value "
                    "inspection are connected. Session workflow and results review are continuing.",
                    color=(165, 180, 195, 255),
                    wrap=760,
                )

    def _add_capability_card(self, heading: str, description: str, *, width: int) -> None:
        with self._dpg.child_window(width=width, height=118, border=True):
            self._dpg.add_text(heading, color=(92, 191, 178, 255))
            self._dpg.add_spacer(height=7)
            self._dpg.add_text(description, color=(165, 180, 195, 255), wrap=width - 40)

    def _build_new_calibration_page(self) -> None:
        dpg = self._dpg
        with dpg.group(tag="page_new_calibration", show=False):
            dpg.add_spacer(height=12)
            dpg.add_text("NEW CALIBRATION", color=(92, 191, 178, 255))
            dpg.add_spacer(height=7)
            dpg.add_text("Set up a controlled test.", color=(238, 244, 249, 255))
            dpg.add_text(
                "The desktop workflow will keep your source profiles intact and make each "
                "candidate easy to review.",
                color=(165, 180, 195, 255),
                wrap=760,
            )
            dpg.add_spacer(height=20)

            with dpg.group(horizontal=True):
                for index, label in enumerate(
                    ("PROFILES", "MODULE", "REVIEW", "GENERATE", "ASSESS"), start=1
                ):
                    dpg.add_text(f"{index:02d}  {label}", color=(133, 149, 166, 255))
                    if index < 5:
                        dpg.add_spacer(width=18)

            dpg.add_spacer(height=20)
            dpg.add_text("ORCA CONNECTION AND PROFILE SOURCES", color=(133, 149, 166, 255))
            dpg.add_spacer(height=7)
            setup_page = SetupPage(self.profile_service, dpg)
            setup_page.render()
            dpg.add_spacer(height=12)

            self._profile_selection_page = ProfileSelectionPage(
                self.profile_service,
                dpg,
                on_continue=self._on_profile_selection,
            )
            setup_page.on_profiles_changed = self._profile_selection_page.refresh
            self._profile_selection_page.render()

            self._module_page = ModulePage(
                self.experiment_service,
                dpg,
                on_plan_created=self._on_experiment_plan_created,
            )
            self._module_page.render()
            self._experiment_review_page = ExperimentReviewPage(
                self.experiment_service,
                dpg,
                on_plan_ready=self._on_experiment_plan_ready,
            )
            self._experiment_review_page.render()

            dpg.add_spacer(height=18)
            dpg.add_text("OTHER MODULES", color=(133, 149, 166, 255))
            dpg.add_spacer(height=8)
            with dpg.group(horizontal=True):
                self._add_planned_module_card("Bridge calibration")
                self._add_planned_module_card("Support interface")

    def _on_profile_selection(self, selection: Any) -> None:
        """Retain the validated, provenance-bearing baseline for the next step."""
        self.profile_selection = selection
        if self._dpg is not None and self._dpg.does_item_exist("module_selection_panel"):
            self._dpg.configure_item("profile_selection_panel", show=False)
            self._dpg.configure_item("module_selection_panel", show=True)
            if self._module_page is not None:
                self._module_page.set_profiles(selection)

    def _on_experiment_plan_created(self, plan: ExperimentPlan, selection: Any) -> None:
        if self._dpg is None or self._experiment_review_page is None:
            return
        self._experiment_review_page.set_plan(plan, selection)
        self._dpg.configure_item("module_selection_panel", show=False)
        self._dpg.configure_item("experiment_review_panel", show=True)

    def _on_experiment_plan_ready(self, plan: ExperimentPlan) -> None:
        self.experiment_plan = plan

    def _add_planned_module_card(self, name: str) -> None:
        with self._dpg.child_window(width=278, height=90, border=True):
            self._dpg.add_text(name, color=(165, 180, 195, 255))
            self._dpg.add_spacer(height=6)
            self._dpg.add_text("PLANNED  ·  NOT AVAILABLE", color=(133, 149, 166, 255))

    def _build_sessions_page(self) -> None:
        dpg = self._dpg
        with dpg.group(tag="page_sessions", show=False):
            dpg.add_spacer(height=12)
            dpg.add_text("SESSIONS", color=(92, 191, 178, 255))
            dpg.add_spacer(height=7)
            dpg.add_text("Your calibration history.", color=(238, 244, 249, 255))
            dpg.add_text(
                "Saved experiments will appear here with their selected profiles, candidate "
                "results, and generated artifacts.",
                color=(165, 180, 195, 255),
                wrap=760,
            )
            dpg.add_spacer(height=22)
            with dpg.child_window(width=-1, height=180, border=True):
                dpg.add_text("Session browser not connected yet", color=(238, 244, 249, 255))
                dpg.add_text(
                    "Session storage is not part of this first UI slice. Once it is connected, "
                    "you will be able to resume a calibration from its last saved step.",
                    color=(165, 180, 195, 255),
                    wrap=760,
                )

    def _build_settings_page(self) -> None:
        dpg = self._dpg
        with dpg.group(tag="page_settings", show=False):
            dpg.add_spacer(height=12)
            dpg.add_text("SETTINGS", color=(92, 191, 178, 255))
            dpg.add_spacer(height=7)
            dpg.add_text("Local application setup.", color=(238, 244, 249, 255))
            dpg.add_text(
                "Workspace and OrcaSlicer locations will be configurable here when the "
                "profile and session services are connected.",
                color=(165, 180, 195, 255),
                wrap=760,
            )
            dpg.add_spacer(height=22)
            with dpg.child_window(width=-1, height=144, border=True):
                dpg.add_text("WORKSPACE ROOT", color=(133, 149, 166, 255))
                dpg.add_text("Not configured in this UI slice", color=(165, 180, 195, 255))
                dpg.add_spacer(height=12)
                dpg.add_text("ORCASLICER EXECUTABLE", color=(133, 149, 166, 255))
                dpg.add_text("Not configured in this UI slice", color=(165, 180, 195, 255))

    def _on_navigation(self, sender: Any, app_data: Any, user_data: str) -> None:
        del sender, app_data
        self.navigate(user_data)
