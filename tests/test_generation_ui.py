"""Headless contracts for generation preview, worker events, and validation gates."""

from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
import queue
import threading
import time
import unittest

from calibrate3dp.app.services.experiment_service import ExperimentService
from calibrate3dp.app.models import ProfileSelection, SessionSnapshot
from calibrate3dp.profiles import ProfileDocument, ResolvedProfile

try:
    from calibrate3dp.app.pages.generation_page import GenerationPage
    from calibrate3dp.app.services.generation_port import (
        ExperimentPreview,
        GenerationEvent,
        GenerationState,
        ValidationState,
    )
except ImportError as exc:
    GenerationPage = None
    ExperimentPreview = None
    GenerationEvent = None
    GenerationState = None
    ValidationState = None
    _GENERATION_IMPORT_ERROR = str(exc)
else:
    _GENERATION_IMPORT_ERROR = None


class FakeDpg:
    """Record UI mutations and the thread that issued each Dear PyGui call."""

    def __init__(self):
        self.items = {}
        self.callbacks = {}
        self.call_threads = set()
        self.mvTable_SizingStretchProp = 0

    @contextmanager
    def _container(self, *args, **kwargs):
        self._record()
        tag = kwargs.get("tag")
        if tag:
            self.items[tag] = dict(kwargs)
        yield

    def group(self, *args, **kwargs):
        return self._container(*args, **kwargs)

    def child_window(self, *args, **kwargs):
        return self._container(*args, **kwargs)

    def add_child_window(self, **kwargs):
        self._add_widget(**kwargs)

    def table(self, *args, **kwargs):
        return self._container(*args, **kwargs)

    def table_row(self, *args, **kwargs):
        return self._container(*args, **kwargs)

    def collapsing_header(self, *args, **kwargs):
        return self._container(*args, **kwargs)

    def add_text(self, value="", **kwargs):
        self._record()
        tag = kwargs.get("tag")
        if tag:
            self.items[tag] = {"value": value, **kwargs}

    def add_button(self, *, label, **kwargs):
        self._record()
        tag = kwargs.get("tag")
        if tag:
            self.items[tag] = {"label": label, **kwargs}
            self.callbacks[tag] = kwargs.get("callback")

    def add_loading_indicator(self, **kwargs):
        self._add_widget(**kwargs)

    def add_progress_bar(self, **kwargs):
        self._add_widget(**kwargs)

    def add_input_text(self, **kwargs):
        self._add_widget(**kwargs)

    def add_table_column(self, **kwargs):
        self._record()

    def add_spacer(self, **kwargs):
        self._record()

    def _add_widget(self, **kwargs):
        self._record()
        tag = kwargs.get("tag")
        if tag:
            self.items[tag] = dict(kwargs)

    def set_value(self, tag, value):
        self._record()
        self.items[tag]["value"] = value

    def configure_item(self, tag, **kwargs):
        self._record()
        self.items.setdefault(tag, {}).update(kwargs)

    def does_item_exist(self, tag):
        return tag in self.items

    def delete_item(self, tag):
        self._record()
        self.items.pop(tag, None)

    def _record(self):
        self.call_threads.add(threading.get_ident())


class FakeHandle:
    def __init__(self, job_id="run-1"):
        self.job_id = job_id
        self.events = queue.Queue()

    def next_event(self, timeout=0.05):
        try:
            return self.events.get(timeout=timeout)
        except queue.Empty:
            return None

    def push(self, event):
        self.events.put(event)


class FakeGenerationService:
    def __init__(self, preview, events=(), *, start_gate=None):
        self.preview_value = preview
        self.initial_events = tuple(events)
        self.start_gate = start_gate
        self.handle = FakeHandle()
        self.started = threading.Event()
        self.cancelled_job_ids = []

    def preview(self, session, plan):
        return self.preview_value

    def start(self, session, plan):
        if self.start_gate is not None:
            self.start_gate.wait(timeout=2)
        self.started.set()
        for event in self.initial_events:
            self.handle.push(event)
        return self.handle

    def cancel(self, job_id):
        self.cancelled_job_ids.append(job_id)
        self.handle.push(
            GenerationEvent(
                state=GenerationState.CANCELED,
                phase="Canceled by user",
                stdout_delta="Cancellation acknowledged.\n",
                artifact_paths=("runs/run-1/partial.gcode",),
            )
        )


class GenerationUiTests(unittest.TestCase):
    def setUp(self):
        self.experiment_service = ExperimentService()
        self.profiles = _profiles()
        self.plan = self.experiment_service.create_initial("ironing", self.profiles)
        timestamp = datetime.now(timezone.utc).isoformat()
        self.session = SessionSnapshot(
            session_id="generation-test",
            created_at_utc=timestamp,
            updated_at_utc=timestamp,
            module_id=self.plan.module_id,
            current_step="generation",
            profile_selection=self.profiles,
            plan=self.plan,
            status="ready",
        )
        self.dpg = FakeDpg()

    def _require_api(self):
        if GenerationPage is None:
            self.fail(f"generation UI is not implemented yet: {_GENERATION_IMPORT_ERROR}")

    def _make_page(self, service=None, *, on_recovery=None, session=None):
        self._require_api()
        page = GenerationPage(
            self.experiment_service,
            self.dpg,
            generation_service=service,
            on_recovery=on_recovery,
        )
        page.set_context(session=session or self.session, plan=self.plan, profiles=self.profiles)
        page.render()
        return page

    def _preview(self):
        review = self.experiment_service.review(self.plan, self.profiles)
        return ExperimentPreview.from_review(review)

    def _wait_for_pending(self, page, count=1):
        deadline = time.monotonic() + 2
        while time.monotonic() < deadline and page.pending_event_count < count:
            time.sleep(0.005)
        self.assertGreaterEqual(page.pending_event_count, count, "worker did not publish the expected events")

    def test_generation_page_disables_start_when_orca_unavailable(self):
        page = self._make_page(service=None)

        self.assertFalse(page.can_start)
        self.assertFalse(self.dpg.items["generation_start"]["enabled"])
        self.assertEqual(page.preview.plate_count, 9)
        self.assertIsNone(page.preview.estimated_duration)
        self.assertIn("slicing service", page.unavailable_reason.casefold())

    def test_running_job_keeps_window_responsive(self):
        self._require_api()
        gate = threading.Event()
        service = FakeGenerationService(self._preview(), start_gate=gate)
        page = self._make_page(service)

        started_at = time.monotonic()
        self.assertTrue(page.start())
        elapsed = time.monotonic() - started_at
        try:
            self.assertLess(elapsed, 0.1)
            self.assertTrue(service.started.wait(timeout=1) is False)
            self.assertEqual(page.state, GenerationState.QUEUED)
            self.assertTrue(self.dpg.items["generation_cancel"]["enabled"])
        finally:
            gate.set()
            page.close()

    def test_generation_ui_applies_worker_events_on_ui_loop(self):
        self._require_api()
        events = (
            GenerationEvent(state=GenerationState.RUNNING, phase="Slicing candidate 1 of 9", stdout_delta="slice started\n"),
            GenerationEvent(
                state=GenerationState.SUCCEEDED,
                phase="All candidates sliced",
                stdout_delta="done\n",
                validation_state=ValidationState.VALID,
                validation_messages=("G-code checks passed.",),
                artifact_paths=("runs/run-1/manifest.json",),
            ),
        )
        service = FakeGenerationService(self._preview(), events)
        page = self._make_page(service)
        ui_thread = threading.get_ident()

        self.assertTrue(page.start())
        self._wait_for_pending(page, 2)
        self.assertEqual(page.stdout, "")
        self.assertEqual(self.dpg.call_threads, {ui_thread})

        applied = page.poll_events()

        self.assertEqual(applied, 2)
        self.assertEqual(page.state, GenerationState.SUCCEEDED)
        self.assertEqual(page.stdout, "slice started\ndone\n")
        self.assertEqual(page.validation_state, ValidationState.VALID)
        self.assertEqual(self.dpg.call_threads, {ui_thread})
        page.close()

    def test_cancel_preserves_logs_and_marks_partial_output_invalid(self):
        self._require_api()
        service = FakeGenerationService(self._preview())
        page = self._make_page(service)
        self.assertTrue(page.start())
        self.assertTrue(service.started.wait(timeout=1))
        service.handle.push(
            GenerationEvent(
                state=GenerationState.RUNNING,
                phase="Slicing candidate 3 of 9",
                stdout_delta="candidate 3 started\n",
                stderr_delta="warning from Orca\n",
                artifact_paths=("runs/run-1/candidate-1.gcode",),
            )
        )
        self._wait_for_pending(page)
        page.poll_events()

        self.assertTrue(page.cancel())
        self._wait_for_pending(page)
        page.poll_events()

        self.assertEqual(service.cancelled_job_ids, ["run-1"])
        self.assertEqual(page.state, GenerationState.CANCELED)
        self.assertIn("candidate 3 started", page.stdout)
        self.assertIn("warning from Orca", page.stderr)
        self.assertTrue(page.partial_outputs_invalid)
        self.assertFalse(page.can_open_results)
        self.assertIn("runs/run-1/candidate-1.gcode", page.artifact_paths)
        self.assertIn("runs/run-1/partial.gcode", page.artifact_paths)
        page.close()

    def test_failure_shows_recovery_action(self):
        self._require_api()
        recovered = []
        service = FakeGenerationService(
            self._preview(),
            (
                GenerationEvent(state=GenerationState.RUNNING, phase="Slicing", stderr_delta="profile rejected\n"),
                GenerationEvent(state=GenerationState.FAILED, phase="Orca exited with code 1", exit_code=1),
            ),
        )
        page = self._make_page(service, on_recovery=lambda: recovered.append(True))

        self.assertTrue(page.start())
        self._wait_for_pending(page, 2)
        page.poll_events()

        self.assertTrue(page.recovery_available)
        self.assertTrue(self.dpg.items["generation_recovery"]["show"])
        self.assertIn("partial", page.recovery_message.casefold())
        self.dpg.callbacks["generation_recovery"](None, None)
        self.assertEqual(recovered, [True])
        self.assertFalse(page.can_open_results)
        page.close()

    def test_results_step_requires_successful_validation(self):
        self._require_api()
        invalid_service = FakeGenerationService(
            self._preview(),
            (
                GenerationEvent(
                    state=GenerationState.SUCCEEDED,
                    phase="Slicing complete",
                    validation_state=ValidationState.INVALID,
                    validation_messages=("Bed bounds check failed.",),
                ),
            ),
        )
        invalid_page = self._make_page(invalid_service)
        self.assertFalse(invalid_page.can_open_results)
        self.assertTrue(invalid_page.start())
        self._wait_for_pending(invalid_page)
        invalid_page.poll_events()
        self.assertEqual(invalid_page.state, GenerationState.SUCCEEDED)
        self.assertFalse(invalid_page.can_open_results)
        self.assertFalse(self.dpg.items["generation_results"]["enabled"])
        invalid_page.close()

        valid_dpg = FakeDpg()
        valid_service = FakeGenerationService(
            self._preview(),
            (
                GenerationEvent(
                    state=GenerationState.SUCCEEDED,
                    phase="G-code validated",
                    validation_state=ValidationState.VALID,
                    validation_messages=("All configured checks passed.",),
                ),
            ),
        )
        valid_page = GenerationPage(
            self.experiment_service,
            valid_dpg,
            generation_service=valid_service,
        )
        valid_page.set_context(session=self.session, plan=self.plan, profiles=self.profiles)
        valid_page.render()
        self.assertTrue(valid_page.start())
        self._wait_for_pending(valid_page)
        valid_page.poll_events()
        self.assertTrue(valid_page.can_open_results)
        self.assertTrue(valid_dpg.items["generation_results"]["enabled"])
        valid_page.close()


def _profiles():
    printer = ProfileDocument("Printer", "machine", "fixture", {"type": "machine", "name": "Printer"}, "printer.json")
    filament = ProfileDocument("PLA", "filament", "fixture", {"type": "filament", "name": "PLA"}, "filament.json")
    process = ProfileDocument(
        "Fine",
        "process",
        "fixture",
        {"type": "process", "name": "Fine", "ironing_flow": "10", "ironing_speed": "30", "ironing_type": "top"},
        "process.json",
    )
    return ProfileSelection(
        printer=ResolvedProfile(printer, printer.raw, {}, (printer,)),
        filament=ResolvedProfile(filament, filament.raw, {}, (filament,)),
        process=ResolvedProfile(
            process,
            process.raw,
            {key: process for key in process.raw},
            (process,),
        ),
    )


if __name__ == "__main__":
    unittest.main()
