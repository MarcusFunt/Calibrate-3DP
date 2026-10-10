from __future__ import annotations

from dataclasses import replace
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from calibrate3dp.app.services.assessment_service import AssessmentService
from calibrate3dp.app.services.experiment_configuration_service import ExperimentConfigurationService
from calibrate3dp.app.services.grouped_orca_service import (
    CalibrationBlockedError,
    GroupedOrcaGenerationService,
)
from calibrate3dp.app.services.run_decision_service import RunDecisionService
from calibrate3dp.calibration.dependencies import DependencyEvaluator
from calibrate3dp.calibration.ironing import IRONING_DEPENDENCY_GRAPH
from calibrate3dp.calibration.state import CalibrationStatus
from calibrate3dp.app.services.calibration_state_service import CalibrationStateService
from calibrate3dp.domain.assessment import PrintAttestation
from calibrate3dp.domain.records import ArtifactRecord, CalibrationRunRecord, utc_now
from calibrate3dp.experiments import CandidateAssessment, ExperimentResults
from calibrate3dp.storage.library_store import LibraryRepository
from calibrate3dp.storage.session_store import SessionRepository
from tests.test_grouped_orca_service import FakeGroupedOrca, _create_test_library, _write_profile


def _state_service(library, *, slicer_available=lambda: True):
    return CalibrationStateService(
        library,
        DependencyEvaluator(IRONING_DEPENDENCY_GRAPH),
        slicer_available=slicer_available,
    )


def _make_run(
    repository: LibraryRepository,
    configuration,
    *,
    run_id: str,
    validation: dict,
    status: str = "settings_validated",
    preflight_passed: bool = False,
    artifacts: bool = False,
) -> CalibrationRunRecord:
    samples = tuple(
        {
            "label": f"Sample-{label}",
            "candidate_id": candidate.candidate_id,
            "settings": dict(candidate.overrides),
        }
        for label, candidate in zip("ABCDEFGHI", configuration.plan.candidates)
    )
    run = CalibrationRunRecord(
        run_id=run_id,
        plate_code=repository.allocate_plate_code(),
        printer_id=configuration.printer_id,
        material_id=configuration.material_id,
        status="generating",
        created_at_utc=utc_now(),
        plan=configuration.plan,
        profiles=configuration.profile_selection,
        sample_map=samples,
        validation={"state": "pending", "print_ready": False},
    )
    repository.create_run(run, configuration_id=configuration.config_id)
    artifact_records = ()
    if artifacts:
        artifact_path = repository.runs_root / run_id / "evidence.json"
        artifact_path.parent.mkdir(parents=True, exist_ok=True)
        payload = b'{"evidence":true}\n'
        artifact_path.write_bytes(payload)
        artifact_records = (ArtifactRecord(
            artifact_path.relative_to(repository.root).as_posix(),
            "application/json",
            len(payload),
            hashlib.sha256(payload).hexdigest(),
        ),)
    final_validation = {
        "state": "sample_settings_validated" if status == "settings_validated" else status,
        "print_ready": False,
        **validation,
    }
    if preflight_passed:
        final_validation["gcode_preflight"] = {
            "passed": True,
            "parser_coverage_complete": True,
        }
    return repository.finalize_run(
        run_id,
        status=status,
        artifacts=artifact_records,
        validation=final_validation,
    )


def _physical_attestation() -> PrintAttestation:
    return PrintAttestation(
        True,
        True,
        label_legible=True,
        frame_adhesion_sound=True,
        samples_separable=True,
        trial_material="PLA",
        trial_nozzle="0.4 mm",
        layer_height_mm=0.2,
        orientation="Flat on bed; labels up",
    )


class CalibrationStateServiceTests(unittest.TestCase):
    def test_compatible_saved_pair_is_untested_and_snapshot_records_exact_hashes(self):
        with tempfile.TemporaryDirectory() as temp:
            repository, library, printer, material = _create_test_library(Path(temp))
            service = _state_service(library)

            state = service.states_for(printer.printer_id, material.material_id)[0]
            configuration = ExperimentConfigurationService(library).prepare_ironing(
                printer.printer_id, material.material_id
            ).configuration
            snapshot = service.snapshot_for(configuration)

            self.assertEqual(state.calibration_id, "ironing")
            self.assertEqual(state.status, CalibrationStatus.UNTESTED)
            self.assertTrue(state.can_start)
            self.assertEqual(snapshot["ruleset_id"], "ironing-flow-speed-context")
            self.assertEqual(snapshot["ruleset_version"], 1)
            self.assertEqual(
                snapshot["source_profile_hashes"],
                dict(configuration.source_profile_hashes),
            )
            self.assertEqual(
                {item["rule_id"] for item in snapshot["rule_outcomes"]},
                {rule.rule_id for rule in IRONING_DEPENDENCY_GRAPH.rules},
            )
            self.assertTrue(all(item["passed"] for item in snapshot["rule_outcomes"]))

    def test_missing_or_changed_source_profile_blocks_new_generation(self):
        for change in ("missing", "changed"):
            with self.subTest(change=change), tempfile.TemporaryDirectory() as temp:
                root = Path(temp)
                repository, library, printer, material = _create_test_library(root)
                configuration = ExperimentConfigurationService(library).prepare_ironing(
                    printer.printer_id, material.material_id
                ).configuration
                source = Path(configuration.profile_selection.source_paths["process"])
                if change == "missing":
                    source.unlink()
                else:
                    payload = json.loads(source.read_text(encoding="utf-8"))
                    payload["ironing_speed"] = "8"
                    _write_profile(source, payload)

                state = _state_service(library).states_for(
                    printer.printer_id, material.material_id
                )[0]

                self.assertEqual(state.status, CalibrationStatus.BLOCKED)
                self.assertFalse(state.can_start)
                self.assertTrue(any("profile_hash_state.process" in item for item in state.reasons))
                self.assertEqual(repository.list_runs(), ())

    def test_missing_material_and_mismatched_nozzle_have_actionable_blockers(self):
        with tempfile.TemporaryDirectory() as temp:
            repository, library, printer, material = _create_test_library(Path(temp))
            missing = _state_service(library).states_for(printer.printer_id, None)[0]
            wrong_nozzle = library.add_material(
                display_name="Workshop PLA for 0.6",
                nozzle_context="0.6 mm",
                filament_choice=library.profile_choices("filament")[0],
            )
            mismatch = _state_service(library).states_for(
                printer.printer_id, wrong_nozzle.material_id
            )[0]

            self.assertEqual(missing.status, CalibrationStatus.BLOCKED)
            self.assertTrue(any("material" in item.lower() for item in missing.reasons))
            self.assertEqual(mismatch.status, CalibrationStatus.BLOCKED)
            self.assertTrue(any("nozzle_context_state" in item for item in mismatch.reasons))
            self.assertEqual(repository.list_runs(), ())

    def test_blocked_configuration_allocates_no_code_run_or_artifact_directory(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            repository, library, printer, material = _create_test_library(root)
            generation = GroupedOrcaGenerationService(library, cli_provider=lambda: None)
            configuration = generation.prepare_ironing_configuration(
                printer.printer_id, material.material_id
            ).configuration
            generation.save_configuration(configuration)

            with patch.object(repository, "allocate_plate_code", wraps=repository.allocate_plate_code) as allocate:
                with self.assertRaises(CalibrationBlockedError) as blocked:
                    generation.generate_from_configuration(configuration.config_id)

            self.assertFalse(blocked.exception.state.can_start)
            self.assertFalse(allocate.called)
            self.assertEqual(repository.list_runs(), ())
            self.assertEqual(list(repository.runs_root.iterdir()), [])

    def test_run_lifecycle_distinguishes_active_failed_cancelled_and_unassessed(self):
        expected = {
            "generating": CalibrationStatus.IN_PROGRESS,
            "settings_validated": CalibrationStatus.NEEDS_REVIEW,
            "generation_failed": CalibrationStatus.READY,
            "cancelled": CalibrationStatus.READY,
        }
        for run_status, expected_status in expected.items():
            with self.subTest(run_status=run_status), tempfile.TemporaryDirectory() as temp:
                root = Path(temp)
                repository, library, printer, material = _create_test_library(root)
                generation = GroupedOrcaGenerationService(
                    library, cli_provider=FakeGroupedOrca
                )
                configuration = generation.prepare_ironing_configuration(
                    printer.printer_id, material.material_id
                ).configuration
                generation.save_configuration(configuration)
                state_service = generation.calibration_state_service
                dependency_snapshot = state_service.snapshot_for(configuration)
                if run_status == "generating":
                    samples = tuple({
                        "label": f"Sample-{label}",
                        "candidate_id": candidate.candidate_id,
                        "settings": dict(candidate.overrides),
                    } for label, candidate in zip("ABCDEFGHI", configuration.plan.candidates))
                    pending = CalibrationRunRecord(
                        "run-active", repository.allocate_plate_code(), printer.printer_id,
                        material.material_id, "generating", utc_now(), configuration.plan,
                        configuration.profile_selection, samples,
                        {"state": "pending", "print_ready": False,
                         "dependency_snapshot": dependency_snapshot},
                    )
                    repository.create_run(pending, configuration_id=configuration.config_id)
                else:
                    _make_run(
                        repository,
                        configuration,
                        run_id=f"run-{run_status}",
                        status=run_status,
                        validation={"dependency_snapshot": dependency_snapshot},
                    )

                state = state_service.states_for(printer.printer_id, material.material_id)[0]

                self.assertEqual(state.status, expected_status)
                if run_status == "settings_validated":
                    self.assertTrue(state.can_start)

    def test_relevant_profile_change_stales_a_run_but_unrelated_profile_does_not(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            repository, library, printer, material = _create_test_library(root)
            generation = GroupedOrcaGenerationService(library, cli_provider=FakeGroupedOrca)
            run = generation.generate_ironing(printer.printer_id, material.material_id)
            state_service = generation.calibration_state_service

            unrelated = root / "orca" / "system" / "process" / "unrelated.json"
            _write_profile(unrelated, {"name": "Other process", "type": "process"})
            unaffected = state_service.states_for(printer.printer_id, material.material_id)[0]
            process_source = Path(run.profiles.source_paths["process"])
            payload = json.loads(process_source.read_text(encoding="utf-8"))
            payload["ironing_speed"] = "8"
            _write_profile(process_source, payload)
            stale = state_service.states_for(printer.printer_id, material.material_id)[0]

            self.assertEqual(unaffected.status, CalibrationStatus.NEEDS_REVIEW)
            self.assertEqual(stale.status, CalibrationStatus.STALE)
            self.assertTrue(any("profile_hash_state.process" in item for item in stale.reasons))
            self.assertFalse(run.validation["print_ready"])

    def test_legacy_run_without_snapshot_is_reconstructed_when_hashes_exist(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            repository, library, printer, material = _create_test_library(root)
            configuration = ExperimentConfigurationService(library).prepare_ironing(
                printer.printer_id, material.material_id
            ).configuration
            repository.save_configuration(configuration)
            _make_run(
                repository,
                configuration,
                run_id="run-legacy",
                validation={"legacy_validation": True},
            )
            run = repository.get_run("run-legacy")
            draft = AssessmentService(repository).empty_draft(run.run_id)
            AssessmentService(repository).save(
                run.run_id,
                expected_revision=0,
                results=draft,
                attestation=PrintAttestation(False, False, synthetic=True),
            )

            state = _state_service(library).states_for(
                printer.printer_id, material.material_id
            )[0]
            reopened = LibraryRepository(SessionRepository(root / "workspace"))

            self.assertEqual(state.status, CalibrationStatus.READY)
            self.assertNotIn("dependency_snapshot", reopened.get_run(run.run_id).validation)

    def test_accepted_state_requires_a_physical_confirmation_decision(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            repository, library, printer, material = _create_test_library(root)
            generation = GroupedOrcaGenerationService(library, cli_provider=FakeGroupedOrca)
            configuration = generation.prepare_ironing_configuration(
                printer.printer_id, material.material_id
            ).configuration
            generation.save_configuration(configuration)
            parent = _make_run(
                repository,
                configuration,
                run_id="run-parent",
                validation={"dependency_snapshot": generation.calibration_state_service.snapshot_for(configuration)},
                preflight_passed=True,
                artifacts=True,
            )
            selected = parent.plan.candidates[4].candidate_id
            parent_results = ExperimentResults(
                parent.plan.plan_id,
                tuple(CandidateAssessment(
                    item.candidate_id,
                    verdict="pass" if item.candidate_id == selected else "fail",
                ) for item in parent.plan.candidates),
                selected_candidate_id=selected,
            )
            assessments = AssessmentService(repository)
            parent_assessment = assessments.save(
                parent.run_id,
                expected_revision=0,
                results=parent_results,
                attestation=_physical_attestation(),
            )
            decisions = RunDecisionService(repository)
            recommendation = decisions.evaluate(parent.run_id, parent_assessment.assessment_revision_id)
            confirmation = decisions.create_followup(recommendation.decision_id, kind="confirmation")
            candidate = confirmation.plan.candidates[0]
            child = _make_run(
                repository,
                confirmation,
                run_id="run-confirmation",
                validation={"dependency_snapshot": generation.calibration_state_service.snapshot_for(confirmation)},
                preflight_passed=True,
                artifacts=True,
            )
            self.assertEqual(child.status, "settings_validated")
            child_assessment = assessments.save(
                child.run_id,
                expected_revision=0,
                results=ExperimentResults(
                    confirmation.plan.plan_id,
                    (CandidateAssessment(candidate.candidate_id, verdict="pass"),),
                    selected_candidate_id=candidate.candidate_id,
                ),
                attestation=_physical_attestation(),
            )
            accepted = decisions.evaluate(parent.run_id, parent_assessment.assessment_revision_id)

            self.assertTrue(accepted.can_accept)
            self.assertEqual(
                accepted.confirmation_evidence["assessment_revision_id"],
                child_assessment.assessment_revision_id,
            )
            state = generation.calibration_state_service.states_for(
                printer.printer_id, material.material_id
            )[0]
            self.assertEqual(state.status, CalibrationStatus.ACCEPTED)
            self.assertFalse(repository.get_run(child.run_id).validation["print_ready"])

    def test_snapshot_identifies_followup_parent_assessment_and_run(self):
        with tempfile.TemporaryDirectory() as temp:
            _, library, printer, material = _create_test_library(Path(temp))
            configuration = ExperimentConfigurationService(library).prepare_ironing(
                printer.printer_id, material.material_id
            ).configuration
            followup = configuration.__class__(
                config_id="config-followup",
                experiment_id="experiment-followup",
                revision_no=1,
                printer_id=configuration.printer_id,
                material_id=configuration.material_id,
                profile_selection=configuration.profile_selection,
                plan=replace(configuration.plan, parent_plan_id="plan-parent"),
                layout_options=configuration.layout_options,
                created_at_utc="2026-10-10T12:00:00Z",
                relation_type="refinement",
                parent_run_id="run-parent",
                parent_assessment_revision_id="assessment-parent",
                parent_candidate_id=configuration.plan.candidates[0].candidate_id,
            )
            snapshot = _state_service(library).snapshot_for(followup)

            self.assertEqual(snapshot["prerequisite_refs"]["parent_run_id"], "run-parent")
            self.assertEqual(
                snapshot["prerequisite_refs"]["parent_assessment_revision_id"],
                "assessment-parent",
            )


if __name__ == "__main__":
    unittest.main()
