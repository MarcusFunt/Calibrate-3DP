"""Versioned, append-only policy decisions tied to exact assessment revisions."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from types import MappingProxyType
from typing import Any, Mapping, Sequence

from calibrate3dp.experiments import ExperimentPlan


DECISION_SCHEMA_VERSION = 2
_LEGACY_DECISION_SCHEMA_VERSION = 1
_CONFIRMATION_EVIDENCE_KEYS = frozenset({
    "run_id", "plan_id", "candidate_id", "assessment_revision_id",
})


@dataclass(frozen=True)
class RunDecisionRecord:
    decision_id: str
    run_id: str
    assessment_revision_id: str
    created_at_utc: str
    action: str
    reasons: tuple[str, ...]
    selected_candidate_id: str | None
    proposed_plan: ExperimentPlan | None
    can_accept: bool
    confirmation_required: bool
    opt_out_record: Mapping[str, Any] | None = None
    confirmation_evidence: Mapping[str, str] | None = None

    def __post_init__(self) -> None:
        for field in ("decision_id", "run_id", "assessment_revision_id", "action"):
            value = getattr(self, field)
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"{field} must be a non-empty string")
        if self.action not in {"inconclusive", "resolve_tie", "refine", "extend_boundary", "accept"}:
            raise ValueError("unsupported decision action")
        parsed = datetime.fromisoformat(self.created_at_utc.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            raise ValueError("decision timestamp must include a timezone")
        object.__setattr__(self, "created_at_utc", parsed.astimezone(timezone.utc).isoformat(timespec="microseconds").replace("+00:00", "Z"))
        reasons = tuple(self.reasons)
        if not reasons or any(not isinstance(item, str) or not item.strip() for item in reasons):
            raise ValueError("decision must include human-readable reasons")
        object.__setattr__(self, "reasons", reasons)
        if self.selected_candidate_id is not None and (not isinstance(self.selected_candidate_id, str) or not self.selected_candidate_id.strip()):
            raise ValueError("selected_candidate_id must be a non-empty string or None")
        if self.proposed_plan is not None and not isinstance(self.proposed_plan, ExperimentPlan):
            raise ValueError("proposed_plan must be an ExperimentPlan or None")
        if not isinstance(self.can_accept, bool) or not isinstance(self.confirmation_required, bool):
            raise ValueError("decision gate values must be booleans")
        if self.can_accept and self.confirmation_required:
            raise ValueError("an acceptable decision cannot still require confirmation")
        if self.opt_out_record is not None and not isinstance(self.opt_out_record, Mapping):
            raise ValueError("opt_out_record must be an object or None")
        if self.confirmation_evidence is not None:
            if not isinstance(self.confirmation_evidence, Mapping) or set(self.confirmation_evidence) != _CONFIRMATION_EVIDENCE_KEYS:
                raise ValueError("confirmation_evidence must identify its run, plan, candidate, and assessment revision")
            evidence = dict(self.confirmation_evidence)
            if any(not isinstance(value, str) or not value.strip() for value in evidence.values()):
                raise ValueError("confirmation_evidence values must be non-empty strings")
            object.__setattr__(self, "confirmation_evidence", MappingProxyType(evidence))

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": DECISION_SCHEMA_VERSION,
            "decision_id": self.decision_id,
            "run_id": self.run_id,
            "assessment_revision_id": self.assessment_revision_id,
            "created_at_utc": self.created_at_utc,
            "action": self.action,
            "reasons": list(self.reasons),
            "selected_candidate_id": self.selected_candidate_id,
            "proposed_plan": self.proposed_plan.to_dict() if self.proposed_plan else None,
            "can_accept": self.can_accept,
            "confirmation_required": self.confirmation_required,
            "opt_out_record": dict(self.opt_out_record) if self.opt_out_record else None,
            "confirmation_evidence": dict(self.confirmation_evidence) if self.confirmation_evidence else None,
        }

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "RunDecisionRecord":
        legacy_required = {"schema_version", "decision_id", "run_id", "assessment_revision_id", "created_at_utc", "action", "reasons", "selected_candidate_id", "proposed_plan", "can_accept", "confirmation_required", "opt_out_record"}
        required = legacy_required | {"confirmation_evidence"}
        if not isinstance(payload, Mapping):
            raise ValueError("unsupported or malformed run decision")
        version = payload.get("schema_version")
        if version == _LEGACY_DECISION_SCHEMA_VERSION and set(payload) == legacy_required:
            confirmation_evidence = None
        elif version == DECISION_SCHEMA_VERSION and set(payload) == required:
            confirmation_evidence = payload["confirmation_evidence"]
        else:
            raise ValueError("unsupported or malformed run decision")
        reasons = payload["reasons"]
        if not isinstance(reasons, list):
            raise ValueError("decision reasons must be a list")
        proposed = payload["proposed_plan"]
        return cls(
            payload["decision_id"], payload["run_id"], payload["assessment_revision_id"], payload["created_at_utc"],
            payload["action"], tuple(reasons), payload["selected_candidate_id"],
            ExperimentPlan.from_dict(proposed) if proposed is not None else None,
            payload["can_accept"], payload["confirmation_required"], payload["opt_out_record"],
            confirmation_evidence,
        )
