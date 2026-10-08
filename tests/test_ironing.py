import importlib
import unittest

try:
    ironing_api = importlib.import_module("calibrate3dp.ironing")
    ironing_import_error = None
except ImportError as error:
    ironing_api = None
    ironing_import_error = error

try:
    experiment_api = importlib.import_module("calibrate3dp.experiments")
except ImportError:
    experiment_api = None


class IroningExperimentTests(unittest.TestCase):
    def api(self):
        self.assertIsNotNone(
            ironing_api,
            f"ironing refinement is not implemented yet: {ironing_import_error}",
        )
        self.assertIsNotNone(experiment_api, "experiment domain is required")
        return ironing_api, experiment_api

    def test_initial_grid_preserves_profile_types_and_unvaried_settings(self):
        iron, experiments = self.api()
        plan = iron.create_initial_ironing_experiment(
            plan_id="iron-01",
            baseline_settings={
                "ironing_flow": "10",
                "ironing_speed": "30",
                "ironing_pattern": "rectilinear",
                "ironing_spacing": "0.1",
            },
            flow_values=(8, 10, 12),
            speed_values=(20, 30, 40),
        )

        self.assertEqual(plan.module_id, "ironing")
        self.assertEqual(len(plan.candidates), 9)
        self.assertEqual(plan.candidates[0].overrides, {"ironing_flow": "8", "ironing_speed": "20"})
        self.assertEqual(
            plan.settings_for("I005"),
            {
                "ironing_flow": "10",
                "ironing_speed": "30",
                "ironing_pattern": "rectilinear",
                "ironing_spacing": "0.1",
            },
        )
        self.assertIsInstance(experiments.ExperimentResults, type)

    def test_interior_winner_refines_both_dimensions_and_links_parent_plan(self):
        iron, experiments = self.api()
        plan = iron.create_initial_ironing_experiment(
            plan_id="iron-01",
            baseline_settings={"ironing_flow": "10", "ironing_speed": "30"},
            flow_values=(8, 10, 12),
            speed_values=(20, 30, 40),
        )
        results = experiments.ExperimentResults(
            plan_id=plan.plan_id,
            assessments=(experiments.CandidateAssessment("I005", ratings={"finish": 4}),),
            selected_candidate_id="I005",
            accepted=False,
        )

        refined = iron.propose_ironing_refinement(plan, results, next_plan_id="iron-02")
        self.assertEqual(refined.parent_plan_id, "iron-01")
        self.assertEqual([dimension.key for dimension in refined.dimensions], ["ironing_flow", "ironing_speed"])
        self.assertEqual(refined.dimensions[0].values, ("9", "10", "11"))
        self.assertEqual(refined.dimensions[1].values, ("25", "30", "35"))
        self.assertIn("interior", refined.rationale)

    def test_lower_boundary_winner_extends_both_sweeps(self):
        iron, experiments = self.api()
        plan = iron.create_initial_ironing_experiment(
            plan_id="iron-01",
            baseline_settings={"ironing_flow": "10", "ironing_speed": "30"},
            flow_values=(8, 10, 12),
            speed_values=(20, 30, 40),
        )
        results = experiments.ExperimentResults(
            plan_id=plan.plan_id,
            assessments=(experiments.CandidateAssessment("I001"),),
            selected_candidate_id="I001",
            accepted=False,
        )

        refined = iron.propose_ironing_refinement(plan, results, next_plan_id="iron-02")
        self.assertEqual(refined.dimensions[0].values, ("6", "7", "8"))
        self.assertEqual(refined.dimensions[1].values, ("10", "15", "20"))
        self.assertIn("lower boundary", refined.rationale)

    def test_refinement_requires_rejection_and_respects_explicit_limits(self):
        iron, experiments = self.api()
        plan = iron.create_initial_ironing_experiment(
            plan_id="iron-01",
            baseline_settings={"ironing_flow": "10", "ironing_speed": "30"},
            flow_values=(8, 10, 12),
            speed_values=(20, 30, 40),
        )
        accepted = experiments.ExperimentResults(
            plan_id=plan.plan_id,
            assessments=(experiments.CandidateAssessment("I005"),),
            selected_candidate_id="I005",
            accepted=True,
        )
        with self.assertRaises(iron.IroningCalibrationError):
            iron.propose_ironing_refinement(plan, accepted, next_plan_id="iron-02")

        lower = experiments.ExperimentResults(
            plan_id=plan.plan_id,
            assessments=(experiments.CandidateAssessment("I001"),),
            selected_candidate_id="I001",
            accepted=False,
        )
        with self.assertRaises(iron.RefinementLimitError):
            iron.propose_ironing_refinement(
                plan,
                lower,
                next_plan_id="iron-02",
                limits={"ironing_flow": (7, 15), "ironing_speed": (0, 100)},
            )

    def test_initial_ironing_grid_rejects_missing_or_invalid_parameters(self):
        iron, _ = self.api()
        with self.assertRaises(iron.IroningCalibrationError):
            iron.create_initial_ironing_experiment(
                plan_id="missing",
                baseline_settings={"ironing_flow": "10"},
                flow_values=(8, 10, 12),
                speed_values=(20, 30, 40),
            )
        for flow_values, speed_values in (
            ((8, 10, 12), (1, 1, 2)),
            ((8, 10, 13), (20, 30, 40)),
            ((8, 10, 12), (20, 25, 40)),
            ((-1, 0, 1), (20, 30, 40)),
            ((8, 10, 12), (0, 30, 60)),
        ):
            with self.subTest(flow_values=flow_values, speed_values=speed_values):
                with self.assertRaises(iron.IroningCalibrationError):
                    iron.create_initial_ironing_experiment(
                        plan_id="invalid",
                        baseline_settings={"ironing_flow": "10", "ironing_speed": "30"},
                        flow_values=flow_values,
                        speed_values=speed_values,
                    )


if __name__ == "__main__":
    unittest.main()
