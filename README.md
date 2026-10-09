# Calibrate-3DP

Calibrate-3DP is a local desktop workbench for structured FDM calibration using OrcaSlicer profiles and OrcaSlicer CLI. The goal is to make every test traceable to the selected printer and material, preserve manual observations, and show when a calibration is untested, blocked, accepted, or stale.

## Current state

The current main branch is an early implementation, not V1. It contains a Python headless core and a Dear PyGui shell. It imports and resolves Orca profiles, builds a deterministic ironing flow/speed plan, records manual assessments, proposes ironing refinements, runs isolated Orca CLI jobs, saves session artifacts and manifests, and supports reviewed profile export.

The current ironing path creates one simple STL and one separate Orca job per candidate. It has no multi-sample plate compiler, printed six-character plate code, compact sample labels, generic calibration dependency graph, complete V1 calibration catalog, printer calibration history model, or PySide6 interface. Multi-object 3MF overrides are unverified. The V1 goal and implementation plan describe intended work; they do not describe shipped code.

The latest repository status record reports 137 headless tests passing and one opt-in Orca integration test skipped. It records a successful nine-candidate Windows spike on 2026-10-08, plus a later CLI probe failure before slicing on 2026-10-09. These are recorded results, not tests rerun for this documentation update.

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

## Run the current prototype

The current GUI still uses Dear PyGui. Install the repository's optional GUI dependencies and run:

    python -m pip install -e ".[gui]"
    calibrate3dp

This command starts the prototype, not the planned PySide6 V1. New headless tests can be run with:

    python -m unittest discover -s tests -v

The installed Orca integration test is opt-in. It requires ORCA_SLICER_EXE and ORCA_PROFILE_ROOT to identify a tested executable and profile tree. A successful unit suite does not establish support for an untested Orca installation.

## Development principles

Keep domain models and calibration logic independent of the GUI. Preserve profile provenance and unknown settings. Do not write to the user's Orca preset directory. Use Orca as the final slicer, validate each generated candidate, and preserve failed or canceled job evidence. Keep user assessment manual and explicit.
