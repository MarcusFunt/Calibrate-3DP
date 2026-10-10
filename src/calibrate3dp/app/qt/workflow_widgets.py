"""Qt dialogs and pages for the saved-library and grouped ironing workflow."""

from __future__ import annotations

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
    QTableWidget,
    QTableWidgetItem,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from calibrate3dp.app.services.grouped_orca_service import GroupedOrcaGenerationService
from calibrate3dp.app.services.calibration_state_service import CalibrationStateService
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

        overview_layout = QVBoxLayout(self.overview_tab)
        overview_layout.setContentsMargins(20, 18, 20, 20)
        overview_layout.setSpacing(12)
        self.calibration_status_panel = CalibrationStatusPanel(self.overview_tab)
        overview_layout.addWidget(self.calibration_status_panel)

        actions = QHBoxLayout()
        self.generate_button = QPushButton("Configure grouped ironing sweep")
        self.generate_button.setObjectName("primaryAction")
        self.generate_button.clicked.connect(self._start_generation)
        self.cancel_button = QPushButton("Cancel")
        self.cancel_button.setObjectName("secondaryAction")
        self.cancel_button.setEnabled(False)
        self.cancel_button.clicked.connect(self._cancel_generation)
        actions.addWidget(self.generate_button)
        actions.addWidget(self.cancel_button)
        actions.addStretch(1)
        overview_layout.addLayout(actions)

        self.state = QLabel("Select or add a material to prepare an ironing comparison.")
        self.state.setObjectName("mutedStatus")
        self.state.setWordWrap(True)
        overview_layout.addWidget(self.state)
        overview_layout.addStretch(1)

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
        self.saved_configuration_choice.setAccessibleName("Open a saved ironing configuration")
        self.saved_configuration_choice.currentIndexChanged.connect(self._sync_saved_configuration_controls)
        saved_configuration_row.addWidget(self.saved_configuration_choice, 1)
        self.open_configuration_button = QPushButton("Open draft…")
        self.open_configuration_button.setObjectName("secondaryAction")
        self.open_configuration_button.clicked.connect(self._open_saved_configuration)
        saved_configuration_row.addWidget(self.open_configuration_button)
        experiments_layout.addLayout(saved_configuration_row)

        self.run_table = QTableWidget(0, 3)
        self.run_table.setHorizontalHeaderLabels(("Plate code", "Run state", "Created"))
        self.run_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.run_table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.run_table.horizontalHeader().setStretchLastSection(True)
        self.run_table.cellDoubleClicked.connect(self._open_run_by_row)
        experiments_layout.addWidget(self.run_table, 1)

        materials_layout = QVBoxLayout(self.printer_materials_tab)
        materials_layout.setContentsMargins(20, 18, 20, 20)
        materials_layout.setSpacing(12)
        material_row = QHBoxLayout()
        material_label = QLabel("Active material")
        self.material_choice = QComboBox()
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
        if printer is None:
            self.printer_id = None
            self.printer_context.setText("No printer is selected.")
            self.material_choice.clear()
            self.run_table.setRowCount(0)
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
        for material in materials:
            self.material_choice.addItem(
                f"{material.display_name}  ·  {material.filament_profile.profile.profile.name}",
                material.material_id,
            )
        index = self.material_choice.findData(selected, Qt.ItemDataRole.UserRole)
        if index >= 0:
            self.material_choice.setCurrentIndex(index)
        runs = self.library.list_runs(printer_id=self.printer_id)
        self.run_table.setRowCount(len(runs))
        for row, record in enumerate(runs):
            values = (
                record.plate_code,
                _run_state_label(record),
                record.created_at_utc,
            )
            for column, value in enumerate(values):
                self.run_table.setItem(row, column, QTableWidgetItem(value))
            self.run_table.item(row, 0).setData(Qt.ItemDataRole.UserRole, record.run_id)
        self._refresh_saved_configurations()
        self._refresh_calibration_state()
        if not materials:
            self.state.setText("Add a filament profile before generating a calibration run.")

    def _refresh_calibration_state(self, *_args) -> None:
        material_id = self.material_choice.currentData(Qt.ItemDataRole.UserRole)
        service = self.calibration_state_service
        if service is None:
            self.calibration_status_panel.set_unavailable(
                "Calibration state service is unavailable."
            )
            self.generate_button.setEnabled(False)
            return
        try:
            states = service.states_for(
                self.printer_id,
                str(material_id) if material_id else None,
            )
        except Exception as exc:
            self.calibration_status_panel.set_unavailable(
                f"Calibration status could not be refreshed: {exc}"
            )
            self.generate_button.setEnabled(False)
            return
        self.calibration_status_panel.set_states(states)
        ironing = next(
            (item for item in states if item.calibration_id == "ironing"),
            None,
        )
        self.generate_button.setEnabled(
            self.generation is not None
            and self._task is None
            and bool(material_id)
            and ironing is not None
            and ironing.can_start
        )

    def _request_add_material(self) -> None:
        if self.printer_id is not None:
            self.add_material_requested.emit(self.printer_id)

    def _open_run_by_row(self, row: int, _column: int) -> None:
        item = self.run_table.item(row, 0)
        run_id = item.data(Qt.ItemDataRole.UserRole) if item is not None else None
        self._open_run(str(run_id)) if run_id else None

    def _open_run(self, run_id: str) -> None:
        if self.library is not None:
            dialog = ExperimentDetailsDialog(
                self.library, run_id, self, generation_service=self.generation
            )
            dialog.exec()
            self.refresh()

    def _start_generation(self) -> None:
        material_id = self.material_choice.currentData(Qt.ItemDataRole.UserRole)
        if self.generation is None or self.printer_id is None or not material_id:
            self.state.setText("Select a saved printer and material first.")
            return
        try:
            review = self.generation.prepare_ironing_configuration(
                self.printer_id, str(material_id)
            )
        except Exception as exc:
            self.state.setText(f"Configuration review could not be prepared: {exc}")
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
                self.state.setText(f"Saved configuration drafts could not be loaded: {exc}")
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
            self.state.setText(f"Saved configuration could not be reopened: {exc}")
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
        self.state.setText("Generating and validating the grouped plate with OrcaSlicer…")
        self._refresh_calibration_state()
        QThreadPool.globalInstance().start(task)

    def _cancel_generation(self) -> None:
        if self._cancel_event is not None:
            self._cancel_event.set()
            self.state.setText("Cancellation requested; waiting for OrcaSlicer to stop.")
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
            self.state.setText(
                f"Plate {record.plate_code} saved.\n"
                f"Geometry: {geometry_state} ({geometry.get('backend_id', 'unknown')} {geometry.get('backend_version', '')}, "
                f"{geometry.get('object_count', 0)} objects). {markings}\n"
                f"Sample settings: {settings_state}. Print-ready: no.\n"
                f"{identity}\nRemaining print-readiness checks:\n{remaining}"
            )
        else:
            messages = record.validation.get("messages", ())
            self.state.setText(f"Run {record.status}: " + (" ".join(messages) if messages else record.validation.get("state", "Unknown result")))
        self.run_finished.emit()

    @Slot(str)
    def _generation_failed(self, message: str) -> None:
        self._task = None
        self._cancel_event = None
        self.cancel_button.setEnabled(False)
        self.refresh()
        self.state.setText(f"Generation failed: {message}")
        self.run_finished.emit()


class RunHistoryPage(QWidget):
    """Searchable list and six-character plate-code lookup."""

    def __init__(self, library: LibraryService | None, parent: QWidget | None = None, *, generation_service=None) -> None:
        super().__init__(parent)
        self.library = library
        self.generation_service = generation_service
        layout = QVBoxLayout(self)
        layout.setContentsMargins(40, 36, 40, 36)
        layout.setSpacing(14)
        heading = QLabel("Runs / History")
        heading.setObjectName("pageTitle")
        description = QLabel("Every generated plan and its settings evidence remains linked to its plate code.")
        description.setObjectName("bodyCopy")
        description.setWordWrap(True)
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
        self.lookup_result.setPlaceholderText("Code lookup shows the saved printer, material, sample map, and validation state.")
        layout.addWidget(self.lookup_result)
        self.table = QTableWidget(0, 5)
        self.table.setHorizontalHeaderLabels(("Plate code", "Printer", "Material", "Run state", "Created"))
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.cellDoubleClicked.connect(self._open_run_by_row)
        layout.addWidget(self.table, 1)
        self.refresh()

    def refresh(self) -> None:
        if self.library is None:
            return
        runs = self.library.list_runs()
        printers = {item.printer_id: item.display_name for item in self.library.list_printers()}
        materials = {item.material_id: item.display_name for item in self.library.list_materials()}
        self.table.setRowCount(len(runs))
        for row, run in enumerate(runs):
            values = (run.plate_code, printers.get(run.printer_id, run.printer_id), materials.get(run.material_id, run.material_id), _run_state_label(run), run.created_at_utc)
            for column, value in enumerate(values):
                self.table.setItem(row, column, QTableWidgetItem(value))
            self.table.item(row, 0).setData(Qt.ItemDataRole.UserRole, run.run_id)

    def _lookup(self) -> None:
        if self.library is None:
            self.lookup_result.setPlainText("The local library is unavailable.")
            return
        try:
            run = self.library.repository.get_run_by_plate_code(self.code_entry.text())
            printer = self.library.repository.get_printer(run.printer_id)
            material = self.library.repository.get_material(run.material_id)
        except Exception as exc:
            self.lookup_result.setPlainText(str(exc))
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
        layout.setContentsMargins(40, 36, 40, 36)
        layout.setSpacing(14)
        heading = QLabel("New Calibration")
        heading.setObjectName("pageTitle")
        copy = QLabel(
            "The current end-to-end experiment is an ironing flow and speed sweep. Open a saved printer workspace, choose a matching material, edit the A–I candidate values, review the connected plate, then generate with OrcaSlicer."
        )
        copy.setObjectName("bodyCopy")
        copy.setWordWrap(True)
        self.open_button = QPushButton("Choose a saved printer")
        self.open_button.setObjectName("primaryAction")
        self.open_button.clicked.connect(self.open_printer_library.emit)
        layout.addWidget(heading)
        layout.addWidget(copy)
        layout.addWidget(self.open_button, 0, Qt.AlignmentFlag.AlignLeft)
        layout.addStretch(1)


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
