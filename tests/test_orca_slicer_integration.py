"""Opt-in integration test for an installed OrcaSlicer and stock profile tree.

Set ORCA_SLICER_EXE and ORCA_PROFILE_ROOT to enable this test. The profile
root must contain machine/, process/, and filament/ JSON directories.
"""

import hashlib
import json
import os
from pathlib import Path
import tempfile
import unittest

from calibrate3dp.ironing import create_initial_ironing_experiment
from calibrate3dp.orca_cli import OrcaCli
from calibrate3dp.orca_jobs import slice_ironing_experiment
from calibrate3dp.profiles import ProfileCatalog, ProfileDocument


_REQUIRED_ENV = ("ORCA_SLICER_EXE", "ORCA_PROFILE_ROOT")


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
