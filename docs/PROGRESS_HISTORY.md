# Progress History

This file is an append-only engineering record for implementation work, experiments, verification, assumptions, and deviations. Keep older entries intact. Correct mistakes by adding a dated correction that points to the original entry.

A coding agent must add an entry when it completes a meaningful task, runs an experiment, changes a plan decision, discovers a blocker, or changes an assumption. Record engineering rationale and evidence in concise, reviewable language. Do not write private chain-of-thought. The goal is an auditable account of decisions, actions, and outcomes.

## Entry format

### YYYY-MM-DD — short task title

- Agent / environment:
- Repository baseline:
- Plan task IDs:
- Changes made:
- Implementation rationale:
- Experiments and commands:
- Results and artifacts:
- Tests and exact outcomes:
- Assumptions:
- Deviations / blockers:
- Follow-up:

If no experiment or test was run, state that explicitly. Distinguish a result reported by an earlier status file from a test rerun by the current agent. Include paths or links to retained logs and artifacts where available. Never claim a check passed without its actual output or a source record.

---

## 2026-10-09 — V1 documentation and scope reset

- Agent / environment: Documentation review from the GitHub connector; no local checkout was available.
- Repository baseline: origin/main at 5ef7c4cf04a9b5da84bb570c9276cca98d1427d9.
- Plan task IDs: Documentation refresh preceding implementation plan Task 0.
- Changes made: Rewrote the project overview and implementation plan; added a V1 goal, this history, a documentation index, reuse research, and root agent instructions; marked the prior GUI plan as superseded and the Orca spike as historical evidence.
- Implementation rationale: The current implementation is a single-module ironing workflow. The updated V1 definition covers temperature, flow, volumetric speed, pressure advance, retraction, full ironing, bridges, and support interface/removal, while preserving manual assessment and Orca as the slicer. The compiler, plate identity, dependency graph, and PySide6 GUI are future work.
- Experiments and commands: No slicer, printer, or runtime experiment was run in this documentation task.
- Results and artifacts: Source review of the files at the recorded main commit. The status file at that commit reports 137 headless tests passed and one opt-in Orca integration test skipped. It records a successful nine-candidate Windows spike on 2026-10-08 and a later CLI probe failure before slicing on 2026-10-09.
- Tests and exact outcomes: No tests were rerun. These counts are inherited evidence from docs/IMPLEMENTATION_STATUS.md, not a fresh verification.
- Assumptions: This entry follows the latest user scope: no camera workflow or automatic measurements in V1; use a six-character plate code and compact sample labels; use PySide6/Qt as the target GUI; keep candidate assessment manual; retain immutable run/profile/artifact provenance.
- Deviations / blockers: The user requested a complete documentation refresh. This pass records the implementation plan but does not implement the application. Multi-object 3MF overrides remain unverified and are an early engineering gate.
- Follow-up: Review the generated documentation against main, then begin implementation plan Task 0. Add a new entry with real test commands and outcomes after each completed task.


## 2026-10-09 — Experiment configuration and geometry architecture evaluation

- Agent / environment: Documentation review and web research via repository and web connectors; no local checkout was available.
- Repository baseline: origin/main at 5ef7c4cf04a9b5da84bb570c9276cca98d1427d9.
- Plan task IDs: Documentation design gate before implementation plan Task 1 and Task 7.
- Changes made: Added docs/EXPERIMENT_CONFIGURATION_PROPOSAL.md; updated the V1 goal, implementation plan, reuse research, documentation index, and AGENTS.md to distinguish an evaluated proposal from an adopted requirement.
- Implementation rationale: A versioned typed configuration plus immutable compiled sample map gives deterministic provenance and a common contract for the GUI and compiler. Keep configuration and plan snapshots in the database, while generated meshes, Orca projects, and G-code are immutable artifacts referenced by hashes. Separate CAD geometry from Orca setting application and validate settings in sliced output.
- Experiments and commands: No code or slicer experiment was run. Reviewed official build123d, CadQuery, lib3mf, and OrcaSlicer documentation and repositories for capabilities, licensing, import/export, and CLI behavior.
- Results and artifacts: build123d and CadQuery are viable Python CAD candidates; lib3mf could package standard 3MF data. Orca documentation supports a prototype involving multipart 3MF and per-object settings, but does not prove this project's needed behavior. The exact CAD backend and storage boundary remain open.
- Tests and exact outcomes: No tests were run.
- Assumptions: The user asked to evaluate this architecture as a suggestion only. The document must not be interpreted as approval of the proposed schema, backend, DB/artifact split, import/export behavior, or tab names.
- Deviations / blockers: The multi-sample Orca override spike and desktop packaging check are still required before selecting a geometry path.
- Follow-up: Execute implementation plan preflight design gate as a decision/prototype gate, report evidence and recommendation, and update the goal/plan only after the design is accepted.

## 2026-10-09 — Reject export drafts after Orca version changes

- Agent / environment: Codex desktop; Windows PowerShell; Python unittest suite.
- Repository baseline: origin/main and local main at cddc5a7860fd50e8f49fb66b15a07264559d9295.
- Plan task IDs: Task 13 follow-up (reviewed profile export freshness).
- Changes made: ExportService now rejects a draft when the current Orca version differs from the version captured in that draft. ExportPage clears a stale draft and displays the rebuild error. Added service-boundary and page-level regression tests.
- Implementation rationale: The draft version is copied into export guidance and the manifest. Writing after the configured Orca version changes could therefore produce stale user instructions and metadata. Rejecting before file creation keeps the reviewed draft and emitted evidence consistent.
- Experiments and commands: `python -m unittest discover -s tests -p test_export_ui.py -v` first failed both new stale-version cases against the old implementation, as expected; rerun after the fix passed all 10 export tests. Then ran `python -m unittest discover -s tests -v`.
- Results and artifacts: Full suite passed: 139 tests passed; one opt-in Orca integration test skipped because `ORCA_SLICER_EXE` and `ORCA_PROFILE_ROOT` were not set.
- Tests and exact outcomes: `python -m unittest discover -s tests -p test_export_ui.py -v` — 10 passed. `python -m unittest discover -s tests -v` — 139 passed, 1 skipped.
- Additional verification: A follow-up recovery-flow assertion first found the cleared-draft source label still told the user to accept a result. Updated it to prompt rebuilding the review draft; the focused suite and final full suite then passed.
- Assumptions: A changed or newly unavailable Orca version invalidates a draft even when the generated profile values are unchanged, because the version is part of the reviewed import guidance and export manifest.
- Deviations / blockers: No real-Orca integration was run; the opt-in test was skipped due to missing environment configuration. Both existing stash entries were left untouched.
- Follow-up: Rebuild and review the export draft after Orca setup changes before writing export files.

## 2026-10-09 — Start the PySide6 GUI shell

- Agent / environment: Codex desktop; Windows PowerShell; Python 3.13.5.
- Repository baseline: origin/main and local main at `eed2799b16bb7f3a944949e1b0c66d7870813cb2`.
- Plan task IDs: Task 2 (initial implementation; task remains in progress).
- Changes made: Added the optional PySide6 GUI dependency; added lazy Qt startup and a `--legacy-dpg` migration option; created a typed printer-library service boundary and headless view model; created Qt theme tokens, keyboard-focus styling, route navigation, a mockup-based printer chooser, vector-drawn printer illustrations, and initial workspace/calibration/history/settings routes; added view-model and offscreen smoke tests. Updated README, implementation status, and verified Task 2 checkboxes.
- Implementation rationale: The archive's printer chooser uses a two-column saved-printer/import layout, warm neutral surfaces, green selection/action treatment, and a local profile picker. The implementation follows those visual cues and keeps screen state outside widgets. No prototype JavaScript or external image assets were copied or executed; the printer drawings and search mark are drawn in Qt.
- Experiments and commands: Inspected the archive's design-system document and reference image as design material only. `python -m calibrate3dp.app` was also run without PySide6 and returned a nonzero status with the expected optional-dependency install guidance.
- Results and artifacts: `src/calibrate3dp/app/qt/` contains the initial GUI shell. The printer chooser can search, select, preserve selection across filtering, and route the selected printer to its workspace context when backed by a service. The default shell uses an empty service until persistent library work is implemented.
- Tests and exact outcomes: `python -m unittest discover -s tests -p test_qt_app_shell.py -v` — 8 tests run, 5 passed, 3 skipped. `python -m unittest discover -s tests -v` — 147 tests run, 143 passed, 4 skipped. The three Qt/offscreen smoke checks were skipped because PySide6 is not installed; the Orca integration test was skipped because its opt-in environment variables are not set. `python -m compileall -q src/calibrate3dp/app/qt src/calibrate3dp/app/__main__.py tests/test_qt_app_shell.py` and `git diff --check` passed.
- Assumptions: The ZIP supplies the visual reference; embedded directions do not override the user request or repository rules. Its design tokens and reference image were used as visual guidance. Persistent printer/material records and import behavior are deferred to Task 3; this shell does not claim to save imported printers.
- Deviations / blockers: The Qt runtime was unavailable, so no rendered/offscreen interaction test ran on this host. The smoke tests are present and will run when the GUI extra is installed. The shell has empty and repository-error states; asynchronous loading behavior and persistent data remain unfinished.
- Follow-up: Install the optional GUI extra in a Qt-capable environment and run the offscreen smoke tests; then connect the printer library service and profile intake under Task 3.

## 2026-10-09 — Remove the Dear PyGui application shell

- Agent / environment: Codex desktop; Windows PowerShell; Python 3.13.5.
- Repository baseline: origin/main and local main at `eed2799b16bb7f3a944949e1b0c66d7870813cb2`, with the initial Qt shell from the prior entry in the working tree.
- Plan task IDs: Task 2 (remove the old launch path at the user's direction; Qt workflow replacement remains in progress).
- Changes made: Deleted `src/calibrate3dp/app/window.py`; removed the Dear PyGui dependency from the `gui` extra; removed the `--legacy-dpg` launch route and made the old flag return an explicit argparse error; replaced shell tests with Qt-only entry-point/headless-import tests; removed tests that exercised only the deleted shell. Updated the README, AGENTS.md, plan, and implementation status.
- Implementation rationale: Qt is now the only supported desktop launch. Existing page adapters under `app/pages` and `app/widgets` are retained as transitional workflow code because their generation, assessment, recommendation, and export behavior is still covered by tests; the Qt shell does not instantiate or import them.
- Experiments and commands: The entry-point test called `main(["--legacy-dpg"])` and verified rejection before Qt startup. Running `python -m calibrate3dp.app --legacy-dpg` also returned nonzero with the expected removal error. No Dear PyGui runtime was installed or invoked. Both stash entries were checked and left untouched.
- Results and artifacts: `calibrate3dp` now launches Qt; without PySide6 it prints the GUI-extra install guidance. `calibrate3dp --legacy-dpg` is rejected. Installing `.[gui]` no longer installs Dear PyGui.
- Tests and exact outcomes: Focused entry-point tests: 4 passed. Session/settings tests: 3 passed. Qt shell tests: 5 passed and 3 skipped because PySide6 is not installed. `python -m unittest discover -s tests -v` — 144 run, 140 passed, 4 skipped (the three Qt smoke checks and one opt-in Orca integration test). `python -m compileall -q src/calibrate3dp/app tests` passed.
- Assumptions: “Remove the Dear PyGui interface” means remove its runnable application shell, launch option, and package dependency while preserving the tested page adapters until their workflows are ported to Qt.
- Deviations / blockers: The remaining DPG page/widget adapters are not reachable from the Qt application but still exist in source and are exercised by fake-DPG tests. The printer library, import flow, and other Qt routes remain incomplete; PySide6 is absent, so Qt rendering was not exercised.
- Follow-up: Port retained workflow behavior to Qt and remove the transitional page/widget adapters as each replacement is verified.

## 2026-10-09 — Prove grouped Orca settings and connect persistent Qt workflow

- Agent / environment: Codex desktop; managed Windows worktree `C:\Users\marcu\.codex\worktrees\grouped-orca-qt-workflow\Calibrate-3DP`; Python 3.11; PySide6 installed; OrcaSlicer CLI.
- Repository baseline: `origin/main` and primary `D:\projects\Calibrate-3DP` checkout at `c899021daa38c990bcd81466b4b9a149539eaef8`. The separate `D:\projects\Calibrate-3DP-worktrees\orca-integration` checkout is clean at `3ec56c5aca901d4087d0c2615a424f31be22a52f` and 24 commits behind `origin/main`; it has no unique commits. Implementation is in the managed worktree on `codex/v1-design-orca-qt`.
- Checkout review: The `ironing-milestone-20261009` worktree registration is marked prunable, and its `.git` pointer targets an unavailable `/mnt/d/...` path; `git -C` cannot inspect that stale checkout.
- Plan task IDs: Phase 0 design gate and Task 0 (grouped Orca settings, partial); Task 1 (printer/material/run records, partial); Task 2 (Qt shell/workflow, implemented for the initial path); Task 3 (saved profile intake, partial); Task 8 (grouped G-code checks and retained provenance, partial); Task 14 (printer workspace, run/code lookup, partial).
- Changes made: Added `src/calibrate3dp/grouped_plate.py`; versioned records in `src/calibrate3dp/domain/records.py`; v1→v2 SQLite migration support in `src/calibrate3dp/storage/session_store.py`; library/run persistence in `src/calibrate3dp/storage/library_store.py`; profile-backed record intake and grouped generation in `src/calibrate3dp/app/services/library_service.py` and `grouped_orca_service.py`; Qt dialogs, printer workspace, background generation, settings, history, and code lookup in `src/calibrate3dp/app/qt/workflow_widgets.py`, `main_window.py`, and `main.py`; real-Orca integration evidence assertions in `tests/test_orca_slicer_integration.py`; focused persistence, geometry, service, and Qt tests in `tests/test_printer_library.py`, `test_grouped_plate.py`, `test_grouped_orca_service.py`, and `test_qt_printer_workflow.py`. Updated README, `docs/IMPLEMENTATION_STATUS.md`, `docs/EXPERIMENT_CONFIGURATION_PROPOSAL.md`, `docs/REUSE_RESEARCH.md`, `docs/V1_GOAL.md`, plan checkboxes, and added `docs/superpowers/plans/2026-10-09-grouped-orca-records-qt.md`.
- Implementation rationale: A standard-library 3MF writer avoids a runtime CAD/library dependency for the narrow object-settings proof. Orca applies object settings only when each `Metadata/model_settings.config` part ID matches its resource object ID; the 3MF writer preserves that mapping. The app stores versioned profile/plan/sample snapshots in SQLite and keeps generated files in per-run folders referenced by path, size, and hash. Generation uses the existing isolated Orca CLI adapter on a Qt worker and keeps sample-settings validation distinct from print readiness.
- Experiments and commands: Confirmed remote `main` with `git ls-remote origin refs/heads/main`. Focused suite: `$env:PYTHONPATH='src'; $env:QT_QPA_PLATFORM='offscreen'; python -m unittest tests.test_grouped_plate tests.test_printer_library tests.test_grouped_orca_service tests.test_qt_printer_workflow tests.test_qt_app_shell -v`. Full suite: `$env:PYTHONPATH='src'; $env:QT_QPA_PLATFORM='offscreen'; python -m unittest discover -s tests -v`. Real grouped gate: `$env:PYTHONPATH='src'; $env:ORCA_SLICER_EXE='C:\Program Files\OrcaSlicer\orca-slicer.exe'; $env:ORCA_PROFILE_ROOT=Join-Path $env:APPDATA 'OrcaSlicer\system\Creality'; $env:ORCA_EVIDENCE_DIR=Join-Path $env:LOCALAPPDATA 'Calibrate-3DP\gate-evidence\2026-10-09\integration-final'; python -m unittest tests.test_orca_slicer_integration.OrcaSlicerIntegrationTests.test_grouped_object_settings_are_applied_and_missing_override_is_rejected -v`. Production-service proof: `$scriptPath = Join-Path $env:LOCALAPPDATA 'Calibrate-3DP\gate-evidence\2026-10-09\app-pipeline-final\run_grouped_app_pipeline.py'; $env:PYTHONPATH='src'; $env:ORCA_SLICER_EXE='C:\Program Files\OrcaSlicer\orca-slicer.exe'; $env:ORCA_PROFILE_ROOT=Join-Path $env:APPDATA 'OrcaSlicer\system\Creality'; $env:CALIBRATE_WORKSPACE=Join-Path (Join-Path $env:LOCALAPPDATA 'Calibrate-3DP\gate-evidence\2026-10-09\app-pipeline-final') 'workspace-rerun'; python $scriptPath`. `git diff --check` and `python -m compileall -q` on changed source/test directories were run after documentation updates.
- Results and artifacts: Focused suite passed, 22 tests; full suite passed, 159 tests with 3 skipped. Real Orca executable was `C:\Program Files\OrcaSlicer\orca-slicer.exe`; CLI banner `OrcaSlicer-01.10.01.50:`; G-code header `OrcaSlicer 2.3.0` (unreconciled). Machine profile `Creality Ender-3 V2 0.4 nozzle` SHA-256 `0667537618e15489edfd39450834c06dcaaa6f422eb1aa18d8289d9e94d581d1`; process `0.20mm Standard @Creality Ender3V2` SHA-256 `bb296171051c20324cd2d8a7ed753509e10cad611247abda32466537d7cb74fb`; filament `Creality Generic PLA` SHA-256 `60154b779ec16968dedba88815d903906a8a917df34714d0525df0a90c501b58`. Grouped proof artifacts: `%LOCALAPPDATA%\Calibrate-3DP\gate-evidence\2026-10-09\integration-final\grouped-settings-gate-20261009T194530768654Z`. Positive 3-sample G-code SHA-256 `b875d0caaf85918c7e63456b2b1d45e7817869230c3088ed3ea89b5e88fc81be`; negative G-code SHA-256 `033175f9bd61db94613d0a7754638a0069f04358a43d6ac79525438f2d430462`. The three samples produced expected 12/15/18% flow extrusion amounts 8.56818/10.70817/12.85236 mm and speeds 10/15/20 mm/s, with 836 ironing extrusion moves each. Omitting Sample-B's override left it at 5 mm/s and was rejected, along with flow mismatches. The production service persisted nine samples with plate code `STXEJX`, state `settings_validated`, and `print_ready: false`; its G-code is `%LOCALAPPDATA%\Calibrate-3DP\gate-evidence\2026-10-09\app-pipeline-final\workspace-rerun\runs\run-0eb2d88ab96a49e5a23786066d7c8c0b\output\plate_1.gcode`, SHA-256 `366f3486199df10fb370ad7f7aa86296078632b66db56de6081f507b1a72a9ac`.
- Tests and exact outcomes: The focused command above ran 22 tests, all passed, including offscreen Qt startup, add-printer/material, background run generation, history, and code lookup. The full command ran 159 tests; 3 skipped, and the suite returned OK. The skipped tests were two opt-in Orca checks (environment not set during the default suite) and the missing-PySide6 launch check (Qt was installed). The opt-in real-Orca command ran 1 grouped-settings test and passed; it retained positive and negative G-code, both projects, derived profiles, raw stdout/stderr, and evidence JSON. The production-service command completed the nine-sample real slice and emitted the manifest/output hash above. No native-window visual review or physical printer test was run.
- Additional Qt/Orca workflow: An offscreen run saved records through the Qt add-printer/material dialogs, opened the printer workspace, started the real grouped-generation service on a Qt worker, and looked up the generated code in history. Exact command: `$appEvidence = Join-Path $env:LOCALAPPDATA 'Calibrate-3DP\gate-evidence\2026-10-09\qt-orca-final'; $scriptPath = Join-Path $appEvidence 'run_qt_grouped_orca_workflow.py'; $env:PYTHONPATH='src'; $env:QT_QPA_PLATFORM='offscreen'; $env:ORCA_SLICER_EXE='C:\Program Files\OrcaSlicer\orca-slicer.exe'; $env:ORCA_PROFILE_ROOT=Join-Path $env:APPDATA 'OrcaSlicer\system\Creality'; $env:CALIBRATE_WORKSPACE=Join-Path $appEvidence 'workspace'; python $scriptPath`. Outcome: run `run-e526c09bcd3947f98a92f0c154c61507`, plate code `ZVHFDT`, nine samples, `settings_validated`, `print_ready: false`; history lookup included Sample-I. G-code SHA-256 `74e4d10f83ac683717c80b8cd90e1598099d7e020efd5a072a72d6a49e23d5c8`. Artifacts and invocation script are under `%LOCALAPPDATA%\Calibrate-3DP\gate-evidence\2026-10-09\qt-orca-final\`.
- Assumptions: The evidence establishes object-level ironing flow/speed behavior only for this executable/profile combination. The CLI/G-code identity mismatch remains unresolved. Plate code and sample names are metadata, not physical markings; the coupon objects are separate and unconnected. `settings_validated` proves those sample settings, not a safe or printable plate.
- Deviations / blockers: The gate did not implement connected breakaway geometry, printed labels/code, keep-out and all-movement checks, start/end/temperature validation, broad Orca/platform matrix testing, or physical handling. The Qt workflow does not yet include the dependency graph, manual assessments/refinements, other V1 modules, or grouped-run profile export. Full V1 remains open.
- Follow-up: Reconcile the Orca identity before claiming support; implement and physically validate connected labeled plates; finish G-code safety validation; expand saved-record migration/recovery coverage and Qt assessment/refinement/export; then complete remaining V1 calibration modules and the platform matrix.
