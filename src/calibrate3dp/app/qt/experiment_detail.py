"""Qt editor and inspection view for one persisted calibration run."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

from PySide6.QtCore import QObject, QRunnable, Qt, Signal, Slot, QThreadPool, QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFileDialog,
    QFormLayout,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from calibrate3dp.app.services.assessment_service import AssessmentService
from calibrate3dp.app.services.run_decision_service import RunDecisionService
from calibrate3dp.app.services.run_export_service import RunExportService
from calibrate3dp.app.qt.export_review import RunExportReviewDialog
from calibrate3dp.domain.assessment import PrintAttestation
from calibrate3dp.domain.records import CalibrationRunRecord
from calibrate3dp.experiments import CandidateAssessment, ExperimentResults
from calibrate3dp.storage.library_store import AssessmentRevisionConflictError


class _CandidateEditor(QGroupBox):
    edited = Signal()

    def __init__(self, run: CalibrationRunRecord, candidate_id: str, index: int, parent=None) -> None:
        sample_label = _sample_label(run, candidate_id, index)
        settings = run.plan.settings_for(candidate_id)
        super().__init__(f"Sample-{sample_label}  ·  {candidate_id}", parent)
        self.candidate_id = candidate_id
        self.new_photo_sources: list[str] = []
        layout = QFormLayout(self)
        self.settings = QLabel("  ·  ".join(f"{key}: {value}" for key, value in settings.items()))
        self.settings.setWordWrap(True)
        self.settings.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        layout.addRow("Exact candidate settings", self.settings)

        self.verdict = QComboBox()
        self.verdict.addItem("Unreviewed", None)
        for value in ("pass", "fail", "uncertain", "missing"):
            self.verdict.addItem(value.capitalize(), value)
        self.verdict.currentIndexChanged.connect(self._sync_rating)
        layout.addRow("Verdict", self.verdict)

        self.quality = QComboBox()
        self.quality.addItem("Not rated", None)
        for rating in range(1, 6):
            self.quality.addItem(f"{rating} / 5", rating)
        layout.addRow("Surface quality", self.quality)
        self.defects = QLineEdit()
        self.defects.setPlaceholderText("Comma-separated tags, e.g. gaps, rough edge")
        layout.addRow("Defect tags", self.defects)
        self.notes = QPlainTextEdit()
        self.notes.setPlaceholderText("Manual observations for this sample")
        self.notes.setMaximumHeight(72)
        layout.addRow("Notes", self.notes)

        photos_row = QHBoxLayout()
        self.photos = QLabel("No copied photos")
        self.photos.setWordWrap(True)
        self.add_photo_button = QPushButton("Add photo…")
        self.add_photo_button.clicked.connect(self._choose_photo)
        photos_row.addWidget(self.photos, 1)
        photos_row.addWidget(self.add_photo_button)
        layout.addRow("Evidence photos", photos_row)

    def _choose_photo(self) -> None:
        path, _filter = QFileDialog.getOpenFileName(
            self, "Add assessment photo", str(Path.home()), "Images (*.jpg *.jpeg *.png *.webp)"
        )
        if not path:
            return
        self.new_photo_sources.append(path)
        self.photos.setText(f"{len(self.new_photo_sources)} new photo(s) selected")
        self.edited.emit()

    def load_assessment(self, assessment: CandidateAssessment) -> None:
        index = self.verdict.findData(assessment.verdict)
        self.verdict.setCurrentIndex(max(index, 0))
        index = self.quality.findData(assessment.ratings.get("surface"))
        self.quality.setCurrentIndex(max(index, 0))
        self.defects.setText(", ".join(assessment.defect_tags))
        self.notes.setPlainText(assessment.notes)
        if assessment.photo_paths:
            self.photos.setText(f"{len(assessment.photo_paths)} copied photo(s) attached")
        self._sync_rating()

    def _sync_rating(self, *_args) -> None:
        if self.verdict.currentData() in {"uncertain", "missing"}:
            self.quality.setCurrentIndex(0)
            self.quality.setEnabled(False)
        else:
            self.quality.setEnabled(True)

    def build_assessment(self, previous: CandidateAssessment | None) -> CandidateAssessment:
        verdict = self.verdict.currentData()
        rating = self.quality.currentData()
        ratings = dict(previous.ratings) if previous is not None else {}
        ratings.pop("surface", None)
        if rating is not None:
            ratings["surface"] = rating
        return CandidateAssessment(
            self.candidate_id,
            ratings=ratings,
            defect_tags=tuple(dict.fromkeys(tag.strip() for tag in self.defects.text().split(",") if tag.strip())),
            notes=self.notes.toPlainText(),
            verdict=verdict,
            photo_paths=previous.photo_paths if previous is not None else (),
        )


class _FollowupSignals(QObject):
    completed = Signal(object)
    failed = Signal(str)


class _FollowupTask(QRunnable):
    def __init__(self, generation_service, config_id: str) -> None:
        super().__init__()
        self.generation_service = generation_service
        self.config_id = config_id
        self.signals = _FollowupSignals()

    @Slot()
    def run(self) -> None:
        try:
            record = self.generation_service.generate_from_configuration(self.config_id)
        except Exception as exc:
            self.signals.failed.emit(str(exc))
        else:
            self.signals.completed.emit(record)


class ExperimentDetailsDialog(QDialog):
    """Show run evidence and append a resumable manual assessment revision."""

    def __init__(self, library, run_id: str, parent: QWidget | None = None, *, generation_service=None) -> None:
        super().__init__(parent)
        self.library = library
        self.generation_service = generation_service
        self.service = AssessmentService(library.repository)
        self.decision_service = RunDecisionService(library.repository)
        self.run = library.repository.get_run(run_id)
        self.revision = self.service.load_latest(run_id)
        self.expected_revision = self.revision.revision_no if self.revision else 0
        self.editors: dict[str, _CandidateEditor] = {}
        self.decision = None
        self._assessment_dirty = False
        self._loading_assessment = False
        self._followup_task: _FollowupTask | None = None
        self.generated_child_run_id: str | None = None
        self.setWindowTitle(f"Experiment Details · {self.run.plate_code}")
        self.resize(1040, 920)
        root = QVBoxLayout(self)
        root.setSpacing(10)

        title = QLabel(f"Plate {self.run.plate_code}")
        title.setObjectName("pageTitle")
        root.addWidget(title)
        printer = library.repository.get_printer(self.run.printer_id)
        material = library.repository.get_material(self.run.material_id)
        context = QLabel(
            f"{printer.display_name} · {material.display_name} · {self.run.created_at_utc} · "
            f"Run state: {_run_state(self.run)}"
        )
        context.setWordWrap(True)
        context.setObjectName("bodyCopy")
        root.addWidget(context)
        readiness = QLabel("Sample settings and grouped layout are software checks. Print-ready: no.")
        readiness.setWordWrap(True)
        readiness.setObjectName("mutedStatus")
        root.addWidget(readiness)

        validation = QPlainTextEdit()
        validation.setReadOnly(True)
        validation.setMaximumHeight(155)
        validation.setPlainText(_validation_summary(self.run))
        root.addWidget(validation)
        artifact_row = QHBoxLayout()
        artifact_row.addWidget(QLabel("Saved artifact"))
        self.artifact_choice = QComboBox()
        self.artifact_choice.setAccessibleName("Select a verified run artifact to open")
        for artifact in self.run.artifacts:
            self.artifact_choice.addItem(artifact.relative_path, artifact.relative_path)
        artifact_row.addWidget(self.artifact_choice, 1)
        self.open_artifact_button = QPushButton("Open artifact")
        self.open_artifact_button.setEnabled(bool(self.run.artifacts))
        self.open_artifact_button.clicked.connect(self._open_selected_artifact)
        artifact_row.addWidget(self.open_artifact_button)
        root.addLayout(artifact_row)

        attestation_group = QGroupBox("Physical trial attestation")
        attestation_form = QGridLayout(attestation_group)
        self.physical_print = QComboBox()
        self.physical_print.addItem("Not recorded", None)
        self.physical_print.addItem("No physical print", False)
        self.physical_print.addItem("Physical print performed", True)
        self.physical_reviewed = QCheckBox("I reviewed the physical samples")
        self.synthetic = QCheckBox("Synthetic assessment data (software walkthrough only)")
        self.attestation_notes = QLineEdit()
        self.attestation_notes.setPlaceholderText("Optional trial context")
        self.label_legible = QCheckBox("Printed A–I labels and plate code are legible")
        self.frame_adhesion_sound = QCheckBox("Frame adhered to the plate without lifting")
        self.samples_separable = QCheckBox("Samples separate with the intended hand tool")
        self.trial_material = QLineEdit(material.display_name)
        self.trial_nozzle = QLineEdit(printer.nozzle)
        self.layer_height = QDoubleSpinBox()
        self.layer_height.setRange(0.0, 5.0)
        self.layer_height.setDecimals(3)
        self.layer_height.setSingleStep(0.04)
        self.layer_height.setSpecialValueText("Not recorded")
        self.orientation = QLineEdit("Flat on bed; sample labels facing up")
        attestation_form.addWidget(QLabel("Was the plate printed?"), 0, 0)
        attestation_form.addWidget(self.physical_print, 0, 1)
        attestation_form.addWidget(self.physical_reviewed, 0, 2)
        attestation_form.addWidget(self.label_legible, 1, 0)
        attestation_form.addWidget(self.frame_adhesion_sound, 1, 1)
        attestation_form.addWidget(self.samples_separable, 1, 2)
        attestation_form.addWidget(QLabel("Trial material"), 2, 0)
        attestation_form.addWidget(self.trial_material, 2, 1)
        attestation_form.addWidget(QLabel("Nozzle"), 2, 2)
        attestation_form.addWidget(self.trial_nozzle, 2, 3)
        attestation_form.addWidget(QLabel("Layer height (mm)"), 3, 0)
        attestation_form.addWidget(self.layer_height, 3, 1)
        attestation_form.addWidget(QLabel("Orientation"), 3, 2)
        attestation_form.addWidget(self.orientation, 3, 3)
        attestation_form.addWidget(self.synthetic, 4, 0, 1, 2)
        attestation_form.addWidget(QLabel("Trial notes"), 4, 2)
        attestation_form.addWidget(self.attestation_notes, 4, 3)
        attestation_form.setColumnStretch(1, 1)
        attestation_form.setColumnStretch(3, 1)
        root.addWidget(attestation_group)
        self.physical_print.currentIndexChanged.connect(self._sync_attestation_controls)
        self.synthetic.toggled.connect(self._sync_attestation_controls)

        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll_content = QWidget()
        self.samples_layout = QVBoxLayout(self.scroll_content)
        for index, candidate in enumerate(self.run.plan.candidates):
            editor = _CandidateEditor(self.run, candidate.candidate_id, index, self.scroll_content)
            self.editors[candidate.candidate_id] = editor
            self.samples_layout.addWidget(editor)
        self.samples_layout.addStretch(1)
        self.scroll.setWidget(self.scroll_content)
        root.addWidget(self.scroll, 1)

        decision_group = QGroupBox("Selection (manual)")
        decision_form = QFormLayout(decision_group)
        self.selected_candidate = QComboBox()
        self.selected_candidate.addItem("No selection", None)
        for candidate in self.run.plan.candidates:
            self.selected_candidate.addItem(candidate.candidate_id, candidate.candidate_id)
        self.tie_candidates = QLineEdit()
        self.tie_candidates.setPlaceholderText("Optional tied candidate IDs, comma-separated")
        self.opt_out_reason = QLineEdit()
        self.opt_out_reason.setPlaceholderText("Optional reason if proceeding without confirmation")
        decision_form.addRow("Selected candidate", self.selected_candidate)
        decision_form.addRow("Other tied candidates", self.tie_candidates)
        decision_form.addRow("Confirmation opt-out reason", self.opt_out_reason)
        root.addWidget(decision_group)

        self.decision_summary = QLabel("Save a manual assessment before asking for a recommendation.")
        self.decision_summary.setWordWrap(True)
        self.decision_summary.setObjectName("mutedStatus")
        root.addWidget(self.decision_summary)
        self.export_history = QLabel("No process exports saved for this run.")
        self.export_history.setWordWrap(True)
        self.export_history.setObjectName("mutedStatus")
        root.addWidget(self.export_history)
        self.lineage_summary = QLabel("No linked follow-up plates.")
        self.lineage_summary.setWordWrap(True)
        self.lineage_summary.setObjectName("mutedStatus")
        root.addWidget(self.lineage_summary)

        action_row = QHBoxLayout()
        self.feedback = QLabel("")
        self.feedback.setWordWrap(True)
        self.feedback.setObjectName("mutedStatus")
        self.save_button = QPushButton("Save assessment draft")
        self.save_button.setObjectName("primaryAction")
        self.save_button.clicked.connect(self._save)
        self.evaluate_button = QPushButton("Get recommendation")
        self.evaluate_button.setEnabled(self.revision is not None)
        self.evaluate_button.clicked.connect(self._evaluate_decision)
        self.refine_button = QPushButton("Create refinement run")
        self.refine_button.setEnabled(False)
        self.refine_button.clicked.connect(lambda: self._create_followup("refinement"))
        self.confirmation_button = QPushButton("Create confirmation run")
        self.confirmation_button.setEnabled(False)
        self.confirmation_button.clicked.connect(lambda: self._create_followup("confirmation"))
        self.export_button = QPushButton("Review Orca export…")
        self.export_button.setEnabled(False)
        self.export_button.clicked.connect(self._review_export)
        self.child_button = QPushButton("Open generated run details")
        self.child_button.setEnabled(False)
        self.child_button.clicked.connect(self._open_generated_child)
        close_button = QPushButton("Close")
        close_button.clicked.connect(self.reject)
        action_row.addWidget(self.save_button)
        action_row.addWidget(self.evaluate_button)
        action_row.addWidget(self.refine_button)
        action_row.addWidget(self.confirmation_button)
        action_row.addStretch(1)
        root.addLayout(action_row)
        history_row = QHBoxLayout()
        history_row.addWidget(self.export_button)
        history_row.addWidget(self.child_button)
        history_row.addWidget(self.feedback, 1)
        history_row.addWidget(close_button)
        root.addLayout(history_row)
        self._load_revision()
        self._load_saved_decision()
        self._load_export_history()
        self._load_child_runs()
        self._sync_attestation_controls()
        self._connect_dirty_signals()

    def _load_revision(self) -> None:
        self._loading_assessment = True
        if self.revision is None:
            results = self.service.empty_draft(self.run.run_id)
            self.feedback.setText("Draft only · no assessment saved yet.")
        else:
            results = self.revision.results
            attestation = self.revision.attestation
            self.physical_print.setCurrentIndex(self.physical_print.findData(attestation.physical_print_performed))
            self.physical_reviewed.setChecked(attestation.physical_review_completed)
            self.synthetic.setChecked(attestation.synthetic)
            self.attestation_notes.setText(attestation.notes)
            self.label_legible.setChecked(attestation.label_legible is True)
            self.frame_adhesion_sound.setChecked(attestation.frame_adhesion_sound is True)
            self.samples_separable.setChecked(attestation.samples_separable is True)
            self.trial_material.setText(attestation.trial_material)
            self.trial_nozzle.setText(attestation.trial_nozzle)
            self.layer_height.setValue(attestation.layer_height_mm or 0.0)
            self.orientation.setText(attestation.orientation)
            if results.selected_candidate_id:
                self.selected_candidate.setCurrentIndex(self.selected_candidate.findData(results.selected_candidate_id))
            self.tie_candidates.setText(", ".join(results.tied_candidate_ids))
            self.feedback.setText(f"Loaded immutable assessment revision {self.revision.revision_no} · {self.revision.assessment_sha256[:12]}…")
        by_candidate = {item.candidate_id: item for item in results.assessments}
        for candidate_id, editor in self.editors.items():
            editor.load_assessment(by_candidate.get(candidate_id, CandidateAssessment(candidate_id)))
        self._loading_assessment = False
        self._assessment_dirty = False

    def _connect_dirty_signals(self) -> None:
        for editor in self.editors.values():
            editor.edited.connect(self._mark_assessment_dirty)
            editor.verdict.currentIndexChanged.connect(self._mark_assessment_dirty)
            editor.quality.currentIndexChanged.connect(self._mark_assessment_dirty)
            editor.defects.textChanged.connect(self._mark_assessment_dirty)
            editor.notes.textChanged.connect(self._mark_assessment_dirty)
        self.physical_print.currentIndexChanged.connect(self._mark_assessment_dirty)
        self.physical_reviewed.toggled.connect(self._mark_assessment_dirty)
        self.synthetic.toggled.connect(self._mark_assessment_dirty)
        self.attestation_notes.textChanged.connect(self._mark_assessment_dirty)
        self.label_legible.toggled.connect(self._mark_assessment_dirty)
        self.frame_adhesion_sound.toggled.connect(self._mark_assessment_dirty)
        self.samples_separable.toggled.connect(self._mark_assessment_dirty)
        self.trial_material.textChanged.connect(self._mark_assessment_dirty)
        self.trial_nozzle.textChanged.connect(self._mark_assessment_dirty)
        self.layer_height.valueChanged.connect(self._mark_assessment_dirty)
        self.orientation.textChanged.connect(self._mark_assessment_dirty)
        self.selected_candidate.currentIndexChanged.connect(self._mark_assessment_dirty)
        self.tie_candidates.textChanged.connect(self._mark_assessment_dirty)
        self.opt_out_reason.textChanged.connect(self._mark_assessment_dirty)

    def _mark_assessment_dirty(self, *_args) -> None:
        if self._loading_assessment:
            return
        self._assessment_dirty = True
        self.evaluate_button.setEnabled(False)
        self.refine_button.setEnabled(False)
        self.confirmation_button.setEnabled(False)
        self.export_button.setEnabled(False)
        self.decision_summary.setText("Unsaved assessment edits are shown. Save them before requesting a recommendation or using its actions.")

    def _sync_attestation_controls(self, *_args) -> None:
        physical = self.physical_print.currentData()
        if self.synthetic.isChecked():
            index = self.physical_print.findData(False)
            self.physical_print.setCurrentIndex(index)
            physical = False
        self.physical_reviewed.setEnabled(physical is True and not self.synthetic.isChecked())
        for check in (self.label_legible, self.frame_adhesion_sound, self.samples_separable):
            check.setEnabled(physical is True and not self.synthetic.isChecked())
        for field in (self.trial_material, self.trial_nozzle, self.layer_height, self.orientation):
            field.setEnabled(physical is True and not self.synthetic.isChecked())
        if physical is not True:
            self.physical_reviewed.setChecked(False)

    def _save(self) -> None:
        try:
            previous_by_candidate = {
                item.candidate_id: item for item in self.revision.results.assessments
            } if self.revision else {}
            assessments = tuple(
                editor.build_assessment(previous_by_candidate.get(candidate_id))
                for candidate_id, editor in self.editors.items()
            )
            selected = self.selected_candidate.currentData()
            tied = tuple(dict.fromkeys(value.strip() for value in self.tie_candidates.text().split(",") if value.strip()))
            results = ExperimentResults(
                self.run.plan.plan_id,
                assessments,
                selected_candidate_id=selected,
                accepted=None,
                tied_candidate_ids=tied,
            )
            attestation = PrintAttestation(
                physical_print_performed=self.physical_print.currentData(),
                physical_review_completed=self.physical_reviewed.isChecked(),
                synthetic=self.synthetic.isChecked(),
                notes=self.attestation_notes.text(),
                label_legible=self.label_legible.isChecked() if self.label_legible.isEnabled() else None,
                frame_adhesion_sound=self.frame_adhesion_sound.isChecked() if self.frame_adhesion_sound.isEnabled() else None,
                samples_separable=self.samples_separable.isChecked() if self.samples_separable.isEnabled() else None,
                trial_material=self.trial_material.text(),
                trial_nozzle=self.trial_nozzle.text(),
                layer_height_mm=self.layer_height.value() or None,
                orientation=self.orientation.text(),
            )
            sources = {
                candidate_id: tuple(editor.new_photo_sources)
                for candidate_id, editor in self.editors.items()
                if editor.new_photo_sources
            }
            self.revision = self.service.save(
                self.run.run_id,
                expected_revision=self.expected_revision,
                results=results,
                attestation=attestation,
                photo_sources_by_candidate=sources,
            )
            self.expected_revision = self.revision.revision_no
            for editor in self.editors.values():
                editor.new_photo_sources.clear()
            self._load_revision()
            self.evaluate_button.setEnabled(True)
            self.decision = None
            self.refine_button.setEnabled(False)
            self.confirmation_button.setEnabled(False)
            self.export_button.setEnabled(False)
            self.decision_summary.setText("Save the updated assessment, then request a new recommendation.")
            self.feedback.setText(
                f"Saved revision {self.revision.revision_no} · assessment complete: "
                f"{_assessment_complete(self.revision.results, self.run)} · physical acceptance: "
                f"{'recorded' if self.revision.attestation.physically_accepted else 'not established'} · print-ready: no"
            )
        except AssessmentRevisionConflictError as exc:
            self.feedback.setText(f"Save conflict: {exc}. Reopen this experiment to load the latest revision.")
        except Exception as exc:
            self.feedback.setText(f"Assessment was not saved: {exc}")

    def _evaluate_decision(self) -> None:
        if self.revision is None or self._assessment_dirty:
            self.decision_summary.setText("Save the current assessment draft before requesting a recommendation.")
            return
        try:
            self.decision = self.decision_service.evaluate(
                self.run.run_id,
                self.revision.assessment_revision_id,
                confirmation_opt_out_reason=self.opt_out_reason.text().strip() or None,
            )
        except Exception as exc:
            self.decision_summary.setText(f"Recommendation could not be saved: {exc}")
            return
        explanation = "\n".join(f"• {reason}" for reason in self.decision.reasons)
        self.decision_summary.setText(
            f"Recommendation: {self.decision.action.replace('_', ' ').capitalize()}\n{explanation}\n"
            f"Assessment accepted: {'yes' if self.decision.can_accept else 'no'} · Print-ready: no"
        )
        supports_followup = self.decision.proposed_plan is not None and self.decision.action in {"refine", "extend_boundary"}
        self.refine_button.setEnabled(supports_followup)
        self.confirmation_button.setEnabled(
            supports_followup and self.decision.action == "refine"
        )
        self.export_button.setEnabled(self.decision.can_accept and self.decision.action == "accept")

    def _create_followup(self, kind: str) -> None:
        if self._assessment_dirty:
            self.decision_summary.setText("Save the current assessment before creating a follow-up.")
            return
        if self.decision is None:
            self.decision_summary.setText("Get a recommendation before creating a follow-up.")
            return
        try:
            configuration = self.decision_service.create_followup(self.decision.decision_id, kind=kind)
        except Exception as exc:
            self.decision_summary.setText(f"Follow-up configuration was not saved: {exc}")
            return
        if self.generation_service is None:
            self.decision_summary.setText(
                f"Saved immutable {kind} configuration {configuration.config_id}. "
                "A configured Orca generation service is needed to create its run."
            )
            return
        self.refine_button.setEnabled(False)
        self.confirmation_button.setEnabled(False)
        self.feedback.setText(f"Generating linked {kind} plate…")
        task = _FollowupTask(self.generation_service, configuration.config_id)
        task.signals.completed.connect(self._followup_completed)
        task.signals.failed.connect(self._followup_failed)
        self._followup_task = task
        QThreadPool.globalInstance().start(task)

    @Slot(object)
    def _followup_completed(self, run) -> None:
        self._followup_task = None
        self.generated_child_run_id = run.run_id
        self.child_button.setEnabled(True)
        self.feedback.setText(
            f"Linked plate {run.plate_code} saved as {run.status}. Print-ready: no."
        )

    @Slot(str)
    def _followup_failed(self, message: str) -> None:
        self._followup_task = None
        self.feedback.setText(f"Follow-up generation failed: {message}")

    def _open_generated_child(self) -> None:
        if self.generated_child_run_id:
            child = ExperimentDetailsDialog(
                self.library, self.generated_child_run_id, self,
                generation_service=self.generation_service,
            )
            child.exec()

    def _load_saved_decision(self) -> None:
        if self.revision is None:
            return
        decisions = self.library.repository.list_run_decisions(self.run.run_id)
        matching = [item for item in decisions if item.assessment_revision_id == self.revision.assessment_revision_id]
        if not matching:
            return
        self.decision = matching[-1]
        explanation = "\n".join(f"• {reason}" for reason in self.decision.reasons)
        self.decision_summary.setText(
            f"Saved recommendation: {self.decision.action.replace('_', ' ').capitalize()}\n{explanation}\n"
            f"Assessment accepted: {'yes' if self.decision.can_accept else 'no'} · Print-ready: no"
        )
        supports_followup = self.decision.proposed_plan is not None and self.decision.action in {"refine", "extend_boundary"}
        self.refine_button.setEnabled(supports_followup)
        self.confirmation_button.setEnabled(supports_followup and self.decision.action == "refine")
        self.export_button.setEnabled(self.decision.can_accept and self.decision.action == "accept")

    def _load_export_history(self) -> None:
        exports = self.library.repository.list_run_exports(self.run.run_id)
        if not exports:
            self.export_history.setText("No process exports saved for this run.")
            return
        service = RunExportService(self.library)
        lines = ["Saved Orca process exports:"]
        for export in exports:
            status = "verified" if service.verify_export(export) else "missing or changed"
            lines.append(
                f"• {export.new_profile_name} · {export.created_at_utc} · {status} · "
                f"sha256 {export.profile_sha256[:12]}…"
            )
        self.export_history.setText("\n".join(lines))

    def _load_child_runs(self) -> None:
        children = self.library.repository.list_child_runs(self.run.run_id)
        if not children:
            self.lineage_summary.setText("No linked follow-up plates.")
            return
        children = tuple(sorted(children, key=lambda item: (item.created_at_utc, item.run_id)))
        self.generated_child_run_id = children[-1].run_id
        self.child_button.setEnabled(True)
        lines = ["Linked follow-up plates:"]
        for child in children:
            link = self.library.repository.get_run_config_link(child.run_id) or {}
            relation = str(link.get("relation_type") or "follow-up").replace("_", " ")
            lines.append(f"• {relation.capitalize()} · {child.plate_code} · {child.status}")
        self.lineage_summary.setText("\n".join(lines))

    def _review_export(self) -> None:
        if self._assessment_dirty:
            self.decision_summary.setText("Save the current assessment before opening export review.")
            return
        if self.decision is None or not self.decision.can_accept:
            self.decision_summary.setText("Export is locked until saved evidence passes the acceptance gates.")
            return
        dialog = RunExportReviewDialog(
            self.library, self.run.run_id, self.decision.decision_id, self
        )
        dialog.exec()
        self._load_export_history()

    def _open_selected_artifact(self) -> None:
        relative_path = self.artifact_choice.currentData()
        artifact = next(
            (item for item in self.run.artifacts if item.relative_path == relative_path), None
        )
        if artifact is None:
            self.feedback.setText("Select a saved artifact first.")
            return
        try:
            path = self.library.repository.artifact_path(artifact)
        except Exception as exc:
            self.feedback.setText(f"Artifact is unavailable or changed: {exc}")
            return
        if not QDesktopServices.openUrl(QUrl.fromLocalFile(str(path))):
            self.feedback.setText(f"The system could not open {artifact.relative_path}.")
            return
        self.feedback.setText(f"Opened {artifact.relative_path}.")


def _sample_label(run: CalibrationRunRecord, candidate_id: str, index: int) -> str:
    sample = next((item for item in run.sample_map if item.get("candidate_id") == candidate_id), None)
    label = sample.get("label") if sample else None
    return label.removeprefix("Sample-") if isinstance(label, str) else "ABCDEFGHI"[index]


def _run_state(run: CalibrationRunRecord) -> str:
    if run.status == "settings_validated":
        return "sample settings validated"
    return run.status.replace("_", " ")


def _validation_summary(run: CalibrationRunRecord) -> str:
    validation = dict(run.validation)
    reasons = validation.get("print_readiness_reasons", ())
    lines = [f"Generation status: {_run_state(run)}", f"Artifact records: {len(run.artifacts)}"]
    if reasons:
        lines.append("Remaining print-readiness checks:")
        lines.extend(f"• {reason}" for reason in reasons)
    else:
        lines.append("Physical acceptance and overall print readiness have not been established.")
    preflight = validation.get("gcode_preflight")
    if isinstance(preflight, dict):
        lines.append(
            f"G-code preflight: {preflight.get('status', 'unverified')} · "
            f"linear moves checked: {preflight.get('movement_count', 0)} · "
            f"travel: {preflight.get('travel_move_count', 0)} · "
            f"extrusion: {preflight.get('extrusion_move_count', 0)}"
        )
        for label, key in (("Preflight errors", "errors"), ("Unsupported commands", "unsupported_commands"), ("Unverified checks", "unverified")):
            values = preflight.get(key, ())
            if values:
                lines.append(label + ":")
                lines.extend(f"• {item}" for item in values)
    if run.artifacts:
        lines.append("Saved artifacts:")
        lines.extend(f"• {item.relative_path} · {item.size_bytes} bytes · sha256 {item.sha256[:12]}…" for item in run.artifacts)
    return "\n".join(lines)


def _assessment_complete(results: ExperimentResults, run: CalibrationRunRecord) -> bool:
    by_id = {item.candidate_id: item for item in results.assessments}
    return all(candidate.candidate_id in by_id and by_id[candidate.candidate_id].verdict is not None for candidate in run.plan.candidates)
