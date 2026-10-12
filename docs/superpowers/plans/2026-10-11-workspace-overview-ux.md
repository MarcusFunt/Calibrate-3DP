# Printer Workspace Overview UX Implementation Plan

> **For agentic workers:** Execute this plan task by task with the applicable test-first workflow. This work stays in the current checkout because it already contains authorized, uncommitted UI changes.

**Goal:** Make the printer workspace Overview a practical continuation point by showing a state-aware next action, the current plate lineage, and recent plates.

**Architecture:** Keep dependency and acceptance decisions in existing services. The Qt page reads the evaluated `CalibrationState` and immutable repository records to select a navigation target; it does not infer print readiness or accept results. Existing plate details remain the single Inspect, Assess, Decision, and Export workflow.

**Tech Stack:** Python 3.11+, PySide6/Qt, current library repository, `unittest` with Qt offscreen mode.

**Spec:** `docs/V1_GOAL.md`; visual context from the user-provided Calibrate-3DP v2 mockup and design notes. The mockup is inspiration, not a replacement product specification.

## Global Constraints

- V1 remains local and manual; no camera workflow, automatic assessment, printer control, cloud service, or firmware writes.
- A plate is never described as print-ready unless the versioned Orca/G-code validation contract passes.
- Assessment uncertainty and immutable revisions remain explicit; UI routing does not change decision or export policy.
- Use only existing Qt/runtime dependencies and repository services; do not copy mockup code or artwork.

## Review Focus

- A run for another selected material must not become the recommended next step; test material filtering.
- A validated run without an assessment must route to Assess, while failed or canceled runs route to Inspect; test both.
- Saved assessments route to Decision without implying acceptance; test the label and target tab.
- Missing or cyclic lineage metadata must degrade to a useful single-plate view without breaking the Overview; test incomplete links.
- Empty history and blocked setup must retain clear guidance and existing generation gates; test both.

---

### Task 1: State-aware Overview recommendation

**Files:**
- Modify: `src/calibrate3dp/app/qt/workflow_widgets.py`
- Modify: `src/calibrate3dp/app/qt/experiment_detail.py`
- Test: `tests/test_qt_calibration_graph.py`

**Interfaces:**
- `PrinterWorkspacePage` consumes `CalibrationStateService.states_for`, selected-material run records, and the repository's latest assessment revision.
- `ExperimentDetailsDialog` accepts `initial_tab: str = "Inspect"` and selects one of its existing four tabs.

- [x] Write tests for no-run configuration, validated-unassessed Assess routing, failed-run Inspect routing, saved-assessment Decision routing, and cross-material filtering.
- [x] Run the focused offscreen tests and confirm the new behavior fails before implementation.
- [x] Add the recommendation card and route its primary action through existing generation and run-detail flows; keep blocker copy tied to the status panel.
- [x] Run focused Qt tests and verify the state-aware paths pass.

### Task 2: Read-only lineage and recent plates

**Files:**
- Modify: `src/calibrate3dp/app/qt/workflow_widgets.py`
- Test: `tests/test_qt_app_shell.py` and `tests/test_qt_calibration_graph.py`

**Interfaces:**
- Lineage reads `get_run_config_link` and `get_run` from the existing repository.
- Recent plates use the existing printer-filtered `list_runs` result; the Experiments tab remains the complete list.

- [x] Write tests for chronological parent-to-child lineage, clickable plate entries, four-row recent history, and missing/cyclic link fallback.
- [x] Run the focused offscreen tests and confirm the new behavior fails before implementation.
- [x] Render linked plate-code steps and a compact recent-plates table with explicit run status and material labels; route activated entries to Inspect.
- [x] Run focused Qt tests and inspect the Overview at the minimum supported window size.

### Task 3: Record and verify

**Files:**
- Update: `IMPLEMENTATION_PLAN.md`, `docs/IMPLEMENTATION_STATUS.md`, `docs/PROGRESS_HISTORY.md`
- Capture: offscreen Overview preview and verification logs under `%LOCALAPPDATA%\\Calibrate-3DP\\gate-evidence\\2026-10-11\\workspace-overview-ux\\`

- [x] Run the focused Qt suite, the full relevant Qt suite, Python compilation, and `git diff --check`.
- [x] Record exact commands, actual outcomes, skips, repository baseline, and remaining manual accessibility/display-scaling review.
- [x] Mark the Task 14 UX checkbox complete only after verification.
