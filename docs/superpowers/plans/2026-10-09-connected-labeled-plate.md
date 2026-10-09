# Connected labeled plate geometry and Orca gate

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the nine disconnected metadata-only coupons with a connected grouped ironing plate designed for hand-tool separation and carrying physical sample labels and a plate code, then prove that the tested Orca executable still applies each sample's settings to the intended toolpaths.

**Architecture:** Keep a typed plate request and geometry backend independent of Qt and Orca. Generate nine distinct setting-bearing sample objects plus any necessary connector/frame geometry, preserve Orca's part-to-object mapping in the 3MF, and let the existing grouped generation service slice and validate the artifact. The Qt workflow presents geometry and sample-settings results without promoting them to print-ready status.

**Tech Stack:** Python >=3.11; current standard-library 3MF and Orca CLI adapters; PySide6 for the existing workflow. Measure build123d packaging as the initial CAD candidate, then use the first-party standard-library voxel backend for the bounded axis-aligned plate because the isolated Windows install measured 688.5 MiB. Revisit a CAD runtime only if later geometry needs curved/boolean operations and packaging evidence justifies it.

**Spec:** `docs/V1_GOAL.md`, `IMPLEMENTATION_PLAN.md` (Phase 0 design gate and Tasks 0, 7, 8), and `docs/EXPERIMENT_CONFIGURATION_PROPOSAL.md`.

## Global Constraints

- Preserve V1 as a manual, local workflow. Do not add camera measurement, automatic scoring, printer control, cloud behavior, or firmware writes.
- Keep geometry planning and validation independent of Qt widgets and Orca process invocation.
- Assign a unique six-character code before geometry generation. Put that code and deterministic A–I labels into printable geometry as well as saved metadata.
- Preserve nine independently addressable sample settings in the 3MF/Orca project. The connector strategy must not silently merge the sample setting identities.
- Retain source profiles, exact executable identity, command arguments, logs, settings evidence, geometry/project/G-code hashes, and generator versions.
- A successful CAD export or slicer exit is not print-safety evidence. Keep `print_ready` false while movement, keep-out, start/end, temperature, support-matrix, or physical-print checks remain incomplete.
- Do not modify source Orca profiles or activate exported profiles.
- Append verified outcomes to `docs/PROGRESS_HISTORY.md`; update implementation checkboxes only after their evidence passes.

## Review Focus

- All nine samples have visible A–I labels and the plate has the allocated code as geometry, not only metadata.
- Connectors survive mesh/export validation and are designed for deliberate hand-tool separation; their dimensions and placement are explicit and deterministic. Actual printed readability and separation remain manual acceptance checks.
- Sample objects remain separately mapped to their intended override records after 3MF packaging and Orca import.
- Bounds and keep-outs are checked against the selected printer geometry; malformed or unsupported geometry fails closed.
- Repeated generation from the same request and backend version preserves placements, labels, sample mapping, and geometry parameters.
- The Orca CLI banner/G-code identity mismatch is investigated with exact executable evidence. If still unexplained, retain `unresolved` and make no release-version support claim.
- Qt shows connected-plate generation and sample-settings validation accurately while preserving the non-print-ready state.

---

## Baseline and scope

- Start at clean `main` / `origin/main` commit `4a16ccfa4f84436f8ec339305147179397aba4f7`.
- The 2026-10-09 real-Orca gate proves per-object flow and speed settings but not physical continuity: Orca auto-arranged the objects. The saved Qt run has nine samples and `print_ready: false`, but its initial `settings_validated` state did not include a shared-layout check. The 2026-10-10 correction disables automatic arrangement/orientation and validates one shared XY translation across all ten named toolpaths; the current Qt workflow records that layout proof.
- The prior gate recorded CLI banner `OrcaSlicer-01.10.01.50` and G-code header `OrcaSlicer 2.3.0`; their relationship is not yet explained. Only one Windows Orca/profile combination has real output evidence.
- The implementation-status snapshot and implementation plan still describe the pre-merge checkout. Refresh those factual baselines as part of this pass.
- This pass does not close complete G-code safety validation, physical print/handling acceptance, multi-platform support, other calibration modules, or assessment/refinement/export. Keep those gates open.

## Work sequence

### Task 1: Reconcile the current repository and Orca evidence baseline

**Files:**
- Modify: `docs/IMPLEMENTATION_STATUS.md`
- Modify: `IMPLEMENTATION_PLAN.md`
- Modify after the spike decision: `docs/EXPERIMENT_CONFIGURATION_PROPOSAL.md`, `docs/REUSE_RESEARCH.md`
- Modify after running the gate: `docs/PROGRESS_HISTORY.md`
- Modify as needed: `src/calibrate3dp/orca_cli.py`, `src/calibrate3dp/app/services/grouped_orca_service.py`
- Test: `tests/test_orca_cli.py`, `tests/test_grouped_orca_service.py`

**Steps:**

- [x] Update the status and plan baseline to commit `4a16ccf`; describe the current implementation, clean checkout, and already-passed evidence without copying stale checkout claims.
- [x] Capture the selected executable's resolved path and SHA-256, available Windows file/product version metadata, CLI help/version output when supported, and the G-code identity from a fresh isolated slice. Preserve the raw outputs.
- [x] Define identity evidence so the executable hash is the exact binary identity, while banner/header strings remain separately recorded. Mark identity `reconciled` only when the relationship is supported by executable/package evidence; otherwise keep it `unresolved` and fail closed for release-version claims.
- [x] Add focused tests for missing, conflicting, and explained identity metadata. Tests must show that an unknown identity cannot become a support claim.

**Verification:** Run `python -m unittest tests.test_orca_cli tests.test_grouped_orca_service -v`. The real Orca probe is recorded with the exact executable, raw identity outputs, profiles, command, and artifact hashes. Do not change status to supported or print-ready based only on the binary hash.

### Task 2: Select and implement the connected plate geometry backend

**Files:**
- Create: `src/calibrate3dp/geometry/__init__.py`
- Create: `src/calibrate3dp/geometry/layout.py`
- Create: `src/calibrate3dp/geometry/specimens.py`
- Modify: `src/calibrate3dp/grouped_plate.py` or add a narrow adapter there for geometry artifacts
- Create: `tests/test_plate_layout.py`
- Create: `tests/test_plate_geometry_backend.py`
- Update only if justified: `pyproject.toml`, `docs/REUSE_RESEARCH.md`, `docs/EXPERIMENT_CONFIGURATION_PROPOSAL.md`

**Interface:**

- `PlateLayoutRequest` contains the nine ordered sample records, allocated plate code, printable polygon/keep-outs, margins, specimen dimensions, label/code dimensions, connector strategy, and deterministic generator inputs.
- `PlateLayout` returns sample placements, label/code placement, connector/frame geometry, bounds, backend/version, and validation findings.
- `PlateGeometryBackend.build(request)` returns immutable mesh/object artifacts with a stable mapping from every sample label to its own Orca settings object.
- `PlateGeometryBackend.validate(layout)` rejects invalid solids/meshes, collisions, bed or keep-out violations, unreadable geometry parameters, and non-separated/ambiguous sample mappings.

**Steps:**

- [x] Measure build123d install/import cost before constructing a CAD model. Its 0.13.0 Windows Python 3.11 install/import succeeded but occupied 721,973,890 bytes in `Lib/site-packages`; use the standard-library voxel adapter for the axis-aligned requirement.
- [x] Verify the connected mesh 3MF contains ten objects and nine per-sample settings objects whose object/part IDs match. Real Orca preservation remains the next task's gate.
- [x] Validate mesh closure, deterministic A–I mapping, a 4.8 mm outer margin, 2.8 mm tab clearance, eight 2.4 × 0.8 mm links per sample, 1.92 mm² tab contact area, bitmap mark geometry, bed and keep-out constraints, and repeatability.
- [x] Do not install CadQuery: its published dependency set includes OpenCascade and scientific/visualization packages; the selected axis-aligned geometry does not need those operations. Record the evidence rather than adding either CAD runtime.
- [x] Record backend/version, license/options reviewed, package size, no-new-dependency decision, and the reason in the proposal and reuse research.

**Verification:** The two focused geometry modules cover deterministic fixtures, malformed mesh/layout cases, tight/large bed cases, an irregular printable polygon, keep-out intersection, connector failure, and preserved A–I mapping. Binary STL and 3MF export are checked in the current Windows environment. This establishes a local prototype only, not cross-platform support or physical handling.

### Task 3: Round-trip the connected plate through real Orca and the Qt workflow

**Files:**
- Modify: `src/calibrate3dp/grouped_plate.py`
- Modify: `src/calibrate3dp/app/services/grouped_orca_service.py`
- Modify: `src/calibrate3dp/app/qt/workflow_widgets.py` and, if required, `src/calibrate3dp/app/qt/main_window.py`
- Modify: `tests/test_grouped_plate.py`
- Modify: `tests/test_grouped_orca_service.py`
- Modify: `tests/test_qt_printer_workflow.py`
- Modify: `tests/test_orca_slicer_integration.py`
- Update: `docs/IMPLEMENTATION_STATUS.md`, `IMPLEMENTATION_PLAN.md`, `docs/PROGRESS_HISTORY.md`

**Steps:**

- [x] Replace the service's disconnected coupon geometry with the selected backend output while keeping the immutable sample map, profile snapshots, run record, and six-character code behavior intact.
- [x] Package and inspect the connected plate in 3MF. Assert every settings part references the intended sample object and no connector/frame part changes or masks a sample override.
- [x] Extend the opt-in real-Orca gate to all nine samples. Verify expected per-sample flow/speed evidence and a negative case that removes or mis-maps an override and must fail validation.
- [x] Retain the new project, meshes, exact Orca identity evidence, resolved profiles, argv, stdout/stderr, G-code, validation report, and SHA-256 manifest in the evidence directory.
- [x] Present geometry validation and sample-settings validation in the Qt run workflow. Keep unsupported identity/geometry states actionable and keep `print_ready` false with the remaining reasons visible.
- [x] Do not add any control that sends output to a printer. Physical printing and deliberate hand separation remain manual acceptance work.

**Verification:** Run `python -m unittest tests.test_grouped_plate tests.test_plate_layout tests.test_plate_geometry_backend tests.test_grouped_orca_service tests.test_qt_printer_workflow -v` with `QT_QPA_PLATFORM=offscreen`; run `python -m unittest tests.test_orca_slicer_integration.OrcaSlicerIntegrationTests.test_grouped_object_settings_are_applied_and_missing_override_is_rejected -v` with the exact executable/profile/evidence environment; then run `python -m unittest discover -s tests -v`, `python -m compileall -q src/calibrate3dp tests`, and `git diff --check`. Record skipped checks and exact outcomes. The opt-in Orca run must not be represented as physical print acceptance.

### Task 4: Close documentation against actual evidence and hand off remaining gates

**Files:**
- Update: `docs/IMPLEMENTATION_STATUS.md`
- Update: `IMPLEMENTATION_PLAN.md`
- Update: `docs/EXPERIMENT_CONFIGURATION_PROPOSAL.md`
- Update: `docs/REUSE_RESEARCH.md` when a dependency decision was made
- Append: `docs/PROGRESS_HISTORY.md`

**Steps:**

- [x] Mark only the design/geometry and grouped Orca sub-items whose evidence passed. Leave full G-code movement/start/end/temperature checks, release support matrix, physical plate/handling acceptance, cross-platform packaging, and `print_ready` open.
- [x] Record the repository baseline, plan task IDs, changed files, rationale, assumptions, exact commands, actual test outcomes/skips, exact Orca executable and version/identity evidence, profile/G-code hashes, and evidence paths in a new progress-history entry.
- [x] State the next dependent gates: full movement/keep-out and start/end/temperature validation; physical print and hand-tool separation review; tested platform/version matrix; then assessment/refinement/export and remaining calibration definitions.

**Completion criteria:** The Qt flow can generate and persist a connected plate with visible sample labels and plate code; the geometry checks pass; real Orca G-code proves each of nine sample overrides and rejects the negative case; the artifact record retains sufficient provenance for reproduction; documentation matches the evidence. `print_ready` remains false.
