"""Recent-session landing page for the local calibration workbench."""

from __future__ import annotations

from typing import Any, Callable

from calibrate3dp.app.models import SessionSummary
from calibrate3dp.app.services.session_service import SessionService


_STATUS_LABELS = {
    "setup": "Setup",
    "ready_to_print": "Ready to print",
    "awaiting_results": "Awaiting results",
    "refinement_ready": "Refinement ready",
    "confirmation_required": "Confirmation required",
    "export_ready": "Export ready",
    "completed": "Completed",
    "inconclusive": "Inconclusive",
}


class HomePage:
    """Show the latest saved sessions and the available next actions."""

    session_limit = 5

    def __init__(
        self,
        dpg: Any,
        session_service: SessionService,
        *,
        on_new_calibration: Callable[[], None],
        on_resume: Callable[[str], None],
        recovery_notice: str = "",
    ) -> None:
        self.dpg = dpg
        self.session_service = session_service
        self.on_new_calibration = on_new_calibration
        self.on_resume = on_resume
        self.recovery_notice = recovery_notice
        self.sessions: tuple[SessionSummary, ...] = ()
        self.error = ""
        self._rendered = False

    @staticmethod
    def status_label(status: str) -> str:
        """Present the persisted status value as a concise user-facing label."""
        return _STATUS_LABELS.get(status, status.replace("_", " ").capitalize())

    def render(self) -> None:
        if self._rendered:
            self.refresh()
            return
        dpg = self.dpg
        with dpg.group(tag="page_home"):
            dpg.add_spacer(height=12)
            dpg.add_text("LOCAL-FIRST PRINT CALIBRATION", color=(92, 191, 178, 255))
            dpg.add_spacer(height=7)
            dpg.add_text("Make each test easier to trust.", color=(238, 244, 249, 255))
            dpg.add_text(
                "Plan controlled calibration experiments from OrcaSlicer profiles, then compare printed results at your own pace.",
                color=(165, 180, 195, 255), wrap=850,
            )
            dpg.add_spacer(height=14)
            dpg.add_button(
                label="Start a new calibration", tag="home_new_calibration",
                callback=self._on_new_calibration, width=220,
            )
            dpg.add_text(self.recovery_notice, tag="home_recovery_notice", wrap=850,
                         color=(225, 180, 112, 255))
            dpg.add_text("", tag="home_error", wrap=850, color=(235, 130, 125, 255))
            dpg.add_spacer(height=18)
            dpg.add_text("RECENT SESSIONS", color=(133, 149, 166, 255))
            dpg.add_text("Resume at the last step saved in each session.",
                         color=(165, 180, 195, 255))
            dpg.add_text("No saved sessions yet.", tag="home_empty", show=False,
                         color=(165, 180, 195, 255))
            for index in range(self.session_limit):
                with dpg.child_window(
                    tag=f"home_session_{index}", width=-1, height=80, border=True, show=False
                ):
                    dpg.add_text("", tag=f"home_session_title_{index}")
                    dpg.add_text("", tag=f"home_session_detail_{index}",
                                 color=(165, 180, 195, 255), wrap=760)
                    dpg.add_button(
                        label="Resume", tag=f"home_session_resume_{index}",
                        callback=self._on_resume_row, user_data=index,
                    )
        self._rendered = True
        self.refresh()

    def refresh(self) -> None:
        try:
            self.sessions = self.session_service.list_recent(limit=self.session_limit)
            self.error = ""
        except Exception as exc:
            self.sessions = ()
            self.error = f"Saved sessions could not be listed: {exc}"
        if not self._rendered:
            return
        self._set("home_error", self.error)
        self.dpg.configure_item("home_empty", show=not self.sessions and not self.error)
        for index in range(self.session_limit):
            visible = index < len(self.sessions)
            self.dpg.configure_item(f"home_session_{index}", show=visible)
            if not visible:
                continue
            item = self.sessions[index]
            self._set(
                f"home_session_title_{index}",
                f"{item.profile_names['process']}  ·  {self.status_label(item.status)}",
            )
            names = " · ".join(item.profile_names[role] for role in ("printer", "filament", "process"))
            self._set(
                f"home_session_detail_{index}",
                f"{item.module_id.title()}  ·  {names}  ·  Updated {item.updated_at_utc}",
            )

    def show_error(self, message: str) -> None:
        self.error = message
        self._set("home_error", message)

    def _on_new_calibration(self, sender: Any = None, app_data: Any = None) -> None:
        del sender, app_data
        self.on_new_calibration()

    def _on_resume_row(self, sender: Any, app_data: Any, user_data: int) -> None:
        del sender, app_data
        if 0 <= user_data < len(self.sessions):
            self.on_resume(self.sessions[user_data].session_id)

    def _set(self, tag: str, value: str) -> None:
        if self.dpg.does_item_exist(tag):
            self.dpg.set_value(tag, value)


__all__ = ["HomePage"]
