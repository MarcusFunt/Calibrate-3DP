from __future__ import annotations

from dataclasses import replace
import hashlib
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from calibrate3dp.app.models import ProfileSelection
from calibrate3dp.app.services.assessment_service import AssessmentService
from calibrate3dp.app.services.experiment_service import ExperimentService
from calibrate3dp.app.services.run_decision_service import RunDecisionService
from calibrate3dp.app.services.run_export_service import RunExportService, RunExportServiceError
from calibrate3dp.app.services.run_export_service import RunExportDraft, StaleExportDraftError, _draft_hash
from calibrate3dp.app.services.export_service import ExportDraft
from calibrate3dp.app.services.library_service import LibraryService
from calibrate3dp.app.services.profile_service import ProfileService
from calibrate3dp.domain.assessment import PrintAttestation
from calibrate3dp.domain.experiment_config import SavedExperimentConfiguration, default_layout_options
from calibrate3dp.domain.records import ArtifactRecord, CalibrationRunRecord
from calibrate3dp.experiments import CandidateAssessment, ExperimentResults
from calibrate3dp.storage.library_store import LibraryRepository
from calibrate3dp.storage.session_store import SessionRepository
from tests.test_printer_library import _records


def _make_grouped_workspace(root: Path):
    repository = LibraryRepository(SessionRepository(root))
    printer, material, _ = _records(root)
    repository.add_printer(printer)
    repository.add_material(material)
    selection = ProfileSelection(
        printer=printer.machine_profile.profile,
        filament=material.filament_profile.profile,
        process=printer.process_profile.profile,
        source_paths={"printer": "printer.json", "process": "process.json", "filament": "filament.json"},
        source_hashes={"printer": "a" * 64, "process": "b" * 64, "filament": "c" * 64},
    )
    plan = ExperimentService().create_initial("ironing", selection)
    config = SavedExperimentConfiguration(
        "config-initial", "experiment-initial", 1, printer.printer_id, material.material_id,
        selection, plan, default_layout_options(), "2026-10-10T12:00:00Z",
    )
    repository.save_configuration(config)
    run = _run_record("run-initial", "7K3P9D", printer.printer_id, material.material_id, plan, selection)
    repository.create_run(run, configuration_id=config.config_id)
    manifest = repository.runs_root / run.run_id / "manifest.json"
    manifest.parent.mkdir(parents=True, exist_ok=True)
    manifest_bytes = b"{\"fixture\": true}\n"
    manifest.write_bytes(manifest_bytes)
    artifact = ArtifactRecord(
        manifest.relative_to(repository.root).as_posix(), "application/json",
        len(manifest_bytes), hashlib.sha256(manifest_bytes).hexdigest(),
    )
    repository.finalize_run(
        run.run_id, status="settings_validated", artifacts=(artifact,),
        validation={
            "state": "sample_settings_validated", "print_ready": False,
            "gcode_preflight": {"passed": True, "parser_coverage_complete": True, "status": "subset_checked"},
        },
    )
    candidate_ids = [item.candidate_id for item in plan.candidates]
    assessments = tuple(CandidateAssessment(item, verdict="pass" if item == candidate_ids[4] else "fail") for item in candidate_ids)
    results = ExperimentResults(plan.plan_id, assessments, selected_candidate_id=candidate_ids[4])
    assessment = AssessmentService(repository).save(
        run.run_id, expected_revision=0, results=results,
        attestation=PrintAttestation(False, False, synthetic=True, notes="Synthetic test fixture; no physical print was performed."),
    )
    return repository, printer, material, selection, config, run, assessment


def _run_record(run_id, plate_code, printer_id, material_id, plan, selection):
    sample_map = tuple({
        "label": f"Sample-{label}", "candidate_id": candidate.candidate_id,
        "settings": dict(candidate.overrides),
    } for label, candidate in zip("ABCDEFGHI", plan.candidates))
    return CalibrationRunRecord(
        run_id, plate_code, printer_id, material_id, "generating", "2026-10-10T12:01:00Z",
        plan, selection, sample_map, {"state": "pending", "print_ready": False},
    )


class RunDecisionServiceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.repository, self.printer, self.material, self.selection, self.config, self.run, self.assessment = _make_grouped_workspace(self.root)
        self.service = RunDecisionService(self.repository)

    def tearDown(self):
        self.temp.cleanup()

    def test_synthetic_recommendation_is_exploratory_and_cannot_accept(self):
        revision = self.repository.latest_assessment_revision(self.run.run_id)
        synthetic = AssessmentService(self.repository).save(
            self.run.run_id, expected_revision=1, results=revision.results,
            attestation=PrintAttestation(False, False, synthetic=True),
        )
        decision = self.service.evaluate(self.run.run_id, synthetic.assessment_revision_id)
        self.assertEqual(decision.action, "refine")
        self.assertFalse(decision.can_accept)
        self.assertTrue(any("cannot unlock acceptance or export" in reason for reason in decision.reasons))

    def test_code_lookup_assessment_and_followup_configuration_survive_repository_restart(self):
        # A new repository/service graph models the application closing and reopening.
        repository = LibraryRepository(SessionRepository(self.root))
        restored_run = repository.get_run_by_plate_code("7K3P9D")
        self.assertEqual(restored_run.run_id, self.run.run_id)
        self.assertEqual(repository.get_configuration_for_run(restored_run.run_id), self.config)

        assessment = AssessmentService(repository).load_latest(restored_run.run_id)
        self.assertEqual(assessment.assessment_revision_id, self.assessment.assessment_revision_id)
        self.assertEqual(assessment.results.selected_candidate_id, self.assessment.results.selected_candidate_id)
        decision = RunDecisionService(repository).evaluate(restored_run.run_id, assessment.assessment_revision_id)
        self.assertFalse(decision.can_accept)
        self.assertEqual(decision.assessment_revision_id, assessment.assessment_revision_id)

        followup = RunDecisionService(LibraryRepository(SessionRepository(self.root))).create_followup(
            decision.decision_id, kind="refinement"
        )
        restarted = LibraryRepository(SessionRepository(self.root))
        loaded_followup = restarted.get_configuration(followup.config_id)
        loaded_parent = restarted.get_run_by_plate_code("7K3P9D")
        self.assertEqual(loaded_followup.to_dict(), followup.to_dict())
        self.assertEqual(loaded_followup.parent_run_id, loaded_parent.run_id)
        self.assertEqual(loaded_followup.parent_assessment_revision_id, assessment.assessment_revision_id)
        self.assertEqual(restarted.get_run(loaded_parent.run_id).plan.to_dict(), self.run.plan.to_dict())

    def test_followup_is_linked_and_synthetic_confirmation_cannot_unlock_acceptance(self):
        decision = self.service.evaluate(self.run.run_id, self.assessment.assessment_revision_id)
        self.assertEqual(decision.action, "refine")
        self.assertFalse(decision.can_accept)
        confirmation_config = self.service.create_followup(decision.decision_id, kind="confirmation")
        self.assertEqual(confirmation_config.parent_run_id, self.run.run_id)
        self.assertEqual(confirmation_config.parent_assessment_revision_id, self.assessment.assessment_revision_id)
        self.assertEqual(confirmation_config.parent_candidate_id, self.assessment.results.selected_candidate_id)
        self.assertEqual(len(confirmation_config.plan.candidates), 1)
        self.assertEqual(confirmation_config.relation_type, "confirmation")

        candidate = confirmation_config.plan.candidates[0]
        sample_map = ({"label": "Sample-A", "candidate_id": candidate.candidate_id, "settings": dict(candidate.overrides)},)
        child = CalibrationRunRecord(
            "run-confirmation", "8K3P9D", self.printer.printer_id, self.material.material_id,
            "generating", "2026-10-10T12:02:00Z", confirmation_config.plan, self.selection,
            sample_map, {"state": "pending", "print_ready": False},
        )
        self.repository.create_run(child, configuration_id=confirmation_config.config_id)
        self.repository.finalize_run(child.run_id, status="settings_validated", artifacts=(), validation={"state": "sample_settings_validated", "print_ready": False})
        confirmation_results = ExperimentResults(
            confirmation_config.plan.plan_id,
            (CandidateAssessment(candidate.candidate_id, verdict="pass"),),
            selected_candidate_id=candidate.candidate_id,
        )
        AssessmentService(self.repository).save(
            child.run_id, expected_revision=0, results=confirmation_results,
            attestation=PrintAttestation(False, False, synthetic=True, notes="Synthetic test fixture; no physical print was performed."),
        )
        still_blocked = self.service.evaluate(self.run.run_id, self.assessment.assessment_revision_id)
        self.assertFalse(still_blocked.can_accept)
        self.assertTrue(any("Physical print and review" in reason for reason in still_blocked.reasons))
        link = self.repository.get_run_config_link(child.run_id)
        self.assertEqual(link["parent_assessment_revision_id"], self.assessment.assessment_revision_id)

    def test_failed_or_incomplete_gcode_preflight_cannot_unlock_confirmation(self):
        # These typed attestations are isolated policy fixtures; no physical print is claimed.
        reviewed_trial = PrintAttestation(
            True, True, label_legible=True, frame_adhesion_sound=True,
            samples_separable=True, trial_material="PLA", trial_nozzle="0.4 mm",
            layer_height_mm=0.2, orientation="Flat on bed; labels up",
        )
        parent_assessment = AssessmentService(self.repository).save(
            self.run.run_id, expected_revision=1, results=self.assessment.results,
            attestation=reviewed_trial,
        )
        decision = self.service.evaluate(self.run.run_id, parent_assessment.assessment_revision_id)
        self.assertTrue(decision.confirmation_required)
        confirmation_config = self.service.create_followup(decision.decision_id, kind="confirmation")
        candidate = confirmation_config.plan.candidates[0]
        child = CalibrationRunRecord(
            "run-failed-preflight", "8K4P9D", self.printer.printer_id, self.material.material_id,
            "generating", "2026-10-10T12:02:00Z", confirmation_config.plan, self.selection,
            ({"label": "Sample-A", "candidate_id": candidate.candidate_id, "settings": dict(candidate.overrides)},),
            {"state": "pending", "print_ready": False},
        )
        self.repository.create_run(child, configuration_id=confirmation_config.config_id)
        child_artifact_path = self.repository.runs_root / child.run_id / "output" / "plate.gcode"
        child_artifact_path.parent.mkdir(parents=True, exist_ok=True)
        child_gcode = b"G21\nG90\n"
        child_artifact_path.write_bytes(child_gcode)
        child_artifact = ArtifactRecord(
            child_artifact_path.relative_to(self.repository.root).as_posix(), "text/x.gcode",
            len(child_gcode), hashlib.sha256(child_gcode).hexdigest(),
        )
        self.repository.finalize_run(
            child.run_id, status="settings_validated", artifacts=(child_artifact,),
            validation={
                "state": "sample_settings_validated", "print_ready": False,
                "gcode_preflight": {
                    "passed": False, "parser_coverage_complete": False,
                    "status": "failed", "errors": ["Nozzle target is outside saved limits."],
                    "unverified": ["Machine command is unsupported."],
                    "unsupported_commands": ["G28"],
                },
            },
        )
        child_results = ExperimentResults(
            confirmation_config.plan.plan_id,
            (CandidateAssessment(candidate.candidate_id, verdict="pass"),),
            selected_candidate_id=candidate.candidate_id,
        )
        AssessmentService(self.repository).save(
            child.run_id, expected_revision=0, results=child_results, attestation=reviewed_trial,
        )

        reevaluated = self.service.evaluate(self.run.run_id, parent_assessment.assessment_revision_id)
        self.assertFalse(reevaluated.can_accept)
        self.assertTrue(reevaluated.confirmation_required)

    def test_accepted_decision_persists_exact_confirmation_assessment_revision(self):
        reviewed_trial = PrintAttestation(
            True, True, label_legible=True, frame_adhesion_sound=True,
            samples_separable=True, trial_material="PLA", trial_nozzle="0.4 mm",
            layer_height_mm=0.2, orientation="Flat on bed; labels up",
        )
        parent_assessment = AssessmentService(self.repository).save(
            self.run.run_id, expected_revision=1, results=self.assessment.results,
            attestation=reviewed_trial,
        )
        decision = self.service.evaluate(self.run.run_id, parent_assessment.assessment_revision_id)
        confirmation_config = self.service.create_followup(decision.decision_id, kind="confirmation")
        candidate = confirmation_config.plan.candidates[0]
        child = CalibrationRunRecord(
            "run-confirmation-pass", "8H4P9D", self.printer.printer_id, self.material.material_id,
            "generating", "2026-10-10T12:02:00Z", confirmation_config.plan, self.selection,
            ({"label": "Sample-A", "candidate_id": candidate.candidate_id, "settings": dict(candidate.overrides)},),
            {"state": "pending", "print_ready": False},
        )
        self.repository.create_run(child, configuration_id=confirmation_config.config_id)
        gcode_path = self.repository.runs_root / child.run_id / "output" / "plate.gcode"
        gcode_path.parent.mkdir(parents=True, exist_ok=True)
        gcode = b"G21\nG90\n"
        gcode_path.write_bytes(gcode)
        artifact = ArtifactRecord(
            gcode_path.relative_to(self.repository.root).as_posix(), "text/x.gcode",
            len(gcode), hashlib.sha256(gcode).hexdigest(),
        )
        self.repository.finalize_run(
            child.run_id, status="settings_validated", artifacts=(artifact,),
            validation={
                "state": "sample_settings_validated", "print_ready": False,
                "gcode_preflight": {"passed": True, "parser_coverage_complete": True},
            },
        )
        child_assessment = AssessmentService(self.repository).save(
            child.run_id, expected_revision=0,
            results=ExperimentResults(
                confirmation_config.plan.plan_id,
                (CandidateAssessment(candidate.candidate_id, verdict="pass"),),
                selected_candidate_id=candidate.candidate_id,
            ),
            attestation=reviewed_trial,
        )

        accepted = self.service.evaluate(self.run.run_id, parent_assessment.assessment_revision_id)

        self.assertTrue(accepted.can_accept)
        self.assertEqual(
            accepted.confirmation_evidence["assessment_revision_id"],
            child_assessment.assessment_revision_id,
        )
        self.assertEqual(self.repository.get_run_decision(accepted.decision_id), accepted)
        legacy = accepted.to_dict()
        legacy["schema_version"] = 1
        legacy.pop("confirmation_evidence")
        self.assertIsNone(type(accepted).from_dict(legacy).confirmation_evidence)

    def test_missing_parent_artifact_blocks_opt_out_acceptance(self):
        reviewed_trial = PrintAttestation(
            True, True, label_legible=True, frame_adhesion_sound=True,
            samples_separable=True, trial_material="PLA", trial_nozzle="0.4 mm",
            layer_height_mm=0.2, orientation="Flat on bed; labels up",
        )
        assessment = AssessmentService(self.repository).save(
            self.run.run_id, expected_revision=1, results=self.assessment.results,
            attestation=reviewed_trial,
        )
        run = self.repository.get_run(self.run.run_id)
        self.repository.artifact_path(run.artifacts[0]).unlink()

        decision = self.service.evaluate(
            self.run.run_id, assessment.assessment_revision_id,
            confirmation_opt_out_reason="Recorded for this isolated software policy fixture.",
        )

        self.assertFalse(decision.can_accept)
        self.assertTrue(any("artifact" in reason.lower() for reason in decision.reasons))

    def test_reasoned_opt_out_is_recorded_but_does_not_turn_synthetic_results_into_acceptance(self):
        decision = self.service.evaluate(
            self.run.run_id, self.assessment.assessment_revision_id,
            confirmation_opt_out_reason="A separate confirmation plate is not available this month.",
        )
        self.assertFalse(decision.can_accept)
        self.assertEqual(decision.opt_out_record["reason"], "A separate confirmation plate is not available this month.")
        self.assertEqual(self.repository.get_run_decision(decision.decision_id), decision)

    def test_synthetic_assessment_cannot_build_orca_export_review(self):
        decision = self.service.evaluate(self.run.run_id, self.assessment.assessment_revision_id)
        library = LibraryService(self.repository, ProfileService())
        with self.assertRaises(RunExportServiceError):
            RunExportService(library).build_review(
                self.run.run_id, decision.decision_id, new_profile_name="Synthetic only"
            )

    def test_mutated_review_payload_is_rejected_before_export_files_are_written(self):
        library = LibraryService(self.repository, ProfileService())
        service = RunExportService(library)
        draft = ExportDraft(
            session_id=self.run.run_id, module_id="ironing", plan_id=self.run.plan.plan_id,
            source_profile_name="Process", source_profile_sha256="a" * 64,
            new_profile_name="Reviewed process", selected_candidate_id="sample-1",
            setting_changes={"ironing_flow": {"old": "5%", "new": "20%"}},
            supporting_run_ids=(self.run.run_id,), candidate_ids=("sample-1",),
            compatibility_warnings=(), report_paths=("runs/run-initial/manifest.json",),
            confirmation_status="opted_out", confirmation_reason="Fixture only.",
            orca_version="Orca test", profile_payload={"nested": {"flow": "20%"}},
            run_id=self.run.run_id, assessment_revision_id="assessment-fixture",
            decision_id="decision-fixture", evidence_paths=("runs/run-initial/manifest.json",),
        )
        digest = _draft_hash(draft)
        self.assertNotEqual(
            _draft_hash(replace(draft, report_paths=("runs/other/manifest.json",))),
            digest,
        )
        current = RunExportDraft(self.run.run_id, "assessment-fixture", "decision-fixture", "Reviewed process", draft, digest)
        changed_payload = {"nested": {"flow": "999%"}}
        tampered = replace(draft, profile_payload=changed_payload)
        supplied = replace(current, export_draft=tampered)

        with tempfile.TemporaryDirectory() as temporary:
            destination = Path(temporary) / "Reviewed process.json"
            with patch.object(service, "build_review", return_value=current):
                with self.assertRaises(StaleExportDraftError):
                    service.write_reviewed(supplied, destination)
            self.assertFalse(destination.exists())


if __name__ == "__main__":
    unittest.main()
