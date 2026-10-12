from __future__ import annotations

import hashlib
from importlib import import_module, util
from pathlib import Path
import tempfile
import unittest
import xml.etree.ElementTree as ET
import zipfile

from calibrate3dp.app.services.library_service import LibraryService
from calibrate3dp.app.services.profile_service import ProfileService
from calibrate3dp.domain.records import ArtifactRecord
from calibrate3dp.geometry.layout import PlateLayoutRequest, PlateSampleRequest
from calibrate3dp.geometry.specimens import PlateGeometryBackend
from calibrate3dp.grouped_plate import write_plate_geometry_3mf
from calibrate3dp.storage.library_store import LibraryRepository
from calibrate3dp.storage.session_store import SessionRepository
from tests.test_printer_library import _records


_CORE = "http://schemas.microsoft.com/3dmanufacturing/core/2015/02"
_SERVICE_MODULE = "calibrate3dp.app.services.sample_3mf_export_service"


def _saved_run_with_plate(root: Path):
    repository = LibraryRepository(SessionRepository(root))
    printer, material, run = _records(root)
    repository.add_printer(printer)
    repository.add_material(material)
    repository.create_run(run)

    sample = run.sample_map[0]
    geometry = PlateGeometryBackend().build(PlateLayoutRequest(
        samples=(PlateSampleRequest("A", sample["candidate_id"], sample["settings"]),),
        plate_code=run.plate_code,
        printable_polygon=((0, 0), (220, 0), (220, 220), (0, 220)),
    ))
    source = repository.runs_root / run.run_id / "plate.3mf"
    write_plate_geometry_3mf(
        geometry,
        destination=source,
        module_id=run.plan.module_id,
        plan_id=run.plan.plan_id,
    )
    payload = source.read_bytes()
    artifact = ArtifactRecord(
        source.relative_to(repository.root).as_posix(),
        "model/3mf",
        len(payload),
        hashlib.sha256(payload).hexdigest(),
    )
    repository.finalize_run(
        run.run_id,
        status="settings_validated",
        artifacts=(artifact,),
        validation={"state": "sample_settings_validated", "geometry": {"valid": True}, "print_ready": False},
    )
    library = LibraryService(repository, ProfileService())
    return repository, library, run, source


class Sample3MFExportServiceTests(unittest.TestCase):
    def test_exports_first_sample_from_hash_verified_run_without_changing_the_run(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            repository, library, run, source = _saved_run_with_plate(root)
            before = repository.get_run(run.run_id)
            source_hash = hashlib.sha256(source.read_bytes()).hexdigest()
            module_spec = util.find_spec(_SERVICE_MODULE)
            self.assertIsNotNone(module_spec, "single-sample 3MF export service is missing")
            if module_spec is None:
                return
            service_type = getattr(import_module(_SERVICE_MODULE), "Sample3MFExportService")
            destination = root / "exports" / "7K3P9D_Sample-A.3mf"

            exported = service_type(library).export_first_sample(run.run_id, destination)

            self.assertEqual(exported, destination.resolve())
            with zipfile.ZipFile(exported) as archive:
                model = ET.fromstring(archive.read("3D/3dmodel.model"))
                manifest = __import__("json").loads(
                    archive.read("Metadata/calibrate3dp-sample.json")
                )
            objects = model.findall(f".//{{{_CORE}}}resources/{{{_CORE}}}object")
            self.assertEqual([item.get("name") for item in objects], ["Sample-A"])
            self.assertEqual(manifest["run_id"], run.run_id)
            self.assertEqual(manifest["plate_code"], "7K3P9D")
            self.assertEqual(manifest["sample_label"], "A")
            self.assertFalse(manifest["print_ready"])
            self.assertEqual(repository.get_run(run.run_id), before)
            self.assertEqual(hashlib.sha256(source.read_bytes()).hexdigest(), source_hash)

    def test_replaces_an_existing_export_only_after_explicit_overwrite(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            _repository, library, run, _source = _saved_run_with_plate(root)
            module_spec = util.find_spec(_SERVICE_MODULE)
            self.assertIsNotNone(module_spec, "single-sample 3MF export service is missing")
            if module_spec is None:
                return
            service_type = getattr(import_module(_SERVICE_MODULE), "Sample3MFExportService")
            service = service_type(library)
            destination = root / "exports" / "7K3P9D_Sample-A.3mf"
            destination.parent.mkdir(parents=True)
            destination.write_bytes(b"keep existing until overwrite is requested")

            with self.assertRaises(ValueError):
                service.export_first_sample(run.run_id, destination)
            self.assertEqual(destination.read_bytes(), b"keep existing until overwrite is requested")

            exported = service.export_first_sample(run.run_id, destination, overwrite=True)

            self.assertEqual(exported, destination.resolve())
            with zipfile.ZipFile(exported) as archive:
                self.assertIsNone(archive.testzip())
                self.assertIn("Metadata/calibrate3dp-sample.json", archive.namelist())
