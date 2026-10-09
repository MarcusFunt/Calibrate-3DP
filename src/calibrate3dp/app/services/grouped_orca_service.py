"""Compile and slice one grouped ironing run through the Orca CLI adapter."""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
from types import MappingProxyType
from typing import Any, Callable, Mapping
from uuid import uuid4

from calibrate3dp.app.models import ProfileSelection
from calibrate3dp.app.services.experiment_service import ExperimentService, ExperimentServiceError
from calibrate3dp.app.services.library_service import LibraryService
from calibrate3dp.domain.records import ArtifactRecord, CalibrationRunRecord, utc_now
from calibrate3dp.grouped_plate import (
    require_grouped_plate_fits_machine,
    validate_grouped_ironing_gcode,
    write_grouped_plate_3mf,
)
from calibrate3dp.orca_cli import OrcaCli, OrcaCliError
from calibrate3dp.orca_profiles import OrcaProfileAdapter
from calibrate3dp.profiles import ResolvedProfile
from calibrate3dp.storage.library_store import LibraryRepository


class GroupedOrcaGenerationError(RuntimeError):
    """Raised when a grouped ironing run cannot be safely recorded or sliced."""


class GroupedOrcaGenerationService:
    """Create versioned grouped runs, keeping profiles and files as separate layers.

    A ``settings_validated`` record only means per-sample ironing flow and speed
    were observed in the expected object toolpaths. The first coupon geometry has
    no physical code or sample labels and is not marked ready to print.
    """

    def __init__(
        self,
        library: LibraryService,
        *,
        cli_provider: Callable[[], OrcaCli | None],
        adapter: OrcaProfileAdapter | None = None,
        experiment_service: ExperimentService | None = None,
        timeout_seconds: float = 300,
    ) -> None:
        if not isinstance(library, LibraryService):
            raise TypeError("library must be a LibraryService")
        if not callable(cli_provider):
            raise TypeError("cli_provider must be callable")
        if timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be greater than zero")
        self.library = library
        self.repository: LibraryRepository = library.repository
        self._cli_provider = cli_provider
        self._adapter = adapter or OrcaProfileAdapter()
        self._experiments = experiment_service or ExperimentService()
        self._timeout_seconds = timeout_seconds

    def generate_ironing(
        self,
        printer_id: str,
        material_id: str,
        *,
        cancel_event=None,
    ) -> CalibrationRunRecord:
        """Create, slice, verify, and retain a nine-sample ironing run."""
        selection = self.library.resolve_selection(printer_id, material_id)
        process_settings = dict(selection.process.settings)
        # This first module is explicitly a top-surface ironing comparison. The
        # active operation is captured in the plan and derived CLI profile.
        process_settings["ironing_type"] = "top"
        effective_process = replace(selection.process, settings=MappingProxyType(process_settings))
        selection = replace(selection, process=effective_process)
        try:
            plan = self._experiments.create_initial("ironing", selection)
        except ExperimentServiceError as exc:
            raise GroupedOrcaGenerationError(str(exc)) from exc

        run_id = f"run-{uuid4().hex}"
        plate_code = self.repository.allocate_plate_code()
        sample_map = tuple(
            {
                "label": f"Sample-{label}",
                "candidate_id": candidate.candidate_id,
                "settings": dict(candidate.overrides),
                "physical_label_present": False,
            }
            for label, candidate in zip("ABCDEFGHI", plan.candidates, strict=False)
        )
        run = CalibrationRunRecord(
            run_id=run_id,
            plate_code=plate_code,
            printer_id=printer_id,
            material_id=material_id,
            status="generating",
            created_at_utc=utc_now(),
            plan=plan,
            profiles=selection,
            sample_map=sample_map,
            validation={"state": "pending", "print_ready": False},
        )
        self.repository.create_run(run)
        run_root = self.repository.runs_root / run_id
        try:
            return self._generate(run, run_root, cancel_event=cancel_event)
        except Exception as exc:
            return self._finalize_failure(run, run_root, str(exc))

    def _generate(self, run: CalibrationRunRecord, run_root: Path, *, cancel_event=None) -> CalibrationRunRecord:
        run_root.mkdir(parents=True, exist_ok=False)
        profiles_dir = run_root / "profiles"
        output_dir = run_root / "output"
        data_dir = run_root / "data"
        profiles_dir.mkdir()
        output_dir.mkdir()
        data_dir.mkdir()
        process_patch = {
            "ironing_type": "top",
            "gcode_comments": "1",
            "gcode_label_objects": "1",
        }
        cli_profiles = {
            "machine": _flatten_profile(run.profiles.printer),
            "process": self._adapter.to_cli_profile(
                run.profiles.process,
                name=f"{run.profiles.process.profile.name} [Calibrate3DP {run.run_id[-8:]}]",
                settings_patch=process_patch,
            ),
            "filament": self._adapter.to_cli_profile(
                run.profiles.filament,
                name=f"{run.profiles.filament.profile.name} [Calibrate3DP {run.run_id[-8:]}]",
            ),
        }
        profile_paths: dict[str, Path] = {}
        for role, payload in cli_profiles.items():
            path = profiles_dir / f"{role}.json"
            _write_json(path, payload)
            profile_paths[role] = path
        plan_path = run_root / "plan.json"
        sample_map_path = run_root / "sample-map.json"
        _write_json(plan_path, run.plan.to_dict())
        _write_json(sample_map_path, {
            "schema_version": 1,
            "plate_code": run.plate_code,
            "physical_labels_in_mesh": False,
            "samples": [dict(item) for item in run.sample_map],
        })
        geometry_bounds = require_grouped_plate_fits_machine(
            run.plan, run.profiles.printer.settings
        )
        model_path = write_grouped_plate_3mf(
            run.plan, plate_code=run.plate_code, destination=run_root / "plate.3mf"
        )

        cli = self._cli_provider()
        if cli is None:
            raise GroupedOrcaGenerationError(
                "Select an OrcaSlicer CLI executable in Settings before generation."
            )
        try:
            capabilities = cli.probe(timeout_seconds=min(30, self._timeout_seconds))
            result = cli.run_slice(
                model_path=model_path,
                machine_process_profiles=(profile_paths["machine"], profile_paths["process"]),
                filament_profiles=(profile_paths["filament"],),
                output_dir=output_dir,
                data_dir=data_dir,
                timeout_seconds=self._timeout_seconds,
                cancel_event=cancel_event,
            )
        except (OrcaCliError, OSError, TimeoutError) as exc:
            raise GroupedOrcaGenerationError(f"Orca could not complete this run: {exc}") from exc

        logs_dir = run_root / "logs"
        logs_dir.mkdir()
        (logs_dir / "stdout.log").write_text(result.stdout, encoding="utf-8", newline="\n")
        (logs_dir / "stderr.log").write_text(result.stderr, encoding="utf-8", newline="\n")
        if result.timed_out:
            validation = {"state": "timeout", "messages": ["Orca exceeded the configured timeout."], "print_ready": False}
            status = "generation_failed"
        elif result.cancelled:
            validation = {"state": "cancelled", "messages": ["Generation was canceled."], "print_ready": False}
            status = "cancelled"
        elif result.returncode != 0:
            validation = {
                "state": "orca_failed",
                "messages": [f"Orca returned exit status {result.returncode}."],
                "print_ready": False,
            }
            status = "generation_failed"
        elif len(result.gcode_files) != 1:
            validation = {
                "state": "output_failed",
                "messages": [f"Expected one G-code file, found {len(result.gcode_files)}."],
                "print_ready": False,
            }
            status = "validation_failed"
        else:
            expected = {str(item["label"]): item["settings"] for item in run.sample_map}
            proof = validate_grouped_ironing_gcode(result.gcode_files[0], expected)
            validation = {
                "state": "sample_settings_validated" if proof.valid else "sample_settings_failed",
                "messages": list(proof.messages),
                "samples": {
                    label: {
                        "positive_extrusion_mm": evidence.positive_extrusion_mm,
                        "extrusion_per_flow_percent": evidence.extrusion_per_flow_percent,
                        "observed_speed_mm_s": list(evidence.observed_speed_mm_s),
                        "ironing_extrusion_moves": evidence.ironing_extrusion_moves,
                    }
                    for label, evidence in proof.samples.items()
                },
                "print_ready": False,
                "print_readiness_reasons": [
                    "Sample names and the six-character code are recorded in metadata, but not printed on the coupons.",
                    "Connected breakaway plate geometry and physical handling are unverified.",
                    "Planned coupon bounds were checked, but keep-out regions and every emitted G-code movement are not checked.",
                    "Start/end G-code, temperature commands, and hardware safety checks are not part of this gate.",
                    "CLI and generated G-code version identities are not yet reconciled into a support-matrix claim.",
                ],
            }
            status = "settings_validated" if proof.valid else "validation_failed"

        validation["geometry_bounds"] = {
            "checked_against": "resolved machine printable_area or bed_size",
            "within_bounds": True,
            "bounds_mm": geometry_bounds.to_dict(),
        }

        gcode_header = None
        if result.gcode_files:
            try:
                with result.gcode_files[0].open("r", encoding="utf-8", errors="replace") as stream:
                    for line in stream:
                        if line.startswith("; generated by "):
                            gcode_header = line.strip().removeprefix("; generated by ")
                            break
            except OSError:
                pass
        contract = {
            "cli_version_banner": capabilities.version_banner,
            "gcode_identity": gcode_header,
            "identity_status": "unreconciled; support not claimed",
            "cli_options": sorted(capabilities.options),
            "argv": list(result.argv),
            "returncode": result.returncode,
            "timed_out": result.timed_out,
            "cancelled": result.cancelled,
            "stdout_path": "logs/stdout.log",
            "stderr_path": "logs/stderr.log",
            "process_profile_patch": process_patch,
            "source_profiles": {
                "printer": _profile_identity(run.profiles.printer, run.profiles.source_hashes.get("printer")),
                "process": _profile_identity(run.profiles.process, run.profiles.source_hashes.get("process")),
                "filament": _profile_identity(run.profiles.filament, run.profiles.source_hashes.get("filament")),
            },
        }
        validation["orca"] = contract
        manifest = {
            "schema_version": 1,
            "run_id": run.run_id,
            "plate_code": run.plate_code,
            "module_id": run.plan.module_id,
            "created_at_utc": run.created_at_utc,
            "plan_path": "plan.json",
            "plan_sha256": _sha256(plan_path),
            "sample_map_path": "sample-map.json",
            "profiles": {role: path.relative_to(run_root).as_posix() for role, path in profile_paths.items()},
            "model": {"path": model_path.relative_to(run_root).as_posix(), "sha256": _sha256(model_path)},
            "validation": validation,
            "orcacli": contract,
            "generation_state": status,
            "print_ready": False,
        }
        _write_json(run_root / "manifest.json", manifest)
        artifacts = _collect_artifacts(self.repository, run_root)
        return self.repository.finalize_run(
            run.run_id, status=status, artifacts=artifacts, validation=validation
        )

    def _finalize_failure(self, run: CalibrationRunRecord, run_root: Path, message: str) -> CalibrationRunRecord:
        validation = {"state": "generation_failed", "messages": [message], "print_ready": False}
        try:
            if run_root.is_dir():
                logs_dir = run_root / "logs"
                logs_dir.mkdir(exist_ok=True)
                (logs_dir / "generation-error.log").write_text(message + "\n", encoding="utf-8")
                _write_json(run_root / "manifest.json", {
                    "schema_version": 1, "run_id": run.run_id, "plate_code": run.plate_code,
                    "generation_state": "generation_failed", "validation": validation,
                    "print_ready": False,
                })
            artifacts = _collect_artifacts(self.repository, run_root) if run_root.is_dir() else ()
            return self.repository.finalize_run(
                run.run_id, status="generation_failed", artifacts=artifacts, validation=validation
            )
        except Exception as persist_error:
            raise GroupedOrcaGenerationError(
                f"{message}; the failed run record could not be finalized: {persist_error}"
            ) from persist_error


def _flatten_profile(profile: ResolvedProfile) -> dict[str, Any]:
    payload = dict(profile.settings)
    payload.pop("inherits", None)
    # Retain the source machine preset identity so process compatibility rules
    # referring to its name continue to match the selected machine profile.
    payload["name"] = profile.profile.name
    payload["type"] = profile.profile.kind
    json.dumps(payload, allow_nan=False)
    return payload


def _profile_identity(profile: ResolvedProfile, source_sha256: str | None) -> dict[str, Any]:
    return {
        "kind": profile.profile.kind,
        "scope": profile.profile.scope,
        "name": profile.profile.name,
        "source": profile.profile.source,
        "source_sha256": source_sha256,
    }


def _write_json(path: Path, payload: Any) -> None:
    path.write_text(
        json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False) + "\n",
        encoding="utf-8",
        newline="\n",
    )


def _collect_artifacts(repository: LibraryRepository, run_root: Path) -> tuple[ArtifactRecord, ...]:
    artifacts: list[ArtifactRecord] = []
    for path in sorted(run_root.rglob("*"), key=lambda item: item.as_posix().casefold()):
        if not path.is_file():
            continue
        try:
            relative = path.resolve().relative_to(repository.root.resolve()).as_posix()
        except ValueError as exc:
            raise GroupedOrcaGenerationError("a generated artifact escaped the workspace") from exc
        media_type = {
            ".3mf": "model/3mf",
            ".gcode": "text/x.gcode",
            ".json": "application/json",
            ".log": "text/plain",
        }.get(path.suffix.lower(), "application/octet-stream")
        artifacts.append(ArtifactRecord(relative, media_type, path.stat().st_size, _sha256(path)))
    return tuple(artifacts)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()
