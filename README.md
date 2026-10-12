# Calibrate-3DP

> **DEVELOPMENT BUILD — NOT TRUSTWORTHY YET.** Calibrate-3DP is still in development. Do not rely on it for calibration decisions, print readiness, or machine safety. Treat its outputs as experimental and independently verify them in OrcaSlicer and against your printer and material documentation.

Calibrate-3DP is a local desktop workbench for structured FDM calibration using OrcaSlicer profiles and OrcaSlicer CLI. The goal is to make every test traceable to the selected printer and material, preserve manual observations, and show when a calibration is untested, blocked, accepted, or stale.

## Current state

The current implementation is not V1. The PySide6 shell can import or select local Orca profiles, persist printer and material records, open a printer workspace, review and save a versioned nine-sample ironing configuration, reopen an unlinked saved draft after a repository restart, generate it through Orca on a worker thread, and reopen saved runs by plate code. Experiment Details supports per-sample manual assessments, copied evidence photos, physical-trial attestations, immutable assessment revisions, recommendations, and linked refinement/confirmation runs. Accepted confirmation decisions retain the exact child assessment revision, and Experiment Details opens saved artifacts only after path and hash verification. An evidence-gated review path can prepare a new Orca process JSON export; synthetic or physically unreviewed assessments cannot be accepted or exported. Settings holds the Orca executable, profile roots, and local workspace. Runs retain their plan, sample map, profiles, logs, and artifact hashes.

## Download a Windows development build

The GitHub Actions workflow builds a Windows x64 application ZIP, runs the repository test suite and a frozen-app self-check, and uploads the ZIP with its SHA-256 checksum as a 30-day workflow artifact. Open the repository's **Actions** page, select a **Windows development application** run, then download the artifact. This is a downloadable test build, not a stable release; no GitHub Release is created. The ZIP has no installer or code signature. OrcaSlicer must be installed separately.

**The downloaded application is still in development and is not trustworthy yet. Do not rely on it for calibration decisions, print readiness, machine safety, or deciding what to print.**

New schema-v2 grouped-ironing configurations use the optional `build123d@1` CAD backend and create nine individually configurable specimens with pocketed embossed text on the underside. A separate, top-labeled identifier plaque sits at the selected corner and connects to the shared frame by two designed tabs, producing 11 named 3MF objects for a nine-sample plate. Historical schema-v1 configurations still use their original `stdlib-voxel@1` meshes and rail-code geometry. The opt-in real-Orca gate disables automatic arrangement/orientation, validates all eleven object toolpaths in one shared layout, checks the nine sample settings and label/code toolpaths, and rejects an omitted override. The run record retains the mesh files, 3MF, resolved profiles, logs, G-code, and hashes. Qt shows the saved corner and underside geometry together with generation validation and remaining blockers. A bounded G-code parser checks a supported linear subset and reports unknown commands and missing context; it is not a safety certification. No run is `print_ready`; no physical print has qualified the new labels, plaque, or tabs. Orca identity is recorded per run, but this executable/profile/platform is not qualified in a support matrix. The V1 goal and implementation plan describe the remaining target behavior.

The full local suite ran 323 tests on 2026-10-12 and passed with eight skips: four opt-in Orca tests, one test without its retained `.gcode.3mf` fixture, one test requiring Python without PySide6, and two Windows symlink tests requiring a privilege not available in that environment. After the CI self-check diagnostic update, eight focused packaging/UI/entrypoint tests ran: seven passed and the no-PySide test was skipped. Qt tests used the offscreen platform. The connected real-Orca gate passed separately for the exact binary and profiles recorded in docs/IMPLEMENTATION_STATUS.md. Exact commands and retained logs are in docs/PROGRESS_HISTORY.md.

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

The Qt shell provides printer and material intake, a printer workspace, saved grouped-ironing configuration and generation, run history and plate-code lookup, manual assessment and follow-up controls, reviewed-export gating, and Orca settings. The dependency graph, physical print acceptance, complete G-code validation, and the other V1 modules are still in progress. Dear PyGui is no longer an application option.

Headless and optional GUI tests can be run with:

    python -m unittest discover -s tests -v

The installed Orca integration test is opt-in. It requires ORCA_SLICER_EXE and ORCA_PROFILE_ROOT to identify a tested executable and profile tree. A successful unit suite does not establish support for an untested Orca installation.

## Development principles

Keep domain models and calibration logic independent of the GUI. Preserve profile provenance and unknown settings. Do not write to the user's Orca preset directory. Use Orca as the final slicer, validate each generated candidate, and preserve failed or canceled job evidence. Keep user assessment manual and explicit.

## Experimental top-surface flow matrix generator

A standalone versioned Build123d flow-ratio model generator is available at
`tools/generate_top_surface_flow_matrix.py`. It creates 1–9 labeled solid-top
coupons in one multi-object Orca 3MF plate and a JSON candidate/provenance
manifest. It is **not** integrated with the GUI or saved calibration runs and
never marks the output ready to print. See
[`docs/TOP_SURFACE_FLOW_MATRIX.md`](docs/TOP_SURFACE_FLOW_MATRIX.md) for CLI
arguments, slicer assumptions, validation evidence, and remaining blockers.
