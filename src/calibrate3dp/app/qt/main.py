"""Optional PySide6 application startup with no eager Qt import."""

from __future__ import annotations

import sys
from typing import TYPE_CHECKING, Sequence

if TYPE_CHECKING:
    from PySide6.QtWidgets import QApplication


class QtDependencyMissingError(RuntimeError):
    """Raised when the user launches the Qt UI without installing its extra."""


def create_application(argv: Sequence[str] | None = None) -> "QApplication":
    """Create or reuse QApplication, importing PySide6 only at the UI boundary."""
    try:
        from PySide6.QtWidgets import QApplication
    except ModuleNotFoundError as exc:
        if exc.name != "PySide6":
            raise
        raise QtDependencyMissingError(
            "the Qt desktop interface is an optional dependency; install it with "
            "`python -m pip install 'calibrate-3dp[gui]'`"
        ) from exc

    if argv is None:
        qt_argv = sys.argv
    else:
        qt_argv = [sys.argv[0], *argv]
    application = QApplication.instance()
    if application is None:
        application = QApplication(qt_argv)
    application.setApplicationName("Calibrate-3DP")
    application.setOrganizationName("Calibrate-3DP")
    return application


def run(argv: Sequence[str] | None = None) -> int:
    """Start the Qt shell and run its event loop."""
    application = create_application(argv)
    from .main_window import MainWindow
    from .view_models import EmptyPrinterLibraryService

    window = MainWindow(EmptyPrinterLibraryService())
    window.show()
    application._calibrate3dp_main_window = window  # keep the top-level widget alive
    return application.exec()

