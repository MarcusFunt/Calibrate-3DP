"""Command-line entry point for the optional desktop application."""

from __future__ import annotations

import argparse
import sys
from typing import Sequence


def main(argv: Sequence[str] | None = None) -> int:
    """Start the PySide6 desktop application."""
    parser = argparse.ArgumentParser(
        prog="calibrate3dp",
        description="Open the local Calibrate-3DP calibration workbench.",
    )
    _options, qt_args = parser.parse_known_args(argv)
    if "--legacy-dpg" in qt_args:
        parser.error("--legacy-dpg was removed; Calibrate-3DP now uses the Qt interface")

    from .qt.main import QtDependencyMissingError, run

    try:
        return run(qt_args)
    except QtDependencyMissingError as exc:
        print(f"calibrate3dp: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
