"""Command-line entry point for the optional desktop application."""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path
from typing import Sequence


def main(argv: Sequence[str] | None = None) -> int:
    """Start the PySide6 desktop application."""
    parser = argparse.ArgumentParser(
        prog="calibrate3dp",
        description="Open the local Calibrate-3DP calibration workbench.",
    )
    parser.add_argument("--bundle-self-check", action="store_true", help=argparse.SUPPRESS)
    options, qt_args = parser.parse_known_args(argv)
    if "--legacy-dpg" in qt_args:
        parser.error("--legacy-dpg was removed; Calibrate-3DP now uses the Qt interface")
    if options.bundle_self_check:
        return bundle_self_check()

    from .qt.main import QtDependencyMissingError, run

    try:
        return run(qt_args)
    except QtDependencyMissingError as exc:
        print(f"calibrate3dp: {exc}", file=sys.stderr)
        return 2


def bundle_self_check() -> int:
    """Check the packaged GUI, viewer assets, and pinned CAD backend without opening UI."""
    try:
        from calibrate3dp.app.qt.gcode_viewer import _viewer_entrypoint, qt_webengine_supported
        from calibrate3dp.geometry.registry import is_geometry_backend_available
        from PySide6.QtWebEngineWidgets import QWebEngineView  # noqa: F401
    except ImportError as exc:
        return _bundle_self_check_failure(10, f"required GUI/CAD import failed: {exc}")

    if not qt_webengine_supported():
        return _bundle_self_check_failure(11, "PySide6 is below the supported Qt WebEngine version")
    if not _viewer_entrypoint().is_file():
        return _bundle_self_check_failure(12, "bundled G-code viewer entry point is missing")
    if not is_geometry_backend_available("build123d", "1"):
        return _bundle_self_check_failure(13, "build123d CAD backend is unavailable")
    if getattr(sys, "frozen", False):
        development_notice = Path(sys.executable).resolve().parent / "DEVELOPMENT-ONLY.txt"
        if not development_notice.is_file():
            return _bundle_self_check_failure(14, "DEVELOPMENT-ONLY.txt is missing beside the executable")
    return 0


def _bundle_self_check_failure(exit_code: int, detail: str) -> int:
    """Expose actionable self-check diagnostics in windowed CI builds."""
    message = f"Calibrate-3DP bundle self-check failed ({exit_code}): {detail}\n"
    log_path = os.environ.get("CALIBRATE3DP_BUNDLE_SELF_CHECK_LOG")
    if log_path:
        try:
            Path(log_path).write_text(message, encoding="utf-8")
        except OSError:
            pass
    if sys.stderr is not None:
        print(message, file=sys.stderr, end="")
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
