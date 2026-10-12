"""Create a warned, checksummed ZIP from a PyInstaller onedir distribution."""

from __future__ import annotations

import argparse
import hashlib
from pathlib import Path
import re
import shutil
import sys
import zipfile


_APP_DIRECTORY = "Calibrate-3DP"
_EXECUTABLE = "Calibrate-3DP.exe"
_BUILD_ID_PATTERN = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}\Z")
_DEVELOPMENT_NOTICE = (
    Path(__file__).resolve().parents[1] / "packaging" / "windows" / "DEVELOPMENT-ONLY.txt"
)


def package_windows_application(
    distribution_directory: Path,
    artifact_directory: Path,
    build_id: str,
) -> tuple[Path, Path]:
    """Wrap PyInstaller's onedir output with its warning and a SHA-256 file."""
    distribution = Path(distribution_directory).resolve()
    artifact_root = Path(artifact_directory).resolve()
    if not _BUILD_ID_PATTERN.fullmatch(build_id):
        raise ValueError("build_id must contain 1-64 letters, digits, dots, underscores, or dashes")
    if not (distribution / _EXECUTABLE).is_file():
        raise FileNotFoundError(f"missing packaged executable: {distribution / _EXECUTABLE}")
    if not _DEVELOPMENT_NOTICE.is_file():
        raise FileNotFoundError(f"missing development notice: {_DEVELOPMENT_NOTICE}")
    if distribution == artifact_root or distribution in artifact_root.parents:
        raise ValueError("artifact directory must be outside the PyInstaller distribution")

    artifact_root.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(_DEVELOPMENT_NOTICE, distribution / "DEVELOPMENT-ONLY.txt")
    archive_path = artifact_root / (
        f"{_APP_DIRECTORY}-Windows-x64-DEVELOPMENT-ONLY-{build_id}.zip"
    )
    files = sorted(path for path in distribution.rglob("*") if path.is_file())
    if not files:
        raise ValueError("PyInstaller distribution is empty")

    with zipfile.ZipFile(
        archive_path,
        mode="w",
        compression=zipfile.ZIP_DEFLATED,
        compresslevel=6,
        allowZip64=True,
    ) as archive:
        for path in files:
            relative_path = path.relative_to(distribution).as_posix()
            archive.write(path, f"{_APP_DIRECTORY}/{relative_path}")

    hasher = hashlib.sha256()
    with archive_path.open("rb") as packaged_archive:
        for chunk in iter(lambda: packaged_archive.read(1024 * 1024), b""):
            hasher.update(chunk)
    digest = hasher.hexdigest()
    checksum_path = archive_path.with_suffix(archive_path.suffix + ".sha256")
    checksum_path.write_text(f"{digest}  {archive_path.name}\n", encoding="utf-8")
    return archive_path, checksum_path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--distribution", type=Path, required=True)
    parser.add_argument("--artifact-directory", type=Path, required=True)
    parser.add_argument("--build-id", required=True)
    args = parser.parse_args(argv)
    try:
        archive, checksum = package_windows_application(
            args.distribution,
            args.artifact_directory,
            args.build_id,
        )
    except (FileNotFoundError, ValueError, OSError, zipfile.BadZipFile) as exc:
        print(f"package_windows_app: {exc}", file=sys.stderr)
        return 1
    print(f"Artifact: {archive}")
    print(f"SHA-256: {checksum.read_text(encoding='utf-8').split()[0]}")
    print(f"Checksum: {checksum}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
