import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from calibrate3dp.orca_cli import (
    OrcaCli,
    OrcaCliError,
    parse_orca_help,
)


class OrcaCliTests(unittest.TestCase):
    def test_help_parser_records_version_and_does_not_assume_logfile(self):
        help_text = """OrcaSlicer-01.10.01.50:
  --slice arg                 Slice the input file
  --outputdir arg             Output directory
  --datadir arg               Data directory
  --load-settings arg         Load machine/process settings
  --load-filaments arg        Load filament settings
"""
        info = parse_orca_help(help_text, returncode=0)

        self.assertEqual(info.version_banner, "OrcaSlicer-01.10.01.50:")
        self.assertIn("--slice", info.options)
        self.assertNotIn("--logfile", info.options)

    def test_probe_rejects_nonfinite_timeout_before_invoking_process(self):
        with patch("calibrate3dp.orca_cli.subprocess.run") as run:
            with self.assertRaises(OrcaCliError):
                OrcaCli("orca-slicer.exe").probe(timeout_seconds=float("nan"))
        run.assert_not_called()

    def test_probe_uses_help_and_requires_core_options(self):
        completed = subprocess.CompletedProcess(
            args=["orca-slicer.exe", "--help"],
            returncode=0,
            stdout="OrcaSlicer-test:\n  --slice arg\n  --outputdir arg\n  --datadir arg\n  --load-settings arg\n  --load-filaments arg\n",
            stderr="",
        )
        with patch("calibrate3dp.orca_cli.subprocess.run", return_value=completed) as run:
            capabilities = OrcaCli("orca-slicer.exe").probe()

        self.assertEqual(capabilities.version_banner, "OrcaSlicer-test:")
        self.assertEqual(run.call_args.args[0], ["orca-slicer.exe", "--help"])
        self.assertTrue(run.call_args.kwargs["capture_output"])
        self.assertTrue(run.call_args.kwargs["text"])

    def test_builds_shell_free_slice_argv_and_requires_empty_isolated_dirs(self):
        api = __import__("calibrate3dp.orca_cli", fromlist=["OrcaCli"])
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            model = root / "coupon.stl"
            machine = root / "machine.json"
            process = root / "process.json"
            filament = root / "filament.json"
            for path in (model, machine, process, filament):
                path.write_text("{}")
            output = root / "out"
            data = root / "data"
            output.mkdir()
            data.mkdir()

            cli = OrcaCli(root / "orca-slicer.exe")
            argv = cli.build_slice_argv(
                model_path=model,
                machine_process_profiles=(machine, process),
                filament_profiles=(filament,),
                output_dir=output,
                data_dir=data,
            )

            self.assertEqual(argv[0], str(root / "orca-slicer.exe"))
            self.assertEqual(argv[1:3], ("--slice", "0"))
            self.assertEqual(argv[argv.index("--load-settings") + 1], f"{machine};{process}")
            self.assertEqual(argv[argv.index("--load-filaments") + 1], str(filament))
            self.assertEqual(argv[-1], str(model))

            (output / "stale.gcode").write_text("old")
            with self.assertRaises(OrcaCliError):
                cli.build_slice_argv(
                    model_path=model,
                    machine_process_profiles=(machine, process),
                    filament_profiles=(filament,),
                    output_dir=output,
                    data_dir=data,
                )

    def test_rejects_semicolon_profile_path_and_missing_input(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            machine = root / "machine.json"
            filament = root / "filament.json"
            machine.write_text("{}")
            filament.write_text("{}")
            output = root / "out"
            data = root / "data"
            output.mkdir()
            data.mkdir()
            model = root / "coupon.stl"
            model.write_text("solid coupon endsolid coupon")

            with self.assertRaises(OrcaCliError):
                OrcaCli("orca.exe").build_slice_argv(
                    model_path=model,
                    machine_process_profiles=(Path(f"{root};bad.json"),),
                    filament_profiles=(filament,),
                    output_dir=output,
                    data_dir=data,
                )

            with self.assertRaises(OrcaCliError):
                OrcaCli("orca.exe").build_slice_argv(
                    model_path=root / "absent.stl",
                    machine_process_profiles=(machine,),
                    filament_profiles=(filament,),
                    output_dir=output,
                    data_dir=data,
                )

    def test_run_slice_captures_logs_and_generated_gcode_paths(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            model, machine, process, filament = (
                root / "coupon.stl",
                root / "machine.json",
                root / "process.json",
                root / "filament.json",
            )
            for path in (model, machine, process, filament):
                path.write_text("{}")
            output, data = root / "out", root / "data"
            output.mkdir()
            data.mkdir()

            completed = subprocess.CompletedProcess(
                args=[],
                returncode=0,
                stdout="slice complete",
                stderr="warning",
            )

            def simulate_cli(*args, **kwargs):
                (output / "plate_1.gcode").write_text("; generated")
                return completed

            with patch("calibrate3dp.orca_cli.subprocess.run", side_effect=simulate_cli) as run:
                result = OrcaCli(root / "orca-slicer.exe").run_slice(
                    model_path=model,
                    machine_process_profiles=(machine, process),
                    filament_profiles=(filament,),
                    output_dir=output,
                    data_dir=data,
                )

        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stdout, "slice complete")
        self.assertEqual(result.stderr, "warning")
        self.assertEqual(result.gcode_files, (output / "plate_1.gcode",))
        self.assertIsNotNone(result.started_at)
        self.assertIsNotNone(result.finished_at)
        self.assertFalse(result.timed_out)
        self.assertIsInstance(run.call_args.args[0], list)


if __name__ == "__main__":
    unittest.main()
