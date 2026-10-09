"""Headless OrcaSlicer capability probing and isolated G-code slicing.

The wrapper uses only options observed in the installed CLI help, never invokes
a shell, and requires fresh output and data directories for each slice.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import math
import os
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
_KNOWN_ORCA_ENGINE_RELEASES = {
    # OrcaSlicer v2.3.0's version.inc declares both SoftFever_VERSION=2.3.0
    # and SLIC3R_VERSION=01.10.01.50. This explains the labels; it neither
    # authenticates a local binary nor declares printer/profile compatibility.
    "01.10.01.50": (
        "2.3.0",
        "https://github.com/SoftFever/OrcaSlicer/blob/v2.3.0/version.inc",
    ),
}
_ORCA_BANNER = re.compile(r"^OrcaSlicer-(?P<version>[0-9]+(?:\.[0-9]+){3}):?$", re.IGNORECASE)
_ORCA_RELEASE_TEXT = re.compile(r"^OrcaSlicer\s+(?P<version>[0-9]+\.[0-9]+\.[0-9]+)(?:\s|$)", re.IGNORECASE)


class OrcaCliError(RuntimeError):
    """Raised when OrcaSlicer cannot safely run the requested headless slice."""


@dataclass(frozen=True)
class OrcaCliCapabilities:
    executable: Path | None
    version_banner: str | None
    options: frozenset[str]
    help_returncode: int
    executable_sha256: str | None = None
    executable_size_bytes: int | None = None
    file_version: str | None = None
    product_version: str | None = None
    raw_help_output: str | None = None
    version_output: str | None = None
    version_returncode: int | None = None
    version_error: str | None = None


@dataclass(frozen=True)
class OrcaIdentityEvidence:
    """Evidence that CLI and G-code version labels describe one Orca release."""

    executable_path: str | None
    executable_sha256: str | None
    executable_size_bytes: int | None
    file_version: str | None
    product_version: str | None
    version_banner: str | None
    version_output: str | None
    gcode_identity: str | None
    release_version: str | None
    status: str
    explanation: str
    source_url: str | None
    support_claim: bool = False

    def to_dict(self) -> dict[str, object]:
        return {
            "executable_path": self.executable_path,
            "executable_sha256": self.executable_sha256,
            "executable_size_bytes": self.executable_size_bytes,
            "file_version": self.file_version,
            "product_version": self.product_version,
            "version_banner": self.version_banner,
            "version_output": self.version_output,
            "gcode_identity": self.gcode_identity,
            "release_version": self.release_version,
            "status": self.status,
            "explanation": self.explanation,
            "source_url": self.source_url,
            "support_claim": self.support_claim,
        }


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
        raw_help_output=help_text,
    )


def reconcile_orca_identity(
    capabilities: OrcaCliCapabilities,
    gcode_identity: str | None,
) -> OrcaIdentityEvidence:
    """Reconcile known Orca CLI-engine and G-code release labels fail-closed.

    ``reconciled`` means the reported labels match a documented upstream
    version mapping and an exact executable fingerprint was captured. It does
    not certify binary provenance, printer compatibility, or print readiness.
    """
    if not isinstance(capabilities, OrcaCliCapabilities):
        raise TypeError("capabilities must be OrcaCliCapabilities")

    banner_match = _ORCA_BANNER.fullmatch(capabilities.version_banner or "")
    gcode_match = _ORCA_RELEASE_TEXT.match((gcode_identity or "").strip())
    engine_version = banner_match.group("version") if banner_match else None
    gcode_version = gcode_match.group("version") if gcode_match else None
    known = _KNOWN_ORCA_ENGINE_RELEASES.get(engine_version or "")
    release_version, source_url = known if known is not None else (None, None)
    explanations: list[str] = []

    if not capabilities.executable_sha256:
        explanations.append("the executable fingerprint is unavailable")
    elif not re.fullmatch(r"[0-9a-fA-F]{64}", capabilities.executable_sha256):
        explanations.append("the executable fingerprint is malformed")
    if engine_version is None:
        explanations.append("the CLI banner is missing or unrecognized")
    elif known is None:
        explanations.append(f"CLI engine version {engine_version} has no recorded release mapping")
    if gcode_version is None:
        explanations.append("the G-code identity is missing or unrecognized")
    elif release_version is not None and gcode_version != release_version:
        explanations.append(
            f"the CLI engine mapping identifies {release_version}, but G-code identifies {gcode_version}"
        )

    for label, value in (
        ("file version", capabilities.file_version),
        ("product version", capabilities.product_version),
        ("--version output", capabilities.version_output),
    ):
        if value is None or not value.strip():
            continue
        match = re.search(r"(?<![0-9])([0-9]+\.[0-9]+\.[0-9]+)(?:\.[0-9]+)?(?![0-9])", value)
        if match is None:
            explanations.append(f"{label} is present but unrecognized")
        elif release_version is None or match.group(1) != release_version:
            explanations.append(f"{label} conflicts with the mapped release version")

    reconciled = not explanations and release_version is not None
    if reconciled:
        explanation = (
            f"The CLI engine label {engine_version} and G-code label {gcode_version} both map to "
            f"OrcaSlicer {release_version} in the cited upstream version file. The executable hash "
            "identifies this local binary; it does not authenticate its publisher or establish printer support."
        )
    else:
        explanation = "; ".join(explanations) or "No documented mapping reconciles the reported identities."

    executable = capabilities.executable
    return OrcaIdentityEvidence(
        executable_path=str(executable.resolve(strict=False)) if executable is not None else None,
        executable_sha256=capabilities.executable_sha256,
        executable_size_bytes=capabilities.executable_size_bytes,
        file_version=capabilities.file_version,
        product_version=capabilities.product_version,
        version_banner=capabilities.version_banner,
        version_output=capabilities.version_output,
        gcode_identity=gcode_identity,
        release_version=release_version if reconciled else None,
        status="reconciled" if reconciled else "unresolved",
        explanation=explanation,
        source_url=source_url if reconciled else None,
        support_claim=False,
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
        executable_path = self.executable.resolve(strict=False)
        executable_sha256, executable_size_bytes = _fingerprint_executable(executable_path)
        file_version, product_version = _windows_executable_versions(executable_path)
        version_output = None
        version_returncode = None
        version_error = None
        if "--version" in capabilities.options:
            try:
                version_result = subprocess.run(
                    [str(self.executable), "--version"],
                    capture_output=True,
                    text=True,
                    timeout=timeout_seconds,
                    check=False,
                    shell=False,
                )
                version_returncode = version_result.returncode
                version_output = "\n".join(
                    part for part in (version_result.stdout, version_result.stderr) if part
                )
            except subprocess.TimeoutExpired as exc:
                version_output = "\n".join(
                    part for part in (self._as_text(exc.stdout), self._as_text(exc.stderr)) if part
                ) or None
                version_error = "OrcaSlicer --version timed out"
            except OSError as exc:
                version_error = f"OrcaSlicer --version could not run: {exc}"
        return OrcaCliCapabilities(
            executable=executable_path,
            version_banner=capabilities.version_banner,
            options=capabilities.options,
            help_returncode=completed.returncode,
            executable_sha256=executable_sha256,
            executable_size_bytes=executable_size_bytes,
            file_version=file_version,
            product_version=product_version,
            raw_help_output=output,
            version_output=version_output,
            version_returncode=version_returncode,
            version_error=version_error,
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
            "--arrange", "0",
            "--orient", "0",
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


def _fingerprint_executable(path: Path) -> tuple[str | None, int | None]:
    try:
        if not path.is_file():
            return None, None
        size = path.stat().st_size
        digest = hashlib.sha256()
        with path.open("rb") as stream:
            for block in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(block)
        return digest.hexdigest(), size
    except OSError:
        return None, None


def _windows_executable_versions(path: Path) -> tuple[str | None, str | None]:
    """Read optional PE FileVersion/ProductVersion strings using Win32 APIs."""
    if os.name != "nt":
        return None, None
    try:
        import ctypes
        from ctypes import wintypes

        version_api = ctypes.WinDLL("version", use_last_error=True)
        ignored = wintypes.DWORD()
        get_size = version_api.GetFileVersionInfoSizeW
        get_size.argtypes = (wintypes.LPCWSTR, ctypes.POINTER(wintypes.DWORD))
        get_size.restype = wintypes.DWORD
        size = get_size(str(path), ctypes.byref(ignored))
        if not size:
            return None, None

        data = ctypes.create_string_buffer(size)
        get_info = version_api.GetFileVersionInfoW
        get_info.argtypes = (wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, wintypes.LPVOID)
        get_info.restype = wintypes.BOOL
        if not get_info(str(path), 0, size, data):
            return None, None

        query = version_api.VerQueryValueW
        query.argtypes = (
            wintypes.LPCVOID,
            wintypes.LPCWSTR,
            ctypes.POINTER(ctypes.c_void_p),
            ctypes.POINTER(wintypes.UINT),
        )
        query.restype = wintypes.BOOL
        translation_pointer = ctypes.c_void_p()
        translation_length = wintypes.UINT()
        if not query(
            data,
            r"\VarFileInfo\Translation",
            ctypes.byref(translation_pointer),
            ctypes.byref(translation_length),
        ):
            return None, None
        pair_count = translation_length.value // (2 * ctypes.sizeof(wintypes.WORD))
        if pair_count < 1:
            return None, None
        pairs = ctypes.cast(
            translation_pointer,
            ctypes.POINTER(wintypes.WORD * (pair_count * 2)),
        ).contents

        def read_string(key: str) -> str | None:
            for index in range(0, len(pairs), 2):
                language, codepage = pairs[index], pairs[index + 1]
                pointer = ctypes.c_void_p()
                length = wintypes.UINT()
                subblock = f"\\StringFileInfo\\{language:04x}{codepage:04x}\\{key}"
                if query(data, subblock, ctypes.byref(pointer), ctypes.byref(length)) and pointer.value:
                    value = ctypes.wstring_at(pointer.value).strip()
                    if value:
                        return value
            return None

        return read_string("FileVersion"), read_string("ProductVersion")
    except (AttributeError, OSError, TypeError, ValueError):
        return None, None
