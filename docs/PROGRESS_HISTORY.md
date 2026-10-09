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
