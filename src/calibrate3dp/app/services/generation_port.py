"""Typed boundary between the desktop UI and an Orca generation service."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
import math
from types import MappingProxyType
from typing import Mapping, Protocol

from calibrate3dp.app.models import SessionSnapshot
from calibrate3dp.app.services.experiment_service import CandidatePlateEntry, ExperimentReview
from calibrate3dp.experiments import ExperimentPlan


class GenerationState(str, Enum):
    """Lifecycle states reported by an injected generation worker."""

    QUEUED = "queued"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELED = "canceled"


class ValidationState(str, Enum):
    """Validation status for generated output."""

    NOT_RUN = "not_run"
    VALID = "valid"
    INVALID = "invalid"


@dataclass(frozen=True)
class ExperimentPreview:
    """Exact candidate map and only those estimates verified by a service."""

    plan_id: str
    candidate_map: tuple[CandidatePlateEntry, ...]
    fixed_settings: Mapping[str, Any]
    warnings: tuple[str, ...] = ()
    estimated_duration: str | None = None
    estimated_material: str | None = None
    estimates_verified: bool = False
    validation_state: ValidationState = ValidationState.NOT_RUN

    def __post_init__(self) -> None:
        if not isinstance(self.plan_id, str) or not self.plan_id.strip():
            raise ValueError("preview plan_id must be a non-empty string")
        candidate_map = tuple(self.candidate_map)
        if not candidate_map or any(not isinstance(item, CandidatePlateEntry) for item in candidate_map):
            raise ValueError("preview must contain a candidate-to-plate map")
        candidate_ids = [item.candidate_id for item in candidate_map]
        if len(candidate_ids) != len(set(candidate_ids)):
            raise ValueError("preview candidate IDs must be unique")
        if not isinstance(self.fixed_settings, Mapping):
            raise ValueError("preview fixed_settings must be a mapping")
        if type(self.estimates_verified) is not bool:
            raise ValueError("estimates_verified must be a boolean")
        if not self.estimates_verified and (self.estimated_duration or self.estimated_material):
            raise ValueError("unverified duration and material estimates must be omitted")
        if self.validation_state not in tuple(ValidationState):
            raise ValueError("preview validation_state is invalid")
        warnings = tuple(self.warnings)
        if any(not isinstance(item, str) or not item.strip() for item in warnings):
            raise ValueError("preview warnings must be non-empty strings")
        object.__setattr__(self, "candidate_map", candidate_map)
        object.__setattr__(self, "fixed_settings", MappingProxyType(dict(self.fixed_settings)))
        object.__setattr__(self, "warnings", warnings)

    @property
    def plate_count(self) -> int:
        """Count unique physical plates represented by the map."""
        return len({item.plate_label for item in self.candidate_map})

    @classmethod
    def from_review(cls, review: ExperimentReview) -> "ExperimentPreview":
        """Project the existing experiment review into the generation view."""
        return cls(
            plan_id=review.plan.plan_id,
            candidate_map=review.plate_map,
            fixed_settings=review.fixed_settings,
            warnings=(*review.warnings, review.estimate_message),
        )


@dataclass(frozen=True)
class GenerationEvent:
    """One state, log, artifact, or validation update from a worker."""

    state: GenerationState
    phase: str
    stdout_delta: str = ""
    stderr_delta: str = ""
    exit_code: int | None = None
    artifact_paths: tuple[str, ...] = ()
    validation_state: ValidationState | None = None
    validation_messages: tuple[str, ...] = ()
    progress_percent: float | None = None

    def __post_init__(self) -> None:
        if self.state not in tuple(GenerationState):
            raise ValueError("generation event state is invalid")
        if not isinstance(self.phase, str):
            raise ValueError("generation phase must be text")
        if not isinstance(self.stdout_delta, str) or not isinstance(self.stderr_delta, str):
            raise ValueError("generation log deltas must be text")
        if self.exit_code is not None and (isinstance(self.exit_code, bool) or not isinstance(self.exit_code, int)):
            raise ValueError("exit_code must be an integer or None")
        paths = tuple(self.artifact_paths)
        if any(not isinstance(path, str) or not path.strip() for path in paths):
            raise ValueError("artifact paths must be non-empty strings")
        messages = tuple(self.validation_messages)
        if any(not isinstance(message, str) or not message.strip() for message in messages):
            raise ValueError("validation messages must be non-empty strings")
        if self.validation_state is not None and self.validation_state not in tuple(ValidationState):
            raise ValueError("validation_state is invalid")
        if self.progress_percent is not None:
            if (
                isinstance(self.progress_percent, bool)
                or not isinstance(self.progress_percent, (int, float))
                or not math.isfinite(self.progress_percent)
                or not 0 <= self.progress_percent <= 100
            ):
                raise ValueError("progress_percent must be between 0 and 100")
        object.__setattr__(self, "artifact_paths", paths)
        object.__setattr__(self, "validation_messages", messages)


class GenerationHandle(Protocol):
    """Non-blocking event stream for one generation job."""

    @property
    def job_id(self) -> str: ...

    def next_event(self, timeout: float = 0.1) -> GenerationEvent | None: ...


class GenerationService(Protocol):
    """Injected service for prepared previews, slicing, validation, and cancel.

    Implementations own durable writes under the supplied session: log deltas
    and partial artifacts must survive cancellation or failure, and cancel
    must never promote those partial artifacts to valid output.
    """

    def preview(self, session: SessionSnapshot, plan: ExperimentPlan) -> ExperimentPreview: ...

    def start(self, session: SessionSnapshot, plan: ExperimentPlan) -> GenerationHandle: ...

    def cancel(self, job_id: str) -> None: ...
