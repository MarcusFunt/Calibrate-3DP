"""Opt-in integration test for an installed OrcaSlicer and stock profile tree.

Set ORCA_SLICER_EXE and ORCA_PROFILE_ROOT to enable this test. The profile
root must contain machine/, process/, and filament/ JSON directories.
"""

import hashlib
from contextlib import contextmanager
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import tempfile
import unittest

from calibrate3dp.ironing import create_initial_ironing_experiment
from calibrate3dp.experiments import ExperimentCandidate, ExperimentPlan, SweepDimension
from calibrate3dp.grouped_plate import validate_grouped_ironing_gcode, write_grouped_plate_3mf
from calibrate3dp.orca_cli import OrcaCli
from calibrate3dp.orca_jobs import slice_ironing_experiment
from calibrate3dp.orca_profiles import OrcaProfileAdapter
from calibrate3dp.profiles import ProfileCatalog, ProfileDocument


_REQUIRED_ENV = ("ORCA_SLICER_EXE", "ORCA_PROFILE_ROOT")


@contextmanager
def _integration_root(prefix: str):
    evidence = os.environ.get("ORCA_EVIDENCE_DIR")
    if evidence:
        root = Path(evidence).expanduser() / f"{prefix}-{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')}"
        root.mkdir(parents=True, exist_ok=False)
        yield root
        return
    with tempfile.TemporaryDirectory(prefix=f"calibrate3dp-orca-{prefix}-") as temp:
        yield Path(temp)


@unittest.skipUnless(
    all(os.environ.get(name) for name in _REQUIRED_ENV),
    "set ORCA_SLICER_EXE and ORCA_PROFILE_ROOT to run the local Orca integration test",
)
class OrcaSlicerIntegrationTests(unittest.TestCase):
    def test_ironing_candidates_reach_gcode_with_explicit_values(self):
        profile_root = Path(os.environ["ORCA_PROFILE_ROOT"])
        documents = []
        for kind in ("machine", "process", "filament"):
            for path in (profile_root / kind).glob("*.json"):
                try:
                    payload = json.loads(path.read_text(encoding="utf-8-sig"))
                    if isinstance(payload, dict) and isinstance(payload.get("name"), str) and isinstance(payload.get("type"), str):
                        documents.append(ProfileDocument(
                            payload["name"],
                            payload["type"],
                            "orca-integration",
                            payload,
                            str(path),
                        ))
                except (OSError, json.JSONDecodeError, ValueError):
                    continue

        catalog = ProfileCatalog(documents)
        machine = catalog.resolve(
            "machine",
            "orca-integration",
            os.environ.get("ORCA_MACHINE_PROFILE", "Creality Ender-3 V2 0.4 nozzle"),
        )
        process = catalog.resolve(
            "process",
            "orca-integration",
            os.environ.get("ORCA_PROCESS_PROFILE", "0.20mm Standard @Creality Ender3V2"),
        )
        filament = catalog.resolve(
            "filament",
            "orca-integration",
            os.environ.get("ORCA_FILAMENT_PROFILE", "Creality Generic PLA"),
        )

        ironing_type = os.environ.get("ORCA_IRONING_TYPE", "top")
        baseline = dict(process.settings)
        baseline["ironing_type"] = ironing_type
        flow_values = _numbers(os.environ.get("ORCA_FLOW_VALUES", "12,15,18"))
        speed_values = _numbers(os.environ.get("ORCA_SPEED_VALUES", "10,15,20"))
        plan = create_initial_ironing_experiment(
            plan_id="orca-integration-smoke",
            baseline_settings=baseline,
            flow_values=flow_values,
            speed_values=speed_values,
        )

        with tempfile.TemporaryDirectory(prefix="calibrate3dp-orca-test-") as temp:
            run = slice_ironing_experiment(
                plan=plan,
                machine=machine,
                process=process,
                filament=filament,
                process_baseline_patch={"ironing_type": ironing_type},
                cli=OrcaCli(os.environ["ORCA_SLICER_EXE"]),
                output_dir=Path(temp) / "run",
                timeout_seconds=180,
            )
            self.assertTrue(run.success, "one or more Orca candidate slices failed")
            self.assertEqual(len(run.candidates), len(plan.candidates))
            for candidate in run.candidates:
                self.assertEqual(candidate.returncode, 0, candidate.candidate_id)
                self.assertTrue(candidate.gcode_files, candidate.candidate_id)
                text = candidate.gcode_files[0].read_text(encoding="utf-8", errors="replace")
                self.assertIn(f"; ironing_flow = {candidate.settings_patch['ironing_flow']}", text)
                self.assertIn(f"; ironing_speed = {candidate.settings_patch['ironing_speed']}", text)
                self.assertIn(f"; ironing_type = {ironing_type}", text)
                self.assertIn(";TYPE:Ironing", text)

                gcode = candidate.gcode_files[0]
                self.assertEqual(
                    candidate.gcode_sha256[str(gcode.relative_to(Path(temp) / "run"))],
                    _sha256(gcode),
                )

    def test_grouped_object_settings_are_applied_and_missing_override_is_rejected(self):
        profile_root = Path(os.environ["ORCA_PROFILE_ROOT"])
        documents = []
        for kind in ("machine", "process", "filament"):
            for path in (profile_root / kind).glob("*.json"):
                try:
                    payload = json.loads(path.read_text(encoding="utf-8-sig"))
                except (OSError, json.JSONDecodeError):
                    continue
                if isinstance(payload, dict) and isinstance(payload.get("name"), str) and isinstance(payload.get("type"), str):
                    documents.append(ProfileDocument(payload["name"], payload["type"], "orca-grouped", payload, str(path)))
        catalog = ProfileCatalog(documents)
        machine = catalog.resolve("machine", "orca-grouped", os.environ.get("ORCA_MACHINE_PROFILE", "Creality Ender-3 V2 0.4 nozzle"))
        process = catalog.resolve("process", "orca-grouped", os.environ.get("ORCA_PROCESS_PROFILE", "0.20mm Standard @Creality Ender3V2"))
        filament = catalog.resolve("filament", "orca-grouped", os.environ.get("ORCA_FILAMENT_PROFILE", "Creality Generic PLA"))
        adapter = OrcaProfileAdapter()
        process_cli = adapter.to_cli_profile(
            process,
            name=process.profile.name + " [Calibrate3DP grouped gate]",
            settings_patch={
                "ironing_type": "top",
                "ironing_flow": "5%",
                "ironing_speed": "5",
                "gcode_comments": "1",
                "gcode_label_objects": "1",
            },
        )
        machine_cli = dict(machine.settings)
        machine_cli.pop("inherits", None)
        machine_cli["name"] = machine.profile.name
        machine_cli["type"] = machine.profile.kind
        filament_cli = adapter.to_cli_profile(filament, name=filament.profile.name + " [Calibrate3DP grouped gate]")
        plan = ExperimentPlan(
            plan_id="orca-grouped-settings-gate",
            module_id="ironing",
            baseline_settings={"ironing_type": "top", "ironing_flow": "5%", "ironing_speed": "5"},
            dimensions=(
                SweepDimension("ironing_flow", ("12%", "15%", "18%"), "Ironing flow"),
                SweepDimension("ironing_speed", (10, 15, 20), "Ironing speed"),
            ),
            candidates=tuple(
                ExperimentCandidate(
                    f"candidate-{label.lower()}",
                    {"ironing_flow": flow, "ironing_speed": speed},
                )
                for label, flow, speed in zip("ABC", ("12%", "15%", "18%"), (10, 15, 20), strict=True)
            ),
        )
        expected = {
            f"Sample-{label}": candidate.overrides
            for label, candidate in zip("ABC", plan.candidates, strict=True)
        }
        exe = os.environ["ORCA_SLICER_EXE"]
        cli = OrcaCli(exe)
        capabilities = cli.probe(timeout_seconds=30)
        with _integration_root("grouped-settings-gate") as root:
            profiles = root / "profiles"
            profiles.mkdir()
            paths = {}
            for name, payload in (("machine", machine_cli), ("process", process_cli), ("filament", filament_cli)):
                paths[name] = profiles / f"{name}.json"
                paths[name].write_text(json.dumps(payload, sort_keys=True, indent=2) + "\n", encoding="utf-8")

            def slice_plate(stem: str, *, omit: set[str] | None = None):
                project = write_grouped_plate_3mf(
                    plan,
                    plate_code="7K3P9D",
                    destination=root / f"{stem}.3mf",
                    omitted_candidate_ids=omit or set(),
                )
                output, data = root / f"{stem}-output", root / f"{stem}-data"
                output.mkdir()
                data.mkdir()
                result = cli.run_slice(
                    model_path=project,
                    machine_process_profiles=(paths["machine"], paths["process"]),
                    filament_profiles=(paths["filament"],),
                    output_dir=output,
                    data_dir=data,
                    timeout_seconds=180,
                )
                self.assertFalse(result.timed_out, result.stderr)
                self.assertFalse(result.cancelled, result.stderr)
                self.assertEqual(result.returncode, 0, result.stderr or result.stdout)
                self.assertEqual(len(result.gcode_files), 1, result.stderr or result.stdout)
                logs = root / f"{stem}-logs"
                logs.mkdir(exist_ok=True)
                (logs / "stdout.log").write_text(result.stdout, encoding="utf-8")
                (logs / "stderr.log").write_text(result.stderr, encoding="utf-8")
                return project, result

            def record_evidence(stem: str, project: Path, result, validation) -> None:
                gcode = result.gcode_files[0]
                header = next(
                    (line.strip().removeprefix("; generated by ") for line in gcode.read_text(encoding="utf-8", errors="replace").splitlines() if line.startswith("; generated by ")),
                    None,
                )
                payload = {
                    "schema_version": 1,
                    "executable": str(Path(exe).resolve()),
                    "cli_version_banner": capabilities.version_banner,
                    "gcode_identity": header,
                    "profile_sources": {
                        role: {"path": item.profile.source, "name": item.profile.name, "kind": item.profile.kind, "source_sha256": _sha256(Path(item.profile.source))}
                        for role, item in (("machine", machine), ("process", process), ("filament", filament))
                    },
                    "derived_profiles": {
                        name: {"path": str(path), "sha256": _sha256(path)} for name, path in paths.items()
                    },
                    "input_3mf": {"path": str(project), "sha256": _sha256(project)},
                    "argv": list(result.argv),
                    "returncode": result.returncode,
                    "timed_out": result.timed_out,
                    "cancelled": result.cancelled,
                    "stdout_path": f"{stem}-logs/stdout.log",
                    "stderr_path": f"{stem}-logs/stderr.log",
                    "gcode": {"path": str(gcode), "size_bytes": gcode.stat().st_size, "sha256": _sha256(gcode)},
                    "validation": {
                        "valid": validation.valid,
                        "messages": list(validation.messages),
                        "samples": {
                            name: {
                                "positive_extrusion_mm": value.positive_extrusion_mm,
                                "extrusion_per_flow_percent": value.extrusion_per_flow_percent,
                                "observed_speed_mm_s": list(value.observed_speed_mm_s),
                                "ironing_extrusion_moves": value.ironing_extrusion_moves,
                            }
                            for name, value in validation.samples.items()
                        },
                    },
                }
                (root / f"{stem}-evidence.json").write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")

            positive_project, positive = slice_plate("grouped-positive")
            self.assertIsNotNone(capabilities.version_banner)
            positive_validation = validate_grouped_ironing_gcode(positive.gcode_files[0], expected)
            self.assertTrue(positive_validation.valid, positive_validation.messages)
            record_evidence("grouped-positive", positive_project, positive, positive_validation)
            for sample, evidence in positive_validation.samples.items():
                self.assertGreater(evidence.ironing_extrusion_moves, 0, sample)
                self.assertTrue(evidence.observed_speed_mm_s, sample)
                target_speed = float(expected[sample]["ironing_speed"])
                self.assertEqual(evidence.observed_speed_mm_s, (target_speed,), sample)

            missing_candidate = plan.candidates[1].candidate_id
            negative_project, negative = slice_plate("grouped-negative", omit={missing_candidate})
            negative_validation = validate_grouped_ironing_gcode(negative.gcode_files[0], expected)
            self.assertFalse(negative_validation.valid)
            self.assertTrue(any("Sample-B" in message for message in negative_validation.messages))
            self.assertEqual(negative_validation.samples["Sample-B"].observed_speed_mm_s, (5.0,))
            baseline_extrusion = negative_validation.samples["Sample-B"].positive_extrusion_mm
            for sample, expected_flow in (("Sample-A", 12), ("Sample-B", 15), ("Sample-C", 18)):
                observed_ratio = positive_validation.samples[sample].positive_extrusion_mm / baseline_extrusion
                self.assertAlmostEqual(observed_ratio, expected_flow / 5, delta=0.02)
            record_evidence("grouped-negative", negative_project, negative, negative_validation)


def _numbers(value: str) -> tuple[float, ...]:
    try:
        return tuple(float(item.strip()) for item in value.split(","))
    except ValueError as exc:
        raise ValueError("flow/speed environment variables must be comma-separated numbers") from exc


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


if __name__ == "__main__":
    unittest.main()
