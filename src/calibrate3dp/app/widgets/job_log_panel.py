"""Expandable, read-only stdout and stderr views for a generation job."""

from __future__ import annotations

from typing import Any


class JobLogPanel:
    """Collect worker log deltas and display them only from the UI thread."""

    def __init__(self, dpg: Any) -> None:
        self.dpg = dpg
        self.stdout = ""
        self.stderr = ""

    def render(self) -> None:
        dpg = self.dpg
        with dpg.group(tag="generation_log_panel"):
            with dpg.collapsing_header(label="Orca stdout", default_open=False):
                dpg.add_input_text(
                    tag="generation_stdout",
                    default_value="",
                    multiline=True,
                    readonly=True,
                    width=-1,
                    height=120,
                )
            with dpg.collapsing_header(label="Orca stderr", default_open=False):
                dpg.add_input_text(
                    tag="generation_stderr",
                    default_value="",
                    multiline=True,
                    readonly=True,
                    width=-1,
                    height=100,
                )

    def append(self, stdout_delta: str = "", stderr_delta: str = "") -> None:
        """Append log deltas and update the read-only controls on the caller thread."""
        if stdout_delta:
            self.stdout += stdout_delta
            if self.dpg.does_item_exist("generation_stdout"):
                self.dpg.set_value("generation_stdout", self.stdout)
        if stderr_delta:
            self.stderr += stderr_delta
            if self.dpg.does_item_exist("generation_stderr"):
                self.dpg.set_value("generation_stderr", self.stderr)

    def add_attempt_separator(self, label: str) -> None:
        """Keep earlier diagnostics visible when the user starts a fresh attempt."""
        separator = f"\n--- {label} ---\n"
        self.stdout += separator
        self.stderr += separator
        if self.dpg.does_item_exist("generation_stdout"):
            self.dpg.set_value("generation_stdout", self.stdout)
        if self.dpg.does_item_exist("generation_stderr"):
            self.dpg.set_value("generation_stderr", self.stderr)

    def clear(self) -> None:
        """Clear logs when a different session or plan is opened."""
        self.stdout = ""
        self.stderr = ""
        if self.dpg.does_item_exist("generation_stdout"):
            self.dpg.set_value("generation_stdout", "")
        if self.dpg.does_item_exist("generation_stderr"):
            self.dpg.set_value("generation_stderr", "")
