# Contextual Ironing State Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a deterministic dependency evaluator for the currently implemented grouped ironing workflow and show its truthful state and reasons in the Qt printer workspace.

**Architecture:** Put rule and state types in a Qt-independent `calibrate3dp.calibration` package. An application service assembles current printer, material, profile-freshness, run, assessment, and decision evidence from the existing repository, then evaluates the ironing definition. Qt renders only the returned states; it does not contain prerequisite rules. Existing immutable profile, plan, parent-assessment, and run records remain the evidence source, and generated output remains `print_ready: false`.

**Tech Stack:** Python 3.11 standard library, existing SQLite repository, PySide6, `unittest`.

**Spec:** `docs/V1_GOAL.md` acceptance criteria 2, 3, and 8; `IMPLEMENTATION_PLAN.md` Tasks 4 and 14; `docs/IMPLEMENTATION_STATUS.md`.

## Global Constraints

- V1 stays manual and local; no printer control, firmware writes, cloud services, camera workflow, or automatic scoring.
- Keep dependency evaluation and state transitions independent from Qt.
- Use saved profile snapshots, source hashes, assessment revisions, and parent links as evidence; do not rewrite historical runs.
- Unknown or missing required context blocks the next experiment and carries an explanation.
- A `ready` state means eligible to configure or generate this calibration under the implemented rules. It never means ready to print.
- Do not add runtime dependencies or write to Orca's source profile directories.

## Review Focus

- A missing, deleted, changed, or inherited source profile must block new generation or stale the result that depended on it; it must not be treated as unchanged.
- A failed, canceled, in-progress, or unassessed run must never appear accepted.
- A changed input should stale only the calibrations whose rule set declares that input as relevant.
- A missing context value, unknown prerequisite, duplicate rule, or dependency cycle must fail closed with an actionable reason.
- The Qt workspace must render service output verbatim enough to preserve state/reasons and must never infer print readiness from calibration readiness.

## Scope

This pass registers and displays only the existing ironing flow × speed workflow. It establishes reusable dependency primitives but does not add the other seven V1 calibration families, a general calibration-definition registry/compiler, user override persistence, complete Orca dialect validation, or physical-print acceptance. When a required rule is unmet, this first version blocks; there is no override action.

## Task 1: Add the pure dependency and state model

**Files:**

- Create `src/calibrate3dp/calibration/__init__.py`
- Create `src/calibrate3dp/calibration/state.py`
- Create `src/calibrate3dp/calibration/dependencies.py`
- Create `tests/test_calibration_dependencies.py`

**Interfaces:**

- `CalibrationStatus`: string enum with `untested`, `blocked`, `ready`, `in_progress`, `needs_review`, `accepted`, and `stale`.
- `DependencyRule`: frozen value with `rule_id`, `calibration_id`, `source_kind` (`input` or `calibration_result`), `source_key`, `operator` (`equals`, `one_of`, `present`, or `accepted`), `expected_value`, `severity` (`required` or `recommended`), and `invalidates` input keys.
- `DependencyContext`: immutable `values: Mapping[str, JsonValue]`; an absent key means unknown, while a present JSON null is a known null value.
- `CalibrationEvidence`: immutable latest-run status, captured input values, assessment state, and accepted-decision evidence for one calibration.
- `CalibrationState`: immutable calibration ID, status, `can_start`, reasons, recommendations, and evaluated input snapshot.
- `DependencyGraph.from_rules(rules) -> DependencyGraph` validates unique IDs, known calibration references, and acyclic prerequisite edges.
- `DependencyEvaluator(graph).evaluate(context, records) -> tuple[CalibrationState, ...]` applies rules in stable ID order and returns every reason.
- `DependencyEvaluator.affected_by_change(changed_keys, graph) -> frozenset[str]` returns the transitive set of calibration IDs invalidated by the changed keys.
- State precedence: an active generating run is `in_progress`; changed or unavailable relevant inputs for a historical result make it `stale`; a successful settings-validated run without a saved assessment is `needs_review`; a current physically accepted decision is `accepted`; no history with unmet required context is `blocked`; no history with satisfied rules is `untested`; and failed/canceled or reviewed non-accepted history with satisfied rules is `ready`. `can_start` is separate from lifecycle status, so a stale result can also explain why a new run is blocked.

- [x] **Step 1: Write failing tests** for required versus recommended rules, missing input, accepted-result prerequisites, deterministic state ordering, duplicate IDs, unknown references, cycles, and transitive invalidation (`nozzle_diameter` affects pressure advance while an unrelated key does not affect ironing).
- [x] **Step 2: Run** `$env:PYTHONPATH='src'; python -m unittest tests.test_calibration_dependencies -v` **and confirm it fails** with missing model/evaluator behavior.
- [x] **Step 3: Implement the frozen types and pure evaluator** in `state.py` and `dependencies.py`; use explicit operator dispatch rather than executable callbacks so rule sets remain inspectable and serializable.
- [x] **Step 4: Run** `$env:PYTHONPATH='src'; python -m unittest tests.test_calibration_dependencies -v`; all focused tests must pass.
- [x] **Step 5: Commit** the pure domain layer and its tests (`755b7f8`).

## Task 2: Evaluate and retain the current ironing context

**Files:**

- Create `src/calibrate3dp/calibration/ironing.py`
- Create `src/calibrate3dp/app/services/calibration_state_service.py`
- Modify `src/calibrate3dp/app/services/grouped_orca_service.py`
- Modify `src/calibrate3dp/app/qt/main.py` and `src/calibrate3dp/app/qt/main_window.py` to share the service with the workspace.
- Create `tests/test_calibration_state_service.py`
- Extend `tests/test_grouped_orca_service.py`

**Interfaces:**

- `IRONING_DEPENDENCY_RULES` declares the current ironing prerequisites: a resolvable selected printer/material pair, matching nozzle context, available and unchanged printer/process/filament source hashes, and slicer availability for generation. Ironing has no required prior calibration result in this pass.
- `CalibrationStateService(library, evaluator, *, slicer_available: Callable[[], bool])` exposes `states_for(printer_id, material_id) -> tuple[CalibrationState, ...]`, `evaluate_configuration(configuration) -> CalibrationState`, and `snapshot_for(configuration) -> Mapping[str, JsonValue]`. `slicer_available` is supplied by the existing Orca generation setup.
- `GroupedOrcaGenerationService` creates or receives one `CalibrationStateService`, exposes that instance to `MainWindow`, and calls `evaluate_configuration` before allocating a plate code, run record, or artifact directory. A blocked configuration raises a typed `CalibrationBlockedError` carrying the state reasons.
- Each generated run retains a versioned `dependency_snapshot` object under `CalibrationRunRecord.validation`: ruleset ID/version, exact input values and profile hashes evaluated, prerequisite assessment/run references, and rule outcomes. Existing runs without this member remain readable and are evaluated from their saved profiles, plan, and parent links; if a rule cannot be reconstructed, report `needs_review` instead of treating it as current. Never rewrite legacy history during evaluation.
- The service compares current source hashes and selected profile context with the run's captured values. A changed or unavailable relevant input makes that result stale; the service does not use the global run status as a substitute for calibration state.

- [x] **Step 1: Write failing service tests** for a compatible untested printer/material, missing or changed source profile, generating run, failed/canceled run, generated run without assessment, accepted physically attested confirmation, a changed versus unrelated input, and a blocked configuration allocating no plate code, run, or files.
- [x] **Step 2: Run** `$env:PYTHONPATH='src'; python -m unittest tests.test_calibration_state_service -v` **and confirm they fail.**
- [x] **Step 3: Add the versioned ironing rules and service**; have `GroupedOrcaGenerationService` reject a blocked configuration before side effects, and capture the evaluator snapshot for an allowed grouped ironing run without changing the frozen plan or source Orca profiles.
- [x] **Step 4: Add compatibility coverage** showing legacy run records lacking a dependency snapshot still load and are reported conservatively.
- [x] **Step 5: Run** `$env:PYTHONPATH='src'; python -m unittest tests.test_calibration_state_service tests.test_grouped_orca_service -v`; all tests must pass.
- [x] **Step 6: Commit** the state service, snapshot capture, and tests (`66272cf`).

## Task 3: Show calibration state in the Qt workspace

**Files:**

- Create `src/calibrate3dp/app/qt/calibration_status.py`
- Modify `src/calibrate3dp/app/qt/workflow_widgets.py`
- Modify `src/calibrate3dp/app/qt/main_window.py` to pass the generation service's state service into the workspace.
- Create `tests/test_qt_calibration_graph.py`

**Interfaces:**

- `CalibrationStatusPanel(QWidget).set_states(states)` renders a row for the available `Ironing · flow × speed` workflow, a status label, `can_start`, and all blocking/recommendation reasons from the service.
- `PrinterWorkspacePage` refreshes the panel when printer or material selection changes, when generation starts/finishes, and after run review returns.
- If no material is selected, the panel reports blocked with the concrete reason. Unimplemented calibration families are described as not yet available; they are not fabricated as blocked graph nodes.
- The generation action is disabled when required context blocks the workflow or the existing Orca setup is unavailable. The panel keeps a separate statement that generated plates are not print-ready.

- [x] **Step 1: Write failing offscreen Qt tests** for untested/available, blocked, in-progress, needs-review, accepted, and stale states; verify reasons are visible and no state changes `print_ready`.
- [x] **Step 2: Run the Qt tests with** `$env:PYTHONPATH='src'; $env:QT_QPA_PLATFORM='offscreen'; python -m unittest tests.test_qt_calibration_graph -v` and confirm failure.
- [x] **Step 3: Implement the panel and workspace refresh wiring**; keep all status decisions in `CalibrationStateService`.
- [x] **Step 4: Rerun the offscreen Qt tests**; all states and reason text must match service output.
- [x] **Step 5: Commit** the Qt presentation and tests (`a959f9c`).

## Task 4: Verify the pass and update project evidence

**Files:**

- Modify `IMPLEMENTATION_PLAN.md` only for Task 4 and Task 14 checkboxes proven by this pass.
- Modify `docs/IMPLEMENTATION_STATUS.md` with the evaluator scope, test results, and remaining V1 blockers.
- Append exact commands, baseline, files, assumptions, decisions, results, and log paths to `docs/PROGRESS_HISTORY.md`.

- [x] **Step 1: Run focused domain, service, and offscreen Qt tests.**
- [x] **Step 2: Run** `$env:PYTHONPATH='src'; $env:QT_QPA_PLATFORM='offscreen'; python -m unittest discover -s tests -v`; record skips and reasons.
- [x] **Step 3: Run** `python -m compileall -q src/calibrate3dp tests` and `git diff --check`.
- [x] **Step 4: Update only plan items whose tests passed.** Keep all other V1 calibration and support-matrix items open. Do not rerun or claim a new Orca matrix entry because this pass does not change the slicer adapter or sliced toolpaths.

## Acceptance Gate

- Pure dependency evaluation is deterministic, handles missing context and cycles conservatively, and has targeted tests for transitive stale propagation.
- The current ironing status is derived from persisted printer/material/profile/run/assessment/decision evidence; generated runs retain a versioned dependency snapshot and legacy runs remain readable.
- Qt presents service states and reasons under offscreen tests without duplicating rules or implying print readiness.
- The full test suite, compile check, and diff check pass; history records the exact evidence.
- This pass does not close V1. The other calibration families, explicit recorded overrides, complete Orca safety coverage, physical plate acceptance, crash recovery, support matrix, and release checks remain open.
