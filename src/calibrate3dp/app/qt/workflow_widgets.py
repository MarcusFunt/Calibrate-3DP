"""Qt dialogs and pages for the saved-library and grouped ironing workflow."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from threading import Event
from typing import Any
from dataclasses import replace

from PySide6.QtCore import QObject, QRunnable, Qt, Signal, Slot, QThreadPool
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QGridLayout,
    QPushButton,
    QPlainTextEdit,
    QScrollArea,
    QSizePolicy,
    QTableWidget,
    QTableWidgetItem,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from calibrate3dp.app.services.grouped_orca_service import GroupedOrcaGenerationService
from calibrate3dp.app.services.calibration_state_service import CalibrationStateService
from calibrate3dp.calibration.state import CalibrationStatus
from calibrate3dp.app.qt.calibration_status import CalibrationStatusPanel
from calibrate3dp.app.qt.experiment_review import ExperimentConfigurationDialog
from calibrate3dp.app.qt.experiment_detail import ExperimentDetailsDialog
from calibrate3dp.app.services.library_service import LibraryService
from calibrate3dp.app.services.profile_service import ProfileService
from calibrate3dp.app.services.settings_service import AppSettingsService
from calibrate3dp.app.services.profile_service import ProfileChoice
from calibrate3dp.domain.records import CalibrationRunRecord, MaterialRecord, PrinterRecord


def _profile_choice(combo: QComboBox) -> ProfileChoice | None:
    value = combo.currentData(Qt.ItemDataRole.UserRole)
    return value if isinstance(value, ProfileChoice) else None


def _add_choices(combo: QComboBox, choices: tuple[ProfileChoice, ...]) -> None:
    combo.clear()
    for choice in choices:
        combo.addItem(choice.label, choice)


class AddPrinterDialog(QDialog):
    """Choose local machine/process profiles and save resolved snapshots."""

    def __init__(self, library: LibraryService, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.library = library
        self.record: PrinterRecord | None = None
        self.setWindowTitle("Add printer")
        self.setMinimumWidth(540)
        layout = QVBoxLayout(self)
        intro = QLabel(
            "Choose local OrcaSlicer machine and process presets. Calibrate-3DP stores resolved copies and leaves the source presets untouched."
        )
        intro.setWordWrap(True)
        layout.addWidget(intro)

        form = QFormLayout()
        self.display_name = QLineEdit()
        self.display_name.setPlaceholderText("Workshop printer")
        self.model = QLineEdit()
        self.model.setPlaceholderText("Printer model")
        self.nozzle = QLineEdit()
        self.nozzle.setPlaceholderText("0.4 mm")
        self.machine_choice = QComboBox()
        self.process_choice = QComboBox()
        form.addRow("Saved name", self.display_name)
        form.addRow("Printer model", self.model)
        form.addRow("Nozzle", self.nozzle)
        form.addRow("Machine profile", self.machine_choice)
        form.addRow("Process profile", self.process_choice)
        layout.addLayout(form)

        row = QHBoxLayout()
        self.import_button = QPushButton("Import profile file…")
        self.import_button.clicked.connect(self._import_profile)
        self.import_status = QLabel("Installed Orca presets are detected when available.")
        self.import_status.setWordWrap(True)
        row.addWidget(self.import_button)
        row.addWidget(self.import_status, 1)
        layout.addLayout(row)

        self.error = QLabel("")
        self.error.setWordWrap(True)
        self.error.setObjectName("mutedStatus")
        layout.addWidget(self.error)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self._save)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        self.machine_choice.currentIndexChanged.connect(self._suggest_machine_values)
        self._reload_choices()

    def _reload_choices(self) -> None:
        _add_choices(self.machine_choice, self.library.profile_choices("printer"))
        _add_choices(self.process_choice, self.library.profile_choices("process"))
        self._suggest_machine_values()

    def _suggest_machine_values(self) -> None:
        choice = _profile_choice(self.machine_choice)
        if choice is None:
            return
        profile = self.library.profiles.resolve_choice(choice)
        if not self.display_name.text().strip():
            self.display_name.setText(profile.profile.name)
        if not self.model.text().strip():
            self.model.setText(str(profile.settings.get("printer_model") or profile.profile.name))
        if not self.nozzle.text().strip():
            value = profile.settings.get("nozzle_diameter", [""])
            if isinstance(value, (list, tuple)):
                value = value[0] if value else ""
            if value:
                rendered = str(value)
                self.nozzle.setText(rendered if "mm" in rendered.casefold() else f"{rendered} mm")

    def _import_profile(self) -> None:
        path, _filter = QFileDialog.getOpenFileName(
            self,
            "Import OrcaSlicer profile or bundle",
            str(Path.home()),
            "Orca profiles (*.json *.zip);;All files (*)",
        )
        if not path:
            return
        try:
            self.library.import_profile(path)
        except Exception as exc:
            self.error.setText(str(exc))
            return
        self._reload_choices()
        self.import_status.setText(f"Imported {Path(path).name} for local selection.")

    def _save(self) -> None:
        try:
            self.record = self.library.add_printer(
                display_name=self.display_name.text(),
                model=self.model.text(),
                nozzle=self.nozzle.text(),
                machine_choice=_profile_choice(self.machine_choice),
                process_choice=_profile_choice(self.process_choice),
            )
        except Exception as exc:
            self.error.setText(str(exc))
            return
        self.accept()


class AddMaterialDialog(QDialog):
    """Save a filament profile and the nozzle context it was imported for."""

    def __init__(
        self,
        library: LibraryService,
        printer: PrinterRecord,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.library = library
        self.printer = printer
        self.record: MaterialRecord | None = None
        self.setWindowTitle(f"Add material for {printer.display_name}")
        self.setMinimumWidth(520)
        layout = QVBoxLayout(self)
        form = QFormLayout()
        self.display_name = QLineEdit()
        self.nozzle_context = QLineEdit(printer.nozzle)
        self.toolhead_context = QLineEdit()
        self.toolhead_context.setPlaceholderText("Optional")
        self.filament_choice = QComboBox()
        form.addRow("Material name", self.display_name)
        form.addRow("Nozzle context", self.nozzle_context)
        form.addRow("Toolhead context", self.toolhead_context)
        form.addRow("Filament profile", self.filament_choice)
        layout.addLayout(form)
        row = QHBoxLayout()
        self.import_button = QPushButton("Import profile file…")
        self.import_button.clicked.connect(self._import_profile)
        self.import_status = QLabel("Profiles stay on this computer.")
        row.addWidget(self.import_button)
        row.addWidget(self.import_status, 1)
        layout.addLayout(row)
        self.error = QLabel("")
        self.error.setWordWrap(True)
        layout.addWidget(self.error)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self._save)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        self.filament_choice.currentIndexChanged.connect(self._suggest_material_name)
        self._reload_choices()

    def _reload_choices(self) -> None:
        _add_choices(self.filament_choice, self.library.profile_choices("filament"))
        self._suggest_material_name()

    def _suggest_material_name(self) -> None:
        choice = _profile_choice(self.filament_choice)
        if choice is not None and not self.display_name.text().strip():
            self.display_name.setText(choice.name)

    def _import_profile(self) -> None:
        path, _filter = QFileDialog.getOpenFileName(
            self, "Import OrcaSlicer filament profile", str(Path.home()),
            "Orca profiles (*.json *.zip);;All files (*)",
        )
        if not path:
            return
        try:
            self.library.import_profile(path)
        except Exception as exc:
            self.error.setText(str(exc))
            return
        self._reload_choices()
        self.import_status.setText(f"Imported {Path(path).name} for local selection.")

    def _save(self) -> None:
        try:
            self.record = self.library.add_material(
                display_name=self.display_name.text(),
                nozzle_context=self.nozzle_context.text(),
                toolhead_context=self.toolhead_context.text(),
                filament_choice=_profile_choice(self.filament_choice),
            )
        except Exception as exc:
            self.error.setText(str(exc))
            return
        self.accept()


class _RunSignals(QObject):
    completed = Signal(object)
    failed = Signal(str)


class _RunTask(QRunnable):
    def __init__(self, service: GroupedOrcaGenerationService, config_id: str, cancel_event: Event) -> None:
        super().__init__()
        self.service = service
        self.config_id = config_id
        self.cancel_event = cancel_event
        self.signals = _RunSignals()

    @Slot()
    def run(self) -> None:
        try:
            record = self.service.generate_from_configuration(
                self.config_id, cancel_event=self.cancel_event
            )
        except Exception as exc:
            self.signals.failed.emit(str(exc))
        else:
            self.signals.completed.emit(record)


class PrinterWorkspacePage(QWidget):
    """Saved printer context, material selection, grouped generation and history."""

    add_material_requested = Signal(str)
    settings_requested = Signal()
    printer_library_requested = Signal()
    run_finished = Signal()

    def __init__(
        self,
        library: LibraryService | None = None,
        generation: GroupedOrcaGenerationService | None = None,
        parent: QWidget | None = None,
        *,
        calibration_state_service: CalibrationStateService | None = None,
    ) -> None:
        super().__init__(parent)
        self.library = library
        self.generation = generation
        self.calibration_state_service = (
            calibration_state_service
            if calibration_state_service is not None
            else getattr(generation, "calibration_state_service", None)
        )
        self.printer_id: str | None = None
        self._workspace_runs: tuple[CalibrationRunRecord, ...] = ()
        self._ironing_state = None
        self._recommended_action: str | None = None
        self._recommended_run_id: str | None = None
        self._recommended_tab = "Inspect"
        self._cancel_event: Event | None = None
        self._task: _RunTask | None = None
        self.review_dialog: ExperimentConfigurationDialog | None = None
        layout = QVBoxLayout(self)
        layout.setContentsMargins(36, 24, 36, 28)
        layout.setSpacing(8)
        self.heading = QLabel("Printer Workspace")
        self.heading.setObjectName("pageTitle")
        self.printer_context = QLabel("No printer is selected.")
        self.printer_context.setObjectName("bodyCopy")
        self.printer_context.setWordWrap(True)
        layout.addWidget(self.heading)
        layout.addWidget(self.printer_context)

        self.tabs = QTabWidget()
        self.tabs.setObjectName("workspaceTabs")
        self.overview_tab = QWidget()
        self.experiments_tab = QWidget()
        self.printer_materials_tab = QWidget()
        self.tabs.addTab(self.overview_tab, "Overview")
        self.tabs.addTab(self.experiments_tab, "Experiments")
        self.tabs.addTab(self.printer_materials_tab, "Printer and materials")
        layout.addWidget(self.tabs, 1)

        overview_columns = QHBoxLayout(self.overview_tab)
        overview_columns.setContentsMargins(20, 18, 20, 20)
        overview_columns.setSpacing(14)

        self.overview_main_scroll = QScrollArea(self.overview_tab)
        self.overview_main_scroll.setObjectName("overviewMainScroll")
        self.overview_main_scroll.setWidgetResizable(True)
        self.overview_main_scroll.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff
        )
        self.overview_main_scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.overview_main_content = QWidget()
        self.overview_main_content.setObjectName("overviewMainContent")
        self.overview_main_scroll.setWidget(self.overview_main_content)
        overview_main = QVBoxLayout(self.overview_main_content)
        overview_main.setContentsMargins(0, 0, 0, 0)
        overview_main.setSpacing(4)
        overview_sidebar = QVBoxLayout()
        overview_sidebar.setSpacing(12)

        self.recommended_next_step_card = QFrame(self.overview_tab)
        self.recommended_next_step_card.setObjectName("recommendedNextStepCard")
        self.recommended_next_step_card.setMinimumHeight(124)
        recommendation_layout = QVBoxLayout(self.recommended_next_step_card)
        recommendation_layout.setContentsMargins(13, 8, 13, 8)
        recommendation_layout.setSpacing(4)
        self.next_step_eyebrow = QLabel("RECOMMENDED NEXT STEP")
        self.next_step_eyebrow.setObjectName("recommendedNextStepEyebrow")
        self.next_step_title = QLabel("Choose a printer to get started")
        self.next_step_title.setObjectName("recommendedNextStepTitle")
        self.next_step_title.setWordWrap(True)
        self.next_step_description = QLabel(
            "Select a saved printer and material before preparing a calibration plate."
        )
        self.next_step_description.setObjectName("recommendedNextStepBody")
        self.next_step_description.setWordWrap(True)
        recommendation_layout.addWidget(self.next_step_eyebrow)
        recommendation_layout.addWidget(self.next_step_title)
        recommendation_layout.addWidget(self.next_step_description)

        actions = QHBoxLayout()
        self.generate_button = QPushButton("Open Printer Library")
        self.generate_button.setObjectName("primaryAction")
        self.generate_button.setFixedHeight(36)
        self.generate_button.clicked.connect(self._activate_recommendation)
        self.cancel_button = QPushButton("Cancel")
        self.cancel_button.setObjectName("secondaryAction")
        self.cancel_button.setEnabled(False)
        self.cancel_button.setFixedHeight(36)
        self.cancel_button.clicked.connect(self._cancel_generation)
        actions.addWidget(self.generate_button)
        actions.addWidget(self.cancel_button)
        actions.addStretch(1)
        recommendation_layout.addLayout(actions)

        self.state = QLabel("")
        self.state.setObjectName("overviewActionFeedback")
        self.state.setWordWrap(True)
        self.state.hide()
        recommendation_layout.addWidget(self.state)
        overview_main.addWidget(self.recommended_next_step_card)

        self.lineage_card = QFrame(self.overview_tab)
        self.lineage_card.setObjectName("card")
        lineage_layout = QVBoxLayout(self.lineage_card)
        lineage_layout.setContentsMargins(13, 6, 13, 6)
        lineage_layout.setSpacing(4)
        lineage_title = QLabel("Plate lineage")
        lineage_title.setObjectName("sectionTitle")
        lineage_layout.addWidget(lineage_title)
        self.lineage_steps_widget = QWidget(self.lineage_card)
        self.lineage_layout = QHBoxLayout(self.lineage_steps_widget)
        self.lineage_layout.setContentsMargins(0, 0, 0, 0)
        self.lineage_layout.setSpacing(7)
        self.lineage_scroll = QScrollArea(self.lineage_card)
        self.lineage_scroll.setWidgetResizable(False)
        self.lineage_scroll.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAsNeeded
        )
        self.lineage_scroll.setVerticalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff
        )
        self.lineage_scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.lineage_scroll.setFixedHeight(42)
        self.lineage_scroll.setWidget(self.lineage_steps_widget)
        self.lineage_empty_state = QLabel(
            "Related plates appear here when you create a refinement or confirmation."
        )
        self.lineage_empty_state.setObjectName("mutedStatus")
        self.lineage_empty_state.setWordWrap(True)
        lineage_layout.addWidget(self.lineage_scroll)
        lineage_layout.addWidget(self.lineage_empty_state)
        self.lineage_note = QLabel("")
        self.lineage_note.setObjectName("mutedStatus")
        self.lineage_note.setWordWrap(True)
        self.lineage_note.hide()
        lineage_layout.addWidget(self.lineage_note)
        overview_main.addWidget(self.lineage_card)

        recent_header = QHBoxLayout()
        recent_title = QLabel("Recent plates")
        recent_title.setObjectName("sectionTitle")
        recent_header.addWidget(recent_title)
        recent_header.addStretch(1)
        self.all_plates_button = QPushButton("All plates")
        self.all_plates_button.setObjectName("subtleAction")
        self.all_plates_button.clicked.connect(
            lambda: self.tabs.setCurrentWidget(self.experiments_tab)
        )
        recent_header.addWidget(self.all_plates_button)
        overview_main.addLayout(recent_header)

        self.recent_plates_table = QTableWidget(0, 4)
        self.recent_plates_table.setObjectName("overviewRecentPlates")
        self.recent_plates_table.setHorizontalHeaderLabels(
            ("Plate", "Material", "Run state", "Created")
        )
        self.recent_plates_table.verticalHeader().hide()
        self.recent_plates_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.recent_plates_table.setSelectionBehavior(
            QTableWidget.SelectionBehavior.SelectRows
        )
        self.recent_plates_table.setSelectionMode(
            QTableWidget.SelectionMode.SingleSelection
        )
        self.recent_plates_table.setColumnWidth(0, 88)
        self.recent_plates_table.setColumnWidth(1, 125)
        self.recent_plates_table.setColumnWidth(2, 215)
        self.recent_plates_table.horizontalHeader().setStretchLastSection(True)
        self.recent_plates_table.setAlternatingRowColors(True)
        self.recent_plates_table.verticalHeader().setDefaultSectionSize(24)
        self.recent_plates_table.setMinimumHeight(0)
        self.recent_plates_table.setMaximumHeight(180)
        self.recent_plates_table.setAccessibleName(
            "Recent plates for the selected printer"
        )
        self.recent_plates_table.cellActivated.connect(self._open_recent_run)
        overview_main.addWidget(self.recent_plates_table)
        self.recent_plates_empty_state = QLabel(
            "No plates yet. Your generated plate history will appear here."
        )
        self.recent_plates_empty_state.setObjectName("emptyState")
        self.recent_plates_empty_state.setWordWrap(True)
        overview_main.addWidget(self.recent_plates_empty_state)
        self.calibration_status_panel = CalibrationStatusPanel(self.overview_tab)
        overview_sidebar.addWidget(self.calibration_status_panel)
        overview_sidebar.addStretch(1)
        overview_columns.addWidget(self.overview_main_scroll, 2)
        overview_columns.addLayout(overview_sidebar, 1)

        experiments_layout = QVBoxLayout(self.experiments_tab)
        experiments_layout.setContentsMargins(20, 18, 20, 20)
        experiments_layout.setSpacing(12)
        self.experiments_intro = QLabel(
            "Saved configurations and generated plates for this printer."
        )
        self.experiments_intro.setObjectName("bodyCopy")
        experiments_layout.addWidget(self.experiments_intro)

        saved_configuration_row = QHBoxLayout()
        saved_configuration_row.addWidget(QLabel("Saved ironing draft"))
        self.saved_configuration_choice = QComboBox()
        self.saved_configuration_choice.addItem("Choose a printer first", None)
        self.saved_configuration_choice.setEnabled(False)
        self.saved_configuration_choice.setAccessibleName(
            "Open a saved ironing configuration"
        )
        self.saved_configuration_choice.currentIndexChanged.connect(
            self._sync_saved_configuration_controls
        )
        saved_configuration_row.addWidget(self.saved_configuration_choice, 1)
        self.open_configuration_button = QPushButton("Open draft…")
        self.open_configuration_button.setObjectName("secondaryAction")
        self.open_configuration_button.clicked.connect(self._open_saved_configuration)
        saved_configuration_row.addWidget(self.open_configuration_button)
        experiments_layout.addLayout(saved_configuration_row)

        self.run_table = QTableWidget(0, 3)
        self.run_table.setObjectName("workspaceRunTable")
        self.run_table.setHorizontalHeaderLabels(("Plate code", "Run state", "Created"))
        self.run_table.verticalHeader().hide()
        self.run_table.setColumnWidth(0, 130)
        self.run_table.setColumnWidth(1, 300)
        self.run_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.run_table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.run_table.horizontalHeader().setStretchLastSection(True)
        self.run_table.cellDoubleClicked.connect(self._open_run_by_row)
        self.empty_runs_state = QLabel(
            "No calibration plates for this printer yet. Generate a run to start its history."
        )
        self.empty_runs_state.setObjectName("emptyState")
        self.empty_runs_state.setWordWrap(True)
        self.empty_runs_state.setSizePolicy(
            QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Maximum
        )
        self.run_table.hide()
        experiments_layout.addWidget(self.empty_runs_state)
        experiments_layout.addWidget(self.run_table, 10)
        experiments_layout.addStretch(1)

        materials_layout = QVBoxLayout(self.printer_materials_tab)
        materials_layout.setContentsMargins(20, 18, 20, 20)
        materials_layout.setSpacing(12)
        material_row = QHBoxLayout()
        material_label = QLabel("Active material")
        self.material_choice = QComboBox()
        self.material_choice.addItem("Choose a printer first", None)
        self.material_choice.setEnabled(False)
        self.material_choice.setAccessibleName("Select a saved material")
        material_label.setBuddy(self.material_choice)
        material_row.addWidget(material_label)
        material_row.addWidget(self.material_choice, 1)
        self.add_material_button = QPushButton("Add material…")
        self.add_material_button.setObjectName("secondaryAction")
        self.add_material_button.clicked.connect(self._request_add_material)
        material_row.addWidget(self.add_material_button)
        materials_layout.addLayout(material_row)
        self.material_helper = QLabel(
            "Future plates keep the selected material and its saved profile snapshot with their results."
        )
        self.material_helper.setObjectName("mutedStatus")
        self.material_helper.setWordWrap(True)
        materials_layout.addWidget(self.material_helper)
        materials_layout.addStretch(1)

        self.material_choice.currentIndexChanged.connect(self._refresh_saved_configurations)
        self.material_choice.currentIndexChanged.connect(self._refresh_calibration_state)
        if self.library is None or self.generation is None:
            self.generate_button.setEnabled(False)
            self.add_material_button.setEnabled(False)
        self._refresh_calibration_state()

    def show_printer(self, printer: Any | None) -> None:
        self.state.clear()
        self.state.hide()
        if printer is None:
            self.printer_id = None
            self.printer_context.setText("No printer is selected.")
            self.material_choice.clear()
            self.material_choice.addItem("Choose a printer first", None)
            self.material_choice.setEnabled(False)
            self.saved_configuration_choice.clear()
            self.saved_configuration_choice.addItem("Choose a printer first", None)
            self.saved_configuration_choice.setEnabled(False)
            self._sync_saved_configuration_controls()
            self.run_table.setRowCount(0)
            self._workspace_runs = ()
            self._refresh_recent_plates(())
            self._refresh_lineage(None)
            self.empty_runs_state.setText("Select a printer to see its calibration runs.")
            self.empty_runs_state.show()
            self.run_table.hide()
            self._refresh_calibration_state()
            return
        self.printer_id = printer.printer_id
        self.heading.setText(printer.name)
        if self.library is not None:
            try:
                record = self.library.repository.get_printer(printer.printer_id)
                self.printer_context.setText(
                    f"{record.model}  ·  {record.nozzle} nozzle\n"
                    f"Machine: {record.machine_profile.profile.profile.name}  ·  "
                    f"Process: {record.process_profile.profile.profile.name}"
                )
            except Exception as exc:
                self.printer_context.setText(f"Saved printer details could not be loaded: {exc}")
        else:
            self.printer_context.setText(f"{printer.name}  ·  {printer.model}  ·  {printer.nozzle} nozzle")
        self.refresh()

    def refresh(self) -> None:
        if self.library is None or self.printer_id is None:
            self._refresh_calibration_state()
            return
        materials = self.library.list_materials()
        selected = self.material_choice.currentData(Qt.ItemDataRole.UserRole)
        self.material_choice.clear()
        if not materials:
            self.material_choice.addItem("No saved materials", None)
            self.material_choice.setEnabled(False)
        else:
            self.material_choice.setEnabled(True)
            for material in materials:
                self.material_choice.addItem(
                    _material_choice_label(material),
                    material.material_id,
                )
        index = self.material_choice.findData(selected, Qt.ItemDataRole.UserRole)
        if index >= 0:
            self.material_choice.setCurrentIndex(index)
        runs = tuple(self.library.list_runs(printer_id=self.printer_id))
        self._workspace_runs = tuple(
            sorted(runs, key=lambda item: (str(item.created_at_utc), item.run_id), reverse=True)
        )
        self.run_table.setRowCount(len(runs))
        for row, record in enumerate(self._workspace_runs):
            values = (
                record.plate_code,
                _run_state_label(record),
                record.created_at_utc,
            )
            for column, value in enumerate(values):
                self.run_table.setItem(row, column, QTableWidgetItem(value))
            self.run_table.item(row, 0).setData(Qt.ItemDataRole.UserRole, record.run_id)
        self.empty_runs_state.setText(
            "No calibration plates for this printer yet. Generate a run to start its history."
        )
        self.empty_runs_state.setVisible(not runs)
        self.run_table.setVisible(bool(runs))
        self._refresh_recent_plates(self._workspace_runs)
        self._refresh_saved_configurations()
        self._refresh_calibration_state()
        if not materials:
            self.state.setText("Add a filament profile before generating a calibration run.")
            self.state.show()
        elif self.state.text() == "Add a filament profile before generating a calibration run.":
            self.state.clear()
            self.state.hide()

    def _refresh_calibration_state(self, *_args) -> None:
        material_id = self.material_choice.currentData(Qt.ItemDataRole.UserRole)
        service = self.calibration_state_service
        if service is None:
            self.calibration_status_panel.set_unavailable(
                "Calibration state service is unavailable."
            )
            self._ironing_state = None
        else:
            try:
                states = service.states_for(
                    self.printer_id,
                    str(material_id) if material_id else None,
                )
            except Exception as exc:
                self.calibration_status_panel.set_unavailable(
                    f"Calibration status could not be refreshed: {exc}"
                )
                self._ironing_state = None
            else:
                self.calibration_status_panel.set_states(states)
                self._ironing_state = next(
                    (item for item in states if item.calibration_id == "ironing"),
                    None,
                )
        self._refresh_overview_context(material_id)

    def _refresh_overview_context(self, material_id: object | None) -> None:
        matching = tuple(
            run for run in self._workspace_runs
            if material_id and run.material_id == str(material_id)
        )
        self._refresh_lineage(matching[0] if matching else None)
        self._refresh_recommended_next_step(matching)

    def _refresh_recommended_next_step(self, matching_runs) -> None:
        self._recommended_action = None
        self._recommended_run_id = None
        self._recommended_tab = "Inspect"
        self.generate_button.setEnabled(False)
        self.cancel_button.setEnabled(self._task is not None)

        if self._task is not None:
            self.next_step_title.setText("Plate generation is in progress")
            self.next_step_description.setText(
                "Calibrate-3DP is running and validating the grouped plate with OrcaSlicer."
            )
            self.generate_button.setText("Generation in progress")
            return

        if self.printer_id is None:
            self.next_step_title.setText("Choose a printer to get started")
            self.next_step_description.setText(
                "Select a saved printer and material before preparing a calibration plate."
            )
            self.generate_button.setText("Open Printer Library")
            self.generate_button.setEnabled(True)
            self._recommended_action = "printer_library"
            return

        if not self.material_choice.currentData(Qt.ItemDataRole.UserRole):
            has_materials = self.material_choice.isEnabled()
            self.next_step_title.setText(
                "Choose a material" if has_materials else "Add a material to continue"
            )
            self.next_step_description.setText(
                "Each plate keeps the selected material profile and printer context with its saved results."
            )
            self.generate_button.setText("Choose material" if has_materials else "Add material")
            self.generate_button.setEnabled(True)
            self._recommended_action = "materials" if has_materials else "add_material"
            return

        latest = matching_runs[0] if matching_runs else None
        state = self._ironing_state
        if state is not None and state.status == CalibrationStatus.STALE and latest is not None:
            self._set_run_recommendation(
                latest, "Inspect", f"Review saved context for plate {latest.plate_code}",
                "Its saved printer or material context has changed. Inspect the recorded values before continuing.",
                f"Inspect {latest.plate_code}",
            )
            return
        if latest is not None and latest.status in {"validation_failed", "generation_failed", "cancelled"}:
            self._set_run_recommendation(
                latest, "Inspect", f"Inspect plate {latest.plate_code}",
                "Review the saved validation result and details before deciding what to do next.",
                f"Inspect {latest.plate_code}",
            )
            return
        if latest is not None and latest.status == "generating":
            self._set_run_recommendation(
                latest, "Inspect", f"Check generation status for plate {latest.plate_code}",
                "This saved run is still marked as generating. Inspect its recorded status before starting another plate.",
                f"Inspect {latest.plate_code}",
            )
            return
        if latest is not None and latest.status == "settings_validated":
            revision = None
            if self.library is not None:
                try:
                    revision = self.library.repository.latest_assessment_revision(latest.run_id)
                except Exception:
                    self._set_run_recommendation(
                        latest, "Inspect", f"Review saved record for plate {latest.plate_code}",
                        "The saved assessment state could not be checked. Inspect this plate before recording another revision.",
                        f"Inspect {latest.plate_code}",
                    )
                    return
            if revision is None:
                self._set_run_recommendation(
                    latest, "Assess", f"Record results for plate {latest.plate_code}",
                    "Enter what you observed on the physical samples. Results stay attached to this saved plate.",
                    f"Assess {latest.plate_code}",
                )
            else:
                self._set_run_recommendation(
                    latest, "Decision", f"Review saved decision for plate {latest.plate_code}",
                    "Review the saved assessment and its recommendation. Acceptance and export remain explicit steps.",
                    f"Review {latest.plate_code}",
                )
            return

        if state is not None and state.status == CalibrationStatus.ACCEPTED and latest is not None:
            self._set_run_recommendation(
                latest, "Decision", f"Review calibration for plate {latest.plate_code}",
                "Review the saved decision and evidence for this calibration.",
                f"Review {latest.plate_code}",
            )
            return

        if state is not None and state.can_start and self.generation is not None:
            self.next_step_title.setText("Configure your first ironing plate")
            self.next_step_description.setText(
                "Choose the flow and speed sweep values, then review the sample layout before generation."
            )
            self.generate_button.setText("Configure plate")
            self.generate_button.setEnabled(True)
            self._recommended_action = "configure"
            return

        reasons = tuple(getattr(state, "reasons", ())) if state is not None else ()
        reason = reasons[0] if reasons else "Review the setup items in the status panel."
        if "orca" in reason.casefold() and ("available" in reason.casefold() or "executable" in reason.casefold()):
            self.next_step_title.setText("Verify OrcaSlicer to continue")
            self.next_step_description.setText(
                "The local slicer setup needs attention before a grouped plate can be generated."
            )
            self.generate_button.setText("Open Settings")
            self._recommended_action = "settings"
        else:
            self.next_step_title.setText("Resolve setup before configuring")
            self.next_step_description.setText(reason)
            self.generate_button.setText("Review printer setup")
            self._recommended_action = "materials"
        self.generate_button.setEnabled(True)

    def _set_run_recommendation(
        self, run, tab: str, title: str, description: str, action_label: str
    ) -> None:
        self._recommended_action = "open_run"
        self._recommended_run_id = run.run_id
        self._recommended_tab = tab
        self.next_step_title.setText(title)
        self.next_step_description.setText(description)
        self.generate_button.setText(action_label)
        self.generate_button.setEnabled(True)

    def _activate_recommendation(self) -> None:
        if self._recommended_action == "open_run" and self._recommended_run_id:
            self._open_run(self._recommended_run_id, initial_tab=self._recommended_tab)
        elif self._recommended_action == "configure":
            self._start_generation()
        elif self._recommended_action == "materials":
            self.tabs.setCurrentWidget(self.printer_materials_tab)
        elif self._recommended_action == "settings":
            self.settings_requested.emit()
        elif self._recommended_action == "printer_library":
            self.printer_library_requested.emit()
        elif self._recommended_action == "add_material":
            self._request_add_material()

    def _refresh_recent_plates(self, runs) -> None:
        visible = tuple(runs[:4])
        self.recent_plates_table.setRowCount(len(visible))
        names = {}
        if self.library is not None:
            try:
                names = {
                    item.material_id: item.display_name
                    for item in self.library.list_materials()
                }
            except Exception:
                names = {}
        for row, run in enumerate(visible):
            values = (
                run.plate_code,
                names.get(run.material_id, "Saved material"),
                _recent_run_state_label(run),
                _compact_timestamp(run.created_at_utc),
            )
            for column, value in enumerate(values):
                item = QTableWidgetItem(value)
                item.setToolTip(
                    _run_state_label(run) if column == 2 else str(value)
                )
                self.recent_plates_table.setItem(row, column, item)
            self.recent_plates_table.item(row, 0).setData(
                Qt.ItemDataRole.UserRole, run.run_id
            )
        self.recent_plates_table.setVisible(bool(visible))
        self.recent_plates_empty_state.setVisible(not visible)
        if visible:
            header_height = max(32, self.recent_plates_table.horizontalHeader().sizeHint().height())
            self.recent_plates_table.setFixedHeight(
                min(180, header_height + len(visible) * 24 + 4)
            )

    def _refresh_lineage(self, latest_run) -> None:
        while self.lineage_layout.count():
            item = self.lineage_layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()
        self.lineage_steps_widget.setMinimumWidth(0)
        self.lineage_steps_widget.setMinimumHeight(0)
        self.lineage_note.clear()
        self.lineage_note.hide()
        self.lineage_empty_state.setVisible(latest_run is None)
        if latest_run is None or self.library is None:
            return

        chain = []
        seen = set()
        current = latest_run
        issue = ""
        while current is not None and len(chain) < 12:
            if current.run_id in seen:
                issue = "A cycle was found in the saved lineage; showing the plates collected so far."
                break
            seen.add(current.run_id)
            link = None
            try:
                link = self.library.repository.get_run_config_link(current.run_id)
            except Exception:
                issue = "Some lineage links are unavailable; showing the plates that could be loaded."
            link = link or {}
            relation = str(link.get("relation_type") or "initial")
            chain.append((current, relation))
            parent_id = link.get("parent_run_id")
            if not parent_id:
                break
            if parent_id in seen:
                issue = "A cycle was found in the saved lineage; showing the plates collected so far."
                break
            try:
                current = self.library.repository.get_run(str(parent_id))
            except Exception:
                issue = "A linked parent plate could not be loaded; showing the remaining lineage."
                break
        else:
            if len(chain) == 12:
                issue = "Showing the 12 most recent plates in this lineage."

        for index, (run, relation) in enumerate(reversed(chain)):
            relation_label = relation.replace("_", " ").capitalize()
            if index:
                connector = QLabel("›")
                connector.setObjectName("lineageConnector")
                connector.setAccessibleName("followed by")
                self.lineage_layout.addWidget(connector)
            button = QPushButton(f"{run.plate_code}\n{relation_label}")
            button.setObjectName("plateLineageStep")
            button.setFixedHeight(40)
            button.setAccessibleName(f"Open plate {run.plate_code} in inspection")
            button.setToolTip(f"Open {run.plate_code} · {relation_label}")
            button.clicked.connect(
                lambda _checked=False, run_id=run.run_id: self._open_run(
                    run_id, initial_tab="Inspect"
                )
            )
            self.lineage_layout.addWidget(button)
        self.lineage_steps_widget.adjustSize()
        self.lineage_steps_widget.setMinimumSize(self.lineage_steps_widget.sizeHint())
        if issue:
            self.lineage_note.setText(issue)
            self.lineage_note.show()

    def _open_recent_run(self, row: int, _column: int) -> None:
        item = self.recent_plates_table.item(row, 0)
        run_id = item.data(Qt.ItemDataRole.UserRole) if item is not None else None
        self._open_run(str(run_id), initial_tab="Inspect") if run_id else None

    def _request_add_material(self) -> None:
        if self.printer_id is not None:
            self.add_material_requested.emit(self.printer_id)

    def _open_run_by_row(self, row: int, _column: int) -> None:
        item = self.run_table.item(row, 0)
        run_id = item.data(Qt.ItemDataRole.UserRole) if item is not None else None
        self._open_run(str(run_id)) if run_id else None

    def _open_run(self, run_id: str, *, initial_tab: str = "Inspect") -> None:
        if self.library is not None:
            dialog = ExperimentDetailsDialog(
                self.library,
                run_id,
                self,
                generation_service=self.generation,
                initial_tab=initial_tab,
            )
            dialog.exec()
            self.refresh()

    def _start_generation(self) -> None:
        material_id = self.material_choice.currentData(Qt.ItemDataRole.UserRole)
        if self.generation is None or self.printer_id is None or not material_id:
            self._show_action_feedback("Select a saved printer and material first.")
            return
        try:
            review = self.generation.prepare_ironing_configuration(
                self.printer_id, str(material_id)
            )
        except Exception as exc:
            self._show_action_feedback(f"Configuration review could not be prepared: {exc}")
            return
        dialog = ExperimentConfigurationDialog(review, self.generation, self)
        self.review_dialog = dialog
        if dialog.exec() != dialog.DialogCode.Accepted or dialog.configuration is None:
            self.review_dialog = None
            self.refresh()
            return
        self._start_run_generation(dialog.configuration.config_id)

    def _refresh_saved_configurations(self, *_args) -> None:
        self.saved_configuration_choice.clear()
        material_id = self.material_choice.currentData(Qt.ItemDataRole.UserRole)
        configurations = ()
        load_failed = False
        if self.library is not None and self.printer_id and material_id:
            try:
                configurations = self.library.repository.list_unlinked_initial_configurations(
                    self.printer_id, str(material_id)
                )
                for configuration in configurations:
                    flow = next(item.values for item in configuration.plan.dimensions if item.key == "ironing_flow")
                    speed = next(item.values for item in configuration.plan.dimensions if item.key == "ironing_speed")
                    self.saved_configuration_choice.addItem(
                        f"Revision {configuration.revision_no} · flow {', '.join(map(str, flow))} · speed {', '.join(map(str, speed))}",
                        configuration.config_id,
                    )
            except Exception as exc:
                load_failed = True
                self._show_action_feedback(f"Saved configuration drafts could not be loaded: {exc}")
        if not configurations:
            placeholder = (
                "Drafts unavailable"
                if load_failed
                else "No saved drafts for this material"
                if material_id
                else "Select a material first"
                if self.printer_id
                else "Choose a printer first"
            )
            self.saved_configuration_choice.addItem(placeholder, None)
            self.saved_configuration_choice.setEnabled(False)
        else:
            self.saved_configuration_choice.setEnabled(True)
        self._sync_saved_configuration_controls()

    def _sync_saved_configuration_controls(self, *_args) -> None:
        self.open_configuration_button.setEnabled(
            self.generation is not None and self.saved_configuration_choice.currentData() is not None
        )

    def _open_saved_configuration(self) -> None:
        config_id = self.saved_configuration_choice.currentData()
        if self.library is None or self.generation is None or not config_id:
            return
        try:
            configuration = self.library.repository.get_configuration(str(config_id))
            review = self.generation.configurations.review(configuration)
        except Exception as exc:
            self._show_action_feedback(f"Saved configuration could not be reopened: {exc}")
            return
        dialog = ExperimentConfigurationDialog(
            review, self.generation, self, saved_configuration=configuration
        )
        self.review_dialog = dialog
        if dialog.exec() != dialog.DialogCode.Accepted or dialog.configuration is None:
            self.review_dialog = None
            self.refresh()
            return
        self._start_run_generation(dialog.configuration.config_id)

    def _start_run_generation(self, config_id: str) -> None:
        self._cancel_event = Event()
        task = _RunTask(self.generation, config_id, self._cancel_event)
        self.review_dialog = None
        task.signals.completed.connect(self._generation_completed)
        task.signals.failed.connect(self._generation_failed)
        self._task = task
        self.cancel_button.setEnabled(True)
        self._show_action_feedback("Generating and validating the grouped plate with OrcaSlicer…")
        self._refresh_calibration_state()
        QThreadPool.globalInstance().start(task)

    def _cancel_generation(self) -> None:
        if self._cancel_event is not None:
            self._cancel_event.set()
            self._show_action_feedback("Cancellation requested; waiting for OrcaSlicer to stop.")
            self.cancel_button.setEnabled(False)

    @Slot(object)
    def _generation_completed(self, record: CalibrationRunRecord) -> None:
        self._task = None
        self._cancel_event = None
        self.cancel_button.setEnabled(False)
        self.refresh()
        if record.status == "settings_validated":
            validation = record.validation
            geometry = validation.get("geometry", {})
            geometry_state = "validated" if geometry.get("valid") else "failed"
            settings_state = "validated" if validation.get("state") == "sample_settings_validated" else "failed"
            markings = (
                "A–I labels and plate code are in the mesh."
                if geometry.get("physical_labels_in_mesh") and geometry.get("physical_plate_code_in_mesh")
                else "Printed sample labels or plate code are missing from the mesh."
            )
            orca = validation.get("orca", {})
            identity_status = orca.get("identity_status", "unresolved")
            identity = (
                "Orca identity is mapped; this executable/profile/platform is not qualified in the support matrix."
                if identity_status == "reconciled"
                else "Orca identity is unresolved; no support-matrix claim is made."
            )
            reasons = tuple(validation.get("print_readiness_reasons", ()))
            remaining = "\n".join(f"• {reason}" for reason in reasons)
            self._show_action_feedback(
                f"Plate {record.plate_code} saved.\n"
                f"Geometry: {geometry_state} ({geometry.get('backend_id', 'unknown')} {geometry.get('backend_version', '')}, "
                f"{geometry.get('object_count', 0)} objects). {markings}\n"
                f"Sample settings: {settings_state}. Print-ready: no.\n"
                f"{identity}\nRemaining print-readiness checks:\n{remaining}"
            )
        else:
            messages = record.validation.get("messages", ())
            self._show_action_feedback(f"Run {record.status}: " + (" ".join(messages) if messages else record.validation.get("state", "Unknown result")))
        self.run_finished.emit()

    @Slot(str)
    def _generation_failed(self, message: str) -> None:
        self._task = None
        self._cancel_event = None
        self.cancel_button.setEnabled(False)
        self.refresh()
        self._show_action_feedback(f"Generation failed: {message}")
        self.run_finished.emit()

    def _show_action_feedback(self, text: str) -> None:
        self.state.setText(text)
        self.state.show()


class RunHistoryPage(QWidget):
    """Searchable list and six-character plate-code lookup."""

    def __init__(
        self,
        library: LibraryService | None,
        parent: QWidget | None = None,
        *,
        generation_service=None,
    ) -> None:
        super().__init__(parent)
        self.library = library
        self.generation_service = generation_service
        self._runs: tuple[CalibrationRunRecord, ...] = ()
        layout = QVBoxLayout(self)
        layout.setContentsMargins(32, 28, 32, 28)
        layout.setSpacing(14)
        heading = QLabel("Runs / History")
        heading.setObjectName("pageTitle")
        heading.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed)
        description = QLabel(
            "Every generated plan and its settings evidence remains linked to its plate code."
        )
        description.setObjectName("bodyCopy")
        description.setWordWrap(True)
        description.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed)
        layout.addWidget(heading)
        layout.addWidget(description)
        self.lookup_controls = QFrame(self)
        self.lookup_controls.setObjectName("plateLookupControls")
        lookup = QHBoxLayout(self.lookup_controls)
        lookup.setContentsMargins(0, 0, 0, 0)
        lookup.setSpacing(8)
        self.code_entry = QLineEdit()
        self.code_entry.setMaxLength(6)
        self.code_entry.setAccessibleName("Find a saved plate by its six-character code")
        self.code_entry.setPlaceholderText("6-character plate code")
        self.lookup_button = QPushButton("Find plate")
        self.lookup_button.setAccessibleName("Find plate by code")
        self.lookup_button.setObjectName("secondaryAction")
        self.lookup_button.clicked.connect(self._lookup)
        self.code_entry.returnPressed.connect(self.lookup_button.click)
        lookup.addWidget(self.code_entry, 1)
        lookup.addWidget(self.lookup_button)
        layout.addWidget(self.lookup_controls)
        self.lookup_result = QPlainTextEdit()
        self.lookup_result.setReadOnly(True)
        self.lookup_result.setMaximumHeight(150)
        self.lookup_result.setPlaceholderText(
            "Code lookup shows the saved printer, material, sample map, and validation state."
        )
        self.lookup_result.hide()
        layout.addWidget(self.lookup_result)
        self.filter_entry = QLineEdit()
        self.filter_entry.setObjectName("historyFilter")
        self.filter_entry.setAccessibleName(
            "Filter saved runs by plate code, printer, material, or state"
        )
        self.filter_entry.setPlaceholderText(
            "Filter runs by plate code, printer, material, or state"
        )
        self.filter_entry.setClearButtonEnabled(True)
        self.filter_entry.textChanged.connect(self._apply_history_filter)
        layout.addWidget(self.filter_entry)
        self.table = QTableWidget(0, 5)
        self.table.setObjectName("historyTable")
        self.table.setHorizontalHeaderLabels(
            ("Plate code", "Printer", "Material", "Run state", "Created")
        )
        self.table.verticalHeader().hide()
        for column, width in ((0, 120), (1, 190), (2, 160), (3, 280)):
            self.table.setColumnWidth(column, width)
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.cellDoubleClicked.connect(self._open_run_by_row)
        self.empty_state = QLabel(
            "No generated plates yet. Your first saved calibration run will appear here."
        )
        self.empty_state.setObjectName("emptyState")
        self.empty_state.setWordWrap(True)
        self.empty_state.setSizePolicy(
            QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Maximum
        )
        self.table.hide()
        self.filter_entry.hide()
        layout.addWidget(self.empty_state)
        layout.addWidget(self.table, 10)
        layout.addStretch(1)
        self.refresh()

    def refresh(self) -> None:
        if self.library is None:
            self._runs = ()
            self._apply_history_filter()
            return
        runs = tuple(self.library.list_runs())
        self._runs = runs
        printers = {item.printer_id: item.display_name for item in self.library.list_printers()}
        materials = {item.material_id: item.display_name for item in self.library.list_materials()}
        self.table.setRowCount(len(runs))
        for row, run in enumerate(runs):
            values = (
                run.plate_code,
                printers.get(run.printer_id, run.printer_id),
                materials.get(run.material_id, run.material_id),
                _run_state_label(run),
                run.created_at_utc,
            )
            for column, value in enumerate(values):
                self.table.setItem(row, column, QTableWidgetItem(value))
            self.table.item(row, 0).setData(Qt.ItemDataRole.UserRole, run.run_id)
        self._apply_history_filter()

    def _apply_history_filter(self, *_args) -> None:
        query = self.filter_entry.text().strip().casefold()
        has_runs = bool(self._runs)
        self.filter_entry.setVisible(has_runs)
        visible_rows = 0
        for row in range(self.table.rowCount()):
            text = " ".join(
                self.table.item(row, column).text()
                for column in range(self.table.columnCount())
                if self.table.item(row, column) is not None
            ).casefold()
            matches = not query or query in text
            self.table.setRowHidden(row, not matches)
            visible_rows += int(matches)

        if not has_runs:
            self.empty_state.setText(
                "No generated plates yet. Your first saved calibration run will appear here."
            )
        elif not visible_rows:
            self.empty_state.setText(
                f'No plates match “{self.filter_entry.text().strip()}”. '
                f"Clear the filter to see all {len(self._runs)} saved runs."
            )
        self.empty_state.setVisible(not visible_rows)
        self.table.setVisible(bool(visible_rows))

    def _lookup(self) -> None:
        if self.library is None:
            self.lookup_result.setPlainText("The local library is unavailable.")
            self.lookup_result.show()
            return
        try:
            run = self.library.repository.get_run_by_plate_code(self.code_entry.text())
            printer = self.library.repository.get_printer(run.printer_id)
            material = self.library.repository.get_material(run.material_id)
        except Exception as exc:
            self.lookup_result.setPlainText(str(exc))
            self.lookup_result.show()
            return
        lines = [
            f"Plate {run.plate_code} · {run.status}",
            f"Printer: {printer.display_name} · {printer.model} · {printer.nozzle} nozzle",
            f"Material: {material.display_name} · {material.filament_profile.profile.profile.name}",
            f"Module: {run.plan.module_id} · samples: {len(run.sample_map)}",
            f"Sample settings validated: {run.validation.get('state') == 'sample_settings_validated'}",
            "Print-ready: no",
        ]
        for sample in run.sample_map:
            lines.append(f"  {sample['label']}: {sample['settings']}")
        reasons = tuple(run.validation.get("print_readiness_reasons", ()))
        if reasons:
            lines.append("Remaining print-readiness checks:")
            lines.extend(f"  • {reason}" for reason in reasons)
        self.lookup_result.setPlainText("\n".join(lines))
        self.lookup_result.show()
        self._open_run(run.run_id)

    def _open_run_by_row(self, row: int, _column: int) -> None:
        item = self.table.item(row, 0)
        run_id = item.data(Qt.ItemDataRole.UserRole) if item is not None else None
        self._open_run(str(run_id)) if run_id else None

    def _open_run(self, run_id: str) -> None:
        if self.library is not None:
            dialog = ExperimentDetailsDialog(
                self.library, run_id, self, generation_service=self.generation_service
            )
            dialog.exec()


class NewCalibrationPage(QWidget):
    """A clear route to the saved printer workspace where runs are created."""

    open_printer_library = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(36, 28, 36, 32)
        layout.setSpacing(18)
        heading = QLabel("Set up a calibration")
        heading.setObjectName("pageTitle")
        copy = QLabel(
            "Choose a printer and saved filament, then review the sample settings and plate before generating with OrcaSlicer."
        )
        copy.setObjectName("bodyCopy")
        copy.setWordWrap(True)
        self.current_context = QLabel("No printer selected. Choose a saved printer to begin.")
        self.current_context.setObjectName("currentPrinterContext")
        self.current_context.setWordWrap(True)

        guide = QFrame()
        guide.setObjectName("workflowGuideCard")
        guide_layout = QVBoxLayout(guide)
        guide_layout.setContentsMargins(20, 18, 20, 20)
        guide_layout.setSpacing(14)
        guide_title = QLabel("Your calibration workflow")
        guide_title.setObjectName("sectionTitle")
        guide_layout.addWidget(guide_title)
        steps = (
            (
                "1",
                "Choose a printer and material",
                "Use a saved printer and a filament profile recorded for its nozzle.",
            ),
            (
                "2",
                "Review nine A–I samples",
                "Adjust ironing flow and speed, then check the exact candidate map and connected plate.",
            ),
            (
                "3",
                "Generate and record by hand",
                "OrcaSlicer creates the local project and G-code. Print through your usual workflow, then enter observations here.",
            ),
        )
        for number, title_text, detail_text in steps:
            step_row = QWidget(guide)
            step_layout = QHBoxLayout(step_row)
            step_layout.setContentsMargins(0, 0, 0, 0)
            step_layout.setSpacing(12)
            number_label = QLabel(number)
            number_label.setObjectName("workflowStepNumber")
            number_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
            number_label.setFixedSize(30, 30)
            text_column = QVBoxLayout()
            text_column.setSpacing(3)
            title_label = QLabel(title_text)
            title_label.setObjectName("workflowStepTitle")
            detail_label = QLabel(detail_text)
            detail_label.setObjectName("mutedStatus")
            detail_label.setWordWrap(True)
            text_column.addWidget(title_label)
            text_column.addWidget(detail_label)
            step_layout.addWidget(number_label)
            step_layout.addLayout(text_column, 1)
            guide_layout.addWidget(step_row)

        self.scope_label = QLabel(
            "Available now: ironing flow × speed. Other V1 calibration workflows are not available yet."
        )
        self.scope_label.setObjectName("mutedStatus")
        self.scope_label.setWordWrap(True)
        self.open_button = QPushButton("Choose a saved printer")
        self.open_button.setObjectName("primaryAction")
        self.open_button.setMinimumWidth(220)
        self.open_button.setMinimumHeight(44)
        self.open_button.clicked.connect(self.open_printer_library.emit)
        layout.addWidget(heading)
        layout.addWidget(copy)
        layout.addWidget(self.current_context)
        layout.addWidget(guide)
        layout.addWidget(self.scope_label)
        layout.addWidget(self.open_button, 0, Qt.AlignmentFlag.AlignLeft)
        layout.addStretch(1)

    def set_printer_context(self, printer_name: str | None) -> None:
        if printer_name:
            self.current_context.setText(f"Current printer: {printer_name}")
            self.open_button.setText(f"Continue with {printer_name}")
            self.open_button.setAccessibleName(
                f"Continue calibration setup with {printer_name}"
            )
        else:
            self.current_context.setText("No printer selected. Choose a saved printer to begin.")
            self.open_button.setText("Choose a saved printer")
            self.open_button.setAccessibleName("Choose a saved printer for calibration")


class OrcaSettingsPage(QWidget):
    """Save local Orca executable and profile-root choices."""

    def __init__(
        self,
        settings: AppSettingsService,
        profiles: ProfileService,
        library: LibraryService,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.settings = settings
        self.profiles = profiles
        self.library = library
        layout = QVBoxLayout(self)
        layout.setContentsMargins(40, 36, 40, 36)
        layout.setSpacing(14)
        heading = QLabel("Settings")
        heading.setObjectName("pageTitle")
        copy = QLabel("Choose local OrcaSlicer locations. Imported profiles and generated runs remain in this workspace.")
        copy.setObjectName("bodyCopy")
        copy.setWordWrap(True)
        layout.addWidget(heading)
        layout.addWidget(copy)
        form = QGridLayout()
        form.setColumnStretch(0, 0)
        form.setColumnStretch(1, 1)
        self.executable = QLineEdit()
        self.profile_root = QLineEdit()
        browse_executable = QPushButton("Browse…")
        browse_root = QPushButton("Browse…")
        browse_executable.clicked.connect(self._browse_executable)
        browse_root.clicked.connect(self._browse_profile_root)
        form.addWidget(QLabel("OrcaSlicer executable"), 0, 0)
        form.addWidget(self.executable, 0, 1)
        form.addWidget(browse_executable, 0, 2)
        form.addWidget(QLabel("Profile root (optional)"), 1, 0)
        form.addWidget(self.profile_root, 1, 1)
        form.addWidget(browse_root, 1, 2)
        layout.addLayout(form)
        self.status = QLabel("")
        self.status.setObjectName("mutedStatus")
        self.status.setWordWrap(True)
        save = QPushButton("Save local setup")
        save.setObjectName("primaryAction")
        save.clicked.connect(self._save)
        layout.addWidget(save, 0, Qt.AlignmentFlag.AlignLeft)
        layout.addWidget(self.status)
        layout.addStretch(1)
        self.refresh()

    def refresh(self) -> None:
        configured = self.settings.settings.orca_executable
        detected = self.profiles.setup_state.executable
        self.executable.setText(str(configured or detected or ""))
        roots = self.settings.settings.orca_config_roots
        self.profile_root.setText(str(roots[0]) if roots else "")
        self.status.setText(
            f"Executable: {self.profiles.setup_state.cli_status}. "
            f"Discovered {len(self.profiles.choices('printer'))} machine/printer, "
            f"{len(self.profiles.choices('process'))} process, and "
            f"{len(self.profiles.choices('filament'))} filament profiles."
        )

    def _browse_executable(self) -> None:
        path, _filter = QFileDialog.getOpenFileName(
            self, "Choose OrcaSlicer CLI executable", str(Path.home()),
            "OrcaSlicer executable (orca-slicer.exe or orca-slicer);;Applications (*)",
        )
        if path:
            self.executable.setText(path)

    def _browse_profile_root(self) -> None:
        path = QFileDialog.getExistingDirectory(self, "Choose OrcaSlicer profile root", str(Path.home()))
        if path:
            self.profile_root.setText(path)

    def _save(self) -> None:
        try:
            executable_text = self.executable.text().strip()
            root_text = self.profile_root.text().strip()
            current = self.settings.settings
            updated = replace(
                current,
                orca_executable=Path(executable_text) if executable_text else None,
                orca_config_roots=(Path(root_text),) if root_text else None,
            )
            self.settings.save(updated)
            self.profiles.set_executable(updated.orca_executable)
            self.profiles.set_config_roots(updated.orca_config_roots)
            self.library.discover_profiles()
        except Exception as exc:
            self.status.setText(f"Setup could not be saved: {exc}")
            return
        self.refresh()
        self.status.setText(
            f"Saved. Found {len(self.profiles.choices('printer'))} machine/printer, "
            f"{len(self.profiles.choices('process'))} process, and "
            f"{len(self.profiles.choices('filament'))} filament profiles."
        )


def _run_state_label(record: CalibrationRunRecord) -> str:
    if record.status == "settings_validated":
        return "Sample settings validated · not print-ready"
    return record.status.replace("_", " ").capitalize()


def _compact_timestamp(value: str) -> str:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return str(value)
    return parsed.strftime("%d %b %H:%M")


def _recent_run_state_label(record: CalibrationRunRecord) -> str:
    if record.status == "settings_validated":
        return "Settings validated"
    return record.status.replace("_", " ").capitalize()


def _material_choice_label(material: MaterialRecord) -> str:
    display_name = material.display_name.strip()
    profile_name = material.filament_profile.profile.profile.name.strip()
    if profile_name.casefold() == display_name.casefold():
        return display_name
    return f"{display_name}  ·  {profile_name}"
