from __future__ import annotations

from dataclasses import replace
from pathlib import Path
import tempfile
import unittest

from calibrate3dp.app.services.assessment_service import AssessmentService, AssessmentServiceError
from calibrate3dp.domain.assessment import PrintAttestation
from calibrate3dp.experiments import CandidateAssessment, ExperimentResults
from calibrate3dp.storage.library_store import AssessmentRevisionConflictError, LibraryRepository, MissingRunArtifactError
from calibrate3dp.storage.library_store import LibraryStoreError
from calibrate3dp.storage.session_store import SessionRepository
from tests.test_printer_library import _records


class AssessmentServiceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.repository = LibraryRepository(SessionRepository(self.root))
        self.printer, self.material, self.run = _records(self.root)
        self.repository.add_printer(self.printer)
        self.repository.add_material(self.material)
        self.repository.create_run(self.run)
        self.repository.finalize_run(self.run.run_id, status="settings_validated", artifacts=(), validation={"print_ready": False})
        self.service = AssessmentService(self.repository)

    def tearDown(self):
        self.temp.cleanup()

    def test_incomplete_unprinted_draft_persists_as_immutable_revision(self):
        draft = self.service.empty_draft(self.run.run_id)
        saved = self.service.save(
            self.run.run_id,
            expected_revision=0,
            results=draft,
            attestation=PrintAttestation(None, False),
        )
        reopened = self.service.load(saved.assessment_revision_id)
        self.assertEqual(reopened.results, draft)
        self.assertEqual(reopened.revision_no, 1)
        self.assertFalse(reopened.attestation.physically_accepted)
        self.assertEqual(self.repository.latest_assessment_revision(self.run.run_id), saved)

    def test_physical_acceptance_requires_print_review_checklist_and_trial_context(self):
        partial = PrintAttestation(False, False)
        synthetic = PrintAttestation(
            False, False, synthetic=True, label_legible=True, frame_adhesion_sound=True,
            samples_separable=True, trial_material="PLA", trial_nozzle="0.4 mm",
            layer_height_mm=0.2, orientation="Flat; labels up",
        )
        self.assertFalse(partial.physically_accepted)
        self.assertFalse(synthetic.physically_accepted)

    def test_copied_photo_is_hash_checked_and_survives_source_removal(self):
        source = self.root / "surface.png"
        source.write_bytes(b"\x89PNG\r\n\x1a\nassessment photo bytes")
        draft = self.service.empty_draft(self.run.run_id)
        assessment = replace(draft.assessments[0], verdict="uncertain", notes="Edges are hard to judge.")
        draft = replace(draft, assessments=(assessment,))
        saved = self.service.save(
            self.run.run_id,
            expected_revision=0,
            results=draft,
            attestation=PrintAttestation(False, False, synthetic=True, notes="Synthetic workflow check."),
            photo_sources_by_candidate={"sample-1": (source,)},
        )
        source.unlink()
        reloaded = self.service.load(saved.assessment_revision_id)
        photo = reloaded.results.assessments[0].photo_paths[0]
        photo_path = self.service.photo_path(reloaded, photo)
        self.assertTrue(photo_path.is_file())
        self.assertEqual(reloaded.results.assessments[0].verdict, "uncertain")
        self.assertEqual(reloaded.photo_artifacts[0].sha256, saved.photo_artifacts[0].sha256)

        photo_path.write_bytes(b"changed")
        with self.assertRaises(MissingRunArtifactError):
            self.service.load(saved.assessment_revision_id)

    def test_optimistic_concurrency_rejects_stale_editor_and_wrong_plan(self):
        draft = self.service.empty_draft(self.run.run_id)
        self.service.save(self.run.run_id, expected_revision=0, results=draft, attestation=PrintAttestation(False, False))
        with self.assertRaises(AssessmentRevisionConflictError):
            self.service.save(self.run.run_id, expected_revision=0, results=draft, attestation=PrintAttestation(False, False))
        with self.assertRaises(AssessmentServiceError):
            self.service.save(self.run.run_id, expected_revision=1, results=ExperimentResults("other-plan", ()), attestation=PrintAttestation(False, False))

    def test_photo_extension_and_content_must_agree(self):
        source = self.root / "not-really.png"
        source.write_bytes(b"plain text")
        with self.assertRaises(AssessmentServiceError):
            self.service.save(
                self.run.run_id,
                expected_revision=0,
                results=self.service.empty_draft(self.run.run_id),
                attestation=PrintAttestation(False, False),
                photo_sources_by_candidate={"sample-1": (source,)},
            )

    def test_run_id_rejects_path_traversal_before_photo_storage(self):
        with self.assertRaisesRegex(ValueError, "safe path component"):
            replace(self.run, run_id="../../outside-run")

    def test_photo_save_rejects_symlinked_evidence_directory_before_copy(self):
        source = self.root / "source.png"
        source.write_bytes(b"\x89PNG\r\n\x1a\nphoto")
        run_root = self.repository.runs_root / self.run.run_id
        assessments = run_root / "evidence" / "assessments"
        assessments.parent.mkdir(parents=True, exist_ok=True)
        outside = self.root / "outside-evidence"
        outside.mkdir()
        try:
            assessments.symlink_to(outside, target_is_directory=True)
        except OSError as exc:
            self.skipTest(f"symbolic links are unavailable in this environment: {exc}")

        with self.assertRaises(LibraryStoreError):
            self.service.save(
                self.run.run_id, expected_revision=0,
                results=self.service.empty_draft(self.run.run_id),
                attestation=PrintAttestation(False, False, synthetic=True),
                photo_sources_by_candidate={"sample-1": (source,)},
            )
        self.assertEqual(list(outside.iterdir()), [])

    def test_reopening_rejects_a_photo_symlink_escape(self):
        source = self.root / "photo.png"
        source.write_bytes(b"\x89PNG\r\n\x1a\nphoto")
        saved = self.service.save(
            self.run.run_id,
            expected_revision=0,
            results=self.service.empty_draft(self.run.run_id),
            attestation=PrintAttestation(False, False, synthetic=True),
            photo_sources_by_candidate={"sample-1": (source,)},
        )
        artifact = saved.photo_artifacts[0]
        stored = self.root.joinpath(*artifact.relative_path.split("/"))
        external = self.root / "outside.png"
        external.write_bytes(source.read_bytes())
        stored.unlink()
        try:
            stored.symlink_to(external)
        except OSError as exc:
            self.skipTest(f"symbolic links are unavailable in this environment: {exc}")
        with self.assertRaises(LibraryStoreError):
            self.service.load(saved.assessment_revision_id)


if __name__ == "__main__":
    unittest.main()
