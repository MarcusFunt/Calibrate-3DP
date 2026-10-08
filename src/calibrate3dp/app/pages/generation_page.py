"""Generation preview and progress UI with a render-loop event boundary."""

from __future__ import annotations

from queue import Empty, Queue
import threading
from typing import Any, Callable

from calibrate3dp.app.models import ProfileSelection, SessionSnapshot
from calibrate3dp.app.services.experiment_service import ExperimentService
from calibrate3dp.app.services.generation_port import (
    ExperimentPreview,
    GenerationEvent,
    GenerationService,
    GenerationState,
    ValidationState,
)
from calibrate3dp.experiments import ExperimentPlan
from calibrate3dp.app.widgets.job_log_panel import JobLogPanel


_TERMINAL_STATES = frozenset({GenerationState.SUCCEEDED, GenerationState.FAILED, GenerationState.CANCELED})
_ACTIVE_STATES = frozenset({GenerationState.QUEUED, GenerationState.RUNNING})


class GenerationPage:
    """Show an exact generation preview and apply worker events on the UI loop."""

    def __init__(
        self,
        experiment_service: ExperimentService,
        dpg: Any,
        *,
        generation_service: GenerationService | None = None,
        on_recovery: Callable[[], None] | None = None,
        on_results: Callable[[], None] | None = None,
    ) -> None:
        self.experiment_service = experiment_service
        self.dpg = dpg
        self.generation_service = generation_service
        self.on_recovery = on_recovery
        self.on_results = on_results
        self.session: SessionSnapshot | None = None
        self.plan: ExperimentPlan | None = None
        self.profiles: ProfileSelection | None = None
        self.preview: ExperimentPreview | None = None
        self.preview_error: str | None = None
        self.state: GenerationState | None = None
        self.phase = "Review the candidate map before generation."
        self.validation_state = ValidationState.NOT_RUN
        self.validation_messages: tuple[str, ...] = ()
        self.progress_percent: float | None = None
        self.artifact_paths: list[str] = []
        self.invalid_artifact_paths: list[str] = []
        self._pending: Queue[GenerationEvent] = Queue()
        self._cancel_requested = threading.Event()
        self._stop_worker = threading.Event()
        self._worker: threading.Thread | None = None
        self._rendered = False
        self._closed = False
        self._logs = JobLogPanel(dpg)

    @property
    def stdout(self) -> str:
        return self._logs.stdout

    @property
    def stderr(self) -> str:
        return self._logs.stderr

    @property
    def pending_event_count(self) -> int:
        """Number of updates awaiting application on the render loop."""
        return self._pending.qsize()

    @property
    def unavailable_reason(self) -> str:
        if self.generation_service is None:
            return "The Orca slicing service is not available; generation stays disabled."
        if self.session is None:
            return "No saved session is connected; generation needs a durable artifact destination."
        if self.preview_error:
            return self.preview_error
        if self.plan is None or self.profiles is None:
            return "Accept a reviewed candidate grid before starting generation."
        return ""

    @property
    def can_start(self) -> bool:
        return (
            not self._closed
            and self.generation_service is not None
            and self.session is not None
            and self.plan is not None
            and self.profiles is not None
            and self.preview is not None
            and self.preview_error is None
            and self.state not in _ACTIVE_STATES
        )

    @property
    def can_open_results(self) -> bool:
        return self.state is GenerationState.SUCCEEDED and self.validation_state is ValidationState.VALID

    @property
    def partial_outputs_invalid(self) -> bool:
        return bool(self.invalid_artifact_paths)

    @property
    def recovery_available(self) -> bool:
        return self.state is GenerationState.FAILED

    @property
    def recovery_message(self) -> str:
        if not self.recovery_available:
            return ""
        return "Review Orca setup and the saved logs, then start a fresh run. Partial outputs remain invalid."

    def set_context(
        self,
        *,
        session: SessionSnapshot | None,
        plan: ExperimentPlan,
        profiles: ProfileSelection,
    ) -> None:
        """Set the accepted plan and build a preview without running Orca."""
        if self.state in _ACTIVE_STATES:
            raise RuntimeError("cannot replace the experiment while a generation job is active")
        self.session = session
        self.plan = plan
        self.profiles = profiles
        self.preview_error = None
        try:
            review = self.experiment_service.review(plan, profiles)
            preview = ExperimentPreview.from_review(review)
            if self.generation_service is not None and session is not None:
                preview = self.generation_service.preview(session, plan)
                if not isinstance(preview, ExperimentPreview):
                    raise TypeError("generation preview service returned an unsupported value")
                if preview.plan_id != plan.plan_id:
                    raise ValueError("generation preview does not match the accepted plan")
                expected_ids = tuple(candidate.candidate_id for candidate in plan.candidates)
                preview_ids = tuple(item.candidate_id for item in preview.candidate_map)
                if preview_ids != expected_ids:
                    raise ValueError("generation preview candidate map does not match the accepted plan")
            self.preview = preview
        except Exception as exc:
            self.preview = None
            self.preview_error = f"Generation preview is unavailable: {exc}"
        self.phase = "Review the candidate map before generation."
        if self._rendered:
            self._render_candidate_map()
            self._update_ui()

    def render(self) -> None:
        """Create all generation controls; callbacks only run on Dear PyGui's UI loop."""
        dpg = self.dpg
        with dpg.group(tag="generation_panel", show=False):
            dpg.add_spacer(height=14)
            dpg.add_text("GENERATE AND VALIDATE", color=(92, 191, 178, 255))
            dpg.add_text("", tag="generation_preview_summary", wrap=850)
            dpg.add_text("", tag="generation_preview_warnings", wrap=850,
                         color=(225, 180, 112, 255))
            dpg.add_child_window(tag="generation_candidate_map_area", width=-1, height=230, border=True)
            dpg.add_spacer(height=8)
            dpg.add_text("", tag="generation_availability", wrap=850,
                         color=(225, 180, 112, 255))
            dpg.add_text(self.phase, tag="generation_phase", wrap=850)
            dpg.add_loading_indicator(tag="generation_indeterminate", show=False)
            dpg.add_progress_bar(tag="generation_progress", default_value=0.0, show=False, width=-1)
            dpg.add_text("Not validated", tag="generation_validation", wrap=850)
            dpg.add_text("", tag="generation_recovery_message", wrap=850,
                         color=(235, 130, 125, 255))
            with dpg.group(horizontal=True):
                dpg.add_button(label="Start generation", tag="generation_start", enabled=False,
                               callback=self._on_start)
                dpg.add_button(label="Cancel job", tag="generation_cancel", enabled=False,
                               callback=self._on_cancel)
                dpg.add_button(label="Open Results", tag="generation_results", enabled=False,
                               callback=self._on_results)
                dpg.add_button(label="Review Orca setup", tag="generation_recovery", show=False,
                               callback=self._on_recovery)
            dpg.add_spacer(height=8)
            self._logs.render()
        self._rendered = True
        if self.preview is not None:
            self._render_candidate_map()
        self._update_ui()

    def _render_candidate_map(self) -> None:
        if self.preview is None or not self.dpg.does_item_exist("generation_candidate_map_area"):
            return
        if self.dpg.does_item_exist("generation_candidate_table"):
            self.dpg.delete_item("generation_candidate_table")
        with self.dpg.table(
            parent="generation_candidate_map_area",
            tag="generation_candidate_table",
            header_row=True,
            borders_innerH=True,
            borders_outerH=True,
            borders_innerV=True,
            borders_outerV=True,
            row_background=True,
            resizable=True,
            policy=self.dpg.mvTable_SizingStretchProp,
        ):
            for label in ("Candidate", "ironing_flow", "ironing_speed", "Plate", "Specimen"):
                self.dpg.add_table_column(label=label)
            for item in self.preview.candidate_map:
                with self.dpg.table_row():
                    self.dpg.add_text(item.candidate_id)
                    self.dpg.add_text(str(item.flow_value))
                    self.dpg.add_text(str(item.speed_value))
                    self.dpg.add_text(item.plate_label)
                    self.dpg.add_text(item.specimen_label)

    def start(self) -> bool:
        """Launch the injected worker on a daemon thread and return immediately."""
        if not self.can_start:
            return False
        assert self.generation_service is not None
        assert self.session is not None
        assert self.plan is not None
        if self.state is not None:
            self._logs.add_attempt_separator("Starting a fresh generation attempt; earlier logs and artifacts are retained")
        while True:
            try:
                self._pending.get_nowait()
            except Empty:
                break
        self._cancel_requested.clear()
        self._stop_worker.clear()
        self.state = GenerationState.QUEUED
        self.phase = "Preparing the generation job…"
        self.validation_state = ValidationState.NOT_RUN
        self.validation_messages = ()
        self.progress_percent = None
        self._update_ui()
        self._worker = threading.Thread(target=self._run_worker, name="calibrate3dp-generation", daemon=True)
        self._worker.start()
        return True

    def _run_worker(self) -> None:
        """Consume service events off-thread and publish immutable values only."""
        service = self.generation_service
        session = self.session
        plan = self.plan
        if service is None or session is None or plan is None:
            return
        try:
            handle = service.start(session, plan)
            if not isinstance(handle.job_id, str) or not handle.job_id.strip():
                raise ValueError("generation service returned an empty job id")
            cancel_sent = False
            while True:
                if self._cancel_requested.is_set() and not cancel_sent:
                    service.cancel(handle.job_id)
                    cancel_sent = True
                if self._stop_worker.is_set() and cancel_sent:
                    return
                event = handle.next_event(timeout=0.05)
                if event is None:
                    continue
                if not isinstance(event, GenerationEvent):
                    raise TypeError("generation service returned an unsupported event")
                self._pending.put(event)
                if event.state in _TERMINAL_STATES:
                    return
        except Exception as exc:
            self._pending.put(
                GenerationEvent(
                    state=GenerationState.FAILED,
                    phase="Generation service failed",
                    stderr_delta=f"{exc}\n",
                    validation_state=ValidationState.INVALID,
                    validation_messages=(str(exc),),
                )
            )

    def cancel(self) -> bool:
        """Request cancellation; the worker calls the service away from the UI loop."""
        if self.state not in _ACTIVE_STATES or self._cancel_requested.is_set():
            return False
        self._cancel_requested.set()
        self.phase = "Cancellation requested; preserving logs and partial output…"
        self._update_ui()
        return True

    def poll_events(self) -> int:
        """Apply queued worker updates on the caller, which must be the UI loop."""
        if self._closed:
            return 0
        applied = 0
        while True:
            try:
                event = self._pending.get_nowait()
            except Empty:
                break
            self._apply_event(event)
            applied += 1
        return applied

    def _apply_event(self, event: GenerationEvent) -> None:
        self.state = event.state
        if event.phase:
            self.phase = event.phase
        self._logs.append(event.stdout_delta, event.stderr_delta)
        for path in event.artifact_paths:
            if path not in self.artifact_paths:
                self.artifact_paths.append(path)
        if event.validation_state is not None:
            self.validation_state = event.validation_state
        self.validation_messages = event.validation_messages
        self.progress_percent = event.progress_percent
        if event.state in {GenerationState.FAILED, GenerationState.CANCELED}:
            self.validation_state = ValidationState.INVALID
            self._mark_artifacts_invalid()
        elif event.state is GenerationState.SUCCEEDED and self.validation_state is not ValidationState.VALID:
            self._mark_artifacts_invalid()
        self._update_ui()

    def _mark_artifacts_invalid(self) -> None:
        self.invalid_artifact_paths.extend(
            path for path in self.artifact_paths if path not in self.invalid_artifact_paths
        )

    def close(self) -> None:
        """Stop UI delivery and request service cancellation for an active job."""
        if self._closed:
            return
        if self.state in _ACTIVE_STATES:
            self._cancel_requested.set()
            self._stop_worker.set()
        self._closed = True

    def _update_ui(self) -> None:
        if not self._rendered or self._closed:
            return
        dpg = self.dpg
        dpg.set_value("generation_preview_summary", self._preview_summary())
        dpg.set_value("generation_preview_warnings", self._preview_warnings())
        dpg.set_value("generation_availability", self.unavailable_reason)
        dpg.set_value("generation_phase", self.phase)
        dpg.set_value("generation_validation", self._validation_summary())
        dpg.set_value("generation_recovery_message", self.recovery_message)
        dpg.configure_item("generation_start", enabled=self.can_start)
        dpg.configure_item("generation_cancel", enabled=self.state in _ACTIVE_STATES and not self._cancel_requested.is_set())
        dpg.configure_item("generation_results", enabled=self.can_open_results)
        dpg.configure_item("generation_recovery", show=self.recovery_available,
                           enabled=self.recovery_available)
        indeterminate = self.state in _ACTIVE_STATES and self.progress_percent is None
        dpg.configure_item("generation_indeterminate", show=indeterminate)
        dpg.configure_item("generation_progress", show=self.state in _ACTIVE_STATES and self.progress_percent is not None)
        if self.progress_percent is not None:
            dpg.set_value("generation_progress", self.progress_percent / 100.0)

    def _preview_summary(self) -> str:
        if self.preview is None:
            return "Candidate map will appear after the experiment grid is accepted."
        estimate = "No verified time or material estimate is available."
        if self.preview.estimates_verified:
            pieces = []
            if self.preview.estimated_duration:
                pieces.append(f"Duration: {self.preview.estimated_duration}")
            if self.preview.estimated_material:
                pieces.append(f"Material: {self.preview.estimated_material}")
            estimate = " · ".join(pieces) or "No estimates provided."
        return (
            f"Plan {self.preview.plan_id} · {len(self.preview.candidate_map)} candidates · "
            f"{self.preview.plate_count} separate plates\n{estimate}"
        )

    def _preview_warnings(self) -> str:
        warnings = list(self.preview.warnings) if self.preview is not None else []
        if self.preview_error:
            warnings.insert(0, self.preview_error)
        return "\n".join(f"• {item}" for item in warnings)

    def _validation_summary(self) -> str:
        if self.state in {GenerationState.FAILED, GenerationState.CANCELED}:
            heading = f"INVALID · {self.state.value.upper()} output cannot be printed."
        elif self.validation_state is ValidationState.VALID:
            heading = "VALID · configured G-code checks passed. Inspect the G-code in Orca and print manually."
        elif self.validation_state is ValidationState.INVALID:
            heading = "INVALID · output failed validation and cannot be printed."
        else:
            heading = "Not validated · Results stays locked until a successful validation."
        if self.partial_outputs_invalid:
            heading += f" {len(self.invalid_artifact_paths)} partial artifact(s) are retained as invalid."
        if self.validation_messages:
            heading += "\n" + "\n".join(self.validation_messages)
        return heading

    def _on_start(self, sender: Any, app_data: Any, user_data: Any = None) -> None:
        del sender, app_data, user_data
        self.start()

    def _on_cancel(self, sender: Any, app_data: Any, user_data: Any = None) -> None:
        del sender, app_data, user_data
        self.cancel()

    def _on_results(self, sender: Any, app_data: Any, user_data: Any = None) -> None:
        del sender, app_data, user_data
        if not self.can_open_results:
            return
        if self.on_results is not None:
            self.on_results()
        else:
            self.phase = "Generation is validated; results entry is the next workflow step."
            self._update_ui()

    def _on_recovery(self, sender: Any, app_data: Any, user_data: Any = None) -> None:
        del sender, app_data, user_data
        if self.recovery_available and self.on_recovery is not None:
            self.on_recovery()
