"""SQLite session index with versioned payloads and local profile snapshots."""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path, PurePosixPath, PureWindowsPath
import secrets
import sqlite3
from typing import Any

from calibrate3dp.app.models import (
    PROFILE_ROLES,
    ProfileSelection,
    SessionSnapshot,
    SessionSummary,
)


CURRENT_SCHEMA_VERSION = 1
PROFILE_SELECTION_RELATIVE_PATH = "profiles/profile-selection.json"


class SessionStoreError(RuntimeError):
    """Base class for local session storage errors."""


class SessionNotFoundError(SessionStoreError, KeyError):
    """Raised when a requested session does not exist."""


class SessionAlreadyExistsError(SessionStoreError):
    """Raised when a session ID or its storage directory is already in use."""


class UnsupportedSessionSchemaError(SessionStoreError):
    """Raised when the database or a session payload needs a newer application."""


class UnsafeArtifactPathError(SessionStoreError, ValueError):
    """Raised when a session artifact reference escapes its session directory."""


class CorruptSessionError(SessionStoreError):
    """Raised when a stored row or profile snapshot cannot be restored safely."""


class SessionStateError(SessionStoreError, ValueError):
    """Raised when a save would change immutable session identity or profiles."""


class SessionRepository:
    """Persist session state in SQLite and profile snapshots as session files.

    ``root`` contains ``sessions.sqlite3`` and one directory per session. Paths
    in ``SessionSnapshot.artifact_paths`` are interpreted relative to that
    per-session directory and are checked against traversal and symlink escapes.
    """

    def __init__(self, root: str | Path) -> None:
        self.root = Path(root).expanduser().resolve()
        self.database_path = self.root / "sessions.sqlite3"
        self.sessions_root = self.root / "sessions"
        self.root.mkdir(parents=True, exist_ok=True)
        self.sessions_root.mkdir(parents=True, exist_ok=True)
        self._initialize_schema()

    def create(self, snapshot: SessionSnapshot) -> None:
        """Create a session and its external profile snapshot atomically."""
        self._validate_snapshot(snapshot)
        session_root = self._session_root(snapshot.session_id)
        profile_path = self._resolve_relative_path(
            session_root, PROFILE_SELECTION_RELATIVE_PATH
        )
        profile_names_json = self._encode_json(self._profile_names(snapshot.profile_selection))
        payload_json = self._encode_session_payload(snapshot)
        selection_json = self._encode_json(snapshot.profile_selection.to_dict())
        connection = self._connect()
        created_session_dir = False
        try:
            connection.execute("BEGIN IMMEDIATE")
            if connection.execute(
                "SELECT 1 FROM sessions WHERE session_id = ?", (snapshot.session_id,)
            ).fetchone():
                raise SessionAlreadyExistsError(
                    f"session {snapshot.session_id!r} already exists"
                )
            if session_root.exists():
                raise SessionAlreadyExistsError(
                    f"session directory already exists for {snapshot.session_id!r}"
                )

            session_root.mkdir(parents=True, exist_ok=False)
            created_session_dir = True
            profile_path.parent.mkdir(parents=True, exist_ok=False)
            self._write_json_atomic(profile_path, selection_json)
            connection.execute(
                """INSERT INTO sessions (
                    session_id, module_id, status, created_at_utc, updated_at_utc,
                    profile_names_json, profile_selection_path, payload_json, archived
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    snapshot.session_id,
                    snapshot.module_id,
                    snapshot.status,
                    snapshot.created_at_utc,
                    snapshot.updated_at_utc,
                    profile_names_json,
                    PROFILE_SELECTION_RELATIVE_PATH,
                    payload_json,
                    int(snapshot.archived),
                ),
            )
            connection.commit()
        except Exception:
            connection.rollback()
            if created_session_dir:
                self._remove_new_session_directory(session_root, profile_path)
            raise
        finally:
            connection.close()

    def save(self, snapshot: SessionSnapshot) -> None:
        """Update a session payload and its index fields in one DB transaction."""
        self._validate_snapshot(snapshot)
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT * FROM sessions WHERE session_id = ?", (snapshot.session_id,)
            ).fetchone()
            if row is None:
                raise SessionNotFoundError(f"session {snapshot.session_id!r} was not found")
            if snapshot.created_at_utc != row["created_at_utc"]:
                raise SessionStateError("created_at_utc cannot change after session creation")

            session_root = self._session_root(snapshot.session_id)
            selection_path = self._resolve_relative_path(
                session_root, row["profile_selection_path"]
            )
            stored_selection = self._read_json(selection_path, snapshot.session_id)
            if stored_selection != snapshot.profile_selection.to_dict():
                raise SessionStateError("the selected profile baseline cannot change after creation")

            updated_at = self._next_updated_at(row["updated_at_utc"])
            saved_snapshot = replace(snapshot, updated_at_utc=updated_at)
            connection.execute(
                """UPDATE sessions SET
                    module_id = ?, status = ?, updated_at_utc = ?,
                    profile_names_json = ?, payload_json = ?, archived = ?
                WHERE session_id = ?""",
                (
                    saved_snapshot.module_id,
                    saved_snapshot.status,
                    saved_snapshot.updated_at_utc,
                    self._encode_json(self._profile_names(saved_snapshot.profile_selection)),
                    self._encode_session_payload(saved_snapshot),
                    int(saved_snapshot.archived),
                    saved_snapshot.session_id,
                ),
            )
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def load(self, session_id: str) -> SessionSnapshot:
        """Restore a complete session, including its separate profile snapshot."""
        connection = self._connect()
        try:
            row = connection.execute(
                "SELECT * FROM sessions WHERE session_id = ?", (session_id,)
            ).fetchone()
        finally:
            connection.close()
        if row is None:
            raise SessionNotFoundError(f"session {session_id!r} was not found")

        try:
            payload = json.loads(row["payload_json"])
            if not isinstance(payload, dict):
                raise ValueError("session payload must be a JSON object")
            if payload.get("schema_version") != CURRENT_SCHEMA_VERSION:
                raise UnsupportedSessionSchemaError(
                    f"unsupported session payload schema {payload.get('schema_version')!r}"
                )
            session_root = self._session_root(session_id)
            selection_path = self._resolve_relative_path(
                session_root, row["profile_selection_path"]
            )
            payload["profile_selection"] = ProfileSelection.from_dict(
                self._read_json(selection_path, session_id)
            ).to_dict()
            snapshot = SessionSnapshot.from_dict(payload)
            self._validate_snapshot(snapshot)
            self._validate_row_matches_snapshot(row, snapshot)
            return snapshot
        except UnsupportedSessionSchemaError:
            raise
        except (OSError, ValueError, TypeError, KeyError, json.JSONDecodeError) as exc:
            raise CorruptSessionError(
                f"session {session_id!r} could not be restored: {exc}"
            ) from exc

    def list_recent(self, limit: int = 20) -> tuple[SessionSummary, ...]:
        """List active sessions by most recent save time."""
        if isinstance(limit, bool) or not isinstance(limit, int) or limit < 0:
            raise ValueError("limit must be a non-negative integer")
        if limit == 0:
            return ()
        connection = self._connect()
        try:
            rows = connection.execute(
                """SELECT session_id, module_id, status, updated_at_utc, profile_names_json
                   FROM sessions WHERE archived = 0
                   ORDER BY updated_at_utc DESC, session_id ASC LIMIT ?""",
                (limit,),
            ).fetchall()
        finally:
            connection.close()

        summaries: list[SessionSummary] = []
        for row in rows:
            try:
                names = json.loads(row["profile_names_json"])
                summaries.append(
                    SessionSummary(
                        session_id=row["session_id"],
                        module_id=row["module_id"],
                        status=row["status"],
                        updated_at_utc=row["updated_at_utc"],
                        profile_names=names,
                    )
                )
            except (ValueError, TypeError, json.JSONDecodeError) as exc:
                raise CorruptSessionError(
                    f"session summary for {row['session_id']!r} is invalid: {exc}"
                ) from exc
        return tuple(summaries)

    def _initialize_schema(self) -> None:
        connection = sqlite3.connect(self.database_path, timeout=10)
        try:
            connection.execute("PRAGMA journal_mode = WAL")
            version = connection.execute("PRAGMA user_version").fetchone()[0]
            if version > CURRENT_SCHEMA_VERSION:
                raise UnsupportedSessionSchemaError(
                    f"session database schema {version} is newer than supported "
                    f"version {CURRENT_SCHEMA_VERSION}"
                )
            if version == 0:
                connection.execute("BEGIN IMMEDIATE")
                connection.execute(
                    """CREATE TABLE sessions (
                        session_id TEXT PRIMARY KEY,
                        module_id TEXT NOT NULL,
                        status TEXT NOT NULL,
                        created_at_utc TEXT NOT NULL,
                        updated_at_utc TEXT NOT NULL,
                        profile_names_json TEXT NOT NULL,
                        profile_selection_path TEXT NOT NULL,
                        payload_json TEXT NOT NULL,
                        archived INTEGER NOT NULL DEFAULT 0 CHECK (archived IN (0, 1))
                    )"""
                )
                connection.execute(
                    "CREATE INDEX sessions_recent_idx "
                    "ON sessions (archived, updated_at_utc DESC, session_id ASC)"
                )
                connection.execute(f"PRAGMA user_version = {CURRENT_SCHEMA_VERSION}")
                connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.database_path, timeout=10, isolation_level=None)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA busy_timeout = 10000")
        return connection

    def _validate_snapshot(self, snapshot: SessionSnapshot) -> None:
        if not isinstance(snapshot, SessionSnapshot):
            raise TypeError("snapshot must be a SessionSnapshot")
        session_root = self._session_root(snapshot.session_id)
        for path in snapshot.artifact_paths:
            self._resolve_relative_path(session_root, path)
        if snapshot.results is not None:
            if snapshot.plan is None:
                raise SessionStateError("results cannot be saved before an experiment plan")
            snapshot.results.validate_for(snapshot.plan)

    def _session_root(self, session_id: str) -> Path:
        if not isinstance(session_id, str) or not session_id.strip():
            raise SessionStateError("session id must be a non-empty string")
        if any(character in session_id for character in ("/", "\\", ":")) or session_id in {".", ".."}:
            raise SessionStateError("session id must be a safe path component")
        return self.sessions_root / session_id

    @staticmethod
    def _resolve_relative_path(session_root: Path, relative_path: str) -> Path:
        if not isinstance(relative_path, str) or not relative_path.strip():
            raise UnsafeArtifactPathError("artifact paths must be non-empty relative paths")
        posix = PurePosixPath(relative_path)
        windows = PureWindowsPath(relative_path)
        if posix.is_absolute() or windows.is_absolute() or windows.drive:
            raise UnsafeArtifactPathError(f"absolute artifact path is not allowed: {relative_path!r}")
        if ".." in posix.parts or ".." in windows.parts:
            raise UnsafeArtifactPathError(f"artifact path escapes its session: {relative_path!r}")
        root = session_root.resolve()
        candidate = (root / Path(relative_path)).resolve()
        try:
            candidate.relative_to(root)
        except ValueError as exc:
            raise UnsafeArtifactPathError(
                f"artifact path escapes its session: {relative_path!r}"
            ) from exc
        return candidate

    @staticmethod
    def _profile_names(selection: ProfileSelection) -> dict[str, str]:
        return {
            "printer": selection.printer.profile.name,
            "filament": selection.filament.profile.name,
            "process": selection.process.profile.name,
        }

    @staticmethod
    def _encode_json(payload: Any) -> str:
        try:
            return json.dumps(payload, ensure_ascii=False, sort_keys=True, allow_nan=False)
        except (TypeError, ValueError, OverflowError) as exc:
            raise SessionStoreError(f"session data is not valid JSON: {exc}") from exc

    def _encode_session_payload(self, snapshot: SessionSnapshot) -> str:
        payload = snapshot.to_dict()
        payload.pop("profile_selection")
        return self._encode_json(payload)

    def _read_json(self, path: Path, session_id: str) -> Any:
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except OSError as exc:
            raise CorruptSessionError(
                f"profile snapshot for session {session_id!r} is unavailable: {exc}"
            ) from exc
        except json.JSONDecodeError as exc:
            raise CorruptSessionError(
                f"profile snapshot for session {session_id!r} is malformed"
            ) from exc

    @staticmethod
    def _write_json_atomic(path: Path, contents: str) -> None:
        temporary_path = path.with_name(f".{path.name}.{secrets.token_hex(6)}.tmp")
        try:
            temporary_path.write_text(contents, encoding="utf-8", newline="\n")
            temporary_path.replace(path)
        finally:
            temporary_path.unlink(missing_ok=True)

    @staticmethod
    def _remove_new_session_directory(session_root: Path, profile_path: Path) -> None:
        try:
            profile_path.unlink(missing_ok=True)
            profile_path.parent.rmdir()
            session_root.rmdir()
        except OSError:
            # Keep unexpected files intact if another process touched the new folder.
            return

    @staticmethod
    def _next_updated_at(previous: str) -> str:
        now = datetime.now(timezone.utc)
        previous_time = datetime.fromisoformat(previous.replace("Z", "+00:00"))
        if now <= previous_time:
            now = previous_time + timedelta(microseconds=1)
        return now.isoformat(timespec="microseconds").replace("+00:00", "Z")

    def _validate_row_matches_snapshot(
        self, row: sqlite3.Row, snapshot: SessionSnapshot
    ) -> None:
        if (
            row["module_id"] != snapshot.module_id
            or row["status"] != snapshot.status
            or row["created_at_utc"] != snapshot.created_at_utc
            or row["updated_at_utc"] != snapshot.updated_at_utc
            or bool(row["archived"]) != snapshot.archived
        ):
            raise CorruptSessionError(
                f"session {snapshot.session_id!r} index and payload disagree"
            )
