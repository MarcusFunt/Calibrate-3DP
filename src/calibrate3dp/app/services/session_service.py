"""Session workflow operations and profile-baseline fingerprinting."""

from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
from typing import Mapping
from uuid import uuid4

from calibrate3dp.app.models import ProfileSelection, SessionSnapshot, SessionSummary
from calibrate3dp.profiles import ResolvedProfile
from calibrate3dp.storage.session_store import SessionRepository, SessionStateError


class SessionService:
    """Create, save, and resume calibration sessions through a repository."""

    def __init__(self, repository: SessionRepository) -> None:
        if not isinstance(repository, SessionRepository):
            raise TypeError("repository must be a SessionRepository")
        self.repository = repository

    def create_session(self, selection: ProfileSelection, module_id: str) -> SessionSnapshot:
        """Fingerprint the selected profiles and persist a new session."""
        if not isinstance(selection, ProfileSelection):
            raise TypeError("selection must be a ProfileSelection")
        if not isinstance(module_id, str) or not module_id.strip():
            raise ValueError("module_id must be a non-empty string")

        selection = self._fingerprint_selection(selection)
        timestamp = datetime.now(timezone.utc).isoformat(timespec="microseconds").replace(
            "+00:00", "Z"
        )
        snapshot = SessionSnapshot(
            session_id=uuid4().hex,
            created_at_utc=timestamp,
            updated_at_utc=timestamp,
            module_id=module_id,
            current_step="module_selection",
            profile_selection=selection,
            status="setup",
        )
        self.repository.create(snapshot)
        return snapshot

    def save(self, snapshot: SessionSnapshot) -> None:
        """Validate plan/result consistency and persist the latest state."""
        if not isinstance(snapshot, SessionSnapshot):
            raise TypeError("snapshot must be a SessionSnapshot")
        if snapshot.results is not None:
            if snapshot.plan is None:
                raise SessionStateError("results cannot be saved before an experiment plan")
            snapshot.results.validate_for(snapshot.plan)
        self.repository.save(snapshot)

    def resume(self, session_id: str) -> SessionSnapshot:
        """Load a session and recheck any saved result-to-plan relationship."""
        snapshot = self.repository.load(session_id)
        if snapshot.results is not None:
            if snapshot.plan is None:
                raise SessionStateError("saved results have no associated experiment plan")
            snapshot.results.validate_for(snapshot.plan)
        return snapshot

    def list_recent(self, limit: int = 20) -> tuple[SessionSummary, ...]:
        return self.repository.list_recent(limit=limit)

    @staticmethod
    def _fingerprint_selection(selection: ProfileSelection) -> ProfileSelection:
        profiles: Mapping[str, ResolvedProfile] = {
            "printer": selection.printer,
            "filament": selection.filament,
            "process": selection.process,
        }
        hashes: dict[str, str] = {}
        for role, profile in profiles.items():
            source = selection.source_paths[role]
            source_path = Path(source).expanduser()
            if "!" not in source and source_path.is_file():
                digest_input = source_path.read_bytes()
            elif role in selection.source_hashes:
                hashes[role] = selection.source_hashes[role]
                continue
            else:
                digest_input = json.dumps(
                    dict(profile.profile.raw),
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                    allow_nan=False,
                ).encode("utf-8")
            hashes[role] = hashlib.sha256(digest_input).hexdigest()
        return selection.with_source_hashes(hashes)
