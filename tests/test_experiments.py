import importlib
import unittest

try:
    experiment_api = importlib.import_module("calibrate3dp.experiments")
    experiment_import_error = None
except ImportError as error:
    experiment_api = None
    experiment_import_error = error


class ExperimentDomainTests(unittest.TestCase):
    def api(self):
        self.assertIsNotNone(
            experiment_api,
            f"experiment domain is not implemented yet: {experiment_import_error}",
        )
        return experiment_api

    def make_plan(self, api):
        return api.create_grid_experiment(
            plan_id="iron-coarse-01",
            module_id="ironing",
            baseline_settings={
                "ironing_flow": "10",
                "ironing_speed": "30",
                "ironing_pattern": "rectilinear",
                "ironing_spacing": "0.1",
            },
            dimensions=(
                api.SweepDimension("ironing_flow", ("8", "10", "12")),
                api.SweepDimension("ironing_speed", ("20", "30", "40")),
            ),
            candidate_prefix="I",
        )

    def test_grid_is_ordered_cartesian_product_and_keeps_baseline_constants(self):
        api = self.api()
        plan = self.make_plan(api)

        self.assertEqual(len(plan.candidates), 9)
        self.assertEqual(plan.candidates[0].candidate_id, "I001")
        self.assertEqual(
            plan.candidates[0].overrides,
            {"ironing_flow": "8", "ironing_speed": "20"},
        )
        self.assertEqual(
            plan.candidates[4].overrides,
            {"ironing_flow": "10", "ironing_speed": "30"},
        )
        self.assertEqual(
            plan.settings_for("I005"),
            {
                "ironing_flow": "10",
                "ironing_speed": "30",
                "ironing_pattern": "rectilinear",
                "ironing_spacing": "0.1",
            },
        )
        self.assertEqual(plan.candidates[-1].candidate_id, "I009")

    def test_grid_rejects_duplicate_keys_missing_baselines_and_oversized_designs(self):
        api = self.api()
        baseline = {"ironing_flow": "10", "ironing_speed": "30"}
        with self.assertRaises(api.ExperimentDefinitionError):
            api.create_grid_experiment(
                plan_id="bad-duplicate",
                module_id="ironing",
                baseline_settings=baseline,
                dimensions=(
                    api.SweepDimension("ironing_flow", ("8", "10")),
                    api.SweepDimension("ironing_flow", ("20", "30")),
                ),
            )
        with self.assertRaises(api.ExperimentDefinitionError):
            api.create_grid_experiment(
                plan_id="bad-missing",
                module_id="ironing",
                baseline_settings=baseline,
                dimensions=(api.SweepDimension("ironing_spacing", ("0.1", "0.2")),),
            )
        with self.assertRaises(api.ExperimentDefinitionError):
            api.create_grid_experiment(
                plan_id="bad-large",
                module_id="ironing",
                baseline_settings=baseline,
                dimensions=(
                    api.SweepDimension("ironing_flow", ("1", "2", "3")),
                    api.SweepDimension("ironing_speed", ("10", "20", "30")),
                ),
                max_candidates=8,
            )

    def test_assessments_validate_ratings_and_result_sets_validate_plan_ids(self):
        api = self.api()
        plan = self.make_plan(api)
        assessment = api.CandidateAssessment(
            candidate_id="I005",
            ratings={"smoothness": 5, "coverage": 4},
            defect_tags=("minor_edge_ridge",),
            notes="Best finish so far",
        )
        results = api.ExperimentResults(
            plan_id=plan.plan_id,
            assessments=(assessment,),
            selected_candidate_id="I005",
            accepted=False,
        )

        results.validate_for(plan)
        self.assertEqual(results.selected_candidate_id, "I005")
        with self.assertRaises(api.ExperimentDefinitionError):
            api.CandidateAssessment(candidate_id="I001", ratings={"smoothness": 6})
        with self.assertRaises(api.ExperimentStateError):
            results.validate_for(self.make_plan_with_other_id(api))

    def make_plan_with_other_id(self, api):
        plan = self.make_plan(api)
        return api.create_grid_experiment(
            plan_id="different-plan",
            module_id=plan.module_id,
            baseline_settings=plan.baseline_settings,
            dimensions=plan.dimensions,
            candidate_prefix="I",
        )

    def test_results_reject_unknown_or_duplicate_candidate_assessments(self):
        api = self.api()
        plan = self.make_plan(api)
        unknown = api.ExperimentResults(
            plan_id=plan.plan_id,
            assessments=(api.CandidateAssessment("I999"),),
            selected_candidate_id=None,
            accepted=None,
        )
        with self.assertRaises(api.ExperimentStateError):
            unknown.validate_for(plan)

        duplicate = api.ExperimentResults(
            plan_id=plan.plan_id,
            assessments=(api.CandidateAssessment("I001"), api.CandidateAssessment("I001")),
            selected_candidate_id=None,
            accepted=None,
        )
        with self.assertRaises(api.ExperimentDefinitionError):
            duplicate.validate_for(plan)

    def test_accepted_results_must_have_a_selected_candidate(self):
        api = self.api()
        with self.assertRaises(api.ExperimentDefinitionError):
            api.ExperimentResults(
                plan_id="iron-coarse-01",
                assessments=(),
                selected_candidate_id=None,
                accepted=True,
            )

    def test_plan_and_manual_results_round_trip_as_versioned_json_data(self):
        api = self.api()
        plan = self.make_plan(api)
        payload = plan.to_dict()
        self.assertEqual(payload["schema_version"], 1)
        restored_plan = api.ExperimentPlan.from_dict(payload)
        self.assertEqual(restored_plan.to_dict(), payload)

        results = api.ExperimentResults(
            plan_id=plan.plan_id,
            assessments=(
                api.CandidateAssessment(
                    candidate_id="I005",
                    ratings={"finish": 5},
                    defect_tags=("none",),
                    notes="preferred finish",
                ),
            ),
            selected_candidate_id="I005",
            accepted=False,
        )
        restored_results = api.ExperimentResults.from_dict(results.to_dict())
        restored_results.validate_for(restored_plan)
        self.assertEqual(restored_results.to_dict(), results.to_dict())

    def test_versioned_payloads_reject_unknown_schema_versions(self):
        api = self.api()
        payload = self.make_plan(api).to_dict()
        payload["schema_version"] = 2
        with self.assertRaises(api.ExperimentDefinitionError):
            api.ExperimentPlan.from_dict(payload)


if __name__ == "__main__":
    unittest.main()
