import subprocess
import tempfile
from threading import Event
import unittest
from io import StringIO
from pathlib import Path
from unittest.mock import patch

from calibrate3dp.orca_cli import (
    OrcaCli,
    OrcaCliCapabilities,
    OrcaCliError,
    OrcaIdentityEvidence,
    parse_orca_help,
    reconcile_orca_identity,
)


class OrcaCliTests(unittest.TestCase):
    @staticmethod
    def _slice_inputs(root):
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
        return model, machine, process, filament, output, data

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

    def test_probe_records_executable_hash_and_raw_version_outputs(self):
        help_text = (
            "OrcaSlicer-01.10.01.50:\n"
            "  --slice arg\n  --outputdir arg\n  --datadir arg\n"
            "  --load-settings arg\n  --load-filaments arg\n  --version\n"
        )
        with tempfile.TemporaryDirectory() as directory:
            executable = Path(directory) / "orca-slicer.exe"
            executable.write_bytes(b"orca binary fixture")
            help_result = subprocess.CompletedProcess([], 0, help_text, "trace output\n")
            version_result = subprocess.CompletedProcess([], 0, "OrcaSlicer 2.3.0\n", "")

            with patch(
                "calibrate3dp.orca_cli.subprocess.run",
                side_effect=(help_result, version_result),
            ) as run:
                capabilities = OrcaCli(executable).probe()

        self.assertEqual(capabilities.executable_sha256, "771b053fa3f9835f1ffcba25b9695d3a73a8ba1fa332bc468beb2c7201f73ae5")
        self.assertEqual(capabilities.raw_help_output, help_text + "\ntrace output\n")
        self.assertEqual(capabilities.version_output, "OrcaSlicer 2.3.0\n")
        self.assertEqual(run.call_count, 2)
        self.assertEqual(run.call_args_list[1].args[0], [str(executable), "--version"])

    def test_reconciles_known_official_orca_release_identity_without_claiming_support(self):
        capabilities = OrcaCliCapabilities(
            executable=Path("orca-slicer.exe"),
            version_banner="OrcaSlicer-01.10.01.50:",
            options=frozenset(),
            help_returncode=0,
            executable_sha256="a" * 64,
        )

        identity = reconcile_orca_identity(capabilities, "OrcaSlicer 2.3.0")

        self.assertIsInstance(identity, OrcaIdentityEvidence)
        self.assertEqual(identity.status, "reconciled")
        self.assertEqual(identity.release_version, "2.3.0")
        self.assertEqual(identity.source_url, "https://github.com/SoftFever/OrcaSlicer/blob/v2.3.0/version.inc")
        self.assertFalse(identity.support_claim)

    def test_unknown_or_incomplete_orca_identity_remains_unresolved(self):
        base = OrcaCliCapabilities(
            executable=Path("orca-slicer.exe"),
            version_banner="OrcaSlicer-99.99.99.99:",
            options=frozenset(),
            help_returncode=0,
            executable_sha256="a" * 64,
        )

        unknown = reconcile_orca_identity(base, "OrcaSlicer 9.9.9")
        missing_fingerprint = reconcile_orca_identity(
            OrcaCliCapabilities(Path("orca-slicer.exe"), "OrcaSlicer-01.10.01.50:", frozenset(), 0),
            "OrcaSlicer 2.3.0",
        )

        self.assertEqual(unknown.status, "unresolved")
        self.assertEqual(missing_fingerprint.status, "unresolved")
        self.assertFalse(unknown.support_claim)
        self.assertFalse(missing_fingerprint.support_claim)

    def test_conflicting_executable_product_version_overrides_known_banner_pair(self):
        capabilities = OrcaCliCapabilities(
            executable=Path("orca-slicer.exe"),
            version_banner="OrcaSlicer-01.10.01.50:",
            options=frozenset(),
            help_returncode=0,
            executable_sha256="a" * 64,
            product_version="2.3.1",
        )

        identity = reconcile_orca_identity(capabilities, "OrcaSlicer 2.3.0")

        self.assertEqual(identity.status, "unresolved")
        self.assertIn("product version", identity.explanation.casefold())

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
            self.assertEqual(argv[argv.index("--arrange") + 1], "0")
            self.assertEqual(argv[argv.index("--orient") + 1], "0")
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

    def test_run_slice_streams_output_from_both_process_channels(self):
        class CompletedProcess:
            stdout = StringIO("slice started\nslice complete\n")
            stderr = StringIO("profile warning\n")

            def poll(self):
                return 0

            def wait(self, timeout=None):
                return 0

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            model, machine, process, filament, output, data = self._slice_inputs(root)
            received = []
            with patch("calibrate3dp.orca_cli.subprocess.Popen", return_value=CompletedProcess()):
                result = OrcaCli(root / "orca-slicer.exe").run_slice(
                    model_path=model,
                    machine_process_profiles=(machine, process),
                    filament_profiles=(filament,),
                    output_dir=output,
                    data_dir=data,
                    output_callback=lambda name, line: received.append((name, line)),
                )

        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stdout, "slice started\nslice complete\n")
        self.assertEqual(result.stderr, "profile warning\n")
        self.assertCountEqual(
            received,
            [
                ("stdout", "slice started\n"),
                ("stdout", "slice complete\n"),
                ("stderr", "profile warning\n"),
            ],
        )
        self.assertFalse(result.cancelled)

    def test_run_slice_terminates_process_when_stream_callback_requests_cancel(self):
        class RunningProcess:
            def __init__(self):
                self.stdout = StringIO("slice started\n")
                self.stderr = StringIO("")
                self.returncode = None

            def poll(self):
                return self.returncode

            def terminate(self):
                self.returncode = -15

            def wait(self, timeout=None):
                return self.returncode

            def kill(self):
                self.returncode = -9

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            model, machine, process, filament, output, data = self._slice_inputs(root)
            process_handle = RunningProcess()
            cancel_event = Event()

            def cancel_after_output(_name, _line):
                cancel_event.set()

            with patch("calibrate3dp.orca_cli.subprocess.Popen", return_value=process_handle):
                result = OrcaCli(root / "orca-slicer.exe").run_slice(
                    model_path=model,
                    machine_process_profiles=(machine, process),
                    filament_profiles=(filament,),
                    output_dir=output,
                    data_dir=data,
                    cancel_event=cancel_event,
                    output_callback=cancel_after_output,
                )

        self.assertTrue(result.cancelled)
        self.assertEqual(result.returncode, -15)
        self.assertEqual(result.stdout, "slice started\n")


if __name__ == "__main__":
    unittest.main()
