"""Command-line entry point for the optional desktop application."""

from __future__ import annotations

import argparse
import sys
from typing import Sequence

from .window import AppShell, GuiDependencyMissingError, load_dearpygui


def main(argv: Sequence[str] | None = None) -> int:
    """Start the Calibrate-3DP desktop application."""
    parser = argparse.ArgumentParser(
        prog="calibrate3dp",
        description="Open the local Calibrate-3DP calibration workbench.",
    )
    parser.parse_args(argv)

    try:
        dpg = load_dearpygui()
    except GuiDependencyMissingError as exc:
        print(f"calibrate3dp: {exc}", file=sys.stderr)
        return 2

    return AppShell(dpg_module=dpg).run()


if __name__ == "__main__":
    raise SystemExit(main())
