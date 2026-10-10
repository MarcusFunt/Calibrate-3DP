"""Keyboard-accessible navigation controls for the Qt application shell."""

from __future__ import annotations

from enum import Enum

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QButtonGroup, QFrame, QPushButton, QHBoxLayout, QWidget


class AppPage(str, Enum):
    PRINTER_LIBRARY = "printer_library"
    PRINTER_WORKSPACE = "printer_workspace"
    NEW_CALIBRATION = "new_calibration"
    RUNS_HISTORY = "runs_history"
    SETTINGS = "settings"


PAGE_LABELS: dict[AppPage, str] = {
    AppPage.PRINTER_LIBRARY: "Printer Library",
    AppPage.PRINTER_WORKSPACE: "Printer Workspace",
    AppPage.NEW_CALIBRATION: "New Calibration",
    AppPage.RUNS_HISTORY: "Runs / History",
    AppPage.SETTINGS: "Settings",
}


class NavigationRail(QFrame):
    """A compact horizontal set of checkable destinations with stable keyboard focus."""

    page_selected = Signal(object)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("navigationRail")
        self.setMinimumHeight(54)
        self.setMaximumHeight(66)
        self.buttons: dict[AppPage, QPushButton] = {}
        group = QButtonGroup(self)
        group.setExclusive(True)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(24, 8, 24, 8)
        layout.setSpacing(8)

        for page, label in PAGE_LABELS.items():
            button = QPushButton(label)
            button.setObjectName("navButton")
            button.setCheckable(True)
            button.setAccessibleName(label)
            button.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
            button.clicked.connect(lambda _checked=False, target=page: self.page_selected.emit(target))
            group.addButton(button)
            self.buttons[page] = button
            layout.addWidget(button)
        layout.addStretch(1)

    def set_current_page(self, page: AppPage) -> None:
        self.buttons[page].setChecked(True)

