"""Qt review and write flow for a saved-run Orca process export."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtWidgets import (
    QDialog,
    QFileDialog,
    QFormLayout,
    QLabel,
    QLineEdit,
    QPlainTextEdit,
    QPushButton,
    QVBoxLayout,
)

from calibrate3dp.app.services.run_export_service import RunExportService


class RunExportReviewDialog(QDialog):
    def __init__(self, library, run_id: str, decision_id: str, parent=None) -> None:
        super().__init__(parent)
        self.service = RunExportService(library)
        self.run_id = run_id
        self.decision_id = decision_id
        self.review = None
        self.record = None
        self.setWindowTitle("Review Orca process export")
        self.resize(740, 650)
        root = QVBoxLayout(self)
        root.addWidget(QLabel("This creates a new process JSON and evidence files. The source Orca profile is not changed or activated."))
        form = QFormLayout()
        self.profile_name = QLineEdit()
        self.profile_name.setPlaceholderText("New profile name")
        form.addRow("New profile name", self.profile_name)
        root.addLayout(form)
        self.review_button = QPushButton("Build export review")
        self.review_button.clicked.connect(self._build_review)
        root.addWidget(self.review_button)
        self.summary = QPlainTextEdit()
        self.summary.setReadOnly(True)
        self.summary.setPlaceholderText("Review will show source identity, exact setting changes, evidence, and remaining readiness warnings.")
        root.addWidget(self.summary, 1)
        self.export_button = QPushButton("Choose destination and write files…")
        self.export_button.setEnabled(False)
        self.export_button.clicked.connect(self._write)
        self.status = QLabel("")
        self.status.setWordWrap(True)
        close = QPushButton("Close")
        close.clicked.connect(self.accept)
        root.addWidget(self.status)
        root.addWidget(self.export_button)
        root.addWidget(close)

    def _build_review(self) -> None:
        try:
            name = self.profile_name.text().strip()
            self.review = self.service.build_review(self.run_id, self.decision_id, new_profile_name=name)
        except Exception as exc:
            self.review = None
            self.export_button.setEnabled(False)
            self.status.setText(f"Export review is blocked: {exc}")
            return
        draft = self.review.export_draft
        lines = [
            f"Run: {draft.run_id}",
            f"Assessment revision: {draft.assessment_revision_id}",
            f"Decision: {draft.decision_id}",
            f"Source process: {draft.source_profile_name}",
            f"Source SHA-256: {draft.source_profile_sha256}",
            f"New process name: {draft.new_profile_name}",
            f"Selected candidate: {draft.selected_candidate_id}",
            f"Confirmation: {draft.confirmation_status}",
            f"Orca identity: {draft.orca_version or 'not resolved'}",
            "",
            "Exact changes:",
        ]
        lines.extend(
            f"  {key}: {values['old']}  →  {values['new']} · inherited from {draft.setting_provenance.get(key, 'saved profile')}"
            for key, values in sorted(draft.setting_changes.items())
        )
        lines.extend(("", "Supporting runs: " + ", ".join(draft.supporting_run_ids)))
        lines.extend(("", "Remaining readiness warnings:"))
        lines.extend(f"  • {warning}" for warning in draft.safety_warnings)
        lines.extend(("", f"Reviewed draft SHA-256: {self.review.draft_sha256}"))
        self.summary.setPlainText("\n".join(lines))
        self.export_button.setEnabled(True)
        self.status.setText("Review the exact values and warnings before selecting a destination.")

    def _write(self) -> None:
        if self.review is None:
            self.status.setText("Build and review the export before writing files.")
            return
        path, _filter = QFileDialog.getSaveFileName(
            self, "Save new Orca process JSON", str(Path.home() / f"{self.review.new_profile_name}.json"), "JSON process profiles (*.json)"
        )
        if not path:
            return
        try:
            self.record, result = self.service.write_reviewed(self.review, path)
        except Exception as exc:
            self.status.setText(f"Export was not written: {exc}")
            return
        self.export_button.setEnabled(False)
        self.status.setText(
            f"Saved {result.profile_path.name}, manifest and evidence report. "
            f"Profile SHA-256: {self.record.profile_sha256}. Source profile remains unchanged."
        )
