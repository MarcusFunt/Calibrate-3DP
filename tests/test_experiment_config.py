from __future__ import annotations

import unittest

from calibrate3dp.app.models import ProfileSelection
from calibrate3dp.app.services.experiment_service import ExperimentService
from calibrate3dp.domain.experiment_config import (
    ExperimentConfigurationError,
    SavedExperimentConfiguration,
    default_layout_options,
)
from calibrate3dp.experiments import SweepDimension, create_grid_experiment
from calibrate3dp.profiles import ProfileDocument, ResolvedProfile


def _profile(name: str, kind: str, settings: dict):
    raw = {"name": name, "type": kind, **settings}
    document = ProfileDocument(name, kind, "fixture", raw, f"{name}.json")
    return ResolvedProfile(document, raw, {key: document for key in raw}, (document,))


def _selection() -> ProfileSelection:
    return ProfileSelection(
        printer=_profile("Printer", "machine", {
            "printer_model": "Test printer",
            "nozzle_diameter": ["0.4"],
            "printable_area": ["0x0", "220x0", "220x220", "0x220"],
        }),
        filament=_profile("PLA", "filament", {"filament_type": ["PLA"]}),
        process=_profile("Process", "process", {
            "ironing_type": "top", "ironing_flow": "5.00%", "ironing_speed": "5.0",
        }),
        source_paths={"printer": "printer.json", "process": "process.json", "filament": "filament.json"},
        source_hashes={"printer": "a" * 64, "process": "b" * 64, "filament": "c" * 64},
    )


def _configuration(*, plan=None, layout_options=None):
    selection = _selection()
    plan = plan or ExperimentService().create_initial("ironing", selection)
    return SavedExperimentConfiguration(
        config_id="config-1",
        experiment_id="experiment-1",
        revision_no=1,
        printer_id="printer-1",
        material_id="material-1",
        profile_selection=selection,
        plan=plan,
        layout_options=layout_options or default_layout_options(),
        created_at_utc="2026-10-10T12:00:00Z",
    )


class SavedExperimentConfigurationTests(unittest.TestCase):
    def test_configuration_roundtrip_preserves_candidate_map_and_canonical_hash(self):
        configuration = _configuration()

        restored = SavedExperimentConfiguration.from_dict(configuration.to_dict())

        self.assertEqual(restored, configuration)
        self.assertEqual(restored.input_sha256, configuration.input_sha256)
        self.assertEqual(
            [configuration.sample_label_for(item.candidate_id) for item in configuration.plan.candidates],
            [f"Sample-{label}" for label in "ABCDEFGHI"],
        )
        self.assertEqual(configuration.source_profile_hashes["process"], "b" * 64)

    def test_unknown_configuration_schema_is_rejected(self):
        payload = _configuration().to_dict()
        payload["schema_version"] = 2

        with self.assertRaisesRegex(ExperimentConfigurationError, "unsupported"):
            SavedExperimentConfiguration.from_dict(payload)

    def test_changed_snapshot_is_rejected_when_canonical_hash_no_longer_matches(self):
        payload = _configuration().to_dict()
        payload["profile_selection"]["source_hashes"]["process"] = "d" * 64

        with self.assertRaisesRegex(ExperimentConfigurationError, "hash"):
            SavedExperimentConfiguration.from_dict(payload)

    def test_unordered_sweep_is_rejected(self):
        selection = _selection()
        plan = create_grid_experiment(
            plan_id="unordered-plan",
            module_id="ironing",
            baseline_settings=dict(selection.process.settings),
            dimensions=(
                SweepDimension("ironing_flow", ("12.00%", "10.00%", "8.00%")),
                SweepDimension("ironing_speed", ("20.0", "30.0", "40.0")),
            ),
            candidate_prefix="I",
        )

        with self.assertRaisesRegex(ExperimentConfigurationError, "increasing"):
            _configuration(plan=plan)

    def test_unknown_layout_strategy_is_rejected(self):
        layout = default_layout_options()
        layout["strategy"] = "guessed-grid"

        with self.assertRaisesRegex(ExperimentConfigurationError, "strategy"):
            _configuration(layout_options=layout)


if __name__ == "__main__":
    unittest.main()
