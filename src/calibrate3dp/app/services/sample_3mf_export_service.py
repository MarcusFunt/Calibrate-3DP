"""Export one sample mesh from a verified grouped-run 3MF artifact."""

from __future__ import annotations

from pathlib import Path
from typing import Mapping

from calibrate3dp.app.services.library_service import LibraryService
from calibrate3dp.grouped_plate import GroupedPlateError, write_sample_3mf_from_plate


class Sample3MFExportError(ValueError):
    """Saved run evidence cannot produce a standalone sample 3MF."""


class Sample3MFExportService:
    """Write the first saved sample as an independent, geometry-only 3MF."""

    def __init__(self, library: LibraryService) -> None:
        if not isinstance(library, LibraryService):
            raise TypeError("library must be a LibraryService")
        self.library = library
        self.repository = library.repository

    def export_first_sample(
        self, run_id: str, destination: str | Path, *, overwrite: bool = False
    ) -> Path:
        run = self.repository.get_run(run_id)
        geometry = run.validation.get("geometry")
        if not isinstance(geometry, Mapping) or geometry.get("valid") is not True:
            raise Sample3MFExportError("the saved run has no validated sample geometry to export")
        if not run.sample_map or not run.plan.candidates:
            raise Sample3MFExportError("the saved run has no sample-to-candidate mapping")

        sample = run.sample_map[0]
        candidate = run.plan.candidates[0]
        if sample.get("candidate_id") != candidate.candidate_id:
            raise Sample3MFExportError("the first saved sample does not match the first plan candidate")
        stored_label = sample.get("label")
        if stored_label not in {"A", "Sample-A"}:
            raise Sample3MFExportError("the first saved sample is not labeled A")
        settings = sample.get("settings")
        if not isinstance(settings, Mapping) or dict(settings) != dict(candidate.overrides):
            raise Sample3MFExportError("the first saved sample settings do not match the plan")

        plate_artifacts = [
            item for item in run.artifacts
            if item.relative_path.replace("\\", "/").casefold().endswith("/plate.3mf")
        ]
        if len(plate_artifacts) != 1:
            raise Sample3MFExportError("the saved run has no unique plate.3mf artifact")
        try:
            source = self.repository.artifact_path(plate_artifacts[0])
            return write_sample_3mf_from_plate(
                source,
                destination=destination,
                run_id=run.run_id,
                plate_code=run.plate_code,
                sample_label="A",
                candidate_id=candidate.candidate_id,
                settings=settings,
                overwrite=overwrite,
            )
        except (GroupedPlateError, OSError) as exc:
            raise Sample3MFExportError(f"Sample A 3MF export failed: {exc}") from exc
