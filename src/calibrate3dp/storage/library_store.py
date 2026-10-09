"""SQLite repositories for printer/material records and immutable run inputs."""

from __future__ import annotations

from dataclasses import replace
import hashlib
import json
from pathlib import Path, PurePosixPath, PureWindowsPath
import secrets
import sqlite3
from typing import Any, Mapping, Sequence

from calibrate3dp.domain.records import (
    ArtifactRecord,
    CalibrationRunRecord,
    MaterialRecord,
    PrinterRecord,
    RUN_STATUSES,
)
from calibrate3dp.storage.session_store import SessionRepository


class LibraryStoreError(RuntimeError):
    """Base error for local library storage operations."""


class DuplicateRecordError(LibraryStoreError):
    """A printer or material name/identifier already exists."""


class DuplicatePlateCodeError(LibraryStoreError):
    """A physical plate code is already assigned to another run."""


class RunStateError(LibraryStoreError):
    """A run update would rewrite its immutable plan or violate its lifecycle."""


class RunNotFoundError(LibraryStoreError, KeyError):
    """A requested run does not exist."""


class MissingRunArtifactError(LibraryStoreError, FileNotFoundError):
    """A finalized run artifact is missing or does not match its recorded hash."""


class CorruptLibraryRecordError(LibraryStoreError):
    """A stored record cannot be restored from its versioned JSON payload."""


_CODE_ALPHABET = "23456789ABCDEFGHJKLMNPQRSTUVWXYZ"


class LibraryRepository:
    """Typed record access over the current session SQLite database.

    ``SessionRepository`` owns the workspace and applies the schema migration.
    The legacy sessions table and the typed V1 records share a database, while
    run output files remain outside SQLite under ``workspace/runs``.
    """

    def __init__(self, sessions: SessionRepository) -> None:
        if not isinstance(sessions, SessionRepository):
            raise TypeError("sessions must be a SessionRepository")
        self._sessions = sessions
        self.root = sessions.root
        self.database_path = sessions.database_path
        self.runs_root = self.root / "runs"
        self.runs_root.mkdir(parents=True, exist_ok=True)
        self._require_schema()

    def add_printer(self, record: PrinterRecord) -> None:
        if not isinstance(record, PrinterRecord):
            raise TypeError("record must be a PrinterRecord")
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                """INSERT INTO printers (
                    printer_id, display_name, model, nozzle, machine_profile_json,
                    process_profile_json, created_at_utc
                ) VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (
                    record.printer_id, record.display_name, record.model, record.nozzle,
                    _encode(record.machine_profile.to_dict()),
                    _encode(record.process_profile.to_dict()), record.created_at_utc,
                ),
            )
            connection.commit()
        except sqlite3.IntegrityError as exc:
            connection.rollback()
            raise DuplicateRecordError(f"printer {record.display_name!r} or ID already exists") from exc
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def get_printer(self, printer_id: str) -> PrinterRecord:
        connection = self._connect()
        try:
            row = connection.execute("SELECT * FROM printers WHERE printer_id = ?", (printer_id,)).fetchone()
        finally:
            connection.close()
        if row is None:
            raise KeyError(f"printer {printer_id!r} was not found")
        try:
            return PrinterRecord.from_dict({
                "schema_version": 1,
                "printer_id": row["printer_id"],
                "display_name": row["display_name"],
                "model": row["model"],
                "nozzle": row["nozzle"],
                "machine_profile": json.loads(row["machine_profile_json"]),
                "process_profile": json.loads(row["process_profile_json"]),
                "created_at_utc": row["created_at_utc"],
            })
        except (ValueError, TypeError, KeyError, json.JSONDecodeError) as exc:
            raise CorruptLibraryRecordError(f"printer {printer_id!r} is corrupt: {exc}") from exc

    def list_printers(self) -> tuple[PrinterRecord, ...]:
        connection = self._connect()
        try:
            ids = connection.execute(
                "SELECT printer_id FROM printers ORDER BY display_name COLLATE NOCASE, printer_id"
            ).fetchall()
        finally:
            connection.close()
        return tuple(self.get_printer(row["printer_id"]) for row in ids)

    def add_material(self, record: MaterialRecord) -> None:
        if not isinstance(record, MaterialRecord):
            raise TypeError("record must be a MaterialRecord")
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                """INSERT INTO materials (
                    material_id, display_name, nozzle_context, filament_profile_json,
                    toolhead_context, created_at_utc
                ) VALUES (?, ?, ?, ?, ?, ?)""",
                (
                    record.material_id, record.display_name, record.nozzle_context,
                    _encode(record.filament_profile.to_dict()), record.toolhead_context,
                    record.created_at_utc,
                ),
            )
            connection.commit()
        except sqlite3.IntegrityError as exc:
            connection.rollback()
            raise DuplicateRecordError(f"material {record.display_name!r} or ID already exists") from exc
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def get_material(self, material_id: str) -> MaterialRecord:
        connection = self._connect()
        try:
            row = connection.execute("SELECT * FROM materials WHERE material_id = ?", (material_id,)).fetchone()
        finally:
            connection.close()
        if row is None:
            raise KeyError(f"material {material_id!r} was not found")
        try:
            return MaterialRecord.from_dict({
                "schema_version": 1,
                "material_id": row["material_id"],
                "display_name": row["display_name"],
                "nozzle_context": row["nozzle_context"],
                "filament_profile": json.loads(row["filament_profile_json"]),
                "toolhead_context": row["toolhead_context"],
                "created_at_utc": row["created_at_utc"],
            })
        except (ValueError, TypeError, KeyError, json.JSONDecodeError) as exc:
            raise CorruptLibraryRecordError(f"material {material_id!r} is corrupt: {exc}") from exc

    def list_materials(self) -> tuple[MaterialRecord, ...]:
        connection = self._connect()
        try:
            ids = connection.execute(
                "SELECT material_id FROM materials ORDER BY display_name COLLATE NOCASE, material_id"
            ).fetchall()
        finally:
            connection.close()
        return tuple(self.get_material(row["material_id"]) for row in ids)

    def allocate_plate_code(self, *, attempts: int = 128) -> str:
        if type(attempts) is not int or attempts < 1:
            raise ValueError("attempts must be a positive integer")
        connection = self._connect()
        try:
            for _ in range(attempts):
                code = "".join(secrets.choice(_CODE_ALPHABET) for _ in range(6))
                if connection.execute(
                    "SELECT 1 FROM calibration_runs WHERE plate_code = ?", (code,)
                ).fetchone() is None:
                    return code
        finally:
            connection.close()
        raise LibraryStoreError("could not allocate a unique six-character plate code")

    def create_run(self, record: CalibrationRunRecord) -> None:
        if not isinstance(record, CalibrationRunRecord):
            raise TypeError("record must be a CalibrationRunRecord")
        if record.status != "generating" or record.artifacts:
            raise RunStateError("a new run must be generating and have no output artifacts")
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                """INSERT INTO calibration_runs (
                    run_id, plate_code, printer_id, material_id, status, created_at_utc,
                    plan_json, profiles_json, sample_map_json, validation_json, artifacts_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    record.run_id, record.plate_code, record.printer_id, record.material_id,
                    record.status, record.created_at_utc, _encode(record.plan.to_dict()),
                    _encode(record.profiles.to_dict()), _encode([dict(item) for item in record.sample_map]),
                    _encode(dict(record.validation)), _encode([]),
                ),
            )
            connection.commit()
        except sqlite3.IntegrityError as exc:
            connection.rollback()
            text = str(exc).casefold()
            if "plate_code" in text:
                raise DuplicatePlateCodeError(f"plate code {record.plate_code!r} is already assigned") from exc
            if "foreign key" in text:
                raise LibraryStoreError("run printer or material record does not exist") from exc
            raise DuplicateRecordError(f"run ID {record.run_id!r} already exists") from exc
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def finalize_run(
        self,
        run_id: str,
        *,
        status: str,
        artifacts: Sequence[ArtifactRecord],
        validation: Mapping[str, Any],
    ) -> CalibrationRunRecord:
        if status not in RUN_STATUSES - {"generating"}:
            raise RunStateError(f"invalid terminal run status {status!r}")
        artifact_records = tuple(artifacts)
        for artifact in artifact_records:
            self._verify_artifact(artifact)
        current = self.get_run(run_id)
        if current.status != "generating":
            raise RunStateError(f"run {run_id!r} is already finalized as {current.status!r}")
        finalized = replace(
            current, status=status, artifacts=artifact_records, validation=dict(validation)
        )
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            cursor = connection.execute(
                """UPDATE calibration_runs SET status = ?, validation_json = ?, artifacts_json = ?
                   WHERE run_id = ? AND status = 'generating'""",
                (
                    finalized.status, _encode(dict(finalized.validation)),
                    _encode([item.to_dict() for item in finalized.artifacts]), run_id,
                ),
            )
            if cursor.rowcount != 1:
                raise RunStateError(f"run {run_id!r} was finalized by another operation")
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()
        return finalized

    def get_run(self, run_id: str) -> CalibrationRunRecord:
        return self._read_run("run_id", run_id)

    def get_run_by_plate_code(self, plate_code: str) -> CalibrationRunRecord:
        if not isinstance(plate_code, str) or not plate_code.strip():
            raise ValueError("plate code must be a non-empty string")
        return self._read_run("plate_code", plate_code.strip().upper())

    def list_runs(self, *, printer_id: str | None = None) -> tuple[CalibrationRunRecord, ...]:
        connection = self._connect()
        try:
            if printer_id is None:
                rows = connection.execute(
                    "SELECT run_id FROM calibration_runs ORDER BY created_at_utc DESC, run_id"
                ).fetchall()
            else:
                rows = connection.execute(
                    "SELECT run_id FROM calibration_runs WHERE printer_id = ? "
                    "ORDER BY created_at_utc DESC, run_id",
                    (printer_id,),
                ).fetchall()
        finally:
            connection.close()
        return tuple(self.get_run(row["run_id"]) for row in rows)

    def artifact_path(self, artifact: ArtifactRecord) -> Path:
        path = self._safe_artifact_path(artifact.relative_path)
        if not path.is_file():
            raise MissingRunArtifactError(f"run artifact is missing: {artifact.relative_path}")
        if path.stat().st_size != artifact.size_bytes or _sha256(path) != artifact.sha256:
            raise MissingRunArtifactError(f"run artifact hash does not match: {artifact.relative_path}")
        return path

    def _read_run(self, field: str, value: str) -> CalibrationRunRecord:
        if field not in {"run_id", "plate_code"}:
            raise ValueError("invalid run lookup field")
        connection = self._connect()
        try:
            row = connection.execute(f"SELECT * FROM calibration_runs WHERE {field} = ?", (value,)).fetchone()
        finally:
            connection.close()
        if row is None:
            raise RunNotFoundError(f"run {value!r} was not found")
        try:
            return CalibrationRunRecord.from_dict({
                "schema_version": 1,
                "run_id": row["run_id"],
                "plate_code": row["plate_code"],
                "printer_id": row["printer_id"],
                "material_id": row["material_id"],
                "status": row["status"],
                "created_at_utc": row["created_at_utc"],
                "plan": json.loads(row["plan_json"]),
                "profiles": json.loads(row["profiles_json"]),
                "sample_map": json.loads(row["sample_map_json"]),
                "validation": json.loads(row["validation_json"]),
                "artifacts": json.loads(row["artifacts_json"]),
            })
        except (ValueError, TypeError, KeyError, json.JSONDecodeError) as exc:
            raise CorruptLibraryRecordError(f"run {value!r} is corrupt: {exc}") from exc

    def _verify_artifact(self, artifact: ArtifactRecord) -> None:
        if not isinstance(artifact, ArtifactRecord):
            raise TypeError("artifacts must be ArtifactRecord values")
        path = self._safe_artifact_path(artifact.relative_path)
        if not path.is_file():
            raise MissingRunArtifactError(f"run artifact is missing: {artifact.relative_path}")
        if path.stat().st_size != artifact.size_bytes or _sha256(path) != artifact.sha256:
            raise MissingRunArtifactError(f"run artifact hash does not match: {artifact.relative_path}")

    def _safe_artifact_path(self, relative_path: str) -> Path:
        posix, windows = PurePosixPath(relative_path), PureWindowsPath(relative_path)
        if posix.is_absolute() or windows.is_absolute() or windows.drive or ".." in posix.parts or ".." in windows.parts:
            raise LibraryStoreError("artifact path must remain inside the workspace")
        root = self.root.resolve()
        path = (root / Path(relative_path)).resolve(strict=False)
        try:
            path.relative_to(root)
        except ValueError as exc:
            raise LibraryStoreError("artifact path escapes the workspace") from exc
        return path

    def _require_schema(self) -> None:
        connection = self._connect()
        try:
            version = connection.execute("PRAGMA user_version").fetchone()[0]
            if version != 2:
                raise LibraryStoreError(f"workspace schema version {version} is not ready for library records")
        finally:
            connection.close()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.database_path, timeout=10, isolation_level=None)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA busy_timeout = 10000")
        return connection


def _encode(payload: Any) -> str:
    try:
        return json.dumps(payload, ensure_ascii=False, sort_keys=True, allow_nan=False)
    except (TypeError, ValueError, OverflowError) as exc:
        raise LibraryStoreError(f"record is not valid JSON: {exc}") from exc


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()
