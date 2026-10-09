"""Headless view models and service contracts for the Qt application."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, Sequence


@dataclass(frozen=True)
class PrinterSummary:
    """Small, presentation-safe description of a saved printer."""

    printer_id: str
    name: str
    model: str
    nozzle: str

    def __post_init__(self) -> None:
        for field_name in ("printer_id", "name", "model", "nozzle"):
            if not getattr(self, field_name).strip():
                raise ValueError(f"{field_name} must not be empty")


class PrinterLibraryService(Protocol):
    """Read-only service boundary consumed by the printer library screen."""

    def list_saved_printers(self) -> Sequence[PrinterSummary]:
        """Return saved printer summaries in display order."""


class PrinterLibraryUnavailableError(RuntimeError):
    """Raised by a repository adapter when the local library cannot be read."""


class EmptyPrinterLibraryService:
    """Default empty data source until the persistent library is connected."""

    def list_saved_printers(self) -> tuple[PrinterSummary, ...]:
        return ()


class PrinterLibraryViewModel:
    """Filter and select printers without depending on Qt widgets."""

    def __init__(self, service: PrinterLibraryService) -> None:
        self._service = service
        self._printers: tuple[PrinterSummary, ...] = ()
        self.load_error: str | None = None
        self._query = ""
        self._selected_id: str | None = None
        self.reload()

    @property
    def printers(self) -> tuple[PrinterSummary, ...]:
        return self._printers

    @property
    def selected_id(self) -> str | None:
        return self._selected_id

    @property
    def selected_printer(self) -> PrinterSummary | None:
        return next(
            (printer for printer in self._printers if printer.printer_id == self._selected_id),
            None,
        )

    def reload(self) -> None:
        """Refresh the saved list, retaining typed repository errors for the UI."""
        try:
            self._printers = tuple(self._service.list_saved_printers())
        except PrinterLibraryUnavailableError as exc:
            self._printers = ()
            self.load_error = str(exc)
        else:
            self.load_error = None
            if self._selected_id and not any(
                printer.printer_id == self._selected_id for printer in self._printers
            ):
                self._selected_id = None

    def visible_printers(self, query: str | None = None) -> tuple[PrinterSummary, ...]:
        if query is not None:
            self._query = query.strip().casefold()
        if not self._query:
            return self._printers
        return tuple(
            printer
            for printer in self._printers
            if self._query in printer.name.casefold()
            or self._query in printer.model.casefold()
        )

    def select(self, printer_id: str) -> None:
        if not any(printer.printer_id == printer_id for printer in self._printers):
            raise ValueError(f"unknown printer id: {printer_id!r}")
        self._selected_id = printer_id

