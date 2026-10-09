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
