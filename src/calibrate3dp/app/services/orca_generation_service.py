"""Persistent, cancellable Orca generation jobs for the desktop workbench."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from queue import Empty, Queue
from threading import Event, Lock, Thread
from typing import Any, Callable, cast
from uuid import uuid4

from calibrate3dp.app.models import SessionSnapshot
from calibrate3dp.app.services.experiment_service import ExperimentService
from calibrate3dp.app.services.generation_port import (
    ExperimentPreview,
    GenerationEvent,
    GenerationState,
    ValidationState,
)
from calibrate3dp.app.services.profile_service import OrcaSetupState
from calibrate3dp.app.services.session_service import SessionService
from calibrate3dp.experiments import ExperimentPlan
from calibrate3dp.orca_cli import OrcaCli
from calibrate3dp.orca_jobs import OrcaExperimentRun, slice_ironing_experiment
from calibrate3dp.storage.session_store import SessionRepository


class OrcaGenerationError(RuntimeError):
    """Raised when the saved session cannot run a safe Orca job."""


class _GenerationHandle:
    def __init__(self, job_id: str, events: Queue[GenerationEvent]) -> None:
        self._job_id = job_id
        self._events = events

    @property
    def job_id(self) -> str:
        return self._job_id

    def next_event(self, timeout: float = 0.1) -> GenerationEvent | None:
        try:
            return self._events.get(timeout=max(0.0, timeout))
        except Empty:
            return None


class OrcaGenerationService:
    """Adapt the tested Orca CLI job to the asynchronous desktop service port."""

    def __init__(
        self,
        *,
        cli_provider: Callable[[], Any | None],
        experiment_service: ExperimentService,
        session_service: SessionService,
        setup_state_provider: Callable[[], OrcaSetupState] | None = None,
        probe_timeout_seconds: float = 8,
        slice_timeout_seconds: float = 300,
    ) -> None:
        if not callable(cli_provider):
            raise TypeError("cli_provider must be callable")
        if not isinstance(experiment_service, ExperimentService):
            raise TypeError("experiment_service must be an ExperimentService")
        if not isinstance(session_service, SessionService):
            raise TypeError("session_service must be a SessionService")
        repository = session_service.repository
        if not isinstance(repository, SessionRepository):
            raise TypeError("session_service must use a SessionRepository")
        self._cli_provider = cli_provider
        self._experiment_service = experiment_service
        self._session_service = session_service
        self._repository = repository
        self._setup_state_provider = setup_state_provider
        self._probe_timeout_seconds = probe_timeout_seconds
        self._slice_timeout_seconds = slice_timeout_seconds
        self._jobs: dict[str, Event] = {}
        self._jobs_lock = Lock()

    def preview(self, session: SessionSnapshot, plan: ExperimentPlan) -> ExperimentPreview:
        """Probe required CLI options before enabling the Start action."""
        self._validate_session_plan(session, plan)
        cli = self._require_cli()
        setup_state = self._setup_state_provider() if self._setup_state_provider else None
        if (
            setup_state is not None
            and setup_state.last_checked_at_utc is not None
            and setup_state.executable == cli.executable
        ):
            if setup_state.cli_status != "CLI responds to the required probe":
                raise OrcaGenerationError(
                    setup_state.error or f"Orca CLI check failed: {setup_state.cli_status}"
                )
            version_banner = setup_state.version_banner
        else:
            capabilities = cli.probe(timeout_seconds=self._probe_timeout_seconds)
            version_banner = capabilities.version_banner
        review = self._experiment_service.review(plan, session.profile_selection)
        return ExperimentPreview(
            plan_id=review.plan.plan_id,
            candidate_map=review.plate_map,
            fixed_settings=review.fixed_settings,
            warnings=(
                *review.warnings,
                f"{version_banner or 'Orca CLI'} exposes the required slice options; "
                "printer and profile compatibility still require validation.",
            ),
        )

    def start(self, session: SessionSnapshot, plan: ExperimentPlan) -> _GenerationHandle:
        """Persist the run identity, then execute slicing on a worker thread."""
        self._validate_session_plan(session, plan)
        self._require_cli()
        job_id = uuid4().hex
        run_root = self._run_root(session, job_id)
        manifest_path = Path("runs") / job_id / "manifest.json"
        self._persist_session(
            session,
            plan,
            job_id=job_id,
            artifact_paths=(manifest_path.as_posix(),),
            status="generation_running",
            current_step="generation",
        )

        events: Queue[GenerationEvent] = Queue()
        cancel_event = Event()
        handle = _GenerationHandle(job_id, events)
        with self._jobs_lock:
            self._jobs[job_id] = cancel_event
        worker = Thread(
            target=self._run_job,
            name=f"calibrate3dp-orca-{job_id[:8]}",
            args=(handle, events, cancel_event, session, plan, run_root),
            daemon=True,
        )
        try:
            worker.start()
        except RuntimeError:
            with self._jobs_lock:
                self._jobs.pop(job_id, None)
            self._persist_session(
                session,
                plan,
                job_id=job_id,
                artifact_paths=(manifest_path.as_posix(),),
                status="generation_failed",
                current_step="generation",
            )
            raise
        return handle

    def cancel(self, job_id: str) -> None:
        """Request cooperative cancellation for an active CLI job."""
        with self._jobs_lock:
            cancel_event = self._jobs.get(job_id)
        if cancel_event is not None:
            cancel_event.set()

    def _run_job(
        self,
        handle: _GenerationHandle,
        events: Queue[GenerationEvent],
        cancel_event: Event,
        session: SessionSnapshot,
        plan: ExperimentPlan,
        run_root: Path,
    ) -> None:
        def emit_output(stream: str, text: str) -> None:
            events.put(GenerationEvent(
                state=GenerationState.RUNNING,
                phase="Orca is slicing candidate plates…",
                stdout_delta=text if stream == "stdout" else "",
                stderr_delta=text if stream == "stderr" else "",
            ))

        def candidate_started(index: int, total: int, candidate_id: str) -> None:
            events.put(GenerationEvent(
                state=GenerationState.RUNNING,
                phase=f"Slicing candidate {index}/{total}: {candidate_id}",
                progress_percent=(index - 1) * 100 / total,
            ))

        try:
            events.put(GenerationEvent(
                state=GenerationState.RUNNING,
                phase="Probing Orca and preparing isolated candidate plates…",
                progress_percent=0,
            ))
            run = slice_ironing_experiment(
                plan=plan,
                machine=session.profile_selection.printer,
                process=session.profile_selection.process,
                filament=session.profile_selection.filament,
                cli=self._require_cli(),
                output_dir=run_root,
                timeout_seconds=self._slice_timeout_seconds,
                cancel_event=cancel_event,
                on_candidate_started=candidate_started,
                on_output=emit_output,
            )
            artifacts = self._collect_artifacts(session, run_root)
            if run.cancelled:
                self._persist_session(
                    session,
                    plan,
                    job_id=handle.job_id,
                    artifact_paths=artifacts,
                    status="generation_cancelled",
                    current_step="generation",
                )
                events.put(GenerationEvent(
                    state=GenerationState.CANCELED,
                    phase="Generation cancelled; partial files are retained as invalid.",
                    artifact_paths=artifacts,
                    validation_state=ValidationState.INVALID,
                    validation_messages=("Cancelled output must not be printed.",),
                    progress_percent=None,
                ))
                return

            messages = self._validate_run(run, plan)
            if messages:
                self._persist_session(
                    session,
                    plan,
                    job_id=handle.job_id,
                    artifact_paths=artifacts,
                    status="generation_failed",
                    current_step="generation",
                )
                events.put(GenerationEvent(
                    state=GenerationState.FAILED,
                    phase="Generated G-code did not pass validation.",
                    artifact_paths=artifacts,
                    validation_state=ValidationState.INVALID,
                    validation_messages=messages,
                    progress_percent=100,
                ))
                return

            self._persist_session(
                session,
                plan,
                job_id=handle.job_id,
                artifact_paths=artifacts,
                status="awaiting_results",
                current_step="results",
            )
            events.put(GenerationEvent(
                state=GenerationState.SUCCEEDED,
                phase="All candidate plates passed the configured G-code checks.",
                artifact_paths=artifacts,
                validation_state=ValidationState.VALID,
                validation_messages=(
                    "Each candidate produced one non-empty G-code file with its requested "
                    "ironing flow, speed, and ironing toolpath marker.",
                ),
                progress_percent=100,
            ))
        except Exception as exc:
            message = str(exc)
            try:
                error_path = (Path("runs") / handle.job_id / "service-error.json").as_posix()
                self._repository.write_json_artifact(
                    session.session_id,
                    error_path,
                    {"job_id": handle.job_id, "error": message},
                )
                artifacts = self._collect_artifacts(session, run_root)
            except Exception as artifact_error:
                artifacts = ()
                message = f"{message}; artifact scan failed: {artifact_error}"
            try:
                self._persist_session(
                    session,
                    plan,
                    job_id=handle.job_id,
                    artifact_paths=artifacts,
                    status="generation_failed",
                    current_step="generation",
                )
            except Exception as persist_error:
                message = f"{message}; failed to persist run status: {persist_error}"
            events.put(GenerationEvent(
                state=GenerationState.FAILED,
                phase="Orca generation failed.",
                stderr_delta=f"{message}\n",
                artifact_paths=artifacts,
                validation_state=ValidationState.INVALID,
                validation_messages=(message,),
            ))
        finally:
            with self._jobs_lock:
                self._jobs.pop(handle.job_id, None)

    def _validate_session_plan(self, session: SessionSnapshot, plan: ExperimentPlan) -> None:
        if not isinstance(session, SessionSnapshot):
            raise OrcaGenerationError("generation requires a saved SessionSnapshot")
        if not isinstance(plan, ExperimentPlan) or plan.module_id != "ironing":
            raise OrcaGenerationError("the Orca generation service currently supports ironing plans only")
        if session.plan is None or session.plan.plan_id != plan.plan_id:
            raise OrcaGenerationError("the accepted plan must be saved in the generation session")

    def _require_cli(self) -> OrcaCli:
        cli = self._cli_provider()
        if cli is None:
            raise OrcaGenerationError("Select an Orca CLI/console executable in Setup before generation.")
        if not callable(getattr(cli, "probe", None)) or not callable(getattr(cli, "run_slice", None)):
            raise OrcaGenerationError("the Orca CLI provider returned an invalid executable wrapper")
        return cast(OrcaCli, cli)

    def _run_root(self, session: SessionSnapshot, job_id: str) -> Path:
        sessions_root = self._repository.sessions_root.resolve()
        session_root = self._repository.sessions_root / session.session_id
        resolved_session_root = session_root.resolve()
        if (
            resolved_session_root.parent != sessions_root
            or resolved_session_root.name != session.session_id
            or not resolved_session_root.is_dir()
        ):
            raise OrcaGenerationError("the saved session directory is unavailable or unsafe")
        return resolved_session_root / "runs" / job_id

    def _persist_session(
        self,
        session: SessionSnapshot,
        plan: ExperimentPlan,
        *,
        job_id: str,
        artifact_paths: tuple[str, ...],
        status: str,
        current_step: str,
    ) -> None:
        latest = self._session_service.resume(session.session_id)
        if latest.plan is None or latest.plan.plan_id != plan.plan_id:
            raise OrcaGenerationError("the saved session plan changed while Orca was running")
        saved = replace(
            latest,
            run_ids=tuple(dict.fromkeys((*latest.run_ids, job_id))),
            artifact_paths=tuple(dict.fromkeys((*latest.artifact_paths, *artifact_paths))),
            status=status,
            current_step=current_step,
        )
        self._session_service.save(saved)

    def _collect_artifacts(self, session: SessionSnapshot, run_root: Path) -> tuple[str, ...]:
        if not run_root.is_dir():
            return (Path("runs") / run_root.name / "manifest.json").as_posix(),
        session_root = (self._repository.sessions_root / session.session_id).resolve()
        paths: list[str] = []
        for path in sorted(run_root.rglob("*"), key=lambda item: str(item).casefold()):
            if not path.is_file():
                continue
            try:
                paths.append(path.resolve().relative_to(session_root).as_posix())
            except ValueError as exc:
                raise OrcaGenerationError("an Orca artifact escaped the saved session directory") from exc
        if not any(path.endswith("/manifest.json") for path in paths):
            paths.append((Path("runs") / run_root.name / "manifest.json").as_posix())
        return tuple(paths)

    @staticmethod
    def _validate_run(run: OrcaExperimentRun, plan: ExperimentPlan) -> tuple[str, ...]:
        messages: list[str] = []
        if not run.success:
            messages.append("One or more candidate slices failed or produced no G-code.")
        if len(run.candidates) != len(plan.candidates):
            messages.append(
                f"Expected {len(plan.candidates)} candidate results; received {len(run.candidates)}."
            )
        expected_by_id = {item.candidate_id: item for item in plan.candidates}
        observed_ids = [item.candidate_id for item in run.candidates]
        if len(observed_ids) != len(set(observed_ids)) or set(observed_ids) != set(expected_by_id):
            messages.append("Candidate results do not match the accepted plan exactly.")
        ironing_type = plan.baseline_settings.get("ironing_type")
        for result in run.candidates:
            candidate = expected_by_id.get(result.candidate_id)
            if candidate is None:
                messages.append(f"Unexpected candidate result: {result.candidate_id}.")
                continue
            if len(result.gcode_files) != 1:
                messages.append(
                    f"{result.candidate_id} must produce exactly one G-code file; "
                    f"found {len(result.gcode_files)}."
                )
                continue
            path = result.gcode_files[0]
            try:
                required_markers = {
                    f"; ironing_flow = {candidate.overrides.get('ironing_flow')}",
                    f"; ironing_speed = {candidate.overrides.get('ironing_speed')}",
                    ";TYPE:Ironing",
                }
                if isinstance(ironing_type, str):
                    required_markers.add(f"; ironing_type = {ironing_type}")
                observed_markers: set[str] = set()
                has_content = False
                with path.open("r", encoding="utf-8", errors="replace") as stream:
                    for line in stream:
                        rendered = line.rstrip("\r\n")
                        has_content = has_content or bool(rendered.strip())
                        if rendered in required_markers:
                            observed_markers.add(rendered)
            except OSError as exc:
                messages.append(f"{result.candidate_id} G-code could not be read: {exc}")
                continue
            if not has_content:
                messages.append(f"{result.candidate_id} G-code is empty.")
                continue
            for marker in sorted(required_markers - observed_markers):
                if marker == ";TYPE:Ironing":
                    messages.append(f"{result.candidate_id} G-code has no ironing toolpath section.")
                else:
                    messages.append(f"{result.candidate_id} G-code is missing {marker!r}.")
        return tuple(messages)
