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
from calibrate3dp.app.services.experiment_configuration_service import (
    ExperimentConfigurationReview,
    ExperimentConfigurationService,
)
from calibrate3dp.app.services.experiment_service import ExperimentService, ExperimentServiceError
from calibrate3dp.app.services.grouped_layout import grouped_ironing_layout_request
from calibrate3dp.app.services.library_service import LibraryService
from calibrate3dp.app.services.calibration_state_service import CalibrationStateService
from calibrate3dp.calibration.dependencies import DependencyEvaluator
from calibrate3dp.calibration.ironing import IRONING_DEPENDENCY_GRAPH
from calibrate3dp.calibration.state import CalibrationState
from calibrate3dp.domain.experiment_config import SavedExperimentConfiguration
from calibrate3dp.domain.records import ArtifactRecord, CalibrationRunRecord, utc_now
from calibrate3dp.geometry.layout import PlateLayoutError
from calibrate3dp.geometry.specimens import PlateGeometry, PlateGeometryBackend
from calibrate3dp.gcode_preflight import validate_gcode_preflight
from calibrate3dp.grouped_plate import (
    machine_keep_out_polygons,
    require_plate_geometry_fits_machine,
    validate_grouped_ironing_gcode,
    validate_grouped_plate_layout_gcode,
    write_plate_geometry_3mf,
)
from calibrate3dp.orca_cli import OrcaCli, OrcaCliError, reconcile_orca_identity
from calibrate3dp.orca_profiles import OrcaProfileAdapter
from calibrate3dp.profiles import ResolvedProfile
from calibrate3dp.storage.library_store import (
    DuplicatePlateCodeError,
    LibraryRepository,
)


class GroupedOrcaGenerationError(RuntimeError):
    """Raised when a grouped ironing run cannot be safely recorded or sliced."""


class CalibrationBlockedError(GroupedOrcaGenerationError):
    """The current printer, material, profile, or slicer context blocks generation."""

    def __init__(self, state: CalibrationState) -> None:
        self.state = state
        self.reasons = state.reasons
        message = "; ".join(state.reasons) or "required calibration context is unavailable"
        super().__init__(f"Ironing calibration is blocked: {message}")


class GroupedOrcaGenerationService:
    """Create connected, versioned grouped runs and slice through Orca.

    A ``settings_validated`` record only means per-sample ironing flow and speed
    were observed in the expected object toolpaths. It does not mean the plate
    has passed physical handling or full G-code safety checks.
    """

    def __init__(
        self,
        library: LibraryService,
        *,
        cli_provider: Callable[[], OrcaCli | None],
        adapter: OrcaProfileAdapter | None = None,
        experiment_service: ExperimentService | None = None,
        calibration_state_service: CalibrationStateService | None = None,
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
        self.configurations = ExperimentConfigurationService(
            library, experiment_service=self._experiments
        )
        if calibration_state_service is not None and not isinstance(
            calibration_state_service, CalibrationStateService
        ):
            raise TypeError("calibration_state_service must be a CalibrationStateService")
        if (
            calibration_state_service is not None
            and calibration_state_service.repository is not self.repository
        ):
            raise ValueError("calibration state service must use the generation library repository")
        self.calibration_state_service = calibration_state_service or CalibrationStateService(
            library,
            DependencyEvaluator(IRONING_DEPENDENCY_GRAPH),
            slicer_available=lambda: self._cli_provider() is not None,
        )
        self._timeout_seconds = timeout_seconds

    def generate_ironing(
        self,
        printer_id: str,
        material_id: str,
        *,
        cancel_event=None,
    ) -> CalibrationRunRecord:
        """Freeze current review inputs and generate their linked grouped run."""
        try:
            configuration = self.configurations.prepare_ironing(printer_id, material_id).configuration
            self.configurations.save(configuration)
        except (ExperimentServiceError, ValueError) as exc:
            raise GroupedOrcaGenerationError(str(exc)) from exc
        return self.generate_from_configuration(configuration.config_id, cancel_event=cancel_event)

    def prepare_ironing_configuration(
        self,
        printer_id: str,
        material_id: str,
        options=None,
    ) -> ExperimentConfigurationReview:
        """Return a candidate/settings/layout review without allocating a code."""
        try:
            return self.configurations.prepare_ironing(printer_id, material_id, options)
        except (ExperimentServiceError, ValueError) as exc:
            raise GroupedOrcaGenerationError(str(exc)) from exc

    def save_configuration(self, configuration: SavedExperimentConfiguration) -> SavedExperimentConfiguration:
        try:
            return self.configurations.save(configuration)
        except (ValueError, RuntimeError) as exc:
            raise GroupedOrcaGenerationError(str(exc)) from exc

    def generate_from_configuration(
        self,
        config_id: str,
        *,
        cancel_event=None,
    ) -> CalibrationRunRecord:
        """Generate only from a saved immutable revision whose profile context is current."""
        configuration = self.repository.get_configuration(config_id)
        state = self.calibration_state_service.evaluate_configuration(configuration)
        if not state.can_start:
            raise CalibrationBlockedError(state)
        try:
            self._verify_configuration_context(configuration)
        except GroupedOrcaGenerationError as exc:
            refreshed = self.calibration_state_service.evaluate_configuration(configuration)
            if not refreshed.can_start:
                raise CalibrationBlockedError(refreshed) from exc
            raise
        dependency_snapshot = self.calibration_state_service.snapshot_for(configuration)
        snapshot_state = dependency_snapshot.get("state")
        if not isinstance(snapshot_state, Mapping) or snapshot_state.get("can_start") is not True:
            raise CalibrationBlockedError(
                self.calibration_state_service.evaluate_configuration(configuration)
            )
        run_id = f"run-{uuid4().hex}"
        geometry: PlateGeometry | None = None
        geometry_error: str | None = None
        run: CalibrationRunRecord | None = None
        for _attempt in range(128):
            plate_code = self.repository.allocate_plate_code()
            try:
                geometry = PlateGeometryBackend().build(grouped_ironing_layout_request(
                    configuration.plan,
                    plate_code,
                    configuration.profile_selection.printer.settings,
                    configuration.layout_options,
                ))
                require_plate_geometry_fits_machine(
                    geometry, configuration.profile_selection.printer.settings
                )
                geometry_error = None
            except (GroupedOrcaGenerationError, PlateLayoutError, ValueError) as exc:
                geometry = None
                geometry_error = str(exc)
            sample_map = tuple(
                {
                    "label": f"Sample-{label}",
                    "candidate_id": candidate.candidate_id,
                    "settings": dict(candidate.overrides),
                    "physical_label_present": geometry is not None,
                    "physical_plate_code_present": geometry is not None,
                }
                for label, candidate in zip("ABCDEFGHI"[:len(configuration.plan.candidates)], configuration.plan.candidates, strict=True)
            )
            run = CalibrationRunRecord(
                run_id=run_id,
                plate_code=plate_code,
                printer_id=configuration.printer_id,
                material_id=configuration.material_id,
                status="generating",
                created_at_utc=utc_now(),
                plan=configuration.plan,
                profiles=configuration.profile_selection,
                sample_map=sample_map,
                validation={
                    "state": "pending",
                    "geometry": _geometry_validation(geometry) if geometry is not None else {"valid": False, "messages": [geometry_error or "Geometry was not built."]},
                    "print_ready": False,
                    "dependency_snapshot": dependency_snapshot,
                },
            )
            try:
                self.repository.create_run(run, configuration_id=configuration.config_id)
                break
            except DuplicatePlateCodeError:
                run = None
                geometry = None
                geometry_error = None
        if run is None:
            raise GroupedOrcaGenerationError("could not reserve a unique plate code after repeated collisions")
        run_root = self.repository.runs_root / run_id
        if geometry_error is not None or geometry is None:
            return self._finalize_failure(run, run_root, f"Connected plate geometry could not be generated: {geometry_error or 'unknown geometry error'}")
        try:
            return self._generate(run, run_root, geometry=geometry, cancel_event=cancel_event)
        except Exception as exc:
            return self._finalize_failure(run, run_root, str(exc))

    def _verify_configuration_context(self, configuration: SavedExperimentConfiguration) -> None:
        try:
            selection = self.library.resolve_selection(
                configuration.printer_id, configuration.material_id
            )
        except Exception as exc:
            raise GroupedOrcaGenerationError(
                f"The selected printer/material context changed since review: {exc}"
            ) from exc
        process_settings = dict(selection.process.settings)
        process_settings["ironing_type"] = "top"
        selection = replace(
            selection,
            process=replace(selection.process, settings=MappingProxyType(process_settings)),
        )
        if selection.to_dict() != configuration.profile_selection.to_dict():
            raise GroupedOrcaGenerationError(
                "The selected printer/material profile context changed since review; reopen the configuration review."
            )
        current_hashes = self.library.current_source_hashes(selection)
        changed = [
            role for role, expected in configuration.source_profile_hashes.items()
            if current_hashes.get(role) != expected
        ]
        if changed:
            raise GroupedOrcaGenerationError(
                "A source profile changed since review or is no longer available ("
                + ", ".join(sorted(changed))
                + "); review and save the configuration again before generating."
            )

    def _generate(self, run: CalibrationRunRecord, run_root: Path, *, geometry: PlateGeometry, cancel_event=None) -> CalibrationRunRecord:
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
            "physical_labels_in_mesh": True,
            "physical_plate_code_in_mesh": True,
            "samples": [dict(item) for item in run.sample_map],
        })
        geometry_bounds = require_plate_geometry_fits_machine(geometry, run.profiles.printer.settings)
        geometry_dir = run_root / "geometry"
        geometry_dir.mkdir()
        mesh_records: list[dict[str, Any]] = []
        for obj in geometry.objects:
            mesh_path = geometry_dir / f"{obj.name.casefold()}.stl"
            obj.mesh.write_binary_stl(mesh_path, solid_name=obj.name)
            mesh_records.append({
                "name": obj.name,
                "path": mesh_path.relative_to(run_root).as_posix(),
                "sha256": _sha256(mesh_path),
                "size_bytes": mesh_path.stat().st_size,
                "vertices": len(obj.mesh.vertices),
                "triangles": len(obj.mesh.triangles),
                "bounds_mm": list(obj.mesh.bounds or ()),
                "sample_label": obj.sample_label,
                "candidate_id": obj.candidate_id,
                "settings": dict(obj.settings),
                "printed_marking": obj.printed_marking,
            })
        geometry_payload = {
            "schema_version": 1,
            "plate_code": run.plate_code,
            "backend_id": geometry.backend_id,
            "backend_version": geometry.backend_version,
            "voxel_mm": geometry.layout.voxel_mm,
            "physical_labels_in_mesh": True,
            "physical_plate_code_in_mesh": True,
            "layout_bounds_mm": _bounds_dict(geometry.layout.bounds),
            "sample_placements": [
                {
                    "label": item.label,
                    "candidate_id": item.candidate_id,
                    "row": item.row,
                    "column": item.column,
                    "x_mm": item.x_mm,
                    "y_mm": item.y_mm,
                    "width_mm": item.width_mm,
                    "depth_mm": item.depth_mm,
                    "height_mm": item.height_mm,
                }
                for item in geometry.layout.sample_placements
            ],
            "connectors": [
                {
                    "sample_label": item.sample_label,
                    "orientation": item.orientation,
                    "bounds_mm": [item.rectangle.min_x, item.rectangle.min_y, item.rectangle.max_x, item.rectangle.max_y],
                    "width_mm": item.width_mm,
                    "height_mm": item.height_mm,
                    "contact_area_mm2": item.contact_area_mm2,
                }
                for item in geometry.layout.connectors
            ],
            "connections": [
                {"sample_label": item.sample_label, "target_name": item.target_name, "contact_area_mm2": item.contact_area_mm2}
                for item in geometry.connections
            ],
            "objects": mesh_records,
        }
        geometry_manifest_path = geometry_dir / "geometry.json"
        _write_json(geometry_manifest_path, geometry_payload)
        model_path = write_plate_geometry_3mf(
            geometry,
            destination=run_root / "plate.3mf",
            module_id=run.plan.module_id,
            plan_id=run.plan.plan_id,
        )

        cli = self._cli_provider()
        if cli is None:
            raise GroupedOrcaGenerationError(
                "Select an OrcaSlicer CLI executable in Settings before generation."
            )
        try:
            capabilities = cli.probe(timeout_seconds=min(30, self._timeout_seconds))
            logs_dir = run_root / "logs"
            logs_dir.mkdir()
            if capabilities.raw_help_output is not None:
                (logs_dir / "orca-help.log").write_text(
                    capabilities.raw_help_output, encoding="utf-8", newline="\n"
                )
            if capabilities.version_output is not None:
                (logs_dir / "orca-version.log").write_text(
                    capabilities.version_output, encoding="utf-8", newline="\n"
                )
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
        logs_dir.mkdir(exist_ok=True)
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
            layout_proof = validate_grouped_plate_layout_gcode(
                result.gcode_files[0], geometry, run.profiles.printer.settings
            )
            preflight = validate_gcode_preflight(
                result.gcode_files[0],
                run.profiles.printer.settings,
                run.profiles.filament.settings,
                run.profiles.process.settings,
            )
            messages = [*proof.messages, *layout_proof.messages]
            validation = {
                "state": (
                    "plate_layout_failed" if not layout_proof.valid
                    else "sample_settings_validated" if proof.valid
                    else "sample_settings_failed"
                ),
                "messages": messages,
                "samples": {
                    label: {
                        "positive_extrusion_mm": evidence.positive_extrusion_mm,
                        "extrusion_per_flow_percent": evidence.extrusion_per_flow_percent,
                        "observed_speed_mm_s": list(evidence.observed_speed_mm_s),
                        "ironing_extrusion_moves": evidence.ironing_extrusion_moves,
                    }
                    for label, evidence in proof.samples.items()
                },
                "sliced_layout": layout_proof.to_dict(),
                "gcode_preflight": preflight.to_dict(),
                "print_ready": False,
                "print_readiness_reasons": [
                    "A-I sample labels and the six-character code are in the mesh; human readability after printing and physical handling are unverified.",
                    "Breakaway links pass mesh geometry checks but have not passed a physical print or hand-tool separation review.",
                    "The object-to-object plate layout was checked in G-code; every emitted movement is not checked against printable areas and keep-outs.",
                    "Start/end G-code, temperature commands, and hardware safety checks are not part of this gate.",
                    "CLI and generated G-code version identities are not yet reconciled into a support-matrix claim.",
                    *(
                        ["The bounded G-code preflight found issues: " + "; ".join(preflight.errors)]
                        if preflight.errors else []
                    ),
                    *(
                        ["The bounded G-code preflight is incomplete: " + "; ".join(preflight.unverified)]
                        if preflight.unverified else []
                    ),
                    *(
                        ["The bounded G-code preflight does not interpret: " + ", ".join(preflight.unsupported_commands)]
                        if preflight.unsupported_commands else []
                    ),
                ],
            }
            status = "settings_validated" if proof.valid and layout_proof.valid else "validation_failed"

        validation["geometry"] = _geometry_validation(geometry)
        if "sliced_layout" in validation:
            validation["geometry"]["sliced_layout"] = validation["sliced_layout"]
            validation["geometry"]["valid"] = validation["geometry"]["valid"] and validation["sliced_layout"]["valid"]
        validation["geometry_bounds"] = {
            "checked_against": "resolved machine printable_area or bed_size plus explicit bed_exclude_area",
            "within_bounds": bool(validation.get("sliced_layout", {}).get("valid", False)),
            "source_geometry_within_bounds": True,
            "sliced_layout_valid": validation.get("sliced_layout", {}).get("valid"),
            "bounds_mm": geometry_bounds.to_dict(),
            "keep_out_count": len(machine_keep_out_polygons(run.profiles.printer.settings)),
        }
        validation["dependency_snapshot"] = run.validation["dependency_snapshot"]

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
        identity = reconcile_orca_identity(capabilities, gcode_header)
        identity_status = identity.status
        identity_message = (
            "Orca CLI and G-code labels reconcile to OrcaSlicer 2.3.0, but this binary/profile/platform entry is not yet qualified in the support matrix."
            if identity.status == "reconciled"
            else "CLI and generated G-code version identities remain unresolved; no support-matrix claim is made."
        )
        for reason_index, reason in enumerate(validation.get("print_readiness_reasons", ())):
            if reason.startswith("CLI and generated G-code version identities") or reason.startswith(
                "CLI and G-code version identities"
            ):
                validation["print_readiness_reasons"][reason_index] = identity_message
        contract = {
            "cli_version_banner": capabilities.version_banner,
            "gcode_identity": gcode_header,
            "identity_status": identity_status,
            "identity": identity.to_dict(),
            "executable_sha256": capabilities.executable_sha256,
            "executable_size_bytes": capabilities.executable_size_bytes,
            "file_version": capabilities.file_version,
            "product_version": capabilities.product_version,
            "version_output": capabilities.version_output,
            "version_returncode": capabilities.version_returncode,
            "version_error": capabilities.version_error,
            "cli_options": sorted(capabilities.options),
            "argv": list(result.argv),
            "returncode": result.returncode,
            "timed_out": result.timed_out,
            "cancelled": result.cancelled,
            "stdout_path": "logs/stdout.log",
            "stderr_path": "logs/stderr.log",
            "help_output_path": "logs/orca-help.log" if capabilities.raw_help_output is not None else None,
            "version_output_path": "logs/orca-version.log" if capabilities.version_output is not None else None,
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
            "geometry": {
                "path": geometry_manifest_path.relative_to(run_root).as_posix(),
                "sha256": _sha256(geometry_manifest_path),
                "backend_id": geometry.backend_id,
                "backend_version": geometry.backend_version,
                "mesh_objects": mesh_records,
                "physical_labels_in_mesh": True,
                "physical_plate_code_in_mesh": True,
            },
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
        validation = {
            "state": "generation_failed",
            "messages": [message],
            "print_ready": False,
            "dependency_snapshot": run.validation["dependency_snapshot"],
        }
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
            ".stl": "model/stl",
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


def _bounds_dict(bounds) -> dict[str, float]:
    return {
        "min_x": bounds.min_x,
        "min_y": bounds.min_y,
        "min_z": bounds.min_z,
        "max_x": bounds.max_x,
        "max_y": bounds.max_y,
        "max_z": bounds.max_z,
    }


def _geometry_validation(geometry: PlateGeometry) -> dict[str, Any]:
    report = PlateGeometryBackend().validate(geometry)
    return {
        "valid": report.valid,
        "messages": list(report.messages),
        "backend_id": geometry.backend_id,
        "backend_version": geometry.backend_version,
        "object_count": len(geometry.objects),
        "sample_count": sum(obj.sample_label is not None for obj in geometry.objects),
        "connection_count": len(geometry.connections),
        "physical_labels_in_mesh": report.valid and all(obj.printed_marking == obj.sample_label for obj in geometry.objects if obj.sample_label is not None),
        "physical_plate_code_in_mesh": report.valid and any(obj.sample_label is None and obj.printed_marking == geometry.layout.plate_code for obj in geometry.objects),
        "bounds_mm": _bounds_dict(geometry.layout.bounds),
    }
