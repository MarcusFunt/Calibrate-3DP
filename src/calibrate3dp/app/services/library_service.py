"""Headless profile intake and saved printer/material selection service."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Sequence
from uuid import uuid4

from calibrate3dp.app.models import ProfileSelection
from calibrate3dp.app.services.profile_service import ProfileChoice, ProfileService
from calibrate3dp.domain.records import MaterialRecord, PrinterRecord, ProfileSnapshot, utc_now
from calibrate3dp.profiles import ResolvedProfile
from calibrate3dp.storage.library_store import LibraryRepository


class PrinterMaterialCompatibilityError(ValueError):
    """Raised when saved printer and material contexts cannot be combined."""


class LibraryService:
    """Connect the Orca profile resolver to persistent local records."""

    def __init__(self, repository: LibraryRepository, profiles: ProfileService) -> None:
        if not isinstance(repository, LibraryRepository):
            raise TypeError("repository must be a LibraryRepository")
        if not isinstance(profiles, ProfileService):
            raise TypeError("profiles must be a ProfileService")
        self.repository = repository
        self.profiles = profiles

    def discover_profiles(self) -> tuple[ProfileChoice, ...]:
        return self.profiles.discover_profiles()

    def import_profile(self, path: str | Path) -> tuple[ProfileChoice, ...]:
        self.profiles.import_source(path)
        return tuple(
            choice
            for role in ("printer", "process", "filament")
            for choice in self.profiles.choices(role)
        )

    def profile_choices(self, role: str) -> tuple[ProfileChoice, ...]:
        return self.profiles.choices(role)

    def add_printer(
        self,
        *,
        display_name: str,
        model: str,
        nozzle: str,
        machine_choice: ProfileChoice,
        process_choice: ProfileChoice,
    ) -> PrinterRecord:
        machine = self._resolve(machine_choice, {"machine", "printer"}, "machine")
        process = self._resolve(process_choice, {"process"}, "process")
        record = PrinterRecord(
            printer_id=f"printer-{uuid4().hex}",
            display_name=display_name.strip(),
            model=model.strip(),
            nozzle=nozzle.strip(),
            machine_profile=ProfileSnapshot.capture(machine, self.profiles.source_sha256(machine_choice)),
            process_profile=ProfileSnapshot.capture(process, self.profiles.source_sha256(process_choice)),
            created_at_utc=utc_now(),
        )
        self.repository.add_printer(record)
        return record

    def add_material(
        self,
        *,
        display_name: str,
        nozzle_context: str,
        filament_choice: ProfileChoice,
        toolhead_context: str | None = None,
    ) -> MaterialRecord:
        filament = self._resolve(filament_choice, {"filament"}, "filament")
        record = MaterialRecord(
            material_id=f"material-{uuid4().hex}",
            display_name=display_name.strip(),
            nozzle_context=nozzle_context.strip(),
            filament_profile=ProfileSnapshot.capture(
                filament, self.profiles.source_sha256(filament_choice)
            ),
            toolhead_context=toolhead_context.strip() if toolhead_context and toolhead_context.strip() else None,
            created_at_utc=utc_now(),
        )
        self.repository.add_material(record)
        return record

    def list_printers(self) -> tuple[PrinterRecord, ...]:
        return self.repository.list_printers()

    def list_materials(self) -> tuple[MaterialRecord, ...]:
        return self.repository.list_materials()

    def list_runs(self, *, printer_id: str | None = None):
        return self.repository.list_runs(printer_id=printer_id)

    def resolve_selection(self, printer_id: str, material_id: str) -> ProfileSelection:
        printer = self.repository.get_printer(printer_id)
        material = self.repository.get_material(material_id)
        if _normalize_context(material.nozzle_context) != _normalize_context(printer.nozzle):
            raise PrinterMaterialCompatibilityError(
                f"material {material.display_name!r} is recorded for {material.nozzle_context}, "
                f"but printer {printer.display_name!r} uses {printer.nozzle}"
            )
        source_paths = {
            "printer": printer.machine_profile.profile.profile.source,
            "process": printer.process_profile.profile.profile.source,
            "filament": material.filament_profile.profile.profile.source,
        }
        source_hashes = {
            "printer": printer.machine_profile.source_sha256,
            "process": printer.process_profile.source_sha256,
            "filament": material.filament_profile.source_sha256,
        }
        return ProfileSelection(
            printer=printer.machine_profile.profile,
            process=printer.process_profile.profile,
            filament=material.filament_profile.profile,
            source_paths=source_paths,
            source_hashes=source_hashes,
            compatibility_warnings=(
                "This profile selection is saved locally; only combinations with recorded real-Orca checks are verified.",
            ),
        )

    def _resolve(
        self,
        choice: ProfileChoice,
        expected_kinds: set[str],
        role: str,
    ) -> ResolvedProfile:
        if not isinstance(choice, ProfileChoice) or choice.kind not in expected_kinds:
            raise ValueError(f"select a {role} profile before saving this record")
        return self.profiles.resolve_choice(choice)


def _normalize_context(value: str) -> str:
    return " ".join(value.casefold().replace("mm", " mm").split())
