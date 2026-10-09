"""Reviewed preset export and evidence manifest tests."""

from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import tempfile
import unittest

from calibrate3dp.app.models import ProfileSelection, SessionSnapshot
from calibrate3dp.app.pages.export_page import ExportPage
from calibrate3dp.app.services.acceptance_service import AcceptanceService
from calibrate3dp.app.services.export_service import ExportService, ExportServiceError
from calibrate3dp.app.services.generation_port import GenerationState, ValidationState
from calibrate3dp.experiments import CandidateAssessment, ExperimentResults
from calibrate3dp.ironing import create_initial_ironing_experiment
from calibrate3dp.profiles import ProfileDocument, ResolvedProfile
from calibrate3dp.storage.session_store import SessionRepository


class FakeDpg:
    def __init__(self):
        self.items = {}
        self.callbacks = {}

    @contextmanager
    def group(self, *args, **kwargs):
        yield

    @contextmanager
    def file_dialog(self, *args, **kwargs):
        yield

    def add_spacer(self, **kwargs):
        return None

    def add_text(self, value="", **kwargs):
        tag = kwargs.get("tag")
        if tag:
            self.items[tag] = {"value": value, **kwargs}

    def add_input_text(self, *, label="", **kwargs):
        self._add_widget(label=label, **kwargs)

    def add_button(self, *, label, **kwargs):
        self._add_widget(label=label, **kwargs)

    def add_file_extension(self, *args, **kwargs):
        return None

    def _add_widget(self, **kwargs):
        tag = kwargs.get("tag")
        if tag:
            self.items[tag] = dict(kwargs)
            if kwargs.get("callback"):
                self.callbacks[tag] = kwargs["callback"]

    def set_value(self, tag, value):
        self.items.setdefault(tag, {})["value"] = value

    def get_value(self, tag):
        return self.items.get(tag, {}).get("value")

    def configure_item(self, tag, **kwargs):
        self.items.setdefault(tag, {}).update(kwargs)

    def does_item_exist(self, tag):
        return tag in self.items

    def show_item(self, tag):
        self.configure_item(tag, show=True)


class ReviewedExportTests(unittest.TestCase):
    def test_diff_contains_only_accepted_ironing_keys(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            session, service, _, _ = _fixture(root)

            draft = service.build_draft(session, new_profile_name="Calibrated Process")

            self.assertEqual(set(draft.setting_changes), {"ironing_flow", "ironing_speed"})
            self.assertEqual(draft.setting_changes["ironing_flow"]["old"], "8")
            self.assertEqual(draft.setting_changes["ironing_flow"]["new"], "10")
            self.assertEqual(draft.profile_payload["ironing_flow"], "10")
            self.assertEqual(draft.profile_payload["unknown_future_field"], {"keep": True})

    def test_original_profile_bytes_remain_unchanged(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            session, service, source_path, config_root = _fixture(root)
            original_bytes = source_path.read_bytes()
            destination = root / "exports" / "calibrated-process.json"
            destination.parent.mkdir()
            draft = service.build_draft(session, new_profile_name="Calibrated Process")

            with self.assertRaises(ExportServiceError):
                service.export(draft, config_root / "calibrated-process.json")
            service.export(draft, destination)

            self.assertEqual(source_path.read_bytes(), original_bytes)
            self.assertEqual(tuple(path.name for path in config_root.iterdir()), ("process.json",))
            self.assertTrue(destination.is_file())

    def test_export_requires_accepted_confirmation_result(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            session, service, _, _ = _fixture(root, confirmation=False)

            with self.assertRaises(ExportServiceError):
                service.build_draft(session, new_profile_name="Calibrated Process")

    def test_destination_is_user_selected(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            session, service, _, _ = _fixture(root)
            destination = root / "chosen" / "calibrated.json"
            destination.parent.mkdir()
            draft = service.build_draft(session, new_profile_name="Calibrated Process")

            result = service.export(draft, destination)

            self.assertEqual(result.profile_path, destination.resolve())
            with self.assertRaises(ExportServiceError):
                service.export(draft, None)

    def test_export_rejects_draft_if_orca_version_changes_after_review(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            session, fixture_service, _, config_root = _fixture(root)
            version = {"value": "OrcaSlicer 2.3.0"}
            service = ExportService(
                fixture_service.repository,
                protected_roots=(config_root,),
                orca_version_provider=lambda: version["value"],
            )
            draft = service.build_draft(session, new_profile_name="Calibrated Process")
            destination = root / "exports" / "calibrated.json"
            destination.parent.mkdir()
            version["value"] = "OrcaSlicer 2.4.0"

            with self.assertRaisesRegex(ExportServiceError, "setup changed"):
                service.export(draft, destination)

            self.assertFalse(destination.exists())
            self.assertFalse(destination.with_name("calibrated.manifest.json").exists())
            self.assertFalse(destination.with_name("calibrated.report.md").exists())

    def test_export_page_discards_stale_draft_after_orca_version_changes(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            session, fixture_service, _, config_root = _fixture(root)
            version = {"value": "OrcaSlicer 2.3.0"}
            service = ExportService(
                fixture_service.repository,
                protected_roots=(config_root,),
                orca_version_provider=lambda: version["value"],
            )
            dpg = FakeDpg()
            page = ExportPage(dpg, service)
            page.set_session(session)
            page.render()
            self.assertIsNotNone(page.build_draft("Calibrated Process"))
            destination = root / "exports" / "calibrated.json"
            destination.parent.mkdir()
            version["value"] = "OrcaSlicer 2.4.0"

            result = page.write_to_destination(destination)

            self.assertIsNone(result)
            self.assertIsNone(page.draft)
            self.assertIn("setup changed", page.error)
            self.assertFalse(destination.exists())
            self.assertIn("build a review draft", dpg.get_value("export_source").lower())

            current_draft = page.build_draft("Calibrated Process")
            self.assertEqual(current_draft.orca_version, "OrcaSlicer 2.4.0")
            current_destination = root / "exports" / "calibrated-current.json"
            current_result = page.write_to_destination(current_destination)

            self.assertIsNotNone(current_result)
            manifest = json.loads(current_result.manifest_path.read_text(encoding="utf-8"))
            self.assertEqual(manifest["orca_version"], "OrcaSlicer 2.4.0")

    def test_manifest_and_report_include_evidence_scope(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            session, service, _, _ = _fixture(root)
            destination = root / "exports" / "calibrated.json"
            destination.parent.mkdir()
            draft = service.build_draft(session, new_profile_name="Calibrated Process")

            result = service.export(draft, destination)
            manifest = json.loads(result.manifest_path.read_text(encoding="utf-8"))
            report = result.report_path.read_text(encoding="utf-8")

            self.assertEqual(manifest["session_id"], session.session_id)
            self.assertEqual(manifest["source_profile"]["name"], "Standard Process")
            self.assertIn("I005", manifest["candidate_ids"])
            self.assertIn("CONFIRM", manifest["candidate_ids"])
            self.assertIn("confirmation-run-1", manifest["supporting_run_ids"])
            self.assertTrue(manifest["report_paths"])
            self.assertIn("Printer compatibility was not verified.", manifest["compatibility_warnings"])
            self.assertIn("Evidence scope", report)
            self.assertIn("confirmation-run-1", report)

    def test_recorded_opt_out_can_be_exported_with_its_reason(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            session, service, _, _ = _fixture(root, confirmation=False)
            report_path = f"reports/recommendation-{hashlib.sha256(session.plan.plan_id.encode()).hexdigest()[:16]}.json"
            repository = service.repository
            repository.write_json_artifact(
                session.session_id,
                report_path,
                {
                    "schema_version": 1,
                    "plan_id": session.plan.plan_id,
                    "events": [
                        {
                            "type": "confirmation_opt_out",
                            "plan_id": session.plan.plan_id,
                            "candidate_id": "I005",
                            "reason": "The remaining spool is reserved for production.",
                        }
                    ],
                },
            )
            from dataclasses import replace

            session = replace(session, artifact_paths=(report_path,))
            repository.save(session)

            draft = service.build_draft(session, new_profile_name="Calibrated Process")

            self.assertEqual(draft.confirmation_status, "opted_out")
            self.assertEqual(
                draft.confirmation_reason,
                "The remaining spool is reserved for production.",
            )

    def test_import_bundle_export_stays_disabled_without_verified_adapter(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            session, service, _, _ = _fixture(root)
            page = ExportPage(FakeDpg(), service)
            page.set_session(session)
            page.build_draft("Calibrated Process")

            self.assertFalse(page.bundle_export_available)
            with self.assertRaises(ExportServiceError):
                service.export_bundle(page.draft, root / "calibrated.zip")

    def test_export_page_shows_diff_and_writes_to_browsed_destination(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            session, service, _, _ = _fixture(root)
            dpg = FakeDpg()
            page = ExportPage(dpg, service)
            page.set_session(session)
            page.render()

            self.assertEqual(
                dpg.get_value("export_profile_name"),
                "Standard Process - Calibrated",
            )
            self.assertIsNotNone(page.build_draft("Calibrated Process"))
            self.assertIn("ironing_flow: 8 → 10", dpg.get_value("export_changes"))
            destination = root / "exports" / "selected-process.json"
            destination.parent.mkdir()
            page._on_destination_selected(None, {"file_path_name": str(destination)})

            result = page.write_to_destination()

            self.assertIsNotNone(result)
            self.assertEqual(result.profile_path, destination.resolve())


def _fixture(
    root: Path,
    *,
    confirmation: bool = True,
    accepted: bool = True,
):
    config_root = root / "orca-config"
    config_root.mkdir()
    source_path = config_root / "process.json"
    raw_process = {
        "type": "process",
        "name": "Standard Process",
        "inherits": "Common Process",
        "ironing_flow": "8",
        "ironing_speed": "20",
        "ironing_type": "top",
        "unknown_future_field": {"keep": True},
    }
    source_path.write_text(json.dumps(raw_process), encoding="utf-8")
    process = _resolved("Standard Process", "process", raw_process, str(source_path))
    printer = _resolved("CoreXY", "machine", {"type": "machine", "name": "CoreXY"}, "printer.json")
    filament = _resolved("PLA", "filament", {"type": "filament", "name": "PLA"}, "filament.json")
    selection = ProfileSelection(
        printer,
        filament,
        process,
        source_paths={
            "printer": "printer.json",
            "filament": "filament.json",
            "process": str(source_path),
        },
        source_hashes={
            "printer": "p" * 64,
            "filament": "f" * 64,
            "process": hashlib.sha256(source_path.read_bytes()).hexdigest(),
        },
        compatibility_warnings=("Printer compatibility was not verified.",),
    )
    plan = create_initial_ironing_experiment(
        plan_id="export-plan",
        baseline_settings={
            "ironing_flow": "8",
            "ironing_speed": "20",
            "ironing_type": "top",
        },
        flow_values=(8, 10, 12),
        speed_values=(20, 30, 40),
    )
    assessments = tuple(
        CandidateAssessment(
            item.candidate_id,
            verdict="pass" if item.candidate_id == "I005" else "fail",
        )
        for item in plan.candidates
    )
    results = ExperimentResults(
        plan_id=plan.plan_id,
        assessments=assessments,
        selected_candidate_id="I005",
        accepted=accepted,
    )
    timestamp = datetime.now(timezone.utc).isoformat()
    session = SessionSnapshot(
        session_id="export-session",
        created_at_utc=timestamp,
        updated_at_utc=timestamp,
        module_id="ironing",
        current_step="export",
        profile_selection=selection,
        plan=plan,
        results=results,
        run_ids=("initial-run-1",),
        status="export_ready",
    )
    repository = SessionRepository(root / "workspace")
    repository.create(session)
    if confirmation:
        acceptance = AcceptanceService()
        confirmation_plan = acceptance.create_confirmation_plan(
            plan, results, plan_id="export-confirmation"
        )
        candidate_id = confirmation_plan.candidates[0].candidate_id
        report_path = f"reports/recommendation-{hashlib.sha256(plan.plan_id.encode()).hexdigest()[:16]}.json"
        repository.write_json_artifact(
            session.session_id,
            report_path,
            {
                "schema_version": 1,
                "plan_id": plan.plan_id,
                "events": [
                    {
                        "type": "confirmation_run",
                        "source_plan_id": plan.plan_id,
                        "source_candidate_id": "I005",
                        "plan_id": confirmation_plan.plan_id,
                        "candidate_id": candidate_id,
                        "run_id": "confirmation-run-1",
                        "state": GenerationState.SUCCEEDED.value,
                        "validation_state": ValidationState.VALID.value,
                        "settings": confirmation_plan.settings_for(candidate_id),
                    }
                ],
            },
        )
        from dataclasses import replace

        session = replace(session, artifact_paths=(report_path,))
        repository.save(session)
    acceptance_service = AcceptanceService()
    export_service = ExportService(
        repository,
        acceptance_service=acceptance_service,
        protected_roots=(config_root,),
    )
    return session, export_service, source_path, config_root


def _resolved(name, kind, raw, source):
    document = ProfileDocument(name, kind, "test", raw, source)
    return ResolvedProfile(document, raw, {}, (document,))


if __name__ == "__main__":
    unittest.main()
