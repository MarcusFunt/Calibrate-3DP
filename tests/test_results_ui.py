"""Headless results editor, autosave, and attachment tests."""

from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
import tempfile
import unittest

from calibrate3dp.app.models import ProfileSelection, SessionSnapshot
from calibrate3dp.app.services.experiment_service import ExperimentService
from calibrate3dp.experiments import CandidateAssessment
from calibrate3dp.profiles import ProfileDocument, ResolvedProfile

try:
    from calibrate3dp.app.pages.results_page import ResultsPage
except ImportError as exc:
    ResultsPage = None
    _RESULTS_IMPORT_ERROR = str(exc)
else:
    _RESULTS_IMPORT_ERROR = None


class FakeDpg:
    def __init__(self):
        self.items = {}
        self.callbacks = {}

    @contextmanager
    def _container(self, *args, **kwargs):
        yield

    def group(self, *args, **kwargs):
        return self._container(*args, **kwargs)

    def child_window(self, *args, **kwargs):
        return self._container(*args, **kwargs)

    def collapsing_header(self, *args, **kwargs):
        return self._container(*args, **kwargs)

    def file_dialog(self, *args, **kwargs):
        return self._container(*args, **kwargs)

    def add_text(self, value="", **kwargs):
        tag = kwargs.get("tag")
        if tag:
            self.items[tag] = {"value": value, **kwargs}

    def add_button(self, *, label, **kwargs):
        tag = kwargs.get("tag")
        if tag:
            self.items[tag] = {"label": label, **kwargs}
            self.callbacks[tag] = kwargs.get("callback")

    def add_combo(self, *, label="", items=(), **kwargs):
        self._add_widget(label=label, items=list(items), **kwargs)

    def add_slider_int(self, *, label="", **kwargs):
        self._add_widget(label=label, **kwargs)

    def add_checkbox(self, *, label="", **kwargs):
        self._add_widget(label=label, **kwargs)

    def add_input_text(self, *, label="", **kwargs):
        self._add_widget(label=label, **kwargs)

    def add_spacer(self, **kwargs):
        return None

    def add_file_extension(self, *args, **kwargs):
        return None

    def _add_widget(self, **kwargs):
        tag = kwargs.get("tag")
        if tag:
            self.items[tag] = dict(kwargs)
            if kwargs.get("callback"):
                self.callbacks[tag] = kwargs["callback"]

    def set_value(self, tag, value):
        self.items.setdefault(tag, {})["value"] = value

    def get_value(self, tag):
        return self.items.get(tag, {}).get("value")

    def configure_item(self, tag, **kwargs):
        self.items.setdefault(tag, {}).update(kwargs)

    def does_item_exist(self, tag):
        return tag in self.items

    def show_item(self, tag):
        self.configure_item(tag, show=True)


class FakeClock:
    def __init__(self):
        self.value = 0.0

    def __call__(self):
        return self.value

    def advance(self, seconds):
        self.value += seconds


class FakeRepository:
    def __init__(self):
        self.copied = []

    def copy_photo_to_session(self, session_id, source_path):
        self.copied.append((session_id, Path(source_path)))
        return f"evidence/photos/{Path(source_path).name}"


class FakeSessionService:
    def __init__(self, *, fail_saves=0):
        self.repository = FakeRepository()
        self.fail_saves = fail_saves
        self.saved = []

    def save(self, snapshot):
        if self.fail_saves:
            self.fail_saves -= 1
            raise OSError("temporary session write error")
        self.saved.append(snapshot)


class ResultsUiTests(unittest.TestCase):
    def setUp(self):
        if ResultsPage is None:
            self.fail(f"results UI is not implemented yet: {_RESULTS_IMPORT_ERROR}")
        self.experiment_service = ExperimentService()
        self.profiles = _profiles()
        self.plan = self.experiment_service.create_initial("ironing", self.profiles)
        timestamp = datetime.now(timezone.utc).isoformat()
        self.session = SessionSnapshot(
            session_id="results-ui-session",
            created_at_utc=timestamp,
            updated_at_utc=timestamp,
            module_id=self.plan.module_id,
            current_step="results",
            profile_selection=self.profiles,
            plan=self.plan,
            status="awaiting_results",
        )
        self.clock = FakeClock()
        self.dpg = FakeDpg()
        self.session_service = FakeSessionService()
        self.page = ResultsPage(self.dpg, self.session_service, clock=self.clock)
        self.page.set_context(session=self.session, plan=self.plan)
        self.page.render()

    def test_rating_and_verdict_changes_save_immediately(self):
        self.page.record_rating("I001", "finish", 4)
        self.assertEqual(len(self.session_service.saved), 1)
        self.assertEqual(self.page.assessment_for("I001").ratings["finish"], 4)

        self.page.set_verdict("I001", "pass")

        self.assertEqual(len(self.session_service.saved), 2)
        self.assertEqual(self.page.assessment_for("I001").verdict, "pass")
        self.assertEqual(self.page.save_status, "Saved")

    def test_rating_can_be_cleared_immediately(self):
        self.page.record_rating("I001", "finish", 4)

        self.page.record_rating("I001", "finish", None)

        self.assertNotIn("finish", self.page.assessment_for("I001").ratings)
        self.assertEqual(len(self.session_service.saved), 2)

    def test_note_edits_debounce_for_300ms_and_retry_after_a_write_failure(self):
        self.page.set_notes("I001", "rough after the first layer")
        self.assertEqual(self.session_service.saved, [])
        self.clock.advance(0.299)
        self.assertFalse(self.page.tick())
        self.clock.advance(0.001)
        self.assertTrue(self.page.tick())
        self.assertEqual(len(self.session_service.saved), 1)
        self.assertEqual(self.page.assessment_for("I001").notes, "rough after the first layer")

        self.session_service.fail_saves = 1
        self.page.record_rating("I001", "coverage", 3)
        self.assertEqual(self.page.save_status, "Save Failed")
        self.assertTrue(self.page.retry_save())
        self.assertEqual(self.page.save_status, "Saved")

    def test_pending_note_flushes_when_the_page_closes(self):
        self.page.set_notes("I001", "last note before closing")

        self.assertTrue(self.page.flush_pending())

        self.assertEqual(len(self.session_service.saved), 1)
        self.assertEqual(self.page.save_status, "Saved")

    def test_ties_and_uncertain_or_incomplete_assessments_cannot_be_accepted(self):
        self.page.select_winner("I001")
        self.page.set_verdict("I001", "pass")
        self.page.set_verdict("I002", "pass")
        self.page.set_tie("I002", True)

        self.assertFalse(self.page.can_accept)
        self.page.set_tie("I002", False)
        self.assertFalse(self.page.can_accept, "remaining candidates have not been assessed")

        self.page.set_verdict("I003", "uncertain")
        self.assertFalse(self.page.can_accept)

    def test_photo_attachment_is_copied_into_session_before_saving(self):
        with tempfile.TemporaryDirectory() as directory:
            photo = Path(directory) / "surface.png"
            photo.write_bytes(b"image bytes")

            self.assertTrue(self.page.attach_photo("I001", photo))

        self.assertEqual(
            self.session_service.repository.copied,
            [(self.session.session_id, photo)],
        )
        self.assertEqual(
            self.page.assessment_for("I001").photo_paths,
            ("evidence/photos/surface.png",),
        )
        self.assertEqual(len(self.session_service.saved), 1)


def _profiles():
    def profile(name, kind):
        raw = {"type": kind, "name": name}
        if kind == "process":
            raw.update({"ironing_flow": "10", "ironing_speed": "30", "ironing_type": "top"})
        document = ProfileDocument(name, kind, "fixture", raw, f"{name}.json")
        return ResolvedProfile(document, document.raw, {}, (document,))

    return ProfileSelection(profile("Printer", "machine"), profile("PLA", "filament"), profile("Fine", "process"))


if __name__ == "__main__":
    unittest.main()
