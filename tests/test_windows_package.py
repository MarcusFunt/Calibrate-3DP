from __future__ import annotations

import hashlib
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
import zipfile


ROOT = Path(__file__).resolve().parents[1]


class WindowsDevelopmentPackageTests(unittest.TestCase):
    def test_package_zip_contains_the_warning_and_a_matching_checksum(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            work = Path(temporary)
            distribution = work / "dist" / "Calibrate-3DP"
            distribution.mkdir(parents=True)
            (distribution / "Calibrate-3DP.exe").write_bytes(b"fake windows executable")
            (distribution / "Qt6Core.dll").write_bytes(b"runtime dependency")
            artifacts = work / "artifacts"

            completed = subprocess.run(
                [
                    sys.executable,
                    str(ROOT / "tools" / "package_windows_app.py"),
                    "--distribution",
                    str(distribution),
                    "--artifact-directory",
                    str(artifacts),
                    "--build-id",
                    "run-123-attempt-1",
                ],
                cwd=ROOT,
                capture_output=True,
                text=True,
                check=False,
            )

            self.assertEqual(completed.returncode, 0, completed.stderr)
            package = artifacts / "Calibrate-3DP-Windows-x64-DEVELOPMENT-ONLY-run-123-attempt-1.zip"
            checksum_file = package.with_suffix(package.suffix + ".sha256")
            self.assertTrue(package.is_file())
            self.assertTrue(checksum_file.is_file())

            with zipfile.ZipFile(package) as archive:
                names = set(archive.namelist())
                notice_path = "Calibrate-3DP/DEVELOPMENT-ONLY.txt"
                self.assertIn("Calibrate-3DP/Calibrate-3DP.exe", names)
                self.assertIn("Calibrate-3DP/Qt6Core.dll", names)
                self.assertIn(notice_path, names)
                notice = archive.read(notice_path).decode("utf-8")

            self.assertIn("STILL IN DEVELOPMENT", notice)
            self.assertIn("NOT TRUSTWORTHY", notice)
            expected_digest = hashlib.sha256(package.read_bytes()).hexdigest()
            self.assertEqual(
                checksum_file.read_text(encoding="utf-8"),
                f"{expected_digest}  {package.name}\n",
            )


if __name__ == "__main__":
    unittest.main()
