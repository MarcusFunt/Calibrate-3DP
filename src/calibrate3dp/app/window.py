"""Dear PyGui application shell and first-pass workbench pages."""

from __future__ import annotations

from dataclasses import dataclass, replace
from importlib import import_module
from pathlib import Path
from typing import Any, Mapping

from calibrate3dp.experiments import ExperimentPlan
from calibrate3dp.app.models import SessionSnapshot
from calibrate3dp.app.pages.home_page import HomePage
from calibrate3dp.app.pages.profile_selection_page import ProfileSelectionPage
from calibrate3dp.app.pages.sessions_page import SessionsPage
from calibrate3dp.app.pages.settings_page import SettingsPage
from calibrate3dp.app.pages.experiment_review_page import ExperimentReviewPage
from calibrate3dp.app.pages.generation_page import GenerationPage
from calibrate3dp.app.pages.results_page import ResultsPage
from calibrate3dp.app.pages.recommendation_page import RecommendationPage
from calibrate3dp.app.pages.export_page import ExportPage
from calibrate3dp.app.pages.module_page import ModulePage
from calibrate3dp.app.pages.setup_page import SetupPage
from calibrate3dp.app.services.acceptance_service import AcceptanceService
from calibrate3dp.app.services.experiment_service import ExperimentService
from calibrate3dp.app.services.export_service import ExportService
from calibrate3dp.app.services.generation_port import GenerationState, ValidationState
from calibrate3dp.app.services.orca_generation_service import OrcaGenerationService
from calibrate3dp.app.services.profile_service import ProfileService
from calibrate3dp.app.services.session_service import SessionService
from calibrate3dp.app.services.settings_service import (
    AppSettings,
    AppSettingsService,
    default_workspace_root,
)
from calibrate3dp.storage.session_store import SessionRepository
from calibrate3dp.orca_cli import OrcaCli


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
        injected_session_service = self.services.get("session_service")
        configured_settings = self.services.get("settings_service")
        if isinstance(configured_settings, AppSettingsService):
            self.settings_service = configured_settings
        elif services is not None:
            repository_root = getattr(
                getattr(injected_session_service, "repository", None), "root", None
            )
            workspace_root = Path(repository_root) if repository_root else default_workspace_root()
            self.settings_service = AppSettingsService(
                persistent=False,
                initial_settings=AppSettings(
                    workspace_root=workspace_root,
                    default_export_root=workspace_root / "exports",
                ),
            )
        else:
            self.settings_service = AppSettingsService()
        self.app_settings = self.settings_service.settings
        workspace_database = self.app_settings.workspace_root / "sessions.sqlite3"
        self._workspace_recovery_message = (
            f"No session database was found in the saved workspace: {self.app_settings.workspace_root}. "
            "The workspace opens empty; if you expected existing sessions, point Settings to the folder "
            "containing sessions.sqlite3 and sessions/. Existing files were left untouched."
            if self.settings_service.was_loaded_from_disk and not workspace_database.is_file()
            else self.settings_service.load_error
        )
        self._session_recovery_message = ""
        self._active_workspace_root = Path(
            getattr(
                getattr(injected_session_service, "repository", None),
                "root",
                self.app_settings.workspace_root,
            )
        )
        if injected_session_service is None:
            injected_session_service = SessionService(
                SessionRepository(self.app_settings.workspace_root)
            )
        self.services["session_service"] = injected_session_service
        self.services["settings_service"] = self.settings_service
        self.profile_service = self.services.get("profile_service") or ProfileService(
            executable=self.app_settings.orca_executable,
            config_roots=self.app_settings.orca_config_roots,
        )
        self.experiment_service = self.services.get("experiment_service") or ExperimentService()
        if services is None and "generation_service" not in self.services:
            session_service = self.services["session_service"]
            if isinstance(getattr(session_service, "repository", None), SessionRepository):
                self.services["generation_service"] = OrcaGenerationService(
                    cli_provider=lambda: (
                        OrcaCli(self.profile_service.setup_state.executable)
                        if self.profile_service.setup_state.executable is not None
                        else None
                    ),
                    experiment_service=self.experiment_service,
                    session_service=session_service,
                    setup_state_provider=lambda: self.profile_service.setup_state,
                )
        self.profile_selection: Any | None = None
        self.experiment_plan: ExperimentPlan | None = None
        self.active_session: SessionSnapshot | None = self.services.get("session")
        self.resumed_step: str | None = None
        self._home_page: HomePage | None = None
        self._sessions_page: SessionsPage | None = None
        self._settings_page: SettingsPage | None = None
        self._setup_page: SetupPage | None = None
        self._profile_selection_page: ProfileSelectionPage | None = None
        self._module_page: ModulePage | None = None
        self._experiment_review_page: ExperimentReviewPage | None = None
        self._generation_page: GenerationPage | None = None
        self._results_page: ResultsPage | None = None
        self._recommendation_page: RecommendationPage | None = None
        self._export_page: ExportPage | None = None
        self._confirmation_plan: ExperimentPlan | None = None
        self._confirmation_run_id: str | None = None
        self._last_synchronized_plan: ExperimentPlan | None = None
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
        if page == "home" and self._home_page is not None:
            self._home_page.refresh()
        elif page == "sessions" and self._sessions_page is not None:
            self._sessions_page.refresh()

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
                self._sync_generation_page()
                dpg.render_dearpygui_frame()
        finally:
            if self._generation_page is not None:
                self._generation_page.close()
            if self._results_page is not None:
                self._results_page.flush_pending()
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
        with dpg.handler_registry(tag="app_keyboard_shortcuts"):
            for key, page in (
                (dpg.mvKey_1, "home"),
                (dpg.mvKey_2, "new_calibration"),
                (dpg.mvKey_3, "sessions"),
                (dpg.mvKey_4, "settings"),
            ):
                dpg.add_key_press_handler(
                    key=key,
                    callback=self._on_keyboard_shortcut,
                    user_data=page,
                )
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
                dpg.add_theme_color(
                    dpg.mvThemeCol_NavHighlight,
                    (255, 197, 92, 255),
                    category=dpg.mvThemeCat_Core,
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
            dpg.add_text("Keyboard: Ctrl+1–4", color=(133, 149, 166, 255))

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
        self._home_page = HomePage(
            self._dpg,
            self.services["session_service"],
            on_new_calibration=self._begin_new_calibration,
            on_resume=self._resume_session,
            recovery_notice=self._workspace_recovery_message,
        )
        self._home_page.render()

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
                    ("PROFILES", "MODULE", "REVIEW", "GENERATE", "RESULTS", "RECOMMEND", "EXPORT"), start=1
                ):
                    dpg.add_text(f"{index:02d}  {label}", color=(133, 149, 166, 255))
                    if index < 7:
                        dpg.add_spacer(width=18)

            dpg.add_spacer(height=20)
            dpg.add_text("ORCA CONNECTION AND PROFILE SOURCES", color=(133, 149, 166, 255))
            dpg.add_spacer(height=7)
            dpg.add_text("", tag="session_recovery_notice", wrap=850,
                         color=(225, 180, 112, 255))
            self._setup_page = SetupPage(
                self.profile_service,
                dpg,
                on_setup_checked=self._refresh_generation_preview,
                diagnostics_options=lambda: self.settings_service.settings,
            )
            self._setup_page.render()
            dpg.add_spacer(height=12)

            self._profile_selection_page = ProfileSelectionPage(
                self.profile_service,
                dpg,
                on_continue=self._on_profile_selection,
            )
            self._setup_page.on_profiles_changed = self._profile_selection_page.refresh
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
            self._generation_page = GenerationPage(
                self.experiment_service,
                dpg,
                generation_service=self.services.get("generation_service"),
                on_recovery=self._on_generation_recovery,
                on_results=self._on_results_ready,
                on_back=self._on_generation_back,
            )
            self._generation_page.render()
            self._results_page = ResultsPage(
                dpg,
                self.services.get("session_service"),
                on_back=self._on_results_back,
                on_recommendation=self._on_recommendation_ready,
            )
            self._results_page.render()
            session_service = self.services.get("session_service")
            acceptance_service = AcceptanceService(self.experiment_service)
            self._recommendation_page = RecommendationPage(
                dpg,
                acceptance_service,
                self.experiment_service,
                session_service,
                on_refinement_ready=self._on_refinement_ready,
                on_confirmation_requested=self._on_confirmation_requested,
                on_accepted=self._on_export_ready,
            )
            self._recommendation_page.render()
            repository = getattr(session_service, "repository", None)
            if isinstance(repository, SessionRepository):
                setup_state = getattr(self.profile_service, "setup_state", None)
                config_roots = getattr(setup_state, "config_roots", ())
                self._export_page = ExportPage(
                    dpg,
                    ExportService(
                        repository,
                        acceptance_service=acceptance_service,
                        protected_roots=config_roots,
                        orca_version=getattr(setup_state, "version_banner", None),
                        protected_roots_provider=lambda: getattr(
                            self.profile_service.setup_state, "config_roots", ()
                        ),
                        orca_version_provider=lambda: getattr(
                            self.profile_service.setup_state, "version_banner", None
                        ),
                    ),
                    on_back=self._on_export_back,
                    on_exported=self._on_export_written,
                    default_export_root=self.app_settings.default_export_root,
                )
                self._export_page.render()

            dpg.add_spacer(height=18)
            dpg.add_text("OTHER MODULES", color=(133, 149, 166, 255))
            dpg.add_spacer(height=8)
            with dpg.group(horizontal=True):
                self._add_planned_module_card("Bridge calibration")
                self._add_planned_module_card("Support interface")

    def _on_profile_selection(self, selection: Any) -> None:
        """Retain the validated, provenance-bearing baseline for the next step."""
        self.profile_selection = selection
        self.active_session = None
        self.services.pop("session", None)
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
        selection = self.profile_selection
        session_service = self.services.get("session_service")
        if selection is None or session_service is None:
            return
        try:
            if self.active_session is None or self.active_session.archived:
                session = session_service.create_session(selection, plan.module_id)
            else:
                session = self.active_session
            session = replace(
                session,
                plan=plan,
                current_step="generation",
                status="ready_to_print",
            )
            session_service.save(session)
        except Exception as exc:
            if self._dpg is not None and self._dpg.does_item_exist("generation_availability"):
                self._dpg.set_value("generation_availability", f"This plan could not be saved: {exc}")
            return
        self.active_session = session
        self.services["session"] = session

    def _sync_generation_page(self) -> None:
        """Drain worker events and reveal generation after the plan is accepted."""
        page = self._generation_page
        if page is None:
            if self._results_page is not None:
                self._results_page.tick()
            return
        if (
            self.experiment_plan is not None
            and self.profile_selection is not None
            and self._last_synchronized_plan is not self.experiment_plan
        ):
            page.set_context(
                session=self.services.get("session"),
                plan=self.experiment_plan,
                profiles=self.profile_selection,
            )
            self._last_synchronized_plan = self.experiment_plan
            if self._dpg is not None:
                self._dpg.configure_item("experiment_review_panel", show=False)
                self._dpg.configure_item("generation_panel", show=True)
        page.poll_events()
        if self._results_page is not None:
            self._results_page.tick()

    def _refresh_generation_preview(self) -> None:
        """Re-run the capability gate after the user changes or rechecks Orca."""
        page = self._generation_page
        if page is not None:
            page.refresh_preview()

    def _on_results_ready(self) -> None:
        """Open results only with the reviewed plan and generation session."""
        if self._results_page is None or self.experiment_plan is None:
            return
        session = self._generation_page.session if self._generation_page is not None else None
        if self._confirmation_plan is not None:
            generation_page = self._generation_page
            if (
                self._recommendation_page is not None
                and generation_page is not None
                and generation_page.state is GenerationState.SUCCEEDED
                and generation_page.validation_state is ValidationState.VALID
            ):
                self._recommendation_page.record_confirmation(
                    self._confirmation_plan,
                    state=generation_page.state,
                    validation_state=generation_page.validation_state,
                    run_id=self._confirmation_run_id,
                )
                if self._dpg is not None:
                    self._dpg.configure_item("generation_panel", show=False)
                    self._dpg.configure_item("generation_back", show=False)
                    self._dpg.configure_item("recommendation_panel", show=True)
            self._confirmation_plan = None
            self._confirmation_run_id = None
            return
        if session is not None and self.services.get("session_service") is not None:
            if isinstance(self.services.get("generation_service"), OrcaGenerationService):
                try:
                    session = self.services["session_service"].resume(session.session_id)
                except Exception as exc:
                    if self._dpg is not None and self._dpg.does_item_exist("generation_availability"):
                        self._dpg.set_value(
                            "generation_availability",
                            f"The saved generation artifacts could not be reloaded: {exc}",
                        )
                    return
            session = replace(session, current_step="results", status="awaiting_results")
            self.services["session_service"].save(session)
            self.active_session = session
            self.services["session"] = session
            if self._generation_page is not None:
                self._generation_page.session = session
        self._results_page.set_context(session=session, plan=self.experiment_plan)
        if self._dpg is not None:
            self._dpg.configure_item("generation_panel", show=False)
            self._dpg.configure_item("generation_back", show=False)
            self._dpg.configure_item("results_panel", show=True)

    def _on_results_back(self) -> None:
        if self._dpg is not None:
            self._dpg.configure_item("recommendation_panel", show=False)
            self._dpg.configure_item("results_panel", show=False)
            self._dpg.configure_item("generation_panel", show=True)

    def _on_recommendation_ready(self) -> None:
        if (
            self._recommendation_page is None
            or self._results_page is None
            or self.experiment_plan is None
            or self._results_page.results is None
        ):
            return
        self._recommendation_page.set_context(
            session=self._results_page.session,
            plan=self.experiment_plan,
            results=self._results_page.results,
        )
        decision = self._recommendation_page.decision
        if self._recommendation_page.session is not None:
            state = (
                "confirmation_required"
                if getattr(decision, "confirmation_required", False)
                else "refinement_ready"
            )
            updated = replace(self._recommendation_page.session, status=state)
            session_service = self.services.get("session_service")
            if session_service is not None:
                session_service.save(updated)
            self._recommendation_page.session = updated
            self.active_session = updated
            self.services["session"] = updated
        if self._dpg is not None:
            self._dpg.configure_item("results_panel", show=False)
            self._dpg.configure_item("recommendation_panel", show=True)

    def _on_refinement_ready(self, session: Any, plan: ExperimentPlan) -> None:
        if self._generation_page is None or self.profile_selection is None:
            return
        self.experiment_plan = plan
        self._last_synchronized_plan = plan
        self._generation_page.set_context(
            session=session,
            plan=plan,
            profiles=self.profile_selection,
        )
        if self._dpg is not None:
            self._dpg.configure_item("recommendation_panel", show=False)
            self._dpg.configure_item("generation_back", show=True)
            self._dpg.configure_item("generation_panel", show=True)

    def _on_confirmation_requested(
        self, session: Any, plan: ExperimentPlan, run_id: str
    ) -> None:
        if self._generation_page is None or self.profile_selection is None:
            return
        self._confirmation_plan = plan
        self._confirmation_run_id = run_id
        self._generation_page.set_context(
            session=session,
            plan=plan,
            profiles=self.profile_selection,
        )
        if self._dpg is not None:
            self._dpg.configure_item("recommendation_panel", show=False)
            self._dpg.configure_item("generation_back", show=True)
            self._dpg.configure_item("generation_panel", show=True)

    def _on_generation_back(self) -> None:
        generation_page = self._generation_page
        if generation_page is not None:
            if generation_page.state in {GenerationState.QUEUED, GenerationState.RUNNING}:
                return
            if self._confirmation_plan is not None and generation_page.state in {
                GenerationState.SUCCEEDED,
                GenerationState.FAILED,
                GenerationState.CANCELED,
            }:
                if self._recommendation_page is not None:
                    self._recommendation_page.record_confirmation(
                        self._confirmation_plan,
                        state=generation_page.state,
                        validation_state=generation_page.validation_state,
                        run_id=self._confirmation_run_id,
                    )
                self._confirmation_plan = None
                self._confirmation_run_id = None
        if self._dpg is not None:
            self._dpg.configure_item("generation_back", show=False)
            self._dpg.configure_item("generation_panel", show=False)
            self._dpg.configure_item("recommendation_panel", show=True)

    def _on_export_ready(self, session: Any) -> None:
        if self._export_page is None:
            return
        self._export_page.set_session(session)
        self.active_session = session
        self.services["session"] = session
        if self._dpg is not None:
            self._dpg.configure_item("recommendation_panel", show=False)
            self._dpg.configure_item("export_panel", show=True)

    def _on_export_written(self, session: SessionSnapshot) -> None:
        session_service = self.services.get("session_service")
        if session_service is None:
            return
        completed = replace(session, current_step="export", status="completed")
        session_service.save(completed)
        self.active_session = completed
        self.services["session"] = completed
        if self._export_page is not None:
            self._export_page.session = completed
            self._export_page._refresh_ui()

    def _on_export_back(self) -> None:
        if self._dpg is not None:
            self._dpg.configure_item("export_panel", show=False)
            self._dpg.configure_item("recommendation_panel", show=True)

    def _on_generation_recovery(self) -> None:
        """Return to the setup view so executable/profile configuration can be checked."""
        if self._dpg is not None and self._dpg.does_item_exist("orca_setup_panel"):
            self._dpg.configure_item("orca_setup_panel", show=True)
        self.navigate("new_calibration")

    def _add_planned_module_card(self, name: str) -> None:
        with self._dpg.child_window(width=278, height=90, border=True):
            self._dpg.add_text(name, color=(165, 180, 195, 255))
            self._dpg.add_spacer(height=6)
            self._dpg.add_text("PLANNED  ·  NOT AVAILABLE", color=(133, 149, 166, 255))

    def _build_sessions_page(self) -> None:
        self._sessions_page = SessionsPage(
            self._dpg,
            self.services["session_service"],
            on_resume=self._resume_session,
        )
        self._sessions_page.render()

    def _build_settings_page(self) -> None:
        self._settings_page = SettingsPage(
            self._dpg,
            self.settings_service,
            on_saved=self._on_settings_saved,
        )
        self._settings_page.render()

    def _on_navigation(self, sender: Any, app_data: Any, user_data: str) -> None:
        del sender, app_data
        self.navigate(user_data)

    def _on_keyboard_shortcut(self, sender: Any, app_data: Any, user_data: str) -> None:
        del sender, app_data
        if self._dpg is not None and (
            self._dpg.is_key_down(self._dpg.mvKey_LControl)
            or self._dpg.is_key_down(self._dpg.mvKey_RControl)
        ):
            self.navigate(user_data)

    def _begin_new_calibration(self) -> None:
        """Start at profile selection and clear only the active in-memory session."""
        if self._generation_page is not None and self._generation_page.state in {
            GenerationState.QUEUED, GenerationState.RUNNING
        }:
            if self._home_page is not None:
                self._home_page.show_error(
                    "Cancel or finish the active generation job before starting another calibration."
                )
            return
        self.active_session = None
        self.services.pop("session", None)
        self.profile_selection = None
        self.experiment_plan = None
        self.resumed_step = None
        self._last_synchronized_plan = None
        self._session_recovery_message = ""
        self.navigate("new_calibration")
        if self._dpg is not None:
            for tag in (
                "module_selection_panel", "experiment_review_panel", "generation_panel",
                "results_panel", "recommendation_panel", "export_panel",
            ):
                if self._dpg.does_item_exist(tag):
                    self._dpg.configure_item(tag, show=False)
            if self._dpg.does_item_exist("profile_selection_panel"):
                self._dpg.configure_item("profile_selection_panel", show=True)
            if self._dpg.does_item_exist("session_recovery_notice"):
                self._dpg.set_value("session_recovery_notice", "")

    def _on_settings_saved(self, settings: AppSettings) -> str | None:
        """Apply Orca overrides and report workspace changes that need restart."""
        previous_workspace = self._active_workspace_root
        self.app_settings = settings
        self.profile_service.set_executable(settings.orca_executable)
        self.profile_service.set_config_roots(settings.orca_config_roots)
        self.profile_service.discover_profiles()
        if self._setup_page is not None:
            self._setup_page._render_state(self.profile_service.setup_state)
        if self._profile_selection_page is not None:
            self._profile_selection_page.refresh()
        if self._export_page is not None:
            self._export_page.default_export_root = settings.default_export_root
        if settings.workspace_root != previous_workspace:
            return "Settings saved. Restart the application to switch to the new workspace."
        return "Settings saved. Orca overrides are active."

    def _resume_session(self, session_id: str) -> None:
        """Restore a session and open the page named by its last saved step."""
        session_service = self.services.get("session_service")
        if session_service is None:
            return
        if self._generation_page is not None and self._generation_page.state in {
            GenerationState.QUEUED, GenerationState.RUNNING
        }:
            message = "Cancel or finish the active generation job before opening another session."
            if self._sessions_page is not None:
                self._sessions_page.show_error(message)
            if self._home_page is not None:
                self._home_page.show_error(message)
            return
        try:
            session = session_service.resume(session_id)
        except Exception as exc:
            message = (
                f"Session could not be resumed: {exc}. Its workspace files were not removed. "
                "Check the saved workspace folder and restore any moved session files."
            )
            if self._sessions_page is not None:
                self._sessions_page.show_error(message)
            if self._home_page is not None:
                self._home_page.show_error(message)
            self._session_recovery_message = message
            return
        if session.archived:
            message = "This session is archived and cannot be resumed from the recent list."
            if self._sessions_page is not None:
                self._sessions_page.show_error(message)
            if self._home_page is not None:
                self._home_page.show_error(message)
            return

        self.active_session = session
        self.services["session"] = session
        self.profile_selection = session.profile_selection
        self.experiment_plan = session.plan
        self.resumed_step = session.current_step
        repository = getattr(session_service, "repository", None)
        sessions_root = getattr(repository, "sessions_root", None)
        missing_artifacts = []
        if sessions_root is not None:
            session_root = Path(sessions_root) / session.session_id
            missing_artifacts = [
                path for path in session.artifact_paths
                if not (session_root / Path(path)).is_file()
            ]
        self._session_recovery_message = (
            "Some saved files are missing or were moved: " + ", ".join(missing_artifacts)
            + ". The session is still available; restore those files to recover the evidence."
            if missing_artifacts else ""
        )
        self.navigate("new_calibration")
        if self._dpg is not None:
            for tag in (
                "module_selection_panel", "experiment_review_panel", "generation_panel",
                "results_panel", "recommendation_panel", "export_panel", "profile_selection_panel",
            ):
                if self._dpg.does_item_exist(tag):
                    self._dpg.configure_item(tag, show=False)
            if self._dpg.does_item_exist("session_recovery_notice"):
                self._dpg.set_value("session_recovery_notice", self._session_recovery_message)

        if self._module_page is not None:
            self._module_page.set_profiles(session.profile_selection)
        if session.plan is not None and self._generation_page is not None:
            self._generation_page.set_context(
                session=session,
                plan=session.plan,
                profiles=session.profile_selection,
            )
            self._last_synchronized_plan = session.plan
        if session.plan is not None and session.results is not None:
            if self._results_page is not None:
                self._results_page.set_context(session=session, plan=session.plan)
            if self._recommendation_page is not None:
                self._recommendation_page.set_context(
                    session=session, plan=session.plan, results=session.results
                )
        if self._export_page is not None:
            self._export_page.set_session(session)

        target_panel = {
            "module_selection": "module_selection_panel",
            "experiment_review": "experiment_review_panel",
            "generation": "generation_panel",
            "results": "results_panel",
            "recommendation": "recommendation_panel",
            "export": "export_panel",
        }.get(session.current_step, "module_selection_panel")
        if self._dpg is not None and self._dpg.does_item_exist(target_panel):
            self._dpg.configure_item(target_panel, show=True)
