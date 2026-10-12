"""Offline G-code preview dialog backed by the bundled upstream viewer."""

from __future__ import annotations

import base64
import hashlib
import json
from pathlib import Path
from typing import Any

import PySide6
from PySide6.QtCore import QTimer, QUrl
from PySide6.QtWebEngineCore import (
    QWebEnginePage,
    QWebEngineProfile,
    QWebEngineSettings,
)
from PySide6.QtWebEngineWidgets import QWebEngineView
from PySide6.QtWidgets import QDialog, QLabel, QVBoxLayout

from calibrate3dp.domain.records import ArtifactRecord


_CHUNK_SIZE = 512 * 1024
_READY_POLL_MS = 50
_READY_POLL_LIMIT = 100


class _LocalViewerPage(QWebEnginePage):
    def __init__(self, profile: QWebEngineProfile, entrypoint: Path, parent=None) -> None:
        super().__init__(profile, parent)
        self._entrypoint = entrypoint.resolve()

    def acceptNavigationRequest(self, url: QUrl, navigation_type, is_main_frame: bool) -> bool:  # noqa: N802
        if not is_main_frame:
            return False
        if url.scheme() != "file":
            return False
        try:
            return Path(url.toLocalFile()).resolve() == self._entrypoint
        except (OSError, RuntimeError, ValueError):
            return False


class GcodeViewerDialog(QDialog):
    """Display one hash-verified G-code artifact using the local JS viewer bundle."""

    def __init__(self, artifact_path: Path, artifact: ArtifactRecord, parent=None) -> None:
        super().__init__(parent)
        self.artifact_path = Path(artifact_path).resolve()
        self.artifact = artifact
        self._source = None
        self._digest = hashlib.sha256()
        self._bytes_sent = 0
        self._ready_polls = 0
        self._page_loaded = False
        self._load_failed = False

        self.setWindowTitle(f"G-code preview · {Path(artifact.relative_path).name}")
        self.resize(1180, 820)
        self.setMinimumSize(740, 520)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(5)
        self.notice = QLabel("Loading the offline toolpath viewer…")
        self.notice.setObjectName("mutedStatus")
        layout.addWidget(self.notice)

        if not qt_webengine_supported():
            self.notice.setWordWrap(True)
            self.notice.setText(
                "Embedded G-code preview requires PySide6 6.8 or later. "
                f"This environment has PySide6 {PySide6.__version__}."
            )
            return

        self.view = QWebEngineView(self)
        self.profile = QWebEngineProfile(self)
        self.profile.setHttpCacheType(QWebEngineProfile.HttpCacheType.MemoryHttpCache)
        self.profile.setPersistentCookiesPolicy(
            QWebEngineProfile.PersistentCookiesPolicy.NoPersistentCookies
        )
        self.page = _LocalViewerPage(self.profile, _viewer_entrypoint(), self.view)
        self.view.setPage(self.page)
        settings = self.view.settings()
        settings.setAttribute(QWebEngineSettings.WebAttribute.LocalContentCanAccessFileUrls, True)
        settings.setAttribute(QWebEngineSettings.WebAttribute.LocalContentCanAccessRemoteUrls, False)
        settings.setAttribute(QWebEngineSettings.WebAttribute.JavascriptCanOpenWindows, False)
        settings.setAttribute(QWebEngineSettings.WebAttribute.PluginsEnabled, False)
        layout.addWidget(self.view, 1)
        self.page.loadFinished.connect(self._on_page_loaded)

        if not supports_gcode_preview(artifact.relative_path):
            self._fail("Choose a .gcode or sliced .gcode.3mf artifact to preview.")
            return
        if not self.artifact_path.is_file():
            self._fail("The saved G-code artifact is missing.")
            return
        if self.artifact_path.stat().st_size != artifact.size_bytes:
            self._fail("The saved artifact size changed. Reopen the run to refresh its evidence.")
            return
        entrypoint = _viewer_entrypoint()
        if not entrypoint.is_file():
            self._fail("The offline G-code viewer assets are missing from this installation.")
            return
        self.view.load(QUrl.fromLocalFile(str(entrypoint)))

    def _on_page_loaded(self, success: bool) -> None:
        if not success:
            self._fail("The offline G-code viewer could not be loaded.")
            return
        self._page_loaded = True
        self._wait_for_frontend()

    def _wait_for_frontend(self) -> None:
        if self._load_failed:
            return
        self._ready_polls += 1
        self.page.runJavaScript(
            "Boolean(window.calibrate3dpPreview && window.calibrate3dpPreview.beginLoad)",
            0,
            self._on_frontend_ready,
        )

    def _on_frontend_ready(self, ready: Any) -> None:
        if self._load_failed:
            return
        if ready:
            self.page.runJavaScript(
                "window.calibrate3dpPreview.beginLoad(" +
                f"{json.dumps(Path(self.artifact.relative_path).name)}, {self.artifact.size_bytes})",
                0,
                self._on_load_started,
            )
            return
        if self._ready_polls >= _READY_POLL_LIMIT:
            self._fail("The offline G-code viewer did not finish starting.")
            return
        QTimer.singleShot(_READY_POLL_MS, self._wait_for_frontend)

    def _on_load_started(self, accepted: Any) -> None:
        if self._load_failed:
            return
        if not accepted:
            self._fail("The offline viewer could not prepare this G-code file.")
            return
        try:
            self._source = self.artifact_path.open("rb")
        except OSError as exc:
            self._fail(f"The saved G-code artifact could not be read: {exc}")
            return
        self.notice.setText("Loading the saved file locally and checking its recorded hash…")
        QTimer.singleShot(0, self._send_next_chunk)

    def _send_next_chunk(self) -> None:
        if self._load_failed or self._source is None:
            return
        try:
            chunk = self._source.read(_CHUNK_SIZE)
        except OSError as exc:
            self._fail(f"The saved G-code artifact could not be read: {exc}")
            return
        if not chunk:
            self._finish_stream()
            return
        self._digest.update(chunk)
        self._bytes_sent += len(chunk)
        encoded = base64.b64encode(chunk).decode("ascii")
        self.page.runJavaScript(
            "window.calibrate3dpPreview.pushChunk(" + json.dumps(encoded) + ")",
            0,
            self._on_chunk_accepted,
        )

    def _on_chunk_accepted(self, accepted: Any) -> None:
        if self._load_failed:
            return
        if not accepted:
            self._fail("The offline viewer refused part of the G-code file.")
            return
        QTimer.singleShot(0, self._send_next_chunk)

    def _finish_stream(self) -> None:
        self._close_source()
        actual_hash = self._digest.hexdigest()
        if self._bytes_sent != self.artifact.size_bytes or actual_hash.casefold() != self.artifact.sha256.casefold():
            self._fail("The saved artifact changed while opening. No preview was started.")
            return
        self.notice.setText("File verified · parsing and building the toolpath preview locally…")
        self.page.runJavaScript(
            "window.calibrate3dpPreview.finishLoad()",
            0,
            self._on_load_finished,
        )

    def _on_load_finished(self, accepted: Any) -> None:
        if self._load_failed:
            return
        if not accepted:
            self._fail("The offline viewer could not start parsing this file.")
            return
        self.notice.setText("File hash verified · the viewer is parsing the toolpath in its worker.")

    def _fail(self, message: str) -> None:
        self._load_failed = True
        self._close_source()
        self.notice.setText(message)
        if self._page_loaded:
            self.page.runJavaScript(
                "window.calibrate3dpPreview?.failLoad(" + json.dumps(message) + ")"
            )

    def _close_source(self) -> None:
        if self._source is not None:
            try:
                self._source.close()
            except OSError:
                pass
            self._source = None

    def closeEvent(self, event) -> None:  # noqa: N802 - Qt override name
        self._close_source()
        super().closeEvent(event)


def _asset_root() -> Path:
    return Path(__file__).resolve().parents[2] / "assets" / "gcode_viewer"


def _viewer_entrypoint() -> Path:
    return _asset_root() / "index.html"


def supports_gcode_preview(relative_path: str) -> bool:
    name = relative_path.replace("\\", "/").rsplit("/", 1)[-1].casefold()
    return name.endswith(".gcode") or name.endswith(".gcode.3mf")


def qt_webengine_supported(version: str | None = None) -> bool:
    """The app's declared GUI floor is also the embedded WebEngine floor."""
    version_parts = (version or PySide6.__version__).split(".")
    numeric = []
    for part in version_parts[:2]:
        digits = "".join(character for character in part if character.isdigit())
        if not digits:
            return False
        numeric.append(int(digits))
    return tuple(numeric) >= (6, 8)
