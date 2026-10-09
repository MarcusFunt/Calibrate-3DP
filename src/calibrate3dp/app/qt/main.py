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
    from .view_models import PrinterLibraryUnavailableError, PrinterSummary
    from calibrate3dp.app.services.grouped_orca_service import GroupedOrcaGenerationService
    from calibrate3dp.app.services.library_service import LibraryService
    from calibrate3dp.app.services.profile_service import ProfileService
    from calibrate3dp.app.services.settings_service import AppSettingsService
    from calibrate3dp.orca_cli import OrcaCli
    from calibrate3dp.storage.library_store import LibraryRepository
    from calibrate3dp.storage.session_store import SessionRepository

    settings_service = AppSettingsService()
    workspace = settings_service.settings.workspace_root
    sessions = SessionRepository(workspace)
    records = LibraryRepository(sessions)
    profiles = ProfileService(
        executable=settings_service.settings.orca_executable,
        config_roots=settings_service.settings.orca_config_roots,
    )
    profiles.discover_profiles()
    library = LibraryService(records, profiles)

    class SavedPrinterLibrary:
        def list_saved_printers(self):
            try:
                return tuple(
                    PrinterSummary(record.printer_id, record.display_name, record.model, record.nozzle)
                    for record in library.list_printers()
                )
            except Exception as exc:
                raise PrinterLibraryUnavailableError(str(exc)) from exc

    def cli_provider():
        executable = profiles.setup_state.executable
        if executable is None or not executable.is_file():
            return None
        return OrcaCli(executable)

    generation = GroupedOrcaGenerationService(library, cli_provider=cli_provider)
    window = MainWindow(
        SavedPrinterLibrary(),
        library_service=library,
        generation_service=generation,
        settings_service=settings_service,
        profile_service=profiles,
    )
    window.show()
    application._calibrate3dp_main_window = window  # keep the top-level widget alive
    return application.exec()

