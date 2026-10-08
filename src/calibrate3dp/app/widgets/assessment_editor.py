"""Dear PyGui controls for one candidate's print assessment."""

from __future__ import annotations

from typing import Any, Callable, Mapping

from calibrate3dp.experiments import CandidateAssessment


class AssessmentEditor:
    """Render and update the currently selected candidate assessment."""

    CRITERIA: tuple[tuple[str, str], ...] = (
        ("finish", "Surface finish"),
        ("coverage", "Ironing coverage"),
    )
    VERDICTS: tuple[str, ...] = ("Unreviewed", "pass", "fail", "uncertain", "missing")

    def __init__(
        self,
        dpg: Any,
        *,
        on_candidate: Callable[[str], None],
        on_rating: Callable[[str, str, int | None], None],
        on_verdict: Callable[[str, str | None], None],
        on_tags: Callable[[str, tuple[str, ...]], None],
        on_notes: Callable[[str, str], None],
        on_attach: Callable[[str, str], bool],
    ) -> None:
        self.dpg = dpg
        self.on_candidate = on_candidate
        self.on_rating = on_rating
        self.on_verdict = on_verdict
        self.on_tags = on_tags
        self.on_notes = on_notes
        self.on_attach = on_attach
        self.candidate_ids: tuple[str, ...] = ()
        self.selected_candidate_id: str | None = None
        self._rendered = False

    def render(self, candidate_ids: tuple[str, ...]) -> None:
        """Create the assessment controls once; subsequent calls refresh values."""
        self.candidate_ids = candidate_ids
        if self._rendered:
            self.refresh(None)
            return

        dpg = self.dpg
        with dpg.child_window(tag="assessment_editor", width=-1, height=330, border=True):
            dpg.add_text("CANDIDATE OBSERVATIONS", color=(92, 191, 178, 255))
            dpg.add_combo(
                label="Candidate",
                items=list(candidate_ids),
                tag="assessment_candidate",
                callback=self._candidate_changed,
                width=250,
            )
            dpg.add_combo(
                label="Outcome",
                items=list(self.VERDICTS),
                default_value="Unreviewed",
                tag="assessment_verdict",
                callback=self._verdict_changed,
                width=250,
            )
            for key, label in self.CRITERIA:
                dpg.add_combo(
                    label=f"{label} (1–5)",
                    items=["Unrated", "1", "2", "3", "4", "5"],
                    default_value="Unrated",
                    tag=f"assessment_rating_{key}",
                    callback=self._rating_changed,
                    user_data=key,
                    width=250,
                )
            dpg.add_input_text(
                label="Defect tags (comma-separated)",
                tag="assessment_tags",
                callback=self._tags_changed,
                width=-1,
            )
            dpg.add_input_text(
                label="Notes",
                tag="assessment_notes",
                multiline=True,
                height=56,
                width=-1,
                callback=self._notes_changed,
            )
            with dpg.group(horizontal=True):
                dpg.add_button(label="Attach photo", tag="assessment_attach", callback=self._show_photo_dialog)
                dpg.add_text("No photos attached", tag="assessment_photos", wrap=700)
            with dpg.file_dialog(
                tag="assessment_photo_dialog",
                show=False,
                modal=True,
                width=760,
                height=480,
                directory_selector=False,
                callback=self._photo_selected,
            ):
                for extension in (".png", ".jpg", ".jpeg", ".webp", ".bmp"):
                    dpg.add_file_extension(extension)
        self._rendered = True
        self.refresh(None)

    def refresh(self, assessment: CandidateAssessment | None) -> None:
        if not self._rendered:
            return
        if self.dpg.does_item_exist("assessment_candidate"):
            self.dpg.configure_item("assessment_candidate", items=list(self.candidate_ids))
        selected = self.selected_candidate_id
        if selected is None and self.candidate_ids:
            selected = self.candidate_ids[0]
            self.selected_candidate_id = selected
        assessment = assessment or CandidateAssessment(selected or "unselected")
        self._set("assessment_candidate", selected or "")
        self._set(
            "assessment_verdict",
            assessment.verdict if assessment.verdict is not None else "Unreviewed",
        )
        for key, _label in self.CRITERIA:
            value = assessment.ratings.get(key)
            self._set(f"assessment_rating_{key}", "Unrated" if value is None else str(value))
        self._set("assessment_tags", ", ".join(assessment.defect_tags))
        self._set("assessment_notes", assessment.notes)
        photos = "\n".join(assessment.photo_paths) if assessment.photo_paths else "No photos attached"
        self._set("assessment_photos", photos)

    def _candidate_changed(self, sender: Any, app_data: Any, user_data: Any = None) -> None:
        del sender, user_data
        if isinstance(app_data, str) and app_data in self.candidate_ids:
            self.selected_candidate_id = app_data
            self.on_candidate(app_data)

    def _rating_changed(self, sender: Any, app_data: Any, user_data: Any = None) -> None:
        del sender
        candidate_id = self.selected_candidate_id
        if candidate_id is None or not isinstance(user_data, str):
            return
        if app_data == "Unrated":
            self.on_rating(candidate_id, user_data, None)
            return
        try:
            rating = int(app_data)
        except (TypeError, ValueError):
            return
        if 1 <= rating <= 5:
            self.on_rating(candidate_id, user_data, rating)

    def _verdict_changed(self, sender: Any, app_data: Any, user_data: Any = None) -> None:
        del sender, user_data
        candidate_id = self.selected_candidate_id
        if candidate_id is None:
            return
        verdict = None if app_data == "Unreviewed" else app_data
        if verdict is None or verdict in {"pass", "fail", "uncertain", "missing"}:
            self.on_verdict(candidate_id, verdict)

    def _tags_changed(self, sender: Any, app_data: Any, user_data: Any = None) -> None:
        del sender, user_data
        if self.selected_candidate_id is None or not isinstance(app_data, str):
            return
        tags = tuple(tag.strip() for tag in app_data.split(",") if tag.strip())
        self.on_tags(self.selected_candidate_id, tags)

    def _notes_changed(self, sender: Any, app_data: Any, user_data: Any = None) -> None:
        del sender, user_data
        if self.selected_candidate_id is not None and isinstance(app_data, str):
            self.on_notes(self.selected_candidate_id, app_data)

    def _show_photo_dialog(self, sender: Any = None, app_data: Any = None, user_data: Any = None) -> None:
        del sender, app_data, user_data
        self.dpg.show_item("assessment_photo_dialog")

    def _photo_selected(self, sender: Any, app_data: Any, user_data: Any = None) -> None:
        del sender, user_data
        if self.selected_candidate_id is None:
            return
        path = _selected_path(app_data)
        if path:
            self.on_attach(self.selected_candidate_id, path)

    def _set(self, tag: str, value: Any) -> None:
        if self.dpg.does_item_exist(tag):
            self.dpg.set_value(tag, value)


def _selected_path(app_data: Any) -> str | None:
    if isinstance(app_data, Mapping):
        path = app_data.get("file_path_name")
        return path if isinstance(path, str) and path else None
    return app_data if isinstance(app_data, str) and app_data else None
