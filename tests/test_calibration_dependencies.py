from __future__ import annotations

import unittest

from calibrate3dp.calibration.dependencies import (
    DependencyContext,
    DependencyEvaluator,
    DependencyGraph,
    DependencyRule,
)
from calibrate3dp.calibration.state import CalibrationEvidence, CalibrationStatus


class DependencyEvaluatorTests(unittest.TestCase):
    def test_required_failure_blocks_but_recommendation_only_warns(self):
        graph = DependencyGraph.from_rules((
            DependencyRule(
                "required-height", "ironing", "input", "layer_height", "equals", 0.2,
                severity="required", invalidates=("layer_height",),
            ),
            DependencyRule(
                "recommended-nozzle", "ironing", "input", "nozzle_type", "present",
                severity="recommended", invalidates=("nozzle_type",),
            ),
        ))
        evaluator = DependencyEvaluator(graph)

        blocked = evaluator.evaluate(DependencyContext({}), {})[0]
        self.assertEqual(blocked.status, CalibrationStatus.BLOCKED)
        self.assertFalse(blocked.can_start)
        self.assertTrue(any("layer_height" in reason for reason in blocked.reasons))

        ready_to_start = evaluator.evaluate(DependencyContext({"layer_height": 0.2}), {})[0]
        self.assertEqual(ready_to_start.status, CalibrationStatus.UNTESTED)
        self.assertTrue(ready_to_start.can_start)
        self.assertEqual(len(ready_to_start.recommendations), 1)
        self.assertIn("nozzle_type", ready_to_start.recommendations[0])

    def test_missing_and_explicit_null_are_distinct(self):
        graph = DependencyGraph.from_rules((
            DependencyRule("null-is-known", "ironing", "input", "optional", "equals", None),
            DependencyRule("non-null-required", "ironing", "input", "nozzle", "present"),
        ))

        state = DependencyEvaluator(graph).evaluate(DependencyContext({"optional": None}), {})[0]

        self.assertEqual(state.status, CalibrationStatus.BLOCKED)
        self.assertIn("optional", state.input_snapshot)
        self.assertNotIn("nozzle", state.input_snapshot)
        self.assertEqual(len(state.reasons), 1)
        self.assertIn("nozzle", state.reasons[0])

    def test_one_of_accepts_a_declared_value_and_context_is_immutable(self):
        graph = DependencyGraph.from_rules((
            DependencyRule(
                "supported-layer", "ironing", "input", "layer_height", "one_of", [0.16, 0.2]
            ),
        ))
        context = DependencyContext({"layer_height": 0.2, "nested": {"values": [1, 2]}})

        state = DependencyEvaluator(graph).evaluate(context, {})[0]

        self.assertTrue(state.can_start)
        self.assertEqual(state.input_snapshot["layer_height"], 0.2)
        with self.assertRaises(TypeError):
            context.values["layer_height"] = 0.3
        with self.assertRaises(TypeError):
            context.values["nested"]["values"][0] = 9

    def test_accepted_result_prerequisite_requires_explicit_accepted_evidence(self):
        graph = DependencyGraph.from_rules((
            DependencyRule("flow-current", "flow", "input", "flow_profile", "present"),
            DependencyRule(
                "flow-accepted", "ironing", "calibration_result", "flow", "accepted",
                severity="required",
            ),
        ))
        evaluator = DependencyEvaluator(graph)
        context = DependencyContext({"flow_profile": "profile-1"})

        missing = evaluator.evaluate(context, {})
        self.assertFalse(missing[1].can_start)
        self.assertEqual(missing[1].status, CalibrationStatus.BLOCKED)

        rejected_evidence = CalibrationEvidence(
            latest_run_status="settings_validated",
            captured_input_values={"flow_profile": "profile-1"},
            assessment_state="reviewed",
            accepted_decision_evidence=False,
        )
        rejected = evaluator.evaluate(context, {"flow": rejected_evidence})
        self.assertFalse(rejected[1].can_start)
        self.assertTrue(any("flow" in reason for reason in rejected[1].reasons))

        accepted_evidence = CalibrationEvidence(
            latest_run_status="settings_validated",
            captured_input_values={"flow_profile": "profile-1"},
            assessment_state="accepted",
            accepted_decision_evidence=True,
        )
        accepted = evaluator.evaluate(context, {"flow": accepted_evidence})
        self.assertTrue(accepted[1].can_start)

    def test_states_and_reasons_are_returned_in_stable_order(self):
        graph = DependencyGraph.from_rules((
            DependencyRule("z-second", "ironing", "input", "second", "equals", 2),
            DependencyRule("a-first", "ironing", "input", "first", "equals", 1),
            DependencyRule("b-flow", "flow", "input", "flow", "present"),
        ))

        states = DependencyEvaluator(graph).evaluate(DependencyContext({}), {})

        self.assertEqual([state.calibration_id for state in states], ["flow", "ironing"])
        ironing = states[1]
        self.assertEqual(len(ironing.reasons), 2)
        self.assertTrue(ironing.reasons[0].startswith("a-first:"))
        self.assertTrue(ironing.reasons[1].startswith("z-second:"))

    def test_duplicate_rule_ids_are_rejected(self):
        rule = DependencyRule("same", "ironing", "input", "a", "present")
        with self.assertRaisesRegex(ValueError, "duplicate.*same"):
            DependencyGraph.from_rules((rule, rule))

    def test_unknown_calibration_result_reference_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "unknown.*missing-calibration"):
            DependencyGraph.from_rules((
                DependencyRule(
                    "ironing-needs-missing", "ironing", "calibration_result",
                    "missing-calibration", "accepted",
                ),
            ))

    def test_dependency_cycles_are_rejected(self):
        with self.assertRaisesRegex(ValueError, "cycle"):
            DependencyGraph.from_rules((
                DependencyRule("ironing-needs-flow", "ironing", "calibration_result", "flow", "accepted"),
                DependencyRule("flow-needs-ironing", "flow", "calibration_result", "ironing", "accepted"),
            ))

    def test_input_changes_invalidate_transitively_but_not_unrelated_calibrations(self):
        graph = DependencyGraph.from_rules((
            DependencyRule(
                "pa-nozzle", "pressure_advance", "input", "nozzle_diameter", "present",
                invalidates=("nozzle_diameter",),
            ),
            DependencyRule(
                "retraction-pa", "retraction", "calibration_result", "pressure_advance", "accepted",
            ),
            DependencyRule(
                "ironing-process", "ironing", "input", "ironing_profile", "present",
                invalidates=("ironing_profile",),
            ),
        ))

        affected = DependencyEvaluator.affected_by_change({"nozzle_diameter"}, graph)

        self.assertEqual(affected, frozenset({"pressure_advance", "retraction"}))
        self.assertNotIn("ironing", affected)
        self.assertEqual(DependencyEvaluator.affected_by_change({"unrelated_key"}, graph), frozenset())

    def test_active_run_precedes_input_staleness_and_settings_run_needs_assessment(self):
        graph = DependencyGraph.from_rules((
            DependencyRule(
                "ironing-profile", "ironing", "input", "profile_hash", "present",
                invalidates=("profile_hash",),
            ),
        ))
        evaluator = DependencyEvaluator(graph)
        context = DependencyContext({"profile_hash": "current"})

        active = evaluator.evaluate(context, {
            "ironing": CalibrationEvidence(
                latest_run_status="generating", captured_input_values={"profile_hash": "old"}
            ),
        })[0]
        self.assertEqual(active.status, CalibrationStatus.IN_PROGRESS)

        needs_review = evaluator.evaluate(context, {
            "ironing": CalibrationEvidence(
                latest_run_status="settings_validated", captured_input_values={"profile_hash": "current"}
            ),
        })[0]
        self.assertEqual(needs_review.status, CalibrationStatus.NEEDS_REVIEW)

    def test_changed_historical_input_is_stale_and_reviewed_nonaccepted_is_ready(self):
        graph = DependencyGraph.from_rules((
            DependencyRule(
                "ironing-profile", "ironing", "input", "profile_hash", "present",
                invalidates=("profile_hash",),
            ),
        ))
        evaluator = DependencyEvaluator(graph)
        context = DependencyContext({"profile_hash": "current"})

        stale = evaluator.evaluate(context, {
            "ironing": CalibrationEvidence(
                latest_run_status="settings_validated", captured_input_values={"profile_hash": "old"},
                assessment_state="reviewed",
            ),
        })[0]
        self.assertEqual(stale.status, CalibrationStatus.STALE)

        unavailable = evaluator.evaluate(DependencyContext({}), {
            "ironing": CalibrationEvidence(
                latest_run_status="settings_validated", captured_input_values={"profile_hash": "current"},
                assessment_state="reviewed",
            ),
        })[0]
        self.assertEqual(unavailable.status, CalibrationStatus.STALE)

        reviewed = evaluator.evaluate(context, {
            "ironing": CalibrationEvidence(
                latest_run_status="failed", captured_input_values={"profile_hash": "current"},
                assessment_state="reviewed",
            ),
        })[0]
        self.assertEqual(reviewed.status, CalibrationStatus.READY)

    def test_accepted_lifecycle_status_requires_accepted_decision_evidence(self):
        graph = DependencyGraph.from_rules((
            DependencyRule("iron-profile", "ironing", "input", "profile_hash", "present"),
        ))
        evaluator = DependencyEvaluator(graph)
        context = DependencyContext({"profile_hash": "same"})

        state = evaluator.evaluate(context, {
            "ironing": CalibrationEvidence(
                latest_run_status="settings_validated", captured_input_values={"profile_hash": "same"},
                assessment_state="accepted", accepted_decision_evidence=True,
            ),
        })[0]
        self.assertEqual(state.status, CalibrationStatus.ACCEPTED)


if __name__ == "__main__":
    unittest.main()
