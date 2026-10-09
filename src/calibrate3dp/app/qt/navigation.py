"""Keyboard-accessible navigation controls for the Qt application shell."""

from __future__ import annotations

from enum import Enum

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QButtonGroup, QFrame, QLabel, QPushButton, QVBoxLayout, QWidget


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
    """A compact set of checkable destinations with stable keyboard focus."""

    page_selected = Signal(object)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("navigationRail")
        self.setMinimumWidth(194)
        self.setMaximumWidth(220)
        self.buttons: dict[AppPage, QPushButton] = {}
        group = QButtonGroup(self)
        group.setExclusive(True)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(18, 24, 18, 18)
        layout.setSpacing(7)

        brand = QLabel("Calibrate-3DP")
        brand.setObjectName("brandName")
        caption = QLabel("FDM PRINTER CALIBRATION")
        caption.setObjectName("brandCaption")
        layout.addWidget(brand)
        layout.addWidget(caption)
        layout.addSpacing(28)

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
        context = QLabel("LOCAL WORKSPACE")
        context.setObjectName("eyebrow")
        layout.addWidget(context)

    def set_current_page(self, page: AppPage) -> None:
        self.buttons[page].setChecked(True)

