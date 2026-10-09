# Implementation Status

## Snapshot

This status describes the implementation in the `codex/v1-design-orca-qt` checkout, based on `origin/main` at `c899021daa38c990bcd81466b4b9a149539eaef8`, reviewed on 2026-10-09. The primary `D:\projects\Calibrate-3DP` checkout is clean at that same commit. The older `D:\projects\Calibrate-3DP-worktrees\orca-integration` checkout is clean at `3ec56c5aca901d4087d0c2615a424f31be22a52f` and 24 commits behind `origin/main`. A separate `ironing-milestone-20261009` worktree registration is marked prunable; its `.git` pointer targets an unavailable `/mnt/d/...` path, so Git cannot inspect it. The implementation summarized below is in this task's managed worktree.

The target remains the manual, local-first product in [V1_GOAL.md](V1_GOAL.md). Passing one profile/version configuration does not establish a general support claim.

## Implemented foundation

- Python >=3.11 headless core, existing profile import/inheritance resolution, provenance and unknown-key preservation, deterministic ironing plans, manual result contracts, and separate Orca jobs.
- Database schema v2 adds persistent printer, material, and grouped calibration-run records while preserving existing v1 session rows and payloads. Versioned profile snapshots retain raw documents, resolved values, provenance, and source fingerprints. Plan/sample data stays in SQLite; logs, 3MF, and G-code stay in per-run artifact folders with hashes and checked paths.
- Profile discovery/import and saved-record workflows connect to Qt. Users can add a printer from installed or local JSON/ZIP profiles, add nozzle-contextual filament records, reopen a printer workspace, start a nine-sample ironing run asynchronously, cancel it, inspect run history, and look up a run by its six-character code. Orca executable and profile roots are configurable in Settings.
- A standard-library 3MF writer creates a single project with separate Sample-A through Sample-I coupon objects and per-object settings metadata. Real Orca output proves that selected ironing flow changes extrusion and speed changes the sample toolpaths. The missing-override case is rejected by the G-code validator.
- Generation records the source and derived profiles, exact Orca argument vector, CLI banner, G-code header, logs, bounds, and artifact hashes. The run is marked `settings_validated`; `print_ready` remains false.
- The Dear PyGui shell and launch dependency are removed. Existing `app/pages` adapters remain transitional and are not exposed as the supported Qt workflow.

## Still required for V1

- Complete the design/geometry gate: connected hand-separable specimens, physical A-I labels and printed six-character code, connector and mesh validation, full keep-out/collision checking, and repeatability/packaging on the supported platform matrix. Current objects are separate coupons; their labels and code exist only in saved 3MF metadata and run records.
- Complete print-readiness validation: check every emitted movement against printable areas and keep-outs, verify start/end code and temperatures, reconcile the CLI banner `OrcaSlicer-01.10.01.50` with the G-code identity `OrcaSlicer 2.3.0`, and define a tested support matrix. The observed mismatch is unresolved; this installation is not declared supported.
- Finish the persistent data model and recovery tests for all V1 entities, including manual assessments, refinements, exports, migration rollback, duplicate IDs, and missing/corrupt artifacts. Current persistence covers printer/material/profile snapshots, generated run plans, sample maps, and artifact references.
- Complete profile review in Qt (inheritance/provenance and compatibility diagnostics), broad import/re-import cases, and a contextual calibration dependency graph with stale-result behavior.
- Add end-to-end Qt assessment, refinement, and reviewed export workflows for saved grouped runs. The current new workflow stops after output validation.
- Implement the other V1 calibration modules: temperature, flow ratio, maximum volumetric speed, pressure advance, retraction, bridge, and support calibration. Expand ironing to type, pattern, spacing, inset, angle, fixed-angle behavior, and staged refinement; current grouped workflow covers top-surface flow × speed only.
- Complete accessibility, packaging, backup/recovery, cross-platform acceptance, source-profile immutability verification, and physical print/handling trials.

## Evidence in this checkout

- Focused storage, grouped geometry/validation, fake Orca-service, and offscreen Qt tests passed: 22 tests.
- Full suite passed on 2026-10-09: `python -m unittest discover -s tests -v` — 159 tests, 3 skipped. Two real-Orca tests were skipped in the default run because the opt-in environment variables were not set; one test for launching without PySide6 was skipped because Qt was installed. The new offscreen Qt tests ran.
- Real-Orca grouped-settings test passed on the installed Windows Orca executable with Creality Ender-3 V2 0.4 mm, 0.20 mm Standard process, and Generic PLA profiles. Evidence is retained under `%LOCALAPPDATA%\Calibrate-3DP\gate-evidence\2026-10-09\integration-final\grouped-settings-gate-20261009T194530768654Z`. Positive G-code SHA-256: `b875d0caaf85918c7e63456b2b1d45e7817869230c3088ed3ea89b5e88fc81be`; negative G-code SHA-256: `033175f9bd61db94613d0a7754638a0069f04358a43d6ac79525438f2d430462`.
- The positive three-sample plate produced 836 ironing moves per sample. At 12%, 15%, and 18% flow, positive extrusion was 8.56818, 10.70817, and 12.85236 mm; measured speeds were 10, 15, and 20 mm/s. The negative Sample-B with its object override omitted used baseline 5 mm/s and failed the flow and speed assertions.
- An offscreen Qt workflow run used the actual dialogs, saved local records, opened the printer workspace, launched the background generation service against real Orca, and looked up the saved plate code through Sample-I. It persisted nine samples and returned `settings_validated`, with `print_ready: false`. Evidence, including the reproducible invocation script, manifest, logs, profiles, project, and output, is under `%LOCALAPPDATA%\Calibrate-3DP\gate-evidence\2026-10-09\qt-orca-final\workspace\runs\run-e526c09bcd3947f98a92f0c154c61507\`. Plate code `ZVHFDT`; G-code SHA-256 `74e4d10f83ac683717c80b8cd90e1598099d7e020efd5a072a72d6a49e23d5c8`.
- All retained real-Orca evidence is for one Windows installation and one Creality profile set. A successful sample-settings check is not a print-safety or physical handling check.

## V1 completion gate

Use [V1_GOAL.md](V1_GOAL.md) for acceptance criteria and [IMPLEMENTATION_PLAN.md](../IMPLEMENTATION_PLAN.md) for task evidence. V1 is not complete: grouped settings now pass a narrow real-Orca gate, and a usable first Qt library-to-generation workflow exists, but physical plate design, general calibration coverage, manual assessment/export integration, print-readiness validation, and the supported platform/version matrix remain open.
