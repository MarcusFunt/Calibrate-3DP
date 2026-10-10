from __future__ import annotations

from pathlib import Path
import tempfile
import unittest

from calibrate3dp.app.services.run_export_service import _setup_hash
from calibrate3dp.domain.run_export import RunExportRecord


class RunExportRecordTests(unittest.TestCase):
    def test_setup_fingerprint_includes_orca_executable_bytes(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            first = root / "orca-a.exe"
            second = root / "orca-b.exe"
            first.write_bytes(b"first executable")
            second.write_bytes(b"second executable")

            self.assertNotEqual(
                _setup_hash("Orca same banner", (), first),
                _setup_hash("Orca same banner", (), second),
            )

    def test_versioned_export_record_roundtrips_with_integrity_hash(self):
        record = RunExportRecord.create(
            export_id="export-1",
            run_id="run-1",
            assessment_revision_id="assessment-1",
            decision_id="decision-1",
            created_at_utc="2026-10-10T12:00:00Z",
            destination_profile="C:/exports/PLA-ironing.json",
            manifest_path="C:/exports/PLA-ironing.manifest.json",
            report_path="C:/exports/PLA-ironing.report.md",
            new_profile_name="PLA ironing",
            source_profile_sha256="a" * 64,
            profile_sha256="b" * 64,
            manifest_sha256="c" * 64,
            report_sha256="d" * 64,
            draft_sha256="e" * 64,
            confirmation_status="confirmed",
        )
        self.assertEqual(RunExportRecord.from_dict(record.to_dict()), record)
        tampered = dict(record.to_dict())
        tampered["new_profile_name"] = "Changed"
        with self.assertRaises(ValueError):
            RunExportRecord.from_dict(tampered)


if __name__ == "__main__":
    unittest.main()
