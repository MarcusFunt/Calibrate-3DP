"""Run candidate-specific ironing coupons through isolated Orca CLI jobs."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
import hashlib
import json
import math
from pathlib import Path
from threading import Event
from typing import Any, Callable, Mapping

from calibrate3dp.coupons import CouponSpec, generate_ironing_coupons
from calibrate3dp.experiments import ExperimentPlan
from calibrate3dp.orca_cli import OrcaCliCapabilities, OrcaSliceResult
from calibrate3dp.orca_profiles import OrcaProfileAdapter
from calibrate3dp.profiles import ResolvedProfile


class OrcaJobError(RuntimeError):
    """Raised when an experiment cannot be prepared safely for OrcaSlicer."""


@dataclass(frozen=True)
class CandidateSlice:
    candidate_id: str
    settings_patch: Mapping[str, Any]
    model_path: Path
    process_profile_path: Path
    process_profile_sha256: str
    output_dir: Path
    data_dir: Path
    gcode_files: tuple[Path, ...]
    gcode_sha256: Mapping[str, str]
    returncode: int | None
    timed_out: bool
    stdout_path: Path
    stderr_path: Path
    argv: tuple[str, ...]
    started_at: str
    finished_at: str
    cancelled: bool = False

    @property
    def success(self) -> bool:
        return self.returncode == 0 and not self.timed_out and not self.cancelled and bool(self.gcode_files)


@dataclass(frozen=True)
class OrcaExperimentRun:
    plan_id: str
    version_banner: str | None
    options: frozenset[str]
    candidates: tuple[CandidateSlice, ...]
    manifest_path: Path
    cancelled: bool = False

    @property
    def success(self) -> bool:
        return not self.cancelled and bool(self.candidates) and all(item.success for item in self.candidates)


def slice_ironing_experiment(
    *,
    plan: ExperimentPlan,
    machine: ResolvedProfile,
    process: ResolvedProfile,
    filament: ResolvedProfile,
    cli: Any,
    output_dir: str | Path,
    process_baseline_patch: Mapping[str, Any] | None = None,
    coupon_spec: CouponSpec = CouponSpec(),
    timeout_seconds: float = 300,
    cancel_event: Event | None = None,
    on_candidate_started: Callable[[int, int, str], None] | None = None,
    on_output: Callable[[str, str], None] | None = None,
) -> OrcaExperimentRun:
    """Generate one top-surface coupon and isolated G-code job per candidate.

    Resolved machine/filament profiles retain their stock identities for Orca
    compatibility. Each process candidate receives a unique name and a fully
    flattened inherited settings document. Source profiles are never edited.
    """
    if not isinstance(plan, ExperimentPlan) or plan.module_id != "ironing":
        raise OrcaJobError("slicing requires an ironing ExperimentPlan")
    _require_profile(machine, {"machine", "printer"}, "machine")
    _require_profile(process, {"process"}, "process")
    _require_profile(filament, {"filament"}, "filament")
    if (
        isinstance(timeout_seconds, bool)
        or not isinstance(timeout_seconds, (int, float))
        or not math.isfinite(timeout_seconds)
        or timeout_seconds <= 0
    ):
        raise OrcaJobError("timeout_seconds must be a finite number greater than zero")
    if not isinstance(coupon_spec, CouponSpec):
        raise OrcaJobError("coupon_spec must be a CouponSpec")
    if not hasattr(cli, "probe") or not hasattr(cli, "run_slice"):
        raise OrcaJobError("cli must provide probe() and run_slice()")
    if process_baseline_patch is None:
        baseline_patch: Mapping[str, Any] = {}
    elif not isinstance(process_baseline_patch, Mapping):
        raise OrcaJobError("process_baseline_patch must map process setting names to JSON values")
    else:
        baseline_patch = process_baseline_patch

    adapter = OrcaProfileAdapter()
    try:
        baseline_payload = adapter.to_cli_profile(
            process,
            name=f"{process.profile.name} [calibrate3dp baseline]",
            settings_patch=baseline_patch,
        )
    except ValueError as exc:
        raise OrcaJobError(f"invalid explicit process baseline patch: {exc}") from exc
    baseline_payload["name"] = process.profile.name
    effective_process = deepcopy(dict(process.settings))
    effective_process.update(deepcopy(dict(baseline_patch)))
    for setting, patched_value in baseline_patch.items():
        if plan.baseline_settings.get(setting, _MISSING) != patched_value:
            raise OrcaJobError(
                f"explicit baseline patch {setting!r} must be recorded in the plan baseline"
            )
    ironing_type = effective_process.get("ironing_type")
    if not isinstance(ironing_type, str) or ironing_type.strip().casefold() in {
        "", "none", "off", "no ironing"
    }:
        raise OrcaJobError(
            "ironing is disabled in the selected process; explicitly set ironing_type "
            "to an enabled mode in process_baseline_patch and include it in the plan baseline"
        )

    for setting, expected in plan.baseline_settings.items():
        actual = effective_process.get(setting, _MISSING)
        if actual is _MISSING or actual != expected:
            raise OrcaJobError(
                f"plan baseline {setting!r} does not match the resolved process profile "
                f"({expected!r} != {actual!r})"
            )

    capabilities = cli.probe()
    if not isinstance(capabilities, OrcaCliCapabilities):
        raise OrcaJobError("Orca CLI probe returned an invalid capability result")

    root = _prepare_empty_directory(output_dir)
    profiles_dir = root / "profiles"
    profiles_dir.mkdir()
    machine_path = profiles_dir / "machine.json"
    filament_path = profiles_dir / "filament.json"
    machine_payload = _flatten_profile(machine)
    filament_payload = _flatten_profile(filament)
    _write_json(machine_path, machine_payload)
    _write_json(filament_path, filament_payload)
    process_baseline_path = profiles_dir / "process-baseline.json"
    _write_json(process_baseline_path, baseline_payload)

    coupon_artifacts = generate_ironing_coupons(plan, root / "coupons", spec=coupon_spec)
    candidate_dir = root / "candidates"
    candidate_dir.mkdir()

    results: list[CandidateSlice] = []
    cancelled = False
    total_candidates = len(plan.candidates)
    for index, (candidate, coupon) in enumerate(zip(plan.candidates, coupon_artifacts), start=1):
        if cancel_event is not None and cancel_event.is_set():
            cancelled = True
            break
        if on_candidate_started is not None:
            on_candidate_started(index, total_candidates, candidate.candidate_id)
        folder = candidate_dir / candidate.candidate_id
        folder.mkdir()
        process_path = folder / "process.json"
        process_name = f"{process.profile.name} [calibrate3dp {plan.plan_id} {candidate.candidate_id}]"
        candidate_patch = deepcopy(dict(baseline_patch))
        candidate_patch.update(deepcopy(dict(candidate.overrides)))
        process_payload = adapter.to_cli_profile(
            process,
            name=process_name,
            settings_patch=candidate_patch,
        )
        _write_json(process_path, process_payload)
        process_profile_sha256 = _sha256_file(process_path)
        output = folder / "output"
        data = folder / "data"
        output.mkdir()
        data.mkdir()

        try:
            run_options = {}
            if cancel_event is not None:
                run_options["cancel_event"] = cancel_event
            if on_output is not None:
                run_options["output_callback"] = on_output
            slice_result: OrcaSliceResult = cli.run_slice(
                model_path=coupon.model_path,
                machine_process_profiles=(machine_path, process_path),
                filament_profiles=(filament_path,),
                output_dir=output,
                data_dir=data,
                timeout_seconds=timeout_seconds,
                **run_options,
            )
        except Exception as exc:
            raise OrcaJobError(
                f"Orca CLI failed to run candidate {candidate.candidate_id!r}: {exc}"
            ) from exc
        if not isinstance(slice_result, OrcaSliceResult):
            raise OrcaJobError(
                f"Orca CLI returned an invalid result for candidate {candidate.candidate_id!r}"
            )

        stdout_path = folder / "stdout.txt"
        stderr_path = folder / "stderr.txt"
        stdout_path.write_text(slice_result.stdout, encoding="utf-8", newline="\n")
        stderr_path.write_text(slice_result.stderr, encoding="utf-8", newline="\n")
        hashes = {
            str(path.relative_to(root)): _sha256_file(path)
            for path in slice_result.gcode_files
        }
        results.append(CandidateSlice(
            candidate_id=candidate.candidate_id,
            settings_patch=deepcopy(dict(candidate.overrides)),
            model_path=coupon.model_path,
            process_profile_path=process_path,
            process_profile_sha256=process_profile_sha256,
            output_dir=output,
            data_dir=data,
            gcode_files=tuple(slice_result.gcode_files),
            gcode_sha256=hashes,
            returncode=slice_result.returncode,
            timed_out=slice_result.timed_out,
            stdout_path=stdout_path,
            stderr_path=stderr_path,
            argv=slice_result.argv,
            started_at=slice_result.started_at,
            finished_at=slice_result.finished_at,
            cancelled=slice_result.cancelled,
        ))
        if slice_result.cancelled:
            cancelled = True
            break

    run = OrcaExperimentRun(
        plan_id=plan.plan_id,
        version_banner=capabilities.version_banner,
        options=capabilities.options,
        candidates=tuple(results),
        manifest_path=root / "manifest.json",
        cancelled=cancelled,
    )
    manifest = _manifest(
        run=run,
        root=root,
        plan=plan,
        machine=machine,
        process=process,
        filament=filament,
        machine_payload=machine_payload,
        process_payload=baseline_payload,
        process_baseline_patch=baseline_patch,
        filament_payload=filament_payload,
        machine_path=machine_path,
        process_baseline_path=process_baseline_path,
        filament_path=filament_path,
    )
    _write_json(run.manifest_path, manifest)
    return run


def _require_profile(profile: ResolvedProfile, kinds: set[str], label: str) -> None:
    if not isinstance(profile, ResolvedProfile) or profile.profile.kind not in kinds:
        raise OrcaJobError(f"{label} must be a resolved profile of kind {sorted(kinds)}")


def _prepare_empty_directory(path: str | Path) -> Path:
    root = Path(path).resolve()
    if root.exists():
        if not root.is_dir():
            raise OrcaJobError(f"run output path is not a directory: {root}")
        if any(root.iterdir()):
            raise OrcaJobError(f"run output directory must be empty: {root}")
    else:
        root.mkdir(parents=True)
    return root


def _flatten_profile(profile: ResolvedProfile) -> dict[str, Any]:
    payload = deepcopy(dict(profile.settings))
    payload.pop("inherits", None)
    payload["name"] = profile.profile.name
    payload["type"] = profile.profile.kind
    try:
        json.dumps(payload, allow_nan=False)
    except (TypeError, ValueError) as exc:
        raise OrcaJobError(
            f"resolved {profile.profile.kind} profile contains values that are not valid JSON"
        ) from exc
    return payload


def _write_json(path: Path, payload: Mapping[str, Any]) -> None:
    path.write_text(
        json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False) + "\n",
        encoding="utf-8",
        newline="\n",
    )


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _canonical_sha256(payload: Mapping[str, Any]) -> str:
    data = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)
    return hashlib.sha256(data.encode("utf-8")).hexdigest()


def _manifest(
    *,
    run: OrcaExperimentRun,
    root: Path,
    plan: ExperimentPlan,
    machine: ResolvedProfile,
    process: ResolvedProfile,
    filament: ResolvedProfile,
    machine_payload: Mapping[str, Any],
    process_payload: Mapping[str, Any],
    process_baseline_patch: Mapping[str, Any],
    filament_payload: Mapping[str, Any],
    machine_path: Path,
    process_baseline_path: Path,
    filament_path: Path,
) -> dict[str, Any]:
    def profile_record(
        profile: ResolvedProfile,
        payload: Mapping[str, Any],
        snapshot_path: Path,
    ) -> dict[str, Any]:
        return {
            "kind": profile.profile.kind,
            "scope": profile.profile.scope,
            "name": profile.profile.name,
            "source": profile.profile.source,
            "inheritance_chain": [
                {"scope": item.scope, "name": item.name, "source": item.source}
                for item in profile.chain
            ],
            "snapshot": snapshot_path.relative_to(root).as_posix(),
            "snapshot_sha256": _sha256_file(snapshot_path),
            "effective_settings_sha256": _canonical_sha256(payload),
        }

    plan_payload = plan.to_dict()
    return {
        "schema_version": 1,
        "plan_id": run.plan_id,
        "module_id": plan.module_id,
        "plan": plan_payload,
        "plan_sha256": _canonical_sha256(plan_payload),
        "orca": {
            "version_banner": run.version_banner,
            "options": sorted(run.options),
        },
        "profiles": {
            "machine": profile_record(machine, machine_payload, machine_path),
            "process": profile_record(process, process_payload, process_baseline_path),
            "filament": profile_record(filament, filament_payload, filament_path),
        },
        "process_baseline_patch": deepcopy(dict(process_baseline_patch)),
        "candidate_results": [
            {
                "candidate_id": item.candidate_id,
                "settings_patch": deepcopy(dict(item.settings_patch)),
                "model": str(item.model_path.relative_to(root)),
                "process_profile": str(item.process_profile_path.relative_to(root)),
                "process_profile_sha256": item.process_profile_sha256,
                "output_dir": str(item.output_dir.relative_to(root)),
                "data_dir": str(item.data_dir.relative_to(root)),
                "gcode": [
                    {
                        "path": str(path.relative_to(root)),
                        "sha256": item.gcode_sha256[str(path.relative_to(root))],
                    }
                    for path in item.gcode_files
                ],
                "returncode": item.returncode,
                "timed_out": item.timed_out,
                "cancelled": item.cancelled,
                "argv": list(item.argv),
                "started_at": item.started_at,
                "finished_at": item.finished_at,
                "stdout": str(item.stdout_path.relative_to(root)),
                "stderr": str(item.stderr_path.relative_to(root)),
            }
            for item in run.candidates
        ],
        "success": run.success,
        "cancelled": run.cancelled,
    }


_MISSING = object()
