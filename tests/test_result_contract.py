"""Compatibility and validation tests for persisted print assessments."""

from __future__ import annotations

from datetime import datetime, timezone
from dataclasses import replace
from pathlib import Path
import tempfile
import unittest

from calibrate3dp.app.models import ProfileSelection, SessionSnapshot
from calibrate3dp.experiments import (
    CandidateAssessment,
    ExperimentResults,
    ExperimentStateError,
    SweepDimension,
    create_grid_experiment,
    ExperimentDefinitionError,
)
from calibrate3dp.profiles import ProfileDocument, ResolvedProfile
from calibrate3dp.storage.session_store import SessionRepository, UnsafeArtifactPathError


class ResultContractTests(unittest.TestCase):
    def test_version_1_results_still_load(self):
        api = self._require_version_2_fields()
        legacy_payload = {
            "schema_version": 1,
            "plan_id": "iron-results-v1",
            "assessments": [
                {
                    "candidate_id": "I001",
                    "ratings": {"finish": 4},
                    "defect_tags": ["edge_ridge"],
                    "notes": "legacy observation",
                }
            ],
            "selected_candidate_id": "I001",
            "accepted": False,
        }

        restored = api.ExperimentResults.from_dict(legacy_payload)

        self.assertIsNone(restored.assessments[0].verdict)
        self.assertEqual(restored.assessments[0].photo_paths, ())
        self.assertEqual(restored.tied_candidate_ids, ())
        self.assertEqual(restored.to_dict()["schema_version"], 2)

        legacy_payload["accepted"] = True
        legacy_payload["selected_candidate_id"] = "I001"
        reopened = api.ExperimentResults.from_dict(legacy_payload)
        self.assertIsNone(reopened.accepted, "legacy acceptance has no explicit outcome evidence")

    def test_version_2_round_trip_preserves_verdict_tie_and_photos(self):
        api = self._require_version_2_fields()
        plan = _make_plan("iron-results-v2")
        assessments = (
            api.CandidateAssessment(
                candidate_id="I001",
                ratings={"finish": 4},
                defect_tags=("edge_ridge",),
                notes="preferred candidate",
                verdict="pass",
                photo_paths=("evidence/photos/finish.png",),
            ),
            api.CandidateAssessment(
                candidate_id="I002",
                ratings={"finish": 4},
                verdict="pass",
                photo_paths=("evidence/photos/tie.jpg",),
            ),
        )
        results = api.ExperimentResults(
            plan_id=plan.plan_id,
            assessments=assessments,
            selected_candidate_id="I001",
            accepted=False,
            tied_candidate_ids=("I002",),
        )

        restored = api.ExperimentResults.from_dict(results.to_dict())
        restored.validate_for(plan)

        self.assertEqual(restored.to_dict()["schema_version"], 2)
        self.assertEqual(restored, results)

    def test_missing_specimen_cannot_have_numeric_ratings(self):
        api = self._require_version_2_fields()

        with self.assertRaises(ExperimentDefinitionError):
            api.CandidateAssessment(
                candidate_id="I001",
                ratings={"finish": 3},
                verdict="missing",
            )

    def test_uncertain_specimen_cannot_have_numeric_ratings(self):
        with self.assertRaises(ExperimentDefinitionError):
            CandidateAssessment("I001", ratings={"finish": 3}, verdict="uncertain")

    def test_tie_candidates_must_belong_to_plan(self):
        api = self._require_version_2_fields()
        plan = _make_plan("iron-tie-membership")
        results = api.ExperimentResults(
            plan_id=plan.plan_id,
            assessments=(
                api.CandidateAssessment("I001", verdict="pass"),
                api.CandidateAssessment("I002", verdict="pass"),
            ),
            selected_candidate_id="I001",
            tied_candidate_ids=("I999",),
        )

        with self.assertRaises(api.ExperimentStateError):
            results.validate_for(plan)

    def test_accepted_results_require_complete_explicit_outcomes(self):
        api = self._require_version_2_fields()
        plan = _make_plan("iron-accepted-evidence")
        results = api.ExperimentResults(
            plan_id=plan.plan_id,
            assessments=(CandidateAssessment("I001", verdict="pass"),),
            selected_candidate_id="I001",
            accepted=True,
        )

        with self.assertRaises(api.ExperimentStateError):
            results.validate_for(plan)

    def test_photo_paths_cannot_escape_session_root(self):
        api = self._require_version_2_fields()
        plan = _make_plan("iron-photo-path")
        profiles = _profiles()
        results = api.ExperimentResults(
            plan_id=plan.plan_id,
            assessments=(
                api.CandidateAssessment(
                    "I001",
                    verdict="pass",
                    photo_paths=("../outside.png",),
                ),
            ),
        )
        snapshot = _make_snapshot("photo-path-session", profiles, plan, results)

        with tempfile.TemporaryDirectory() as directory:
            repository = SessionRepository(Path(directory) / "workbench")
            with self.assertRaises(UnsafeArtifactPathError):
                repository.create(snapshot)

    def test_photo_is_copied_into_session_before_result_path_is_saved(self):
        plan = _make_plan("iron-photo-copy")
        profiles = _profiles()
        snapshot = _make_snapshot(
            "photo-copy-session", profiles, plan, ExperimentResults(plan.plan_id, ())
        )
        with tempfile.TemporaryDirectory() as directory:
            repository = SessionRepository(Path(directory) / "workbench")
            repository.create(snapshot)
            source = Path(directory) / "surface.png"
            source.write_bytes(b"photo evidence")

            relative_path = repository.copy_photo_to_session(snapshot.session_id, source)
            assessment = CandidateAssessment(
                "I001", verdict="pass", photo_paths=(relative_path,)
            )
            updated = replace(
                snapshot,
                results=ExperimentResults(plan.plan_id, (assessment,)),
            )
            repository.save(updated)

            stored_photo = Path(directory) / "workbench" / "sessions" / snapshot.session_id / relative_path
            self.assertEqual(stored_photo.read_bytes(), b"photo evidence")
            self.assertEqual(repository.load(snapshot.session_id).results.assessments[0].photo_paths,
                             (relative_path,))

    def _require_version_2_fields(self):
        self.assertIn("verdict", CandidateAssessment.__dataclass_fields__)
        self.assertIn("photo_paths", CandidateAssessment.__dataclass_fields__)
        self.assertIn("tied_candidate_ids", ExperimentResults.__dataclass_fields__)
        self.assertTrue(hasattr(ExperimentResults, "from_dict"))
        return type("ResultApi", (), {"CandidateAssessment": CandidateAssessment,
                                       "ExperimentResults": ExperimentResults,
                                       "ExperimentStateError": ExperimentStateError})


def _make_plan(plan_id: str):
    return create_grid_experiment(
        plan_id=plan_id,
        module_id="ironing",
        baseline_settings={"ironing_flow": "10", "ironing_speed": "30"},
        dimensions=(
            SweepDimension("ironing_flow", ("8", "10", "12")),
            SweepDimension("ironing_speed", ("20", "30", "40")),
        ),
        candidate_prefix="I",
    )


def _profiles() -> ProfileSelection:
    def profile(name: str, kind: str) -> ResolvedProfile:
        document = ProfileDocument(name, kind, "fixture", {"name": name, "type": kind}, f"{name}.json")
        return ResolvedProfile(document, document.raw, {}, (document,))

    return ProfileSelection(profile("Printer", "machine"), profile("PLA", "filament"), profile("Fine", "process"))


def _make_snapshot(
    session_id: str,
    profiles: ProfileSelection,
    plan,
    results: ExperimentResults,
) -> SessionSnapshot:
    timestamp = datetime.now(timezone.utc).isoformat()
    return SessionSnapshot(
        session_id=session_id,
        created_at_utc=timestamp,
        updated_at_utc=timestamp,
        module_id=plan.module_id,
        current_step="results",
        profile_selection=profiles,
        plan=plan,
        results=results,
        status="awaiting_results",
    )


if __name__ == "__main__":
    unittest.main()
