import hashlib
import json
from pathlib import Path
import tempfile
from threading import Event
import unittest

from calibrate3dp.orca_cli import OrcaCliCapabilities, OrcaSliceResult
from calibrate3dp.orca_jobs import OrcaJobError, slice_ironing_experiment
from calibrate3dp.profiles import ProfileCatalog, ProfileDocument


class FakeOrcaCli:
    def __init__(self):
        self.calls = []

    def probe(self):
        return OrcaCliCapabilities(
            executable=Path("fake-orca"),
            version_banner="OrcaSlicer-test",
            options=frozenset({"--slice", "--outputdir", "--datadir", "--load-settings", "--load-filaments"}),
            help_returncode=0,
        )

    def run_slice(
        self,
        *,
        model_path,
        machine_process_profiles,
        filament_profiles,
        output_dir,
        data_dir,
        timeout_seconds,
    ):
        process_path = Path(machine_process_profiles[-1])
        profile = json.loads(process_path.read_text(encoding="utf-8"))
        output = Path(output_dir)
        gcode = output / "plate_1.gcode"
        gcode.write_text(
            f"; ironing_flow = {profile['ironing_flow']}\n"
            f"; ironing_speed = {profile['ironing_speed']}\n",
            encoding="utf-8",
        )
        self.calls.append((profile, Path(model_path), Path(output_dir), Path(data_dir)))
        return OrcaSliceResult(
            argv=("fake-orca", "--slice", "0"),
            started_at="2026-10-08T00:00:00+00:00",
            finished_at="2026-10-08T00:00:01+00:00",
            returncode=0,
            timed_out=False,
            stdout="ok",
            stderr="",
            gcode_files=(gcode,),
        )


class OrcaJobTests(unittest.TestCase):
    def resolved_profiles(self, ironing_type="top"):
        docs = [
            ProfileDocument(
                "Printer", "machine", "test",
                {"type": "machine", "name": "Printer", "start_gcode": "G28", "unknown_machine": {"keep": 1}},
                "machine.json",
            ),
            ProfileDocument(
                "Base process", "process", "test",
                {"type": "process", "name": "Base process", "ironing_type": ironing_type, "ironing_flow": "10", "ironing_speed": "30", "unknown_process": [1, 2]},
                "process-parent.json",
            ),
            ProfileDocument(
                "Fine process", "process", "test",
                {"type": "process", "name": "Fine process", "inherits": "Base process", "layer_height": "0.2"},
                "process-child.json",
            ),
            ProfileDocument(
                "PLA", "filament", "test",
                {"type": "filament", "name": "PLA", "temperature": ["210"], "unknown_filament": True},
                "filament.json",
            ),
        ]
        return {
            kind: ProfileCatalog(docs).resolve(kind, "test", name)
            for kind, name in (
                ("machine", "Printer"),
                ("process", "Fine process"),
                ("filament", "PLA"),
            )
        }

    def test_slices_each_candidate_on_an_independent_coupon_with_flattened_profiles(self):
        from calibrate3dp.ironing import create_initial_ironing_experiment

        profiles = self.resolved_profiles()
        plan = create_initial_ironing_experiment(
            plan_id="iron-001",
            baseline_settings={
                "ironing_type": "top",
                "ironing_flow": "10",
                "ironing_speed": "30",
                "layer_height": "0.2",
            },
            flow_values=(8, 10, 12),
            speed_values=(20, 30, 40),
        )
        cli = FakeOrcaCli()
        with tempfile.TemporaryDirectory() as directory:
            run_dir = Path(directory) / "run"
            result = slice_ironing_experiment(
                plan=plan,
                machine=profiles["machine"],
                process=profiles["process"],
                filament=profiles["filament"],
                cli=cli,
                output_dir=run_dir,
            )

            self.assertTrue(result.success)
            self.assertEqual(len(result.candidates), 9)
            self.assertEqual(len(cli.calls), 9)
            manifest = json.loads((run_dir / "manifest.json").read_text(encoding="utf-8"))
            self.assertEqual(manifest["plan_id"], "iron-001")
            self.assertEqual(manifest["orca"]["version_banner"], "OrcaSlicer-test")
            self.assertEqual(len(manifest["candidate_results"]), 9)
            self.assertEqual(manifest["plan"]["schema_version"], 1)
            expected_plan_hash = hashlib.sha256(
                json.dumps(
                    manifest["plan"],
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                    allow_nan=False,
                ).encode("utf-8")
            ).hexdigest()
            self.assertEqual(manifest["plan_sha256"], expected_plan_hash)
            for profile_kind, snapshot in (
                ("machine", "profiles/machine.json"),
                ("process", "profiles/process-baseline.json"),
                ("filament", "profiles/filament.json"),
            ):
                record = manifest["profiles"][profile_kind]
                self.assertEqual(record["snapshot"], snapshot)
                snapshot_bytes = (run_dir / snapshot).read_bytes()
                self.assertEqual(
                    record["snapshot_sha256"],
                    hashlib.sha256(snapshot_bytes).hexdigest(),
                )
            machine_payload = json.loads((run_dir / "profiles" / "machine.json").read_text(encoding="utf-8"))
            filament_payload = json.loads((run_dir / "profiles" / "filament.json").read_text(encoding="utf-8"))
            self.assertEqual(machine_payload["name"], "Printer")
            self.assertEqual(machine_payload["start_gcode"], "G28")
            self.assertEqual(machine_payload["unknown_machine"], {"keep": 1})
            self.assertEqual(filament_payload["unknown_filament"], True)
            coupon_map = json.loads((run_dir / "coupons" / "coupon-map.json").read_text(encoding="utf-8"))
            self.assertEqual(len(coupon_map["candidates"]), 9)
            for coupon in (run_dir / "coupons").glob("*.stl"):
                if coupon.name != "coupon-map.json":
                    self.assertEqual(coupon.read_text(encoding="ascii").count("facet normal"), 12)

            for candidate, call in zip(result.candidates, cli.calls):
                candidate_dir = run_dir / "candidates" / candidate.candidate_id
                process_payload = json.loads(candidate.process_profile_path.read_text(encoding="utf-8"))
                gcode = candidate.gcode_files[0].read_text(encoding="utf-8")
                self.assertEqual(process_payload["name"], call[0]["name"])
                self.assertNotIn("inherits", process_payload)
                self.assertEqual(process_payload["unknown_process"], [1, 2])
                self.assertEqual(
                    candidate.process_profile_sha256,
                    hashlib.sha256(candidate.process_profile_path.read_bytes()).hexdigest(),
                )
                self.assertEqual(candidate.model_path.name, f"coupon_{candidate.candidate_id}.stl")
                self.assertIn(f"ironing_flow = {candidate.settings_patch['ironing_flow']}", gcode)
                self.assertIn(f"ironing_speed = {candidate.settings_patch['ironing_speed']}", gcode)
                self.assertTrue((candidate_dir / "stdout.txt").is_file())
                self.assertTrue((candidate_dir / "stderr.txt").is_file())
                self.assertNotEqual(candidate.output_dir, candidate.data_dir)

            self.assertEqual(len({item.model_path for item in result.candidates}), 9)
            self.assertEqual(len({item.output_dir for item in result.candidates}), 9)
            self.assertEqual(len({item.data_dir for item in result.candidates}), 9)

    def test_rejects_plan_when_process_baseline_does_not_match_resolved_profile(self):
        from calibrate3dp.ironing import create_initial_ironing_experiment

        profiles = self.resolved_profiles()
        plan = create_initial_ironing_experiment(
            plan_id="wrong-base",
            baseline_settings={"ironing_flow": "99", "ironing_speed": "30"},
            flow_values=(8, 10, 12),
            speed_values=(20, 30, 40),
        )
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaises(OrcaJobError):
                slice_ironing_experiment(
                    plan=plan,
                    machine=profiles["machine"],
                    process=profiles["process"],
                    filament=profiles["filament"],
                    cli=FakeOrcaCli(),
                    output_dir=Path(directory) / "run",
                )

    def test_requires_explicitly_enabling_ironing_for_profiles_that_disable_it(self):
        from calibrate3dp.ironing import create_initial_ironing_experiment

        profiles = self.resolved_profiles("no ironing")
        plan = create_initial_ironing_experiment(
            plan_id="enable-ironing",
            baseline_settings={
                "ironing_type": "top",
                "ironing_flow": "10",
                "ironing_speed": "30",
            },
            flow_values=(8, 10, 12),
            speed_values=(20, 30, 40),
        )
        cli = FakeOrcaCli()
        with tempfile.TemporaryDirectory() as directory:
            run_dir = Path(directory) / "run"
            with self.assertRaises(OrcaJobError):
                slice_ironing_experiment(
                    plan=plan,
                    machine=profiles["machine"],
                    process=profiles["process"],
                    filament=profiles["filament"],
                    cli=cli,
                    output_dir=run_dir,
                )
            self.assertFalse(run_dir.exists())

            hidden_patch_plan = create_initial_ironing_experiment(
                plan_id="hidden-enable-ironing",
                baseline_settings={"ironing_flow": "10", "ironing_speed": "30"},
                flow_values=(8, 10, 12),
                speed_values=(20, 30, 40),
            )
            with self.assertRaises(OrcaJobError):
                slice_ironing_experiment(
                    plan=hidden_patch_plan,
                    machine=profiles["machine"],
                    process=profiles["process"],
                    filament=profiles["filament"],
                    process_baseline_patch={"ironing_type": "top"},
                    cli=cli,
                    output_dir=Path(directory) / "hidden-patch",
                )

            result = slice_ironing_experiment(
                plan=plan,
                machine=profiles["machine"],
                process=profiles["process"],
                filament=profiles["filament"],
                process_baseline_patch={"ironing_type": "top"},
                cli=cli,
                output_dir=run_dir,
            )
            self.assertTrue(result.success)
            self.assertEqual(len(cli.calls), 9)
            self.assertTrue(all(call[0]["ironing_type"] == "top" for call in cli.calls))

    def test_rejects_wrong_module_and_wrong_profile_kinds(self):
        from calibrate3dp.experiments import create_grid_experiment, SweepDimension
        from calibrate3dp.ironing import create_initial_ironing_experiment

        profiles = self.resolved_profiles()
        non_ironing = create_grid_experiment(
            plan_id="speed-only",
            module_id="speed",
            baseline_settings={"speed": 50},
            dimensions=(SweepDimension("speed", (40, 50, 60)),),
            candidate_prefix="S",
        )
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaises(OrcaJobError):
                slice_ironing_experiment(
                    plan=non_ironing,
                    machine=profiles["machine"],
                    process=profiles["process"],
                    filament=profiles["filament"],
                    cli=FakeOrcaCli(),
                    output_dir=Path(directory) / "run",
                )
            plan = create_initial_ironing_experiment(
                plan_id="valid",
                baseline_settings={"ironing_flow": "10", "ironing_speed": "30"},
                flow_values=(8, 10, 12),
                speed_values=(20, 30, 40),
            )
            with self.assertRaises(OrcaJobError):
                slice_ironing_experiment(
                    plan=plan,
                    machine=profiles["machine"],
                    process=profiles["filament"],
                    filament=profiles["filament"],
                    cli=FakeOrcaCli(),
                    output_dir=Path(directory) / "wrong-profile",
                )

    def test_cancelled_experiment_writes_manifest_without_starting_a_slice(self):
        from calibrate3dp.ironing import create_initial_ironing_experiment

        profiles = self.resolved_profiles()
        plan = create_initial_ironing_experiment(
            plan_id="cancel-before-first-candidate",
            baseline_settings={"ironing_type": "top", "ironing_flow": "10", "ironing_speed": "30"},
            flow_values=(8, 10, 12),
            speed_values=(20, 30, 40),
        )
        cli = FakeOrcaCli()
        cancel_event = Event()
        cancel_event.set()

        with tempfile.TemporaryDirectory() as directory:
            run_dir = Path(directory) / "cancelled-run"
            result = slice_ironing_experiment(
                plan=plan,
                machine=profiles["machine"],
                process=profiles["process"],
                filament=profiles["filament"],
                cli=cli,
                output_dir=run_dir,
                cancel_event=cancel_event,
            )

            manifest = json.loads((run_dir / "manifest.json").read_text(encoding="utf-8"))

        self.assertTrue(result.cancelled)
        self.assertFalse(result.success)
        self.assertEqual(result.candidates, ())
        self.assertEqual(cli.calls, [])
        self.assertTrue(manifest["cancelled"])
        self.assertFalse(manifest["success"])
        self.assertEqual(manifest["candidate_results"], [])


if __name__ == "__main__":
    unittest.main()
