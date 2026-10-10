"""Assemble saved printer/run evidence for the pure calibration evaluator."""

from __future__ import annotations

from dataclasses import replace
import hashlib
import json
from typing import Callable, Mapping

from calibrate3dp.app.models import ProfileSelection
from calibrate3dp.app.services.library_service import LibraryService
from calibrate3dp.calibration.dependencies import DependencyContext, DependencyEvaluator
from calibrate3dp.calibration.ironing import (
    IRONING_CALIBRATION_ID,
    IRONING_DEPENDENCY_RULESET_ID,
    IRONING_DEPENDENCY_RULESET_VERSION,
)
from calibrate3dp.calibration.state import (
    CalibrationEvidence,
    CalibrationState,
    CalibrationStatus,
    JsonValue,
)
from calibrate3dp.domain.experiment_config import SavedExperimentConfiguration
from calibrate3dp.domain.records import CalibrationRunRecord
from calibrate3dp.profiles import ResolvedProfile


_PROFILE_ROLES = ("printer", "process", "filament")
_SNAPSHOT_SCHEMA_VERSION = 1


class CalibrationStateService:
    """Resolve current context and latest immutable evidence for ironing."""

    def __init__(
        self,
        library: LibraryService,
        evaluator: DependencyEvaluator,
        *,
        slicer_available: Callable[[], bool],
    ) -> None:
        if not isinstance(library, LibraryService):
            raise TypeError("library must be a LibraryService")
        if not isinstance(evaluator, DependencyEvaluator):
            raise TypeError("evaluator must be a DependencyEvaluator")
        if IRONING_CALIBRATION_ID not in evaluator.graph.calibration_ids:
            raise ValueError("dependency evaluator must include the ironing calibration")
        if not callable(slicer_available):
            raise TypeError("slicer_available must be callable")
        self.library = library
        self.repository = library.repository
        self.evaluator = evaluator
        self._slicer_available = slicer_available

    def states_for(
        self, printer_id: str | None, material_id: str | None
    ) -> tuple[CalibrationState, ...]:
        context, _selection, _hashes = self._context_for(printer_id, material_id)
        return self._evaluate_context(context, printer_id, material_id)

    def evaluate_configuration(
        self, configuration: SavedExperimentConfiguration
    ) -> CalibrationState:
        if not isinstance(configuration, SavedExperimentConfiguration):
            raise TypeError("configuration must be a SavedExperimentConfiguration")
        context, _selection, _hashes = self._context_for(
            configuration.printer_id,
            configuration.material_id,
            configuration=configuration,
        )
        return self._evaluate_context(
            context, configuration.printer_id, configuration.material_id
        )[0]

    def snapshot_for(
        self, configuration: SavedExperimentConfiguration
    ) -> Mapping[str, JsonValue]:
        """Return versioned JSON evidence for the exact context being evaluated."""
        if not isinstance(configuration, SavedExperimentConfiguration):
            raise TypeError("configuration must be a SavedExperimentConfiguration")
        context, selection, current_hashes = self._context_for(
            configuration.printer_id,
            configuration.material_id,
            configuration=configuration,
        )
        state = self._evaluate_context(
            context, configuration.printer_id, configuration.material_id
        )[0]
        failed_rule_ids = {
            item.split(":", 1)[0]
            for item in (*state.reasons, *state.recommendations)
        }
        outcomes = []
        for rule in self.evaluator.graph.rules:
            message = next(
                (
                    item.split(":", 1)[1].strip()
                    for item in (*state.reasons, *state.recommendations)
                    if item.startswith(rule.rule_id + ":")
                ),
                None,
            )
            outcomes.append({
                "rule_id": rule.rule_id,
                "severity": rule.severity,
                "passed": rule.rule_id not in failed_rule_ids,
                "message": message,
            })

        input_values = _plain_json(dict(state.input_snapshot))
        return {
            "schema_version": _SNAPSHOT_SCHEMA_VERSION,
            "ruleset_id": IRONING_DEPENDENCY_RULESET_ID,
            "ruleset_version": IRONING_DEPENDENCY_RULESET_VERSION,
            "calibration_id": IRONING_CALIBRATION_ID,
            "inputs": input_values,
            "source_profile_hashes": dict(configuration.source_profile_hashes),
            "evaluated_source_hashes": {
                role: current_hashes.get(role) for role in _PROFILE_ROLES
            },
            "prerequisite_refs": {
                "calibration_results": [],
                "parent_run_id": configuration.parent_run_id,
                "parent_assessment_revision_id": configuration.parent_assessment_revision_id,
                "parent_candidate_id": configuration.parent_candidate_id,
            },
            "rule_outcomes": outcomes,
            "state": {
                "status": state.status.value,
                "can_start": state.can_start,
                "reasons": list(state.reasons),
                "recommendations": list(state.recommendations),
            },
            "profile_context_identity": _profile_identity(selection),
        }

    def _context_for(
        self,
        printer_id: str | None,
        material_id: str | None,
        *,
        configuration: SavedExperimentConfiguration | None = None,
    ) -> tuple[DependencyContext, ProfileSelection | None, dict[str, str | None]]:
        values: dict[str, JsonValue] = {
            "printer_id": printer_id,
            "material_id": material_id,
            "printer_material_state": "resolved",
            "nozzle_context_state": "matches",
            "profile_context_state": "matches",
            "profile_context_identity": None,
            "printer_nozzle": None,
            "material_nozzle_context": None,
            "slicer_state": "available",
        }
        current_hashes: dict[str, str | None] = {role: None for role in _PROFILE_ROLES}
        expected_hashes: dict[str, str | None] = {role: None for role in _PROFILE_ROLES}
        selection: ProfileSelection | None = None

        if not printer_id:
            values["printer_material_state"] = "select a saved printer"
        if not material_id:
            values["printer_material_state"] = "select a saved material"
        printer = material = None
        if printer_id:
            try:
                printer = self.repository.get_printer(printer_id)
                values["printer_nozzle"] = printer.nozzle
            except Exception as exc:
                values["printer_material_state"] = f"saved printer is unavailable ({exc})"
        if material_id:
            try:
                material = self.repository.get_material(material_id)
                values["material_nozzle_context"] = material.nozzle_context
            except Exception as exc:
                values["printer_material_state"] = f"saved material is unavailable ({exc})"

        if printer is not None and material is not None:
            values["printer_material_state"] = "resolved"
            if _normalize_nozzle(printer.nozzle) != _normalize_nozzle(material.nozzle_context):
                values["nozzle_context_state"] = (
                    f"printer uses {printer.nozzle}; material is recorded for {material.nozzle_context}"
                )
            else:
                try:
                    selection = _ironing_selection(
                        self.library.resolve_selection(printer_id, material_id)
                    )
                except Exception as exc:
                    values["printer_material_state"] = f"printer/material profiles cannot be resolved ({exc})"
                    values["nozzle_context_state"] = "profile resolution failed"
                    selection = _selection_from_records(printer, material)

        if selection is not None:
            values["profile_context_identity"] = _profile_identity(selection)
            expected_hashes = {
                role: selection.source_hashes.get(role) for role in _PROFILE_ROLES
            }
            try:
                current_hashes = self.library.current_source_hashes(selection)
            except Exception:
                current_hashes = {role: None for role in _PROFILE_ROLES}
        elif printer is not None and material is not None:
            selection = _selection_from_records(printer, material)
            values["profile_context_identity"] = _profile_identity(selection)
            expected_hashes = {
                role: selection.source_hashes.get(role) for role in _PROFILE_ROLES
            }
            try:
                current_hashes = self.library.current_source_hashes(selection)
            except Exception:
                current_hashes = {role: None for role in _PROFILE_ROLES}

        for role in _PROFILE_ROLES:
            expected = expected_hashes.get(role)
            current = current_hashes.get(role)
            values[f"source_hashes.expected.{role}"] = expected
            values[f"source_hashes.current.{role}"] = current
            values[f"profile_hash_state.{role}"] = (
                "unchanged" if expected is not None and current == expected else "changed or unavailable"
            )

        if configuration is not None:
            if selection is None:
                values["profile_context_state"] = "selected profiles cannot be resolved"
            elif selection.to_dict() != _ironing_selection(configuration.profile_selection).to_dict():
                values["profile_context_state"] = (
                    "saved configuration profiles differ from the current printer/material profiles"
                )

        try:
            slicer_ok = bool(self._slicer_available())
        except Exception as exc:
            slicer_ok = False
            values["slicer_state"] = f"availability check failed ({exc})"
        if not slicer_ok and values["slicer_state"] == "available":
            values["slicer_state"] = "not available in OrcaSlicer settings"

        return DependencyContext(values), selection, current_hashes

    def _evaluate_context(
        self,
        context: DependencyContext,
        printer_id: str | None,
        material_id: str | None,
    ) -> tuple[CalibrationState, ...]:
        runs = tuple(
            run
            for run in self.repository.list_runs(printer_id=printer_id)
            if run.material_id == material_id and run.plan.module_id == "ironing"
        ) if printer_id and material_id else ()
        latest_run = max(runs, key=lambda item: (item.created_at_utc, item.run_id), default=None)
        records: dict[str, CalibrationEvidence] = {}
        reconstruction_incomplete = False
        if latest_run is not None:
            evidence, reconstruction_incomplete = self._evidence_for_run(latest_run, context)
            records[IRONING_CALIBRATION_ID] = evidence
            accepted = self._accepted_evidence_for_runs(runs, latest_run)
            if accepted is not None:
                records[IRONING_CALIBRATION_ID] = accepted

        states = self.evaluator.evaluate(context, records)
        if latest_run is None or not reconstruction_incomplete:
            return states
        state = states[0]
        if state.status in {CalibrationStatus.STALE, CalibrationStatus.IN_PROGRESS}:
            return states
        return (replace(
            state,
            status=CalibrationStatus.NEEDS_REVIEW,
            reasons=(*state.reasons, "Legacy run context is incomplete; review its saved profile and parent evidence."),
        ),)

    def _evidence_for_run(
        self, run: CalibrationRunRecord, context: DependencyContext
    ) -> tuple[CalibrationEvidence, bool]:
        snapshot = run.validation.get("dependency_snapshot")
        incomplete = False
        captured: Mapping[str, JsonValue]
        if (
            isinstance(snapshot, Mapping)
            and snapshot.get("schema_version") == _SNAPSHOT_SCHEMA_VERSION
            and snapshot.get("ruleset_id") == IRONING_DEPENDENCY_RULESET_ID
            and snapshot.get("ruleset_version") == IRONING_DEPENDENCY_RULESET_VERSION
            and isinstance(snapshot.get("inputs"), Mapping)
        ):
            captured = snapshot["inputs"]
        else:
            captured, incomplete = self._reconstruct_legacy_inputs(run, context)

        try:
            assessment = self.repository.latest_assessment_revision(run.run_id)
        except Exception:
            assessment = None
            incomplete = True
        evidence = CalibrationEvidence(
            latest_run_status=run.status,
            captured_input_values=captured,
            assessment_state="reviewed" if assessment is not None else None,
            run_id=run.run_id,
            assessment_revision_id=(assessment.assessment_revision_id if assessment else None),
        )
        return evidence, incomplete

    def _reconstruct_legacy_inputs(
        self, run: CalibrationRunRecord, context: DependencyContext
    ) -> tuple[Mapping[str, JsonValue], bool]:
        captured = dict(context.values)
        hashes = run.profiles.source_hashes
        complete = all(
            isinstance(hashes.get(role), str) and bool(hashes.get(role))
            for role in _PROFILE_ROLES
        )
        if complete:
            for role in _PROFILE_ROLES:
                captured[f"source_hashes.current.{role}"] = hashes[role]
                captured[f"source_hashes.expected.{role}"] = hashes[role]
                captured[f"profile_hash_state.{role}"] = "unchanged"
            captured["printer_id"] = run.printer_id
            captured["material_id"] = run.material_id
            captured["printer_material_state"] = "resolved"
            captured["nozzle_context_state"] = "matches"
            captured["profile_context_identity"] = _profile_identity(run.profiles)
            captured["profile_context_state"] = "matches"
        link = self.repository.get_run_config_link(run.run_id)
        if link is None:
            complete = False
        else:
            try:
                configuration = self.repository.get_configuration(link["config_id"])
                if configuration.profile_selection.to_dict() != run.profiles.to_dict():
                    complete = False
                if configuration.relation_type != "initial" and not all(
                    link.get(key)
                    for key in ("parent_run_id", "parent_assessment_revision_id", "parent_candidate_id")
                ):
                    complete = False
            except Exception:
                complete = False
        return captured, not complete

    def _accepted_evidence_for_runs(
        self,
        runs: tuple[CalibrationRunRecord, ...],
        latest_run: CalibrationRunRecord,
    ) -> CalibrationEvidence | None:
        accepted: CalibrationEvidence | None = None
        for run in runs:
            try:
                assessment = self.repository.latest_assessment_revision(run.run_id)
                decisions = self.repository.list_run_decisions(run.run_id)
            except Exception:
                continue
            if assessment is None or not assessment.attestation.physically_accepted:
                continue
            if (
                run.status != "settings_validated"
                or run.validation.get("state") != "sample_settings_validated"
                or not _preflight_passed(run)
            ):
                continue
            for decision in decisions:
                if (
                    decision.action != "accept"
                    or not decision.can_accept
                    or decision.assessment_revision_id != assessment.assessment_revision_id
                ):
                    continue
                evidence_run = run
                evidence_assessment = assessment
                if decision.confirmation_evidence is not None:
                    confirmation = self._confirmed_child(run, decision, runs)
                    if confirmation is None:
                        continue
                    evidence_run, evidence_assessment = confirmation
                if evidence_run.run_id != latest_run.run_id:
                    continue
                evidence, _incomplete = self._evidence_for_run(evidence_run, DependencyContext({}))
                accepted = replace(
                    evidence,
                    assessment_state="accepted",
                    accepted_decision_evidence=True,
                    assessment_revision_id=evidence_assessment.assessment_revision_id,
                    decision_id=decision.decision_id,
                )
        return accepted

    def _confirmed_child(self, parent, decision, runs):
        reference = decision.confirmation_evidence
        if reference is None:
            return None
        child = next((item for item in runs if item.run_id == reference.get("run_id")), None)
        if child is None or child.status != "settings_validated":
            return None
        if child.validation.get("state") != "sample_settings_validated":
            return None
        if not _preflight_passed(child):
            return None
        link = self.repository.get_run_config_link(child.run_id) or {}
        if (
            link.get("relation_type") != "confirmation"
            or link.get("parent_run_id") != parent.run_id
            or link.get("parent_assessment_revision_id") != decision.assessment_revision_id
            or link.get("parent_candidate_id") != decision.selected_candidate_id
        ):
            return None
        try:
            configuration = self.repository.get_configuration_for_run(child.run_id)
            assessment = self.repository.latest_assessment_revision(child.run_id)
        except Exception:
            return None
        if (
            configuration is None
            or configuration.plan.plan_id != reference.get("plan_id")
            or configuration.relation_type != "confirmation"
            or len(configuration.plan.candidates) != 1
            or assessment is None
            or assessment.assessment_revision_id != reference.get("assessment_revision_id")
            or not assessment.attestation.physically_accepted
        ):
            return None
        candidate = configuration.plan.candidates[0]
        if reference.get("candidate_id") != candidate.candidate_id:
            return None
        outcome = next(
            (item for item in assessment.results.assessments if item.candidate_id == candidate.candidate_id),
            None,
        )
        if outcome is None or outcome.verdict != "pass":
            return None
        return child, assessment


def _ironing_selection(selection: ProfileSelection) -> ProfileSelection:
    process_settings = dict(selection.process.settings)
    process_settings["ironing_type"] = "top"
    process = ResolvedProfile(
        selection.process.profile,
        process_settings,
        selection.process.provenance,
        selection.process.chain,
    )
    return replace(selection, process=process)


def _selection_from_records(printer, material) -> ProfileSelection:
    selection = ProfileSelection(
        printer=printer.machine_profile.profile,
        process=printer.process_profile.profile,
        filament=material.filament_profile.profile,
        source_paths={
            "printer": printer.machine_profile.profile.profile.source,
            "process": printer.process_profile.profile.profile.source,
            "filament": material.filament_profile.profile.profile.source,
        },
        source_hashes={
            "printer": printer.machine_profile.source_sha256,
            "process": printer.process_profile.source_sha256,
            "filament": material.filament_profile.source_sha256,
        },
        compatibility_warnings=(
            "This profile selection is saved locally; only combinations with recorded real-Orca checks are verified.",
        ),
    )
    return _ironing_selection(selection)


def _profile_identity(selection: ProfileSelection | None) -> str | None:
    if selection is None:
        return None
    encoded = json.dumps(
        selection.to_dict(), sort_keys=True, ensure_ascii=False, separators=(",", ":"), allow_nan=False
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _plain_json(value):
    if isinstance(value, Mapping):
        return {str(key): _plain_json(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [_plain_json(item) for item in value]
    return value


def _normalize_nozzle(value: str) -> str:
    return " ".join(value.casefold().replace("mm", " mm").split())


def _preflight_passed(run: CalibrationRunRecord) -> bool:
    report = run.validation.get("gcode_preflight")
    return (
        isinstance(report, Mapping)
        and report.get("passed") is True
        and report.get("parser_coverage_complete") is True
    )
