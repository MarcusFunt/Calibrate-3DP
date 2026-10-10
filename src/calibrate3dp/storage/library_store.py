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
from calibrate3dp.domain.experiment_config import SavedExperimentConfiguration
from calibrate3dp.domain.assessment import AssessmentRevision, PrintAttestation
from calibrate3dp.domain.run_decision import RunDecisionRecord
from calibrate3dp.domain.run_export import RunExportRecord
from calibrate3dp.experiments import ExperimentResults
from calibrate3dp.storage.session_store import CURRENT_SCHEMA_VERSION, SessionRepository


class LibraryStoreError(RuntimeError):
    """Base error for local library storage operations."""


class DuplicateRecordError(LibraryStoreError):
    """A printer or material name/identifier already exists."""


class DuplicatePlateCodeError(LibraryStoreError):
    """A physical plate code is already assigned to another run."""


class DuplicateConfigurationError(DuplicateRecordError):
    """A configuration ID or logical experiment revision already exists."""


class RunStateError(LibraryStoreError):
    """A run update would rewrite its immutable plan or violate its lifecycle."""


class RunNotFoundError(LibraryStoreError, KeyError):
    """A requested run does not exist."""


class MissingRunArtifactError(LibraryStoreError, FileNotFoundError):
    """A finalized run artifact is missing or does not match its recorded hash."""


class CorruptLibraryRecordError(LibraryStoreError):
    """A stored record cannot be restored from its versioned JSON payload."""


class AssessmentRevisionConflictError(LibraryStoreError):
    """Another editor saved a newer assessment revision."""


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

    def save_configuration(self, configuration: SavedExperimentConfiguration) -> None:
        """Insert one immutable, versioned configuration revision."""
        if not isinstance(configuration, SavedExperimentConfiguration):
            raise TypeError("configuration must be a SavedExperimentConfiguration")
        payload = configuration.to_dict()
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                """INSERT INTO experiment_configs (
                    config_id, experiment_id, revision_no, schema_version, created_at_utc,
                    input_sha256, config_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (
                    configuration.config_id, configuration.experiment_id, configuration.revision_no,
                    payload["schema_version"], configuration.created_at_utc,
                    configuration.input_sha256, _encode(payload),
                ),
            )
            connection.commit()
        except sqlite3.IntegrityError as exc:
            connection.rollback()
            raise DuplicateConfigurationError(
                f"configuration {configuration.config_id!r} or revision "
                f"{configuration.experiment_id!r}/{configuration.revision_no} already exists"
            ) from exc
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def get_configuration(self, config_id: str) -> SavedExperimentConfiguration:
        connection = self._connect()
        try:
            row = connection.execute(
                "SELECT * FROM experiment_configs WHERE config_id = ?", (config_id,)
            ).fetchone()
        finally:
            connection.close()
        if row is None:
            raise KeyError(f"configuration {config_id!r} was not found")
        try:
            record = SavedExperimentConfiguration.from_dict(json.loads(row["config_json"]))
            if (
                record.config_id != row["config_id"]
                or record.experiment_id != row["experiment_id"]
                or record.revision_no != row["revision_no"]
                or record.created_at_utc != row["created_at_utc"]
                or record.input_sha256 != row["input_sha256"]
            ):
                raise ValueError("configuration index and snapshot disagree")
            return record
        except (ValueError, TypeError, KeyError, json.JSONDecodeError) as exc:
            raise CorruptLibraryRecordError(f"configuration {config_id!r} is corrupt: {exc}") from exc

    def get_configuration_revision(
        self, experiment_id: str, revision_no: int
    ) -> SavedExperimentConfiguration:
        connection = self._connect()
        try:
            row = connection.execute(
                "SELECT config_id FROM experiment_configs WHERE experiment_id = ? AND revision_no = ?",
                (experiment_id, revision_no),
            ).fetchone()
        finally:
            connection.close()
        if row is None:
            raise KeyError(f"configuration revision {experiment_id!r}/{revision_no} was not found")
        return self.get_configuration(row["config_id"])

    def list_configurations(self, experiment_id: str) -> tuple[SavedExperimentConfiguration, ...]:
        connection = self._connect()
        try:
            rows = connection.execute(
                "SELECT config_id FROM experiment_configs WHERE experiment_id = ? ORDER BY revision_no",
                (experiment_id,),
            ).fetchall()
        finally:
            connection.close()
        return tuple(self.get_configuration(row["config_id"]) for row in rows)

    def list_unlinked_initial_configurations(
        self, printer_id: str, material_id: str
    ) -> tuple[SavedExperimentConfiguration, ...]:
        """List saved initial drafts that have not yet generated a run."""
        connection = self._connect()
        try:
            rows = connection.execute(
                "SELECT config_id FROM experiment_configs "
                "WHERE config_id NOT IN (SELECT config_id FROM run_config_links) "
                "ORDER BY created_at_utc DESC, config_id"
            ).fetchall()
        finally:
            connection.close()
        return tuple(
            configuration
            for row in rows
            if (configuration := self.get_configuration(row["config_id"])).printer_id == printer_id
            and configuration.material_id == material_id
            and configuration.relation_type == "initial"
            and configuration.parent_run_id is None
        )

    def create_run(
        self,
        record: CalibrationRunRecord,
        *,
        configuration_id: str | None = None,
    ) -> None:
        if not isinstance(record, CalibrationRunRecord):
            raise TypeError("record must be a CalibrationRunRecord")
        if record.status != "generating" or record.artifacts:
            raise RunStateError("a new run must be generating and have no output artifacts")
        configuration = None
        if configuration_id is not None:
            configuration = self.get_configuration(configuration_id)
            if (
                configuration.printer_id != record.printer_id
                or configuration.material_id != record.material_id
                or configuration.plan.to_dict() != record.plan.to_dict()
                or configuration.profile_selection.to_dict() != record.profiles.to_dict()
            ):
                raise RunStateError("run inputs do not match the frozen configuration")
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
            if configuration is not None:
                connection.execute(
                    """INSERT INTO run_config_links (
                           run_id, config_id, parent_run_id, parent_assessment_revision_id,
                           parent_candidate_id, relation_type
                       ) VALUES (?, ?, ?, ?, ?, ?)""",
                    (
                        record.run_id, configuration.config_id, configuration.parent_run_id,
                        configuration.parent_assessment_revision_id,
                        configuration.parent_candidate_id, configuration.relation_type,
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

    def get_configuration_for_run(self, run_id: str) -> SavedExperimentConfiguration | None:
        connection = self._connect()
        try:
            row = connection.execute(
                "SELECT config_id FROM run_config_links WHERE run_id = ?", (run_id,)
            ).fetchone()
        finally:
            connection.close()
        return None if row is None else self.get_configuration(row["config_id"])

    def get_run_config_link(self, run_id: str) -> Mapping[str, Any] | None:
        connection = self._connect()
        try:
            row = connection.execute(
                "SELECT run_id, config_id, parent_run_id, parent_assessment_revision_id, "
                "parent_candidate_id, relation_type "
                "FROM run_config_links WHERE run_id = ?", (run_id,)
            ).fetchone()
        finally:
            connection.close()
        return None if row is None else dict(row)

    def list_child_runs(self, parent_run_id: str) -> tuple[CalibrationRunRecord, ...]:
        connection = self._connect()
        try:
            rows = connection.execute(
                "SELECT run_id FROM run_config_links WHERE parent_run_id = ? ORDER BY run_id",
                (parent_run_id,),
            ).fetchall()
        finally:
            connection.close()
        return tuple(self.get_run(row["run_id"]) for row in rows)

    def save_assessment_revision(
        self, revision: AssessmentRevision, *, expected_revision: int
    ) -> AssessmentRevision:
        """Append a run assessment with compare-and-swap revision semantics."""
        if not isinstance(revision, AssessmentRevision):
            raise TypeError("revision must be an AssessmentRevision")
        if type(expected_revision) is not int or expected_revision < 0:
            raise ValueError("expected_revision must be a non-negative integer")
        run = self.get_run(revision.run_id)
        revision.results.validate_for(run.plan)
        if revision.results.plan_id != run.plan.plan_id:
            raise RunStateError("assessment results do not belong to this run's plan")
        if revision.revision_no != expected_revision + 1:
            raise AssessmentRevisionConflictError("assessment revision number does not follow the expected revision")
        for artifact in revision.photo_artifacts:
            self._verify_assessment_photo(revision.run_id, artifact)
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            latest = connection.execute(
                "SELECT COALESCE(MAX(revision_no), 0) AS revision_no "
                "FROM run_assessment_revisions WHERE run_id = ?",
                (revision.run_id,),
            ).fetchone()["revision_no"]
            if latest != expected_revision:
                raise AssessmentRevisionConflictError(
                    f"assessment changed while editing (expected revision {expected_revision}, current {latest})"
                )
            connection.execute(
                """INSERT INTO run_assessment_revisions (
                    assessment_revision_id, run_id, revision_no, schema_version, created_at_utc,
                    assessment_json, print_attestation_json, assessment_sha256
                ) VALUES (?, ?, ?, 1, ?, ?, ?, ?)""",
                (
                    revision.assessment_revision_id, revision.run_id, revision.revision_no,
                    revision.created_at_utc, _encode({
                        "schema_version": 1,
                        "results": revision.results.to_dict(),
                        "photo_artifacts": [item.to_dict() for item in revision.photo_artifacts],
                    }),
                    _encode(revision.attestation.to_dict()), revision.assessment_sha256,
                ),
            )
            connection.commit()
        except sqlite3.IntegrityError as exc:
            connection.rollback()
            raise DuplicateRecordError("assessment revision ID already exists") from exc
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()
        return revision

    def get_assessment_revision(self, assessment_revision_id: str) -> AssessmentRevision:
        connection = self._connect()
        try:
            row = connection.execute(
                "SELECT * FROM run_assessment_revisions WHERE assessment_revision_id = ?",
                (assessment_revision_id,),
            ).fetchone()
        finally:
            connection.close()
        if row is None:
            raise KeyError(f"assessment revision {assessment_revision_id!r} was not found")
        try:
            assessment_payload = json.loads(row["assessment_json"])
            if set(assessment_payload) != {"schema_version", "results", "photo_artifacts"} or assessment_payload["schema_version"] != 1:
                raise ValueError("assessment payload schema is unsupported")
            results = ExperimentResults.from_dict(assessment_payload["results"])
            attestation = PrintAttestation.from_dict(json.loads(row["print_attestation_json"]))
            revision = AssessmentRevision(
                row["assessment_revision_id"], row["run_id"], row["revision_no"], row["created_at_utc"],
                results, attestation,
                tuple(ArtifactRecord.from_dict(item) for item in assessment_payload["photo_artifacts"]),
                row["assessment_sha256"],
            )
            if row["schema_version"] != 1:
                raise ValueError("assessment row schema is unsupported")
            for artifact in revision.photo_artifacts:
                self._verify_assessment_photo(revision.run_id, artifact)
            revision.results.validate_for(self.get_run(revision.run_id).plan)
            return revision
        except (ValueError, TypeError, KeyError, json.JSONDecodeError) as exc:
            if isinstance(exc, MissingRunArtifactError):
                raise
            raise CorruptLibraryRecordError(
                f"assessment revision {assessment_revision_id!r} is corrupt: {exc}"
            ) from exc

    def latest_assessment_revision(self, run_id: str) -> AssessmentRevision | None:
        connection = self._connect()
        try:
            row = connection.execute(
                "SELECT assessment_revision_id FROM run_assessment_revisions "
                "WHERE run_id = ? ORDER BY revision_no DESC LIMIT 1", (run_id,),
            ).fetchone()
        finally:
            connection.close()
        return None if row is None else self.get_assessment_revision(row["assessment_revision_id"])

    def list_assessment_revisions(self, run_id: str) -> tuple[AssessmentRevision, ...]:
        connection = self._connect()
        try:
            rows = connection.execute(
                "SELECT assessment_revision_id FROM run_assessment_revisions "
                "WHERE run_id = ? ORDER BY revision_no", (run_id,),
            ).fetchall()
        finally:
            connection.close()
        return tuple(self.get_assessment_revision(row["assessment_revision_id"]) for row in rows)

    def save_run_decision(self, decision: RunDecisionRecord) -> RunDecisionRecord:
        """Insert a policy result bound to its exact run assessment revision."""
        if not isinstance(decision, RunDecisionRecord):
            raise TypeError("decision must be a RunDecisionRecord")
        assessment = self.get_assessment_revision(decision.assessment_revision_id)
        if assessment.run_id != decision.run_id:
            raise RunStateError("decision assessment belongs to a different run")
        if decision.confirmation_evidence is not None:
            evidence = decision.confirmation_evidence
            child = self.get_run(evidence["run_id"])
            child_assessment = self.get_assessment_revision(evidence["assessment_revision_id"])
            child_configuration = self.get_configuration_for_run(child.run_id)
            link = self.get_run_config_link(child.run_id) or {}
            candidate = next(
                (item for item in child_configuration.plan.candidates if item.candidate_id == evidence["candidate_id"]),
                None,
            ) if child_configuration is not None else None
            outcome = next(
                (item for item in child_assessment.results.assessments if item.candidate_id == evidence["candidate_id"]),
                None,
            )
            if (
                not decision.can_accept or decision.opt_out_record is not None
                or child_assessment.run_id != child.run_id
                or child_configuration is None
                or child_configuration.plan.plan_id != evidence["plan_id"]
                or link.get("relation_type") != "confirmation"
                or link.get("parent_run_id") != decision.run_id
                or link.get("parent_assessment_revision_id") != decision.assessment_revision_id
                or link.get("parent_candidate_id") != decision.selected_candidate_id
                or candidate is None or len(child_configuration.plan.candidates) != 1
                or outcome is None or outcome.verdict != "pass"
                or not child_assessment.attestation.physically_accepted
            ):
                raise RunStateError("confirmation evidence does not match a passing linked physical assessment")
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                """INSERT INTO run_decisions (
                    decision_id, run_id, assessment_revision_id, created_at_utc, decision_json
                ) VALUES (?, ?, ?, ?, ?)""",
                (decision.decision_id, decision.run_id, decision.assessment_revision_id,
                 decision.created_at_utc, _encode(decision.to_dict())),
            )
            connection.commit()
        except sqlite3.IntegrityError as exc:
            connection.rollback()
            raise DuplicateRecordError("decision ID already exists or its assessment link is invalid") from exc
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()
        return decision

    def get_run_decision(self, decision_id: str) -> RunDecisionRecord:
        connection = self._connect()
        try:
            row = connection.execute("SELECT * FROM run_decisions WHERE decision_id = ?", (decision_id,)).fetchone()
        finally:
            connection.close()
        if row is None:
            raise KeyError(f"run decision {decision_id!r} was not found")
        try:
            decision = RunDecisionRecord.from_dict(json.loads(row["decision_json"]))
            if (decision.decision_id, decision.run_id, decision.assessment_revision_id, decision.created_at_utc) != (
                row["decision_id"], row["run_id"], row["assessment_revision_id"], row["created_at_utc"]
            ):
                raise ValueError("decision index and payload disagree")
            return decision
        except (ValueError, TypeError, KeyError, json.JSONDecodeError) as exc:
            raise CorruptLibraryRecordError(f"run decision {decision_id!r} is corrupt: {exc}") from exc

    def list_run_decisions(self, run_id: str) -> tuple[RunDecisionRecord, ...]:
        connection = self._connect()
        try:
            rows = connection.execute(
                "SELECT decision_id FROM run_decisions WHERE run_id = ? ORDER BY created_at_utc, decision_id",
                (run_id,),
            ).fetchall()
        finally:
            connection.close()
        return tuple(self.get_run_decision(row["decision_id"]) for row in rows)

    def save_run_export(self, export: RunExportRecord) -> RunExportRecord:
        """Save export history only after all three files match their recorded hashes."""
        if not isinstance(export, RunExportRecord):
            raise TypeError("export must be a RunExportRecord")
        assessment = self.get_assessment_revision(export.assessment_revision_id)
        decision = self.get_run_decision(export.decision_id)
        if assessment.run_id != export.run_id or decision.run_id != export.run_id or decision.assessment_revision_id != export.assessment_revision_id:
            raise RunStateError("export run, assessment, and decision links do not match")
        if not decision.can_accept or decision.selected_candidate_id is None:
            raise RunStateError("only an accepted saved decision can be exported")
        files = {
            export.destination_profile: export.profile_sha256,
            export.manifest_path: export.manifest_sha256,
            export.report_path: export.report_sha256,
        }
        for filename, expected_hash in files.items():
            path = Path(filename).expanduser()
            if not path.is_file() or _sha256(path) != expected_hash:
                raise MissingRunArtifactError(f"export artifact is missing or changed: {filename}")
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                """INSERT INTO run_exports (
                    export_id, run_id, assessment_revision_id, decision_id, created_at_utc,
                    export_json, export_sha256
                ) VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (export.export_id, export.run_id, export.assessment_revision_id, export.decision_id,
                 export.created_at_utc, _encode(export.to_dict()), export.export_sha256),
            )
            connection.commit()
        except sqlite3.IntegrityError as exc:
            connection.rollback()
            raise DuplicateRecordError("export ID already exists or its evidence links are invalid") from exc
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()
        return export

    def get_run_export(self, export_id: str) -> RunExportRecord:
        connection = self._connect()
        try:
            row = connection.execute("SELECT * FROM run_exports WHERE export_id = ?", (export_id,)).fetchone()
        finally:
            connection.close()
        if row is None:
            raise KeyError(f"run export {export_id!r} was not found")
        try:
            export = RunExportRecord.from_dict(json.loads(row["export_json"]))
            if (export.export_id, export.run_id, export.assessment_revision_id, export.decision_id, export.created_at_utc, export.export_sha256) != (
                row["export_id"], row["run_id"], row["assessment_revision_id"], row["decision_id"], row["created_at_utc"], row["export_sha256"]
            ):
                raise ValueError("export index and payload disagree")
            return export
        except (ValueError, TypeError, KeyError, json.JSONDecodeError) as exc:
            raise CorruptLibraryRecordError(f"run export {export_id!r} is corrupt: {exc}") from exc

    def list_run_exports(self, run_id: str) -> tuple[RunExportRecord, ...]:
        connection = self._connect()
        try:
            rows = connection.execute(
                "SELECT export_id FROM run_exports WHERE run_id = ? ORDER BY created_at_utc, export_id",
                (run_id,),
            ).fetchall()
        finally:
            connection.close()
        return tuple(self.get_run_export(row["export_id"]) for row in rows)

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

    def _verify_assessment_photo(self, run_id: str, artifact: ArtifactRecord) -> Path:
        path = self._safe_photo_path(run_id, artifact.relative_path)
        if not path.is_file():
            raise MissingRunArtifactError(f"assessment photo is missing: {artifact.relative_path}")
        if path.stat().st_size != artifact.size_bytes or _sha256(path) != artifact.sha256:
            raise MissingRunArtifactError(f"assessment photo hash does not match: {artifact.relative_path}")
        return path

    def _safe_photo_path(self, run_id: str, relative_path: str) -> Path:
        posix, windows = PurePosixPath(relative_path), PureWindowsPath(relative_path)
        if posix.is_absolute() or windows.is_absolute() or windows.drive or ".." in posix.parts or ".." in windows.parts:
            raise LibraryStoreError("assessment photo path must remain inside its run")
        expected = PurePosixPath("runs") / run_id
        if posix.parts[:2] != expected.parts:
            raise LibraryStoreError("assessment photo path must remain inside its owning run")
        root = self.root.resolve()
        candidate = root
        for part in posix.parts:
            candidate = candidate / part
            is_junction = getattr(candidate, "is_junction", lambda: False)
            if candidate.is_symlink() or is_junction():
                raise LibraryStoreError("assessment photo path cannot traverse a symlink")
        resolved = candidate.resolve(strict=False)
        run_root = (self.runs_root / run_id).resolve(strict=False)
        try:
            resolved.relative_to(root)
            resolved.relative_to(run_root)
        except ValueError as exc:
            raise LibraryStoreError("assessment photo path escapes the workspace or its owning run") from exc
        return resolved

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
            if version != CURRENT_SCHEMA_VERSION:
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
