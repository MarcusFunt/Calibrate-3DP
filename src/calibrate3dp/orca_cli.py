"""Headless OrcaSlicer capability probing and isolated G-code slicing.

The wrapper uses only options observed in the installed CLI help, never invokes
a shell, and requires fresh output and data directories for each slice.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import math
from pathlib import Path
from queue import Empty, Queue
import re
import subprocess
from threading import Event, Thread
import time
from typing import Callable, Sequence


_REQUIRED_OPTIONS = frozenset({
    "--slice",
    "--outputdir",
    "--datadir",
    "--load-settings",
    "--load-filaments",
})


class OrcaCliError(RuntimeError):
    """Raised when OrcaSlicer cannot safely run the requested headless slice."""


@dataclass(frozen=True)
class OrcaCliCapabilities:
    executable: Path | None
    version_banner: str | None
    options: frozenset[str]
    help_returncode: int


@dataclass(frozen=True)
class OrcaSliceResult:
    argv: tuple[str, ...]
    started_at: str
    finished_at: str
    returncode: int | None
    timed_out: bool
    stdout: str
    stderr: str
    gcode_files: tuple[Path, ...]
    cancelled: bool = False


def parse_orca_help(help_text: str, *, returncode: int = 0) -> OrcaCliCapabilities:
    """Parse the executable version banner and option names from --help output."""
    if not isinstance(help_text, str):
        raise OrcaCliError("OrcaSlicer help output must be text")
    if not help_text.strip():
        raise OrcaCliError(
            "OrcaSlicer --help returned no text; select its CLI/console executable "
            "instead of the graphical launcher"
        )
    if returncode != 0:
        raise OrcaCliError(f"OrcaSlicer --help exited with status {returncode}")
    options = frozenset(re.findall(r"(?<!\S)--[A-Za-z0-9][A-Za-z0-9-]*", help_text))
    version_banner = next(
        (line.strip() for line in help_text.splitlines() if line.strip().startswith("OrcaSlicer-")),
        None,
    )
    missing = _REQUIRED_OPTIONS - options
    if missing:
        raise OrcaCliError(
            "OrcaSlicer CLI is missing required options: " + ", ".join(sorted(missing))
        )
    return OrcaCliCapabilities(
        executable=None,
        version_banner=version_banner,
        options=options,
        help_returncode=returncode,
    )


class OrcaCli:
    """Run an OrcaSlicer executable with explicit profiles and isolated folders."""

    def __init__(self, executable: str | Path) -> None:
        self.executable = Path(executable)

    def probe(self, *, timeout_seconds: float = 30) -> OrcaCliCapabilities:
        if (
            isinstance(timeout_seconds, bool)
            or not isinstance(timeout_seconds, (int, float))
            or not math.isfinite(timeout_seconds)
            or timeout_seconds <= 0
        ):
            raise OrcaCliError("probe timeout must be a finite number greater than zero")
        try:
            completed = subprocess.run(
                [str(self.executable), "--help"],
                capture_output=True,
                text=True,
                timeout=timeout_seconds,
                check=False,
                shell=False,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise OrcaCliError(f"could not probe OrcaSlicer at {self.executable}: {exc}") from exc
        output = "\n".join(part for part in (completed.stdout, completed.stderr) if part)
        capabilities = parse_orca_help(output, returncode=completed.returncode)
        return OrcaCliCapabilities(
            executable=self.executable,
            version_banner=capabilities.version_banner,
            options=capabilities.options,
            help_returncode=completed.returncode,
        )

    def build_slice_argv(
        self,
        *,
        model_path: str | Path,
        machine_process_profiles: Sequence[str | Path],
        filament_profiles: Sequence[str | Path],
        output_dir: str | Path,
        data_dir: str | Path,
    ) -> tuple[str, ...]:
        """Build a shell-free argv for one model and a resolved profile set."""
        model = self._existing_file(model_path, "model")
        machines = tuple(self._profile_file(item) for item in machine_process_profiles)
        filaments = tuple(self._profile_file(item) for item in filament_profiles)
        if not machines:
            raise OrcaCliError("at least one machine/process profile is required")
        if not filaments:
            raise OrcaCliError("at least one filament profile is required")
        for profile in (*machines, *filaments):
            if ";" in str(profile):
                raise OrcaCliError("profile paths cannot contain semicolons used by Orca's list syntax")

        output = self._existing_empty_directory(output_dir, "output")
        data = self._existing_empty_directory(data_dir, "data")
        if output == data or output in data.parents or data in output.parents:
            raise OrcaCliError("output and data directories must be separate, non-overlapping folders")

        return (
            str(self.executable),
            "--slice", "0",
            "--outputdir", str(output),
            "--datadir", str(data),
            "--load-settings", ";".join(str(item) for item in machines),
            "--load-filaments", ";".join(str(item) for item in filaments),
            str(model),
        )

    def run_slice(
        self,
        *,
        model_path: str | Path,
        machine_process_profiles: Sequence[str | Path],
        filament_profiles: Sequence[str | Path],
        output_dir: str | Path,
        data_dir: str | Path,
        timeout_seconds: float = 300,
        cancel_event: Event | None = None,
        output_callback: Callable[[str, str], None] | None = None,
    ) -> OrcaSliceResult:
        """Run one isolated slice and return logs, timing, and generated G-code."""
        if (
            isinstance(timeout_seconds, bool)
            or not isinstance(timeout_seconds, (int, float))
            or not math.isfinite(timeout_seconds)
            or timeout_seconds <= 0
        ):
            raise OrcaCliError("slice timeout must be a finite number greater than zero")
        argv = self.build_slice_argv(
            model_path=model_path,
            machine_process_profiles=machine_process_profiles,
            filament_profiles=filament_profiles,
            output_dir=output_dir,
            data_dir=data_dir,
        )
        started_at = datetime.now(timezone.utc).isoformat()
        if cancel_event is None and output_callback is None:
            try:
                completed = subprocess.run(
                    list(argv),
                    capture_output=True,
                    text=True,
                    timeout=timeout_seconds,
                    check=False,
                    shell=False,
                )
                returncode: int | None = completed.returncode
                timed_out = False
                cancelled = False
                stdout = self._as_text(completed.stdout)
                stderr = self._as_text(completed.stderr)
            except subprocess.TimeoutExpired as exc:
                returncode = None
                timed_out = True
                cancelled = False
                stdout = self._as_text(exc.stdout)
                stderr = self._as_text(exc.stderr)
            except OSError as exc:
                raise OrcaCliError(f"could not start OrcaSlicer at {self.executable}: {exc}") from exc
        else:
            stdout, stderr, returncode, timed_out, cancelled = self._run_streaming(
                argv,
                timeout_seconds=timeout_seconds,
                cancel_event=cancel_event,
                output_callback=output_callback,
            )
        finished_at = datetime.now(timezone.utc).isoformat()
        output = Path(output_dir)
        gcode_files = tuple(sorted(
            (path for path in output.rglob("*") if path.is_file() and path.suffix.lower() == ".gcode"),
            key=lambda path: str(path).casefold(),
        ))
        return OrcaSliceResult(
            argv=argv,
            started_at=started_at,
            finished_at=finished_at,
            returncode=returncode,
            timed_out=timed_out,
            stdout=stdout,
            stderr=stderr,
            gcode_files=gcode_files,
            cancelled=cancelled,
        )

    def _run_streaming(
        self,
        argv: Sequence[str],
        *,
        timeout_seconds: float,
        cancel_event: Event | None,
        output_callback: Callable[[str, str], None] | None,
    ) -> tuple[str, str, int | None, bool, bool]:
        """Run a CLI process with streamed logs and cooperative cancellation."""
        if cancel_event is not None and cancel_event.is_set():
            return "", "", None, False, True
        try:
            process = subprocess.Popen(
                list(argv),
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                encoding="utf-8",
                errors="replace",
                bufsize=1,
                shell=False,
            )
        except OSError as exc:
            raise OrcaCliError(f"could not start OrcaSlicer at {self.executable}: {exc}") from exc

        output: Queue[tuple[str, str | None]] = Queue()

        def drain(name: str, stream: object) -> None:
            try:
                reader = stream
                while True:
                    line = reader.readline()
                    if not line:
                        break
                    output.put((name, self._as_text(line)))
            finally:
                output.put((name, None))

        assert process.stdout is not None and process.stderr is not None
        readers = (
            Thread(target=drain, args=("stdout", process.stdout), daemon=True),
            Thread(target=drain, args=("stderr", process.stderr), daemon=True),
        )
        for reader in readers:
            reader.start()

        stdout_parts: list[str] = []
        stderr_parts: list[str] = []
        finished_streams = 0
        timed_out = False
        cancelled = False
        deadline = time.monotonic() + timeout_seconds

        def stop_process() -> None:
            if process.poll() is not None:
                return
            try:
                process.terminate()
            except OSError:
                pass
            try:
                process.wait(timeout=0.5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()

        try:
            while finished_streams < 2:
                if process.poll() is None:
                    if cancel_event is not None and cancel_event.is_set():
                        cancelled = True
                        stop_process()
                    elif time.monotonic() >= deadline:
                        timed_out = True
                        stop_process()

                try:
                    name, text = output.get(timeout=0.05)
                except Empty:
                    continue
                if text is None:
                    finished_streams += 1
                    continue
                if name == "stdout":
                    stdout_parts.append(text)
                else:
                    stderr_parts.append(text)
                if output_callback is not None:
                    output_callback(name, text)
            returncode = process.wait()
        except BaseException:
            if process.poll() is None:
                process.kill()
                process.wait()
            raise
        finally:
            for reader in readers:
                reader.join(timeout=1)

        return (
            "".join(stdout_parts),
            "".join(stderr_parts),
            returncode,
            timed_out,
            cancelled,
        )

    @staticmethod
    def _as_text(value: str | bytes | None) -> str:
        if value is None:
            return ""
        if isinstance(value, bytes):
            return value.decode("utf-8", errors="replace")
        return value

    @staticmethod
    def _existing_file(path: str | Path, label: str) -> Path:
        try:
            resolved = Path(path).resolve(strict=True)
        except OSError as exc:
            raise OrcaCliError(f"{label} file does not exist: {path}") from exc
        if not resolved.is_file():
            raise OrcaCliError(f"{label} path is not a file: {path}")
        return resolved

    @classmethod
    def _profile_file(cls, path: str | Path) -> Path:
        resolved = cls._existing_file(path, "profile")
        if resolved.suffix.lower() != ".json":
            raise OrcaCliError(f"Orca CLI profile must be a JSON file: {path}")
        return resolved

    @staticmethod
    def _existing_empty_directory(path: str | Path, label: str) -> Path:
        try:
            resolved = Path(path).resolve(strict=True)
        except OSError as exc:
            raise OrcaCliError(f"{label} directory does not exist: {path}") from exc
        if not resolved.is_dir():
            raise OrcaCliError(f"{label} path is not a directory: {path}")
        try:
            next(resolved.iterdir())
        except StopIteration:
            return resolved
        except OSError as exc:
            raise OrcaCliError(f"could not inspect {label} directory {path}: {exc}") from exc
        raise OrcaCliError(f"{label} directory must be empty to isolate this slice: {path}")
