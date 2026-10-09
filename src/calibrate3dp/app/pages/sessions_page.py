"""Browsable recent-session list with safe archive and resume actions."""

from __future__ import annotations

from typing import Any, Callable

from calibrate3dp.app.models import SessionSummary
from calibrate3dp.app.pages.home_page import HomePage
from calibrate3dp.app.services.session_service import SessionService


class SessionsPage:
    """List resumable sessions and archive them without deleting their files."""

    session_limit = 50

    def __init__(
        self,
        dpg: Any,
        session_service: SessionService,
        *,
        on_resume: Callable[[str], None],
    ) -> None:
        self.dpg = dpg
        self.session_service = session_service
        self.on_resume = on_resume
        self.sessions: tuple[SessionSummary, ...] = ()
        self.error = ""
        self._rendered = False

    def render(self) -> None:
        if self._rendered:
            self.refresh()
            return
        dpg = self.dpg
        with dpg.group(tag="page_sessions", show=False):
            dpg.add_spacer(height=12)
            dpg.add_text("SESSIONS", color=(92, 191, 178, 255))
            dpg.add_spacer(height=7)
            dpg.add_text("Your calibration history.", color=(238, 244, 249, 255))
            dpg.add_text(
                "Open a saved workflow at its last step, or archive it to hide it from recent lists. Archived files remain in the workspace.",
                color=(165, 180, 195, 255), wrap=850,
            )
            dpg.add_text("", tag="sessions_error", wrap=850,
                         color=(235, 130, 125, 255))
            dpg.add_text("No active sessions.", tag="sessions_empty", show=False,
                         color=(165, 180, 195, 255))
            for index in range(self.session_limit):
                with dpg.child_window(
                    tag=f"sessions_row_{index}", width=-1, height=90, border=True, show=False
                ):
                    dpg.add_text("", tag=f"sessions_title_{index}")
                    dpg.add_text("", tag=f"sessions_detail_{index}", wrap=760,
                                 color=(165, 180, 195, 255))
                    with dpg.group(horizontal=True):
                        dpg.add_button(
                            label="Open session", tag=f"sessions_open_{index}",
                            callback=self._on_open_row, user_data=index,
                        )
                        dpg.add_button(
                            label="Archive", tag=f"sessions_archive_{index}",
                            callback=self._on_archive_row, user_data=index,
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
        self._set("sessions_error", self.error)
        self.dpg.configure_item("sessions_empty", show=not self.sessions and not self.error)
        for index in range(self.session_limit):
            visible = index < len(self.sessions)
            self.dpg.configure_item(f"sessions_row_{index}", show=visible)
            if not visible:
                continue
            item = self.sessions[index]
            self._set(
                f"sessions_title_{index}",
                f"{item.profile_names['process']}  ·  {HomePage.status_label(item.status)}",
            )
            profiles = " · ".join(item.profile_names[key] for key in ("printer", "filament", "process"))
            self._set(
                f"sessions_detail_{index}",
                f"{item.module_id.title()}  ·  {profiles}  ·  Updated {item.updated_at_utc}",
            )

    def show_error(self, message: str) -> None:
        self.error = message
        self._set("sessions_error", message)

    def _on_open_row(self, sender: Any, app_data: Any, user_data: int) -> None:
        del sender, app_data
        if 0 <= user_data < len(self.sessions):
            self.on_resume(self.sessions[user_data].session_id)

    def _on_archive_row(self, sender: Any, app_data: Any, user_data: int) -> None:
        del sender, app_data
        if not 0 <= user_data < len(self.sessions):
            return
        session_id = self.sessions[user_data].session_id
        try:
            self.session_service.archive(session_id)
        except Exception as exc:
            self.show_error(f"Session could not be archived: {exc}")
            return
        self.refresh()

    def _set(self, tag: str, value: str) -> None:
        if self.dpg.does_item_exist(tag):
            self.dpg.set_value(tag, value)


__all__ = ["SessionsPage"]
