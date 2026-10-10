"""Headless contracts for baseline-relative experiment planning and review."""

from __future__ import annotations

from typing import Any
import unittest

from calibrate3dp.app.models import ProfileSelection
from calibrate3dp.app.services.experiment_service import (
    ExperimentOptions,
    ExperimentService,
    ExperimentServiceError,
    ModuleUnavailableError,
)
from calibrate3dp.profiles import ProfileDocument, ResolvedProfile


class ExperimentReviewServiceTests(unittest.TestCase):
    def setUp(self):
        self.service = ExperimentService()

    def test_ironing_defaults_use_imported_profile_and_match_documented_matrix(self):
        profiles = make_profiles(
            process_settings={
                "ironing_flow": "10",
                "ironing_speed": "30",
                "ironing_type": "top",
                "layer_height": "0.2",
            }
        )

        plan = self.service.create_initial("ironing", profiles)

        self.assertEqual(
            [dimension.values for dimension in plan.dimensions],
            [("8", "10", "12"), ("20", "30", "40")],
        )
        self.assertEqual(len(plan.candidates), 9)
        self.assertEqual(plan.candidates[0].overrides,
                         {"ironing_flow": "8", "ironing_speed": "20"})
        self.assertEqual(plan.candidates[-1].overrides,
                         {"ironing_flow": "12", "ironing_speed": "40"})

    def test_missing_baseline_blocks_plan_creation(self):
        profiles = make_profiles(process_settings={"ironing_flow": "10"})

        with self.assertRaisesRegex(ExperimentServiceError, "ironing_speed"):
            self.service.create_initial("ironing", profiles)

    def test_duplicate_after_schema_clamp_requires_edit(self):
        profiles = make_profiles(
            process_settings={"ironing_flow": "10", "ironing_speed": "30"}
        )

        with self.assertRaisesRegex(ExperimentServiceError, "duplicate"):
            self.service.create_initial(
                "ironing",
                profiles,
                ExperimentOptions(schema_limits={"ironing_flow": (10, 100)}),
            )

    def test_fixed_settings_are_visible_in_review(self):
        profiles = make_profiles(
            process_settings={
                "ironing_flow": "10%",
                "ironing_speed": 30,
                "ironing_type": "top",
                "top_surface_pattern": "monotonic",
            }
        )
        plan = self.service.create_initial("ironing", profiles)

        review = self.service.review(plan, profiles)

        self.assertEqual(review.fixed_settings["ironing_type"], "top")
        self.assertEqual(review.fixed_settings["top_surface_pattern"], "monotonic")
        self.assertEqual(len(review.plate_map), 9)
        self.assertEqual(review.plate_map[0].plate_label, "Connected plate (code assigned at generation)")
        self.assertEqual(review.plate_map[0].specimen_label, "Sample-A")
        self.assertEqual(review.plate_map[-1].specimen_label, "Sample-I")
        self.assertTrue(any("share one connected plate" in item for item in review.assumptions))
        self.assertIn("not available", review.estimate_message.casefold())

    def test_unavailable_module_cannot_start(self):
        profiles = make_profiles(
            process_settings={"ironing_flow": "10", "ironing_speed": "30"}
        )

        with self.assertRaises(ModuleUnavailableError):
            self.service.create_initial("bridge_quality", profiles)

    def test_profile_precision_and_percent_unit_are_preserved(self):
        profiles = make_profiles(
            process_settings={"ironing_flow": "10.00%", "ironing_speed": "30.0"}
        )

        plan = self.service.create_initial("ironing", profiles)

        self.assertEqual(plan.dimensions[0].values, ("8.00%", "10.00%", "12.00%"))
        self.assertEqual(plan.dimensions[1].values, ("20.0", "30.0", "40.0"))

    def test_user_edited_values_rebuild_the_candidate_matrix(self):
        profiles = make_profiles(
            process_settings={"ironing_flow": "10", "ironing_speed": "30"}
        )

        plan = self.service.create_initial(
            "ironing",
            profiles,
            ExperimentOptions(flow_values=("7", "10", "13"),
                              speed_values=("24", "30", "36")),
        )

        self.assertEqual(plan.candidates[0].overrides,
                         {"ironing_flow": "7", "ironing_speed": "24"})
        self.assertEqual(plan.candidates[4].overrides,
                         {"ironing_flow": "10", "ironing_speed": "30"})


def make_profiles(*, process_settings: dict[str, Any]) -> ProfileSelection:
    printer_doc = ProfileDocument(
        name="Printer", kind="machine", scope="fixture",
        raw={"type": "machine", "name": "Printer"}, source="printer.json",
    )
    filament_doc = ProfileDocument(
        name="PLA", kind="filament", scope="fixture",
        raw={"type": "filament", "name": "PLA"}, source="filament.json",
    )
    process_raw = {"type": "process", "name": "Fine", **process_settings}
    process_doc = ProfileDocument(
        name="Fine", kind="process", scope="fixture",
        raw=process_raw, source="fine.json",
    )
    return ProfileSelection(
        printer=ResolvedProfile(printer_doc, printer_doc.raw, {}, (printer_doc,)),
        filament=ResolvedProfile(filament_doc, filament_doc.raw, {}, (filament_doc,)),
        process=ResolvedProfile(
            process_doc,
            process_settings | {"type": "process", "name": "Fine"},
            {key: process_doc for key in process_settings},
            (process_doc,),
        ),
    )


if __name__ == "__main__":
    unittest.main()
