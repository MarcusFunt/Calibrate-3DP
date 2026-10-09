# Calibrate-3DP

Calibrate-3DP is a local desktop workbench for structured FDM calibration using OrcaSlicer profiles and OrcaSlicer CLI. The goal is to make every test traceable to the selected printer and material, preserve manual observations, and show when a calibration is untested, blocked, accepted, or stale.

## Current state

The current implementation is not V1. The PySide6 shell can import or select local Orca profiles, persist printer and material records, open a printer workspace, generate a grouped nine-sample ironing run through Orca on a worker thread, and inspect or look up saved runs by plate code. Settings holds the Orca executable, profile roots, and local workspace. Runs retain their plan, sample map, profiles, logs, and artifact hashes.

The grouped ironing path writes separate coupon objects into one standard 3MF project. An opt-in real-Orca check proves independent ironing flow and speed changes in each sample's toolpaths and rejects an omitted object override. The six-character plate code and sample names are saved in metadata; they are not printed, the coupons are not connected, and the run is never presented as ready to print. The CLI banner and G-code version identity remain unreconciled for the tested Windows installation. The V1 goal and implementation plan describe the remaining target behavior.

The latest full local test run is recorded in docs/PROGRESS_HISTORY.md. Qt smoke tests require the optional PySide6 dependency and run with the offscreen Qt platform; they are skipped when that dependency is absent. The installed-Orca integration test remains opt-in and its result applies only to the recorded binary and profiles.

## V1 scope

V1 is manual and local. It covers nozzle temperature, flow ratio, maximum volumetric speed, pressure advance, retraction, full ironing, bridges, and support interface/removal. The app generates versioned plans and labeled plates, slices through Orca, validates output, records manual assessment, tracks dependencies and stale results, and exports a reviewed new profile.

V1 has no camera workflow, automatic measurements, computer vision, automatic scoring, printer control, cloud service, or firmware writes. See docs/V1_GOAL.md for acceptance criteria.

## Documentation

- docs/V1_GOAL.md — product behavior, boundaries, and V1 acceptance criteria.
- IMPLEMENTATION_PLAN.md — ordered implementation tasks, interfaces, and verification.
- docs/IMPLEMENTATION_STATUS.md — current implementation and open gates.
- docs/PROGRESS_HISTORY.md — append-only task, test, experiment, rationale, and assumption history.
- docs/REUSE_RESEARCH.md — reviewed reusable code and license/integration limits.
- AGENTS.md — instructions for coding agents and documentation maintenance.
- docs/orca-cli-spike-windows-2026-10-08.md — historical, installation-specific integration evidence.
- docs/superpowers/plans/2026-10-08-calibrate-3dp-gui.md — archived prior Dear PyGui plan, superseded by the V1 plan.

## Run the desktop application

Install the optional GUI dependencies and start the new Qt shell with:

    python -m pip install -e ".[gui]"
    calibrate3dp

The Qt shell provides printer and material intake, a printer workspace, grouped ironing generation, run history and plate-code lookup, and Orca settings. Calibration assessment, dependency graph, physical plate design, and the other V1 modules are still in progress. Dear PyGui is no longer an application option.

Headless and optional GUI tests can be run with:

    python -m unittest discover -s tests -v

The installed Orca integration test is opt-in. It requires ORCA_SLICER_EXE and ORCA_PROFILE_ROOT to identify a tested executable and profile tree. A successful unit suite does not establish support for an untested Orca installation.

## Development principles

Keep domain models and calibration logic independent of the GUI. Preserve profile provenance and unknown settings. Do not write to the user's Orca preset directory. Use Orca as the final slicer, validate each generated candidate, and preserve failed or canceled job evidence. Keep user assessment manual and explicit.
