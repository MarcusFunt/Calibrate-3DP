"""Native Qt application shell and the first mockup-based library screen."""

from __future__ import annotations

from PySide6.QtCore import QSize, Qt, Signal
from PySide6.QtGui import QColor, QIcon, QPainter, QPen, QPixmap
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMainWindow,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QStackedWidget,
    QStyle,
    QVBoxLayout,
    QWidget,
)

from .navigation import AppPage, NavigationRail
from .theme import TOKENS, application_stylesheet
from .view_models import PrinterLibraryService, PrinterLibraryViewModel, PrinterSummary
from .workflow_widgets import (
    AddMaterialDialog,
    AddPrinterDialog,
    NewCalibrationPage,
    OrcaSettingsPage,
    PrinterWorkspacePage,
    RunHistoryPage,
)
from calibrate3dp.app.services.grouped_orca_service import GroupedOrcaGenerationService
from calibrate3dp.app.services.library_service import LibraryService
from calibrate3dp.app.services.profile_service import ProfileService
from calibrate3dp.app.services.settings_service import AppSettingsService


def _search_icon() -> QIcon:
    pixmap = QPixmap(20, 20)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setPen(QPen(QColor(TOKENS.muted), 1.8))
    painter.drawEllipse(3, 3, 10, 10)
    painter.drawLine(12, 12, 17, 17)
    painter.end()
    return QIcon(pixmap)


def _settings_icon() -> QIcon:
    pixmap = QPixmap(20, 20)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setPen(QPen(QColor(TOKENS.muted), 1.6))
    painter.setBrush(Qt.BrushStyle.NoBrush)
    painter.drawEllipse(5, 5, 10, 10)
    painter.drawEllipse(8, 8, 4, 4)
    for dx, dy in ((1, 0), (1, 1), (0, 1), (-1, 1), (-1, 0), (-1, -1), (0, -1), (1, -1)):
        painter.drawLine(10 + dx * 6, 10 + dy * 6, 10 + dx * 9, 10 + dy * 9)
    painter.end()
    return QIcon(pixmap)


def _context_pill(caption: str, value: str) -> tuple[QFrame, QLabel]:
    pill = QFrame()
    pill.setObjectName("contextPill")
    content = QVBoxLayout(pill)
    content.setContentsMargins(10, 5, 10, 5)
    content.setSpacing(0)
    label = QLabel(caption.upper())
    label.setObjectName("contextLabel")
    value_label = QLabel(value)
    value_label.setObjectName("contextValue")
    value_label.setMaximumWidth(180)
    content.addWidget(label)
    content.addWidget(value_label)
    return pill, value_label


class RadioIndicator(QWidget):
    """Vector radio marker that pairs selection color with a shape change."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._selected = False
        self.setFixedSize(26, 26)
        self.setAccessibleName("Printer selection")

    def set_selected(self, selected: bool) -> None:
        self._selected = selected
        self.update()

    def paintEvent(self, _event) -> None:  # noqa: N802 - Qt override name
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        color = QColor(TOKENS.green if self._selected else TOKENS.muted)
        painter.setPen(QPen(color, 1.5))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawEllipse(3, 3, 20, 20)
        if self._selected:
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(color)
            painter.drawEllipse(8, 8, 10, 10)
        painter.end()


class PrinterIllustration(QWidget):
    """Small original line-art printer drawing rendered at device-independent size."""

    def __init__(self, *, large: bool = False, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setFixedSize(250 if large else 78, 250 if large else 70)
        self.setAccessibleName("Line drawing of a 3D printer")

    def paintEvent(self, _event) -> None:  # noqa: N802 - Qt override name
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        color = QColor(TOKENS.green if self.width() > 100 else "#65756D")
        pen = QPen(color, 1.7 if self.width() > 100 else 1.25)
        painter.setPen(pen)

        left, top = self.width() * 0.19, self.height() * 0.13
        width, height = self.width() * 0.60, self.height() * 0.73
        right, bottom = left + width, top + height
        depth_x, depth_y = self.width() * 0.10, -self.height() * 0.07

        # Cubic frame and depth rails.
        painter.drawLine(int(left), int(top), int(right), int(top))
        painter.drawLine(int(left), int(top), int(left), int(bottom))
        painter.drawLine(int(right), int(top), int(right), int(bottom))
        painter.drawLine(int(left), int(bottom), int(right), int(bottom))
        painter.drawLine(int(left), int(top), int(left + depth_x), int(top + depth_y))
        painter.drawLine(int(right), int(top), int(right + depth_x), int(top + depth_y))
        painter.drawLine(
            int(left + depth_x), int(top + depth_y),
            int(right + depth_x), int(top + depth_y),
        )
        painter.drawLine(
            int(right + depth_x), int(top + depth_y),
            int(right + depth_x), int(bottom + depth_y),
        )
        painter.drawLine(int(right), int(bottom), int(right + depth_x), int(bottom + depth_y))
        painter.drawLine(int(left + width * 0.14), int(top), int(left + width * 0.14), int(bottom))

        # Gantry, toolhead, and bed make the drawing recognizable at card scale.
        gantry_y = top + height * 0.28
        painter.drawLine(int(left), int(gantry_y), int(right + depth_x * 0.65), int(gantry_y))
        head_x = left + width * 0.55
        painter.drawRect(
            int(head_x - width * 0.07), int(gantry_y - height * 0.04),
            int(width * 0.14), int(height * 0.16),
        )
        bed_y = top + height * 0.69
        painter.drawLine(int(left + width * 0.16), int(bed_y), int(right - width * 0.10), int(bed_y))
        painter.drawLine(
            int(left + width * 0.20), int(bed_y + height * 0.09),
            int(right - width * 0.05), int(bed_y + height * 0.09),
        )
        painter.drawLine(
            int(left + width * 0.28), int(bed_y + height * 0.09),
            int(left + width * 0.28), int(bottom),
        )
        painter.drawLine(
            int(right - width * 0.15), int(bed_y + height * 0.09),
            int(right - width * 0.15), int(bottom),
        )
        painter.end()


class PrinterRow(QPushButton):
    selected = Signal(str)

    def __init__(self, printer: PrinterSummary, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.printer = printer
        self.setObjectName("printerRow")
        self.setCheckable(True)
        self.setMinimumHeight(88)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.setAccessibleName(
            f"Select {printer.name}, {printer.model}, {printer.nozzle} nozzle"
        )
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        row = QHBoxLayout(self)
        row.setContentsMargins(8, 3, 11, 3)
        row.setSpacing(16)
        row.addWidget(PrinterIllustration())

        labels = QVBoxLayout()
        labels.setSpacing(4)
        name = QLabel(printer.name)
        name.setStyleSheet("font-size: 11pt; font-weight: 650;")
        detail = QLabel(f"{printer.model}   |   {printer.nozzle} nozzle")
        detail.setObjectName("mutedStatus")
        labels.addWidget(name)
        labels.addWidget(detail)
        labels.addStretch(1)
        row.addLayout(labels, 1)

        self.radio = RadioIndicator()
        row.addWidget(self.radio)
        self.clicked.connect(lambda: self.selected.emit(printer.printer_id))

    def set_selected(self, selected: bool) -> None:
        self.setChecked(selected)
        self.radio.set_selected(selected)


class PrinterLibraryPage(QWidget):
    open_printer = Signal(str)
    add_printer_requested = Signal()

    def __init__(self, model: PrinterLibraryViewModel, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.model = model
        self.rows: dict[str, PrinterRow] = {}
        self.setObjectName("printerLibraryPage")
        outer = QVBoxLayout(self)
        outer.setContentsMargins(36, 28, 36, 30)
        outer.setSpacing(0)

        title = QLabel("Choose a printer")
        title.setObjectName("pageTitle")
        subtitle = QLabel(
            "Select a saved printer to continue, or import a printer profile from a file."
        )
        subtitle.setObjectName("bodyCopy")
        subtitle.setWordWrap(True)
        outer.addWidget(title)
        outer.addSpacing(5)
        outer.addWidget(subtitle)
        outer.addSpacing(28)

        columns = QHBoxLayout()
        columns.setSpacing(30)
        left_column = QWidget()
        left_column.setMinimumWidth(410)
        left_layout = QVBoxLayout(left_column)
        left_layout.setContentsMargins(0, 0, 0, 0)
        left_layout.setSpacing(12)

        search_label = QLabel("Search printers")
        search_label.setObjectName("eyebrow")
        self.search = QLineEdit()
        search_label.setBuddy(self.search)
        self.search.setObjectName("printerSearch")
        self.search.setPlaceholderText("Search by name or model")
        self.search.setClearButtonEnabled(True)
        self.search.setAccessibleName("Search saved printers by name or model")
        self.search.setMinimumHeight(43)
        self.search.addAction(_search_icon(), QLineEdit.ActionPosition.LeadingPosition)
        left_layout.addWidget(search_label)
        left_layout.addWidget(self.search)

        self.list_host = QWidget()
        self.list_layout = QVBoxLayout(self.list_host)
        self.list_layout.setContentsMargins(0, 0, 0, 0)
        self.list_layout.setSpacing(8)
        self.empty_state = QLabel("No saved printers yet. Import a printer profile to get started.")
        self.empty_state.setObjectName("mutedStatus")
        self.empty_state.setWordWrap(True)
        self.list_layout.addWidget(self.empty_state)
        self.retry_button = QPushButton("Retry")
        self.retry_button.setObjectName("secondaryAction")
        self.retry_button.clicked.connect(self._retry_load)
        self.retry_button.hide()
        left_layout.addWidget(self.retry_button)
        self.list_layout.addStretch(1)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setWidget(self.list_host)
        left_layout.addWidget(scroll, 1)

        selection_footer = QFrame()
        footer_layout = QHBoxLayout(selection_footer)
        footer_layout.setContentsMargins(0, 16, 0, 0)
        footer_layout.setSpacing(18)
        footer_text = QVBoxLayout()
        eyebrow = QLabel("SELECTED PRINTER")
        eyebrow.setObjectName("eyebrow")
        self.selected_name = QLabel("No printer selected")
        self.selected_name.setObjectName("sectionTitle")
        self.selected_name.setWordWrap(True)
        footer_text.addWidget(eyebrow)
        footer_text.addWidget(self.selected_name)
        footer_layout.addLayout(footer_text, 1)
        self.open_button = QPushButton("Open printer   →")
        self.open_button.setObjectName("primaryAction")
        self.open_button.setEnabled(False)
        self.open_button.setMinimumWidth(185)
        self.open_button.clicked.connect(self._open_selected)
        footer_layout.addWidget(self.open_button)
        left_layout.addWidget(selection_footer)
        columns.addWidget(left_column, 6)

        divider = QFrame()
        divider.setObjectName("divider")
        columns.addWidget(divider)

        import_panel = QWidget()
        import_panel.setMinimumWidth(250)
        import_layout = QVBoxLayout(import_panel)
        import_layout.setContentsMargins(4, 0, 0, 0)
        import_layout.setSpacing(14)
        import_title = QLabel("Add a printer")
        import_title.setObjectName("sectionTitle")
        import_description = QLabel(
            "Choose local OrcaSlicer machine and process presets, then save resolved printer records."
        )
        import_description.setObjectName("bodyCopy")
        import_description.setWordWrap(True)
        self.browse_button = QPushButton("Add printer")
        self.browse_button.setObjectName("secondaryAction")
        self.browse_button.setIcon(
            self.style().standardIcon(QStyle.StandardPixmap.SP_DirOpenIcon)
        )
        self.browse_button.setMinimumHeight(47)
        self.browse_button.clicked.connect(lambda _checked=False: self.add_printer_requested.emit())
        self.import_status = QLabel("Source Orca presets remain unchanged.")
        self.import_status.setObjectName("mutedStatus")
        self.import_status.setWordWrap(True)
        import_layout.addWidget(import_title)
        import_layout.addWidget(import_description)
        import_layout.addSpacing(4)
        import_layout.addWidget(self.browse_button)
        import_layout.addStretch(1)
        import_layout.addWidget(PrinterIllustration(large=True))
        import_layout.addWidget(self.import_status)
        columns.addWidget(import_panel, 4)

        outer.addLayout(columns, 1)
        self.search.textChanged.connect(self._refresh_rows)
        self._refresh_rows()

    def _clear_rows(self) -> None:
        while self.list_layout.count():
            item = self.list_layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()
        self.rows.clear()

    def _refresh_rows(self) -> None:
        visible = self.model.visible_printers(self.search.text())
        self._clear_rows()
        if not visible:
            if self.model.load_error:
                message = f"The printer library could not be loaded. {self.model.load_error}"
                self.retry_button.show()
            else:
                message = (
                    "No saved printers yet. Import a printer profile to get started."
                    if not self.model.printers
                    else "No printers match this search. Your current selection is unchanged."
                )
                self.retry_button.hide()
            self.empty_state = QLabel(message)
            self.empty_state.setObjectName("mutedStatus")
            self.empty_state.setWordWrap(True)
            self.list_layout.addWidget(self.empty_state)
        else:
            self.retry_button.hide()
            for printer in visible:
                row = PrinterRow(printer)
                row.set_selected(printer.printer_id == self.model.selected_id)
                row.selected.connect(self._select_printer)
                self.rows[printer.printer_id] = row
                self.list_layout.addWidget(row)
        self.list_layout.addStretch(1)
        selected = self.model.selected_printer
        self.selected_name.setText(selected.name if selected else "No printer selected")
        self.open_button.setEnabled(selected is not None)

    def _retry_load(self) -> None:
        self.model.reload()
        self._refresh_rows()

    def refresh(self) -> None:
        self.model.reload()
        self._refresh_rows()

    def _select_printer(self, printer_id: str) -> None:
        self.model.select(printer_id)
        for row_id, row in self.rows.items():
            row.set_selected(row_id == printer_id)
        selected = self.model.selected_printer
        self.selected_name.setText(selected.name if selected else "No printer selected")
        self.open_button.setEnabled(selected is not None)

    def _open_selected(self) -> None:
        selected = self.model.selected_printer
        if selected is not None:
            self.open_printer.emit(selected.printer_id)

class PlaceholderPage(QWidget):
    def __init__(self, title: str, description: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(40, 36, 40, 36)
        layout.setSpacing(12)
        heading = QLabel(title)
        heading.setObjectName("pageTitle")
        copy = QLabel(description)
        copy.setObjectName("bodyCopy")
        copy.setWordWrap(True)
        layout.addWidget(heading)
        layout.addWidget(copy)
        layout.addStretch(1)


class MainWindow(QMainWindow):
    """Route-aware application frame that receives a typed library service."""

    def __init__(
        self,
        printer_library: PrinterLibraryService,
        *,
        library_service: LibraryService | None = None,
        generation_service: GroupedOrcaGenerationService | None = None,
        calibration_state_service=None,
        settings_service: AppSettingsService | None = None,
        profile_service: ProfileService | None = None,
    ) -> None:
        super().__init__()
        self.setWindowTitle("Calibrate-3DP")
        self.setMinimumSize(1120, 720)
        self.resize(1280, 820)
        self.setStyleSheet(application_stylesheet())
        self.view_model = PrinterLibraryViewModel(printer_library)
        self.library_service = library_service
        self._current_page = AppPage.PRINTER_LIBRARY

        canvas = QWidget()
        canvas.setObjectName("appCanvas")
        root = QVBoxLayout(canvas)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)
        self.navigation = NavigationRail()
        self.navigation.page_selected.connect(self.navigate_to)

        self.pages = QStackedWidget()
        self.pages.setObjectName("pageStack")
        self.page_widgets: dict[AppPage, QWidget] = {}
        library = PrinterLibraryPage(self.view_model)
        library.open_printer.connect(self._open_printer_workspace)
        library.add_printer_requested.connect(self._add_printer)
        self.library_page = library
        self._add_page(AppPage.PRINTER_LIBRARY, library)
        shared_calibration_state = calibration_state_service
        if shared_calibration_state is None and generation_service is not None:
            shared_calibration_state = getattr(
                generation_service, "calibration_state_service", None
            )
        self.workspace_page = PrinterWorkspacePage(
            library_service,
            generation_service,
            calibration_state_service=shared_calibration_state,
        )
        self.workspace_page.add_material_requested.connect(self._add_material)
        self.workspace_page.run_finished.connect(self._refresh_history)
        self._add_page(AppPage.PRINTER_WORKSPACE, self.workspace_page)
        new_calibration = NewCalibrationPage()
        new_calibration.open_printer_library.connect(self._open_selected_for_calibration)
        self._add_page(AppPage.NEW_CALIBRATION, new_calibration)
        self.history_page = RunHistoryPage(library_service, generation_service=generation_service)
        self._add_page(AppPage.RUNS_HISTORY, self.history_page)
        if settings_service is not None and profile_service is not None and library_service is not None:
            settings_page = OrcaSettingsPage(settings_service, profile_service, library_service)
        else:
            settings_page = PlaceholderPage(
                "Settings", "OrcaSlicer setup is available in the configured local application."
            )
        self._add_page(AppPage.SETTINGS, settings_page)

        self.header = QFrame()
        self.header.setObjectName("appHeader")
        header_layout = QHBoxLayout(self.header)
        header_layout.setContentsMargins(24, 12, 24, 12)
        header_layout.setSpacing(12)

        self.brand_button = QPushButton("Calibrate-3DP")
        self.brand_button.setObjectName("brandButton")
        self.brand_button.setAccessibleName("Open the printer library")
        self.brand_button.setToolTip("Printer library")
        self.brand_button.clicked.connect(lambda: self.navigate_to(AppPage.PRINTER_LIBRARY))
        header_layout.addWidget(self.brand_button)

        divider = QFrame()
        divider.setObjectName("divider")
        divider.setFixedWidth(1)
        header_layout.addWidget(divider)

        printer_pill, self.printer_context_value = _context_pill("Printer", "Choose a printer")
        material_pill, self.material_context_value = _context_pill("Material", "No material selected")
        header_layout.addWidget(printer_pill)
        header_layout.addWidget(material_pill)
        header_layout.addStretch(1)

        self.plate_code_entry = self.history_page.code_entry
        self.plate_code_entry.setMinimumWidth(180)
        self.plate_code_entry.setClearButtonEnabled(True)
        self.plate_lookup_button = self.history_page.lookup_button
        self.plate_lookup_button.clicked.disconnect(self.history_page._lookup)
        self.plate_lookup_button.clicked.connect(self._lookup_global_plate)
        header_layout.addWidget(self.history_page.lookup_controls)

        settings_button = self.navigation.buttons[AppPage.SETTINGS]
        settings_button.setText("Settings")
        settings_button.setIcon(_settings_icon())
        settings_button.setIconSize(QSize(17, 17))
        settings_button.setObjectName("settingsButton")
        settings_button.setAccessibleName("Settings")
        settings_button.setToolTip("Settings")
        header_layout.addWidget(settings_button)

        self.workspace_page.material_choice.currentTextChanged.connect(
            lambda _text: self._sync_header_context()
        )
        self._sync_header_context()

        root.addWidget(self.header)
        root.addWidget(self.navigation)
        root.addWidget(self.pages, 1)
        self.setCentralWidget(canvas)
        self.navigation.set_current_page(self._current_page)

    def _add_page(self, page: AppPage, widget: QWidget) -> None:
        self.page_widgets[page] = widget
        self.pages.addWidget(widget)

    def navigate_to(self, page: AppPage) -> None:
        if page not in self.page_widgets:
            raise ValueError(f"unknown application page: {page!r}")
        self._current_page = page
        self.pages.setCurrentWidget(self.page_widgets[page])
        self.navigation.set_current_page(page)

    def _open_printer_workspace(self, printer_id: str) -> None:
        self.view_model.select(printer_id)
        self.workspace_page.show_printer(self.view_model.selected_printer)
        self._sync_header_context()
        self.navigate_to(AppPage.PRINTER_WORKSPACE)

    def _sync_header_context(self) -> None:
        printer = self.view_model.selected_printer
        self.printer_context_value.setText(
            printer.name if printer is not None else "Choose a printer"
        )
        material = self.workspace_page.material_choice.currentText().strip()
        self.material_context_value.setText(material or "No material selected")

    def _lookup_global_plate(self) -> None:
        self.navigate_to(AppPage.RUNS_HISTORY)
        self.history_page._lookup()

    def _add_printer(self) -> None:
        if self.library_service is None:
            self.library_page.import_status.setText("The persistent profile library is unavailable.")
            return
        dialog = AddPrinterDialog(self.library_service, self)
        if dialog.exec() != dialog.DialogCode.Accepted or dialog.record is None:
            return
        self.library_page.refresh()
        self.view_model.select(dialog.record.printer_id)
        self.library_page._refresh_rows()
        self._open_printer_workspace(dialog.record.printer_id)

    def _add_material(self, printer_id: str) -> None:
        if self.library_service is None:
            return
        try:
            printer = self.library_service.repository.get_printer(printer_id)
        except Exception as exc:
            self.workspace_page.state.setText(f"The saved printer could not be loaded: {exc}")
            return
        dialog = AddMaterialDialog(self.library_service, printer, self)
        if dialog.exec() == dialog.DialogCode.Accepted:
            self.workspace_page.refresh()

    def _open_selected_for_calibration(self) -> None:
        printer = self.view_model.selected_printer
        if printer is None:
            self.navigate_to(AppPage.PRINTER_LIBRARY)
            return
        self.workspace_page.show_printer(printer)
        self.navigate_to(AppPage.PRINTER_WORKSPACE)

    def _refresh_history(self) -> None:
        self.history_page.refresh()

