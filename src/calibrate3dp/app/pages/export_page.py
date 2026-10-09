"""Review accepted profile changes and write user-selected export artifacts."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Callable

from calibrate3dp.app.models import SessionSnapshot
from calibrate3dp.app.services.export_service import (
    ExportDraft,
    ExportResult,
    ExportService,
    ExportServiceError,
    StaleExportDraftError,
)


class ExportPage:
    """Show a precise preset diff and require an explicit destination."""

    def __init__(
        self,
        dpg: Any,
        export_service: ExportService,
        *,
        on_back: Callable[[], None] | None = None,
        on_exported: Callable[[SessionSnapshot], None] | None = None,
        default_export_root: str | Path | None = None,
    ) -> None:
        self.dpg = dpg
        self.export_service = export_service
        self.on_back = on_back
        self.on_exported = on_exported
        self.default_export_root = Path(default_export_root) if default_export_root else None
        self.session: SessionSnapshot | None = None
        self.draft: ExportDraft | None = None
        self.result: ExportResult | None = None
        self.error = ""
        self._rendered = False

    @property
    def bundle_export_available(self) -> bool:
        return self.export_service.bundle_export_available

    def set_session(self, session: SessionSnapshot) -> None:
        if not isinstance(session, SessionSnapshot):
            raise TypeError("session must be a SessionSnapshot")
        self.session = session
        self.draft = None
        self.result = None
        self.error = ""
        if self._rendered:
            source_name = session.profile_selection.process.profile.name
            self._set("export_profile_name", f"{source_name} - Calibrated")
            self._refresh_ui()

    def build_draft(self, new_profile_name: str | None = None) -> ExportDraft | None:
        if self.session is None:
            self.error = "Accept a calibration result before preparing a profile export."
            self._refresh_ui()
            return None
        name = new_profile_name
        if name is None and self._rendered:
            value = self.dpg.get_value("export_profile_name")
            name = value if isinstance(value, str) else ""
        if name is None:
            name = f"{self.session.profile_selection.process.profile.name} - Calibrated"
        try:
            draft = self.export_service.build_draft(self.session, new_profile_name=name)
        except (ExportServiceError, TypeError, ValueError) as exc:
            self.error = str(exc)
            self._refresh_ui()
            return None
        self.draft = draft
        self.result = None
        self.error = ""
        self._refresh_ui()
        return draft

    def write_to_destination(self, destination: str | Path | None = None) -> ExportResult | None:
        target = destination
        if target is None and self._rendered:
            value = self.dpg.get_value("export_destination")
            target = value if isinstance(value, str) else None
        if self.draft is None:
            self.error = "Build and review the export draft before writing files."
            self._refresh_ui()
            return None
        try:
            result = self.export_service.export(self.draft, target)
        except StaleExportDraftError as exc:
            self.draft = None
            self.result = None
            self.error = str(exc)
            self._refresh_ui()
            return None
        except (ExportServiceError, OSError, TypeError, ValueError) as exc:
            self.error = str(exc)
            self._refresh_ui()
            return None
        self.result = result
        self.error = ""
        if self.on_exported is not None and self.session is not None:
            try:
                self.on_exported(self.session)
            except Exception as exc:
                self.error = f"Export files were written, but session completion could not be saved: {exc}"
        self._refresh_ui()
        return result

    def render(self) -> None:
        if self._rendered:
            self._refresh_ui()
            return
        dpg = self.dpg
        with dpg.group(tag="export_panel", show=False):
            dpg.add_spacer(height=14)
            dpg.add_text("REVIEWED PROFILE EXPORT", color=(92, 191, 178, 255))
            dpg.add_text(
                "Review the accepted setting changes and evidence before writing a new process preset.",
                color=(165, 180, 195, 255),
                wrap=850,
            )
            dpg.add_text("", tag="export_source", wrap=850)
            dpg.add_text("", tag="export_source_hash", wrap=850)
            dpg.add_text("", tag="export_changes", wrap=850)
            dpg.add_text("", tag="export_evidence", wrap=850)
            dpg.add_text("", tag="export_compatibility", wrap=850)
            dpg.add_text("", tag="export_confirmation", wrap=850)
            dpg.add_input_text(
                label="New process profile name",
                tag="export_profile_name",
                width=-1,
            )
            dpg.add_button(
                label="Build review draft",
                tag="export_build_draft",
                callback=self._on_build_draft,
            )
            dpg.add_spacer(height=8)
            dpg.add_input_text(
                label="Preset destination",
                tag="export_destination",
                readonly=True,
                width=-1,
            )
            with dpg.group(horizontal=True):
                dpg.add_button(
                    label="Browse destination",
                    tag="export_browse",
                    callback=self._on_browse,
                )
                dpg.add_button(
                    label="Write preset, manifest, and report",
                    tag="export_write",
                    enabled=False,
                    callback=self._on_write,
                )
                dpg.add_button(
                    label="Import bundle unavailable",
                    tag="export_bundle",
                    enabled=False,
                )
                dpg.add_button(
                    label="Back to recommendation",
                    tag="export_back",
                    callback=self._on_back,
                )
            dialog_options = {
                "tag": "export_destination_dialog",
                "show": False,
                "modal": True,
                "width": 760,
                "height": 480,
                "directory_selector": False,
                "default_filename": "calibrated-process.json",
                "callback": self._on_destination_selected,
            }
            if self.default_export_root is not None:
                dialog_options["default_path"] = str(self.default_export_root)
            with dpg.file_dialog(**dialog_options):
                dpg.add_file_extension(".json", custom_text="[Orca process preset]")
            dpg.add_text("", tag="export_error", wrap=850, color=(235, 130, 125, 255))
            dpg.add_text("", tag="export_written", wrap=850, color=(92, 191, 178, 255))
            dpg.add_text(
                "Manual import only. The application does not install or activate presets.",
                tag="export_import_instructions",
                wrap=850,
                color=(165, 180, 195, 255),
            )
        self._rendered = True
        if self.session is not None:
            source_name = self.session.profile_selection.process.profile.name
            self._set("export_profile_name", f"{source_name} - Calibrated")
        self._refresh_ui()

    def _refresh_ui(self) -> None:
        if not self._rendered:
            return
        self._set("export_error", self.error)
        if self.draft is None:
            source_message = (
                "Source profile: build a review draft to view accepted changes."
                if self.session is not None
                else "Source profile: accept a result to prepare an export."
            )
            self._set("export_source", source_message)
            self._set("export_source_hash", "")
            self._set("export_changes", "")
            self._set("export_evidence", "")
            self._set("export_compatibility", "")
            self._set("export_confirmation", "")
            self._set("export_written", "")
            self._set("export_import_instructions", "Manual import only. The application does not install or activate presets.")
            self._configure("export_write", enabled=False)
            return

        draft = self.draft
        self._set("export_source", f"Source profile: {draft.source_profile_name} → {draft.new_profile_name}")
        self._set("export_source_hash", f"Source SHA-256: {draft.source_profile_sha256}")
        lines = ["Setting changes:"]
        for key, values in sorted(draft.setting_changes.items()):
            lines.append(f"{key}: {values['old']} → {values['new']}")
        self._set("export_changes", "\n".join(lines))
        evidence = [
            f"Session: {draft.session_id}",
            f"Plan: {draft.plan_id}",
            f"Accepted candidate: {draft.selected_candidate_id}",
            "Candidate IDs: " + (", ".join(draft.candidate_ids) or "none"),
            "Run IDs: " + (", ".join(draft.supporting_run_ids) or "none recorded"),
            "Report paths: " + (", ".join(draft.report_paths) or "none"),
        ]
        self._set("export_evidence", "\n".join(evidence))
        warnings = draft.compatibility_warnings or ("No compatibility warnings were recorded.",)
        self._set("export_compatibility", "Compatibility notes:\n" + "\n".join(f"• {item}" for item in warnings))
        confirmation = f"Confirmation status: {draft.confirmation_status}"
        if draft.confirmation_reason:
            confirmation += f"\nRecorded opt-out reason: {draft.confirmation_reason}"
        self._set("export_confirmation", confirmation)
        if draft.orca_version:
            instructions = (
                f"For OrcaSlicer {draft.orca_version}, choose File → Import → Import Configs... "
                "and select the JSON. Review the imported process values before selecting it. "
                "The standalone preset import path has not been integration-tested for this version."
            )
        else:
            instructions = (
                "OrcaSlicer version was not detected. Confirm its process-preset import steps "
                "before importing this JSON; standalone preset import is not integration-tested."
            )
        self._set("export_import_instructions", instructions + " The application never imports or activates it.")
        self._configure("export_write", enabled=True)
        self._set("export_written", self._written_summary())

    def _written_summary(self) -> str:
        if self.result is None:
            return ""
        return (
            f"Wrote preset: {self.result.profile_path}\n"
            f"Manifest: {self.result.manifest_path}\n"
            f"Evidence report: {self.result.report_path}"
        )

    def _on_build_draft(self, sender: Any, app_data: Any, user_data: Any = None) -> None:
        del sender, app_data, user_data
        self.build_draft()

    def _on_browse(self, sender: Any, app_data: Any, user_data: Any = None) -> None:
        del sender, app_data, user_data
        if self.default_export_root is not None:
            self.dpg.configure_item(
                "export_destination_dialog", default_path=str(self.default_export_root)
            )
        self.dpg.show_item("export_destination_dialog")

    def _on_destination_selected(self, sender: Any, app_data: Any, user_data: Any = None) -> None:
        del sender, user_data
        if isinstance(app_data, dict):
            path = app_data.get("file_path_name")
            if isinstance(path, str) and path.strip():
                self.dpg.set_value("export_destination", str(Path(path)))

    def _on_write(self, sender: Any, app_data: Any, user_data: Any = None) -> None:
        del sender, app_data, user_data
        self.write_to_destination()

    def _on_back(self, sender: Any, app_data: Any, user_data: Any = None) -> None:
        del sender, app_data, user_data
        if self.on_back is not None:
            self.on_back()

    def _set(self, tag: str, value: str) -> None:
        if self.dpg.does_item_exist(tag):
            self.dpg.set_value(tag, value)

    def _configure(self, tag: str, **values: Any) -> None:
        if self.dpg.does_item_exist(tag):
            self.dpg.configure_item(tag, **values)
