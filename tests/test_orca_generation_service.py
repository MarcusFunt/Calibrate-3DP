import json
from pathlib import Path
import tempfile
from threading import Event
import time
import unittest
from dataclasses import replace

from calibrate3dp.app.models import ProfileSelection
from calibrate3dp.app.services.experiment_service import ExperimentService
from calibrate3dp.app.services.generation_port import GenerationState, ValidationState
from calibrate3dp.app.services.orca_generation_service import (
    OrcaGenerationError,
    OrcaGenerationService,
)
from calibrate3dp.app.services.profile_service import OrcaSetupState
from calibrate3dp.app.services.session_service import SessionService
from calibrate3dp.orca_cli import OrcaCliCapabilities, OrcaSliceResult
from calibrate3dp.profiles import ProfileCatalog, ProfileDocument
from calibrate3dp.storage.session_store import SessionRepository


class FakeOrcaCli:
    def __init__(self, *, missing_marker=False, wait_for_cancel=False):
        self.executable = Path("fake-orca")
        self.missing_marker = missing_marker
        self.wait_for_cancel = wait_for_cancel
        self.slice_started = Event()
        self.probe_calls = 0
        self.calls = []

    def probe(self, *, timeout_seconds=30):
        self.probe_calls += 1
        return OrcaCliCapabilities(
            executable=self.executable,
            version_banner="OrcaSlicer-test",
            options=frozenset({
                "--slice", "--outputdir", "--datadir", "--load-settings", "--load-filaments",
            }),
            help_returncode=0,
        )

    def run_slice(
        self,
        *,
        model_path,
        machine_process_profiles,
        filament_profiles,
        output_dir,
        data_dir,
        timeout_seconds,
        cancel_event=None,
        output_callback=None,
    ):
        self.slice_started.set()
        if self.wait_for_cancel and cancel_event is not None:
            cancel_event.wait(timeout=3)
            if cancel_event.is_set():
                return OrcaSliceResult(
                    argv=("fake-orca", "--slice", "0"),
                    started_at="2026-10-09T00:00:00+00:00",
                    finished_at="2026-10-09T00:00:01+00:00",
                    returncode=-15,
                    timed_out=False,
                    stdout="partial slice\n",
                    stderr="",
                    gcode_files=(),
                    cancelled=True,
                )
        process = json.loads(Path(machine_process_profiles[-1]).read_text(encoding="utf-8"))
        output = Path(output_dir)
        gcode = output / "plate_1.gcode"
        marker = "" if self.missing_marker else ";TYPE:Ironing\n"
        gcode.write_text(
            f"; ironing_flow = {process['ironing_flow']}\n"
            f"; ironing_speed = {process['ironing_speed']}\n"
            f"; ironing_type = {process['ironing_type']}\n"
            f"{marker}G1 X10 Y10 E0.1\n",
            encoding="utf-8",
        )
        if output_callback is not None:
            output_callback("stdout", "slice complete\n")
        self.calls.append((Path(model_path), output, Path(data_dir)))
        return OrcaSliceResult(
            argv=("fake-orca", "--slice", "0"),
            started_at="2026-10-09T00:00:00+00:00",
            finished_at="2026-10-09T00:00:01+00:00",
            returncode=0,
            timed_out=False,
            stdout="slice complete\n",
            stderr="",
            gcode_files=(gcode,),
            cancelled=bool(cancel_event and cancel_event.is_set()),
        )


class OrcaGenerationServiceTests(unittest.TestCase):
    def setUp(self):
        documents = [
            ProfileDocument(
                "Printer", "machine", "test",
                {"type": "machine", "name": "Printer", "bed_shape": [[0, 0], [220, 220]]},
                "machine.json",
            ),
            ProfileDocument(
                "Process", "process", "test",
                {
                    "type": "process", "name": "Process", "ironing_type": "top",
                    "ironing_flow": "10", "ironing_speed": "30", "layer_height": "0.2",
                },
                "process.json",
            ),
            ProfileDocument(
                "PLA", "filament", "test",
                {"type": "filament", "name": "PLA", "temperature": ["210"]},
                "filament.json",
            ),
        ]
        catalog = ProfileCatalog(documents)
        selection = ProfileSelection(
            printer=catalog.resolve("machine", "test", "Printer"),
            process=catalog.resolve("process", "test", "Process"),
            filament=catalog.resolve("filament", "test", "PLA"),
        )
        self.experiment_service = ExperimentService()
        self.plan = self.experiment_service.create_initial(
            "ironing", selection, {"plan_id": "service-test"}
        )
        self.temp = tempfile.TemporaryDirectory()
        self.repository = SessionRepository(Path(self.temp.name) / "workspace")
        self.session_service = SessionService(self.repository)
        session = self.session_service.create_session(selection, "ironing")
        self.session = replace(
            session,
            plan=self.plan,
            current_step="generation",
            status="ready_to_print",
        )
        self.session_service.save(self.session)

    def tearDown(self):
        self.temp.cleanup()

    def service(self, cli, *, setup_state_provider=None):
        return OrcaGenerationService(
            cli_provider=lambda: cli,
            experiment_service=self.experiment_service,
            session_service=self.session_service,
            setup_state_provider=setup_state_provider,
        )

    def terminal_event(self, handle):
        deadline = time.monotonic() + 5
        terminal = {GenerationState.SUCCEEDED, GenerationState.FAILED, GenerationState.CANCELED}
        while time.monotonic() < deadline:
            event = handle.next_event(timeout=0.2)
            if event is not None and event.state in terminal:
                return event
        self.fail("generation service did not emit a terminal event")

    def test_preview_probes_cli_and_success_persists_valid_gcode_artifacts(self):
        cli = FakeOrcaCli()
        service = self.service(cli)

        preview = service.preview(self.session, self.plan)
        self.assertEqual(preview.plan_id, self.plan.plan_id)
        self.assertEqual(len(preview.candidate_map), 9)
        self.assertFalse(preview.estimates_verified)

        handle = service.start(self.session, self.plan)
        event = self.terminal_event(handle)
        saved = self.session_service.resume(self.session.session_id)

        self.assertEqual(event.state, GenerationState.SUCCEEDED)
        self.assertEqual(event.validation_state, ValidationState.VALID)
        self.assertEqual(len(cli.calls), 9)
        self.assertEqual(saved.status, "awaiting_results")
        self.assertEqual(saved.current_step, "results")
        self.assertEqual(saved.run_ids, (handle.job_id,))
        self.assertIn(f"runs/{handle.job_id}/manifest.json", saved.artifact_paths)
        gcode_paths = [path for path in saved.artifact_paths if path.endswith(".gcode")]
        self.assertEqual(len(gcode_paths), 9)
        self.assertTrue(all((self.repository.sessions_root / saved.session_id / path).is_file() for path in gcode_paths))

    def test_preview_keeps_start_gated_when_no_cli_is_selected(self):
        service = self.service(None)
        with self.assertRaisesRegex(OrcaGenerationError, "Select an Orca CLI"):
            service.preview(self.session, self.plan)

    def test_preview_uses_the_setup_failure_instead_of_blocking_on_a_second_probe(self):
        cli = FakeOrcaCli()
        state = OrcaSetupState(
            executable=cli.executable,
            config_roots=(),
            last_checked_at_utc="2026-10-09T00:00:00+00:00",
            cli_status="CLI check failed",
            error="OrcaSlicer --help returned no text",
        )
        service = self.service(cli, setup_state_provider=lambda: state)

        with self.assertRaisesRegex(OrcaGenerationError, "returned no text"):
            service.preview(self.session, self.plan)
        self.assertEqual(cli.probe_calls, 0)

    def test_missing_ironing_toolpath_fails_validation_and_keeps_artifacts_invalid(self):
        service = self.service(FakeOrcaCli(missing_marker=True))
        handle = service.start(self.session, self.plan)
        event = self.terminal_event(handle)
        saved = self.session_service.resume(self.session.session_id)

        self.assertEqual(event.state, GenerationState.FAILED)
        self.assertEqual(event.validation_state, ValidationState.INVALID)
        self.assertIn("toolpath section", " ".join(event.validation_messages))
        self.assertEqual(saved.status, "generation_failed")
        self.assertIn(f"runs/{handle.job_id}/manifest.json", saved.artifact_paths)

    def test_cancelled_job_is_persisted_as_invalid_partial_output(self):
        cli = FakeOrcaCli(wait_for_cancel=True)
        service = self.service(cli)
        handle = service.start(self.session, self.plan)
        self.assertTrue(cli.slice_started.wait(timeout=2))
        service.cancel(handle.job_id)
        event = self.terminal_event(handle)
        saved = self.session_service.resume(self.session.session_id)

        self.assertEqual(event.state, GenerationState.CANCELED)
        self.assertEqual(event.validation_state, ValidationState.INVALID)
        self.assertEqual(saved.status, "generation_cancelled")
        self.assertIn(f"runs/{handle.job_id}/manifest.json", saved.artifact_paths)


if __name__ == "__main__":
    unittest.main()
