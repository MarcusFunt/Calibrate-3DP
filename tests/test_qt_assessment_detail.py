from __future__ import annotations

from pathlib import Path
import hashlib
import tempfile
import unittest
from dataclasses import replace
from unittest.mock import patch

from PySide6.QtWidgets import QApplication

from calibrate3dp.app.qt.experiment_detail import ExperimentDetailsDialog
from calibrate3dp.app.services.assessment_service import AssessmentService
from calibrate3dp.app.services.experiment_service import ExperimentService
from calibrate3dp.app.services.library_service import LibraryService
from calibrate3dp.app.services.profile_service import ProfileService
from calibrate3dp.domain.assessment import PrintAttestation
from calibrate3dp.domain.records import ArtifactRecord
from calibrate3dp.experiments import CandidateAssessment, ExperimentResults
from calibrate3dp.storage.library_store import LibraryRepository
from calibrate3dp.storage.session_store import SessionRepository
from tests.test_printer_library import _records


APP = QApplication.instance() or QApplication([])


class QtExperimentDetailsTests(unittest.TestCase):
    def test_details_opens_only_a_hash_verified_saved_artifact(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            repository = LibraryRepository(SessionRepository(root))
            printer, material, run = _records(root)
            repository.add_printer(printer)
            repository.add_material(material)
            repository.create_run(run)
            artifact_path = repository.runs_root / run.run_id / "output" / "plate.gcode"
            artifact_path.parent.mkdir(parents=True, exist_ok=True)
            artifact_bytes = b"G21\nG90\n"
            artifact_path.write_bytes(artifact_bytes)
            artifact = ArtifactRecord(
                artifact_path.relative_to(repository.root).as_posix(), "text/x.gcode",
                len(artifact_bytes), hashlib.sha256(artifact_bytes).hexdigest(),
            )
            repository.finalize_run(
                run.run_id, status="settings_validated", artifacts=(artifact,),
                validation={"state": "sample_settings_validated", "print_ready": False},
            )
            dialog = ExperimentDetailsDialog(LibraryService(repository, ProfileService()), run.run_id)

            with patch("calibrate3dp.app.qt.experiment_detail.QDesktopServices.openUrl", return_value=True) as open_url:
                dialog._open_selected_artifact()

            self.assertEqual(open_url.call_count, 1)
            self.assertEqual(Path(open_url.call_args.args[0].toLocalFile()).resolve(), artifact_path.resolve())

            artifact_path.write_bytes(b"changed")
            with patch("calibrate3dp.app.qt.experiment_detail.QDesktopServices.openUrl", return_value=True) as open_url:
                dialog._open_selected_artifact()
            self.assertEqual(open_url.call_count, 0)
            self.assertIn("unavailable or changed", dialog.feedback.text())

    def test_unsaved_assessment_edits_disable_old_recommendation_actions(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            repository = LibraryRepository(SessionRepository(root))
            printer, material, run = _records(root)
            plan = ExperimentService().create_initial("ironing", run.profiles)
            run = replace(
                run,
                plan=plan,
                sample_map=tuple(
                    {
                        "label": f"Sample-{label}",
                        "candidate_id": candidate.candidate_id,
                        "settings": dict(candidate.overrides),
                    }
                    for label, candidate in zip("ABCDEFGHI", plan.candidates, strict=True)
                ),
            )
            repository.add_printer(printer)
            repository.add_material(material)
            repository.create_run(run)
            repository.finalize_run(
                run.run_id, status="settings_validated", artifacts=(),
                validation={"state": "sample_settings_validated", "print_ready": False},
            )
            library = LibraryService(repository, ProfileService())
            dialog = ExperimentDetailsDialog(library, run.run_id)

            selected = run.plan.candidates[0].candidate_id
            results = ExperimentResults(
                run.plan.plan_id,
                tuple(CandidateAssessment(item.candidate_id, verdict="pass" if item.candidate_id == selected else "fail") for item in run.plan.candidates),
                selected_candidate_id=selected,
            )
            dialog.selected_candidate.setCurrentIndex(dialog.selected_candidate.findData(selected))
            dialog.revision = dialog.service.save(
                run.run_id, expected_revision=0, results=results,
                attestation=PrintAttestation(False, False),
            )
            dialog.expected_revision = dialog.revision.revision_no
            dialog._load_revision()
            dialog.evaluate_button.setEnabled(True)
            dialog._evaluate_decision()
            self.assertTrue(dialog.refine_button.isEnabled())

            dialog.editors[selected].verdict.setCurrentIndex(
                dialog.editors[selected].verdict.findData("uncertain")
            )
            self.assertFalse(dialog.evaluate_button.isEnabled())
            self.assertFalse(dialog.refine_button.isEnabled())
            self.assertFalse(dialog.confirmation_button.isEnabled())
            self.assertFalse(dialog.export_button.isEnabled())
            self.assertIn("Save them before requesting a recommendation", dialog.decision_summary.text())

    def test_details_reopens_by_plate_code_across_repository_restart(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            repository = LibraryRepository(SessionRepository(root))
            printer, material, run = _records(root)
            repository.add_printer(printer)
            repository.add_material(material)
            repository.create_run(run)
            repository.finalize_run(run.run_id, status="settings_validated", artifacts=(), validation={"print_ready": False})

            # Construct fresh storage and service objects as an application restart would.
            repository = LibraryRepository(SessionRepository(root))
            reopened_run = repository.get_run_by_plate_code("7K3P9D")
            self.assertEqual(reopened_run.run_id, run.run_id)
            library = LibraryService(repository, ProfileService())

            dialog = ExperimentDetailsDialog(library, reopened_run.run_id)
            self.assertIn("Sample-A", dialog.editors["sample-1"].title())
            dialog.editors["sample-1"].verdict.setCurrentIndex(
                dialog.editors["sample-1"].verdict.findData("uncertain")
            )
            dialog.editors["sample-1"].notes.setPlainText("Surface varies across the coupon.")
            dialog.selected_candidate.setCurrentIndex(dialog.selected_candidate.findData("sample-1"))
            dialog._save()

            saved = AssessmentService(repository).load_latest(run.run_id)
            self.assertEqual(saved.revision_no, 1)
            self.assertEqual(saved.results.assessments[0].verdict, "uncertain")
            self.assertFalse(saved.attestation.physically_accepted)
            self.assertIn("print-ready: no", dialog.feedback.text())

            # Recreate the repository again to ensure the saved revision is on disk,
            # then resolve the same detail view from the physical plate code.
            repository = LibraryRepository(SessionRepository(root))
            reopened_run = repository.get_run_by_plate_code("7K3P9D")
            library = LibraryService(repository, ProfileService())
            reopened = ExperimentDetailsDialog(library, run.run_id)
            self.assertEqual(reopened.expected_revision, 1)
            self.assertEqual(reopened.editors["sample-1"].verdict.currentData(), "uncertain")
            self.assertEqual(reopened.editors["sample-1"].notes.toPlainText(), "Surface varies across the coupon.")


if __name__ == "__main__":
    unittest.main()
