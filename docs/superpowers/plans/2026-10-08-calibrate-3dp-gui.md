# Calibration Workbench GUI Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox syntax for tracking.

**Goal:** Deliver a native desktop workflow that imports the user's OrcaSlicer profiles, guides a complete calibration session, saves progress, records print results, proposes the next experiment, and exports a reviewed profile without changing the source presets.

**Architecture:** Keep the existing profile and experiment core independent of Dear PyGui. Add a Dear PyGui desktop application shell that calls typed application services, with SQLite for the session index and ordinary files for session evidence and generated artifacts. Orca discovery, profile adapters, slicing, geometry, and G-code validation stay behind services; the GUI reports their actual state and never guesses that a job succeeded. Enable manual callback management so application actions run from the render loop; worker tasks publish progress through a thread-safe queue for the UI loop to consume.

**Tech Stack:** Existing Python package and unittest suite; Python >=3.11; optional Dear PyGui 2.x GUI extra; SQLite from the standard library; Dear PyGui tables and themed widgets for candidate matrices; Orca CLI through the project's isolated, mockable job service.

**Spec:** IMPLEMENTATION_PLAN.md, especially sections 1–2, 4–8, 12–17; README.md; docs/IMPLEMENTATION_STATUS.md. This plan implements the user-interface and session workflow, and preserves the original Orca integration gate.

## Global Constraints

- Preserve the package's Python >=3.11 requirement and keep the headless core importable without installing Dear PyGui.
- Install the GUI through an optional `gui` dependency extra; use Dear PyGui >=2.3 and <3, and verify a compatible wheel for each supported OS/Python combination.
- Do not start GUI implementation until the original plan's Phase 0 exit gate has a checked-in report and fixture proving profile resolution, candidate-specific slicing, machine start/end code preservation, and usable-bed bounds.
- Use the existing ProfileDocument, ProfileCatalog, ResolvedProfile, ExperimentPlan, CandidateAssessment, ExperimentResults, ironing planner, and profile patch primitive; do not duplicate their domain logic in Dear PyGui callbacks or widgets.
- Never silently replace a missing or ambiguous profile setting with an Orca default. Show its provenance and block only the actions that require that value.
- Never write to the source Orca preset directory or modify/activate a source profile. Export a new file only after the user reviews its diff and destination.
- Never invoke Orca through a shell command string. The job service receives an executable and argument list and uses a disposable per-job data directory.
- Keep all profiles, notes, photographs, generated files, and session state local. Diagnostic exports omit profile contents and photographs unless the user explicitly includes them.
- The GUI does not start, upload, or control a printer. Printing happens through the user's normal Orca workflow.
- Do not display invented progress percentages or estimates. When Orca cannot report a percentage, show an indeterminate progress state and the current phase.
- Use Dear PyGui's table API for the candidate matrix; keep calibration calculations and profile-schema interpretation outside callbacks and view-building code.
- Test navigation state, callback handlers, and service adapters with fake services without creating a native viewport. Reserve display-dependent Dear PyGui smoke checks and real Orca execution for gated integration and manual acceptance runs.

## Review Focus

- Malformed, ambiguous, inherited, or unsupported profiles must show provenance and a specific recovery action; they must never appear as a valid baseline.
- Missing Orca or an unverified Orca version must keep generation disabled and retain an actionable setup path.
- Closing and reopening during profile selection, printing, result entry, or export review must restore the last saved session state and its artifacts.
- A failed or canceled Orca process must leave the interface responsive, preserve logs, and mark partial output invalid.
- Ties, missing specimens, uncertain scores, and unreviewed results must remain explicit and must not be silently promoted to an accepted profile.

---

## Product Flow

The main window has Home, New Calibration, Sessions, and Settings destinations. A calibration uses a visible stepper inside the main content area:

1. Select printer, filament, and process profiles.
2. Select a calibration module and inspect the imported baseline.
3. Review the exact candidate matrix, fixed values, and specimen map.
4. Generate and validate the test; print it manually through Orca.
5. Enter observations, choose or resolve tied candidates, and review the next recommendation.
6. Print a confirmation run when required, accept the result, and export a new profile.

The wizard autosaves after every meaningful change. Back navigation does not discard data. Closing an active slicing job first offers Cancel Job and Keep Running options only if the process service can safely detach; otherwise it requires an explicit cancel or wait decision and preserves logs. The app resumes to the last completed step with a visible saved-state timestamp.

The first polished module is Ironing Finish. Bridge and Support Interface cards may be visible as planned modules only when their planners and geometry adapters are not yet available; they must be labeled unavailable and cannot create a session. The UI must not imply that a future module is already implemented.

## File Structure

| Path | Responsibility |
|---|---|
| pyproject.toml | Optional GUI dependency and desktop entry point. |
| src/calibrate3dp/app/__main__.py | GUI entry point and application setup. |
| src/calibrate3dp/app/window.py | Main window, top-level navigation, close/recovery behavior. |
| src/calibrate3dp/app/models.py | Immutable UI-facing profile, session, preview, and job state types. |
| src/calibrate3dp/app/services/profile_service.py | Discovery/import orchestration and calls into ProfileCatalog. |
| src/calibrate3dp/app/services/session_service.py | Session workflow operations and persistence boundary. |
| src/calibrate3dp/app/services/experiment_service.py | Profile-based default sweep creation, review state, and refinement calls. |
| src/calibrate3dp/app/services/generation_port.py | Typed interface for preview, slicing, validation, cancellation, and artifacts. |
| src/calibrate3dp/app/services/export_service.py | Reviewed profile diff and output-package preparation. |
| src/calibrate3dp/storage/session_store.py | SQLite session index, versioned payloads, and schema migrations. |
| src/calibrate3dp/app/pages/ | Home, setup, profile selection, module, review, generation, results, recommendation, and export pages. |
| src/calibrate3dp/app/widgets/ | Reusable profile cards, warnings, stepper, candidate table, and status panels. |
| tests/test_app_shell.py | Application launch and navigation tests. |
| tests/test_session_store.py | Save/resume, migration, and artifact-reference tests. |
| tests/test_profile_selection_ui.py | Profile provenance, import, and blocked-state tests. |
| tests/test_experiment_review_ui.py | Default matrix, fixed values, editing, and preview tests. |
| tests/test_generation_ui.py | Async progress, failure, cancellation, and output-validity tests. |
| tests/test_result_contract.py | Versioned result-model behavior. |
| tests/test_results_ui.py | Assessment editing, autosave, tie, and missing-specimen tests. |
| tests/test_recommendation_ui.py | Refinement explanations and acceptance-gate tests. |
| tests/test_export_ui.py | Diff, destination, and source-preservation tests. |

The implementation must keep this package under the existing calibrate3dp name. Do not create a second calibration_workbench package.

## Interfaces

Define these application-facing types in app/models.py before building the pages:

- ProfileSelection contains exactly one printer, filament, and process ResolvedProfile plus the selected source paths, source hashes, and compatibility warnings.
- SessionSnapshot contains session_id, created_at_utc, updated_at_utc, module_id, current_step, ProfileSelection, optional ExperimentPlan payload, optional ExperimentResults payload, run IDs, relative artifact paths, and a saved-state version.
- SessionSummary contains session_id, module_id, status, updated_at_utc, and human-readable profile names for the Home and Sessions lists.
- ExperimentPreview contains plan_id, ordered plate previews, candidate-to-specimen labels, fixed settings, estimated duration/material when verified, warnings, and validation state.
- GenerationState is one of queued, running, succeeded, failed, or canceled. GenerationEvent contains state, phase, stdout/stderr delta, optional exit code, artifact paths, and validation messages.
- GenerationService exposes preview(session, plan) -> ExperimentPreview, start(session, plan) -> GenerationHandle, and cancel(job_id) -> None. The concrete Orca service is supplied by the original integration plan; the GUI uses a fake in tests.
- SessionRepository exposes create(snapshot) -> None, save(snapshot) -> None, load(session_id) -> SessionSnapshot, and list_recent(limit=20) -> tuple[SessionSummary, ...].
- SessionService exposes create_session(selection, module_id) -> SessionSnapshot, save(snapshot) -> None, resume(session_id) -> SessionSnapshot, and list_recent(limit=20) -> tuple[SessionSummary, ...].
- ProfileService exposes discover() -> ProfileCatalog, import_json(path, scope) -> ProfileDocument, import_bundle(path) -> tuple[ProfileDocument, ...], and resolve(kind, scope, name) -> ResolvedProfile. Unsupported bundle versions return an explicit compatibility error.
- ExportService exposes prepare(session, selected_candidate_id, new_profile_name) -> ExportDraft and write(draft, destination) -> ExportResult. prepare is read-only; write creates a new file and never installs it.

The concrete types may add fields needed by the implementation, but they must retain these semantics and must not make Dear PyGui types part of the domain API.

---

## Task 1: Add the Optional Dear PyGui Application Shell

**Files:**
- Modify: pyproject.toml
- Create: src/calibrate3dp/app/__init__.py
- Create: src/calibrate3dp/app/__main__.py
- Create: src/calibrate3dp/app/window.py
- Create: tests/test_app_shell.py

**Interfaces:**
- Provides main(argv: Sequence[str] | None = None) -> int.
- AppShell starts on Home, creates one primary viewport and root page layout, exposes the four top-level destinations, and receives service instances through its constructor. Keep route state independently testable without creating a viewport.
- Importing calibrate3dp or calibrate3dp.profiles must not import dearpygui.

- [x] Add optional dependency extra `gui` with Dear PyGui >=2.3,<3 and console entry point calibrate3dp = calibrate3dp.app.__main__:main.
- [x] Write tests test_app_shell_starts_on_home, test_navigation_switches_pages, and test_core_import_does_not_load_dearpygui.
- [x] Run route and callback tests without creating a native viewport; verify they pass and the core-import test passes in an environment without the gui extra installed.
- [x] Implement Dear PyGui context and viewport setup, AppShell, page builders, navigation callbacks, manual callback queue dispatch from the render loop, context cleanup, and clear startup errors when the gui extra is missing.
- [x] Run the GUI test file and full existing unittest suite.
- [x] Commit as feat: add optional desktop application shell.

## Task 2: Add Session Persistence and Resume

**Files:**
- Create: src/calibrate3dp/storage/__init__.py
- Create: src/calibrate3dp/storage/session_store.py
- Create: src/calibrate3dp/app/models.py
- Create: src/calibrate3dp/app/services/__init__.py
- Create: src/calibrate3dp/app/services/session_service.py
- Create: tests/test_session_store.py

**Interfaces:**
- SessionRepository and SessionService follow the interfaces above.
- SQLite stores the session index and versioned JSON payloads. Large G-code, 3MF, and image files stay in per-session folders; database values use relative paths.
- Session IDs are generated once and remain stable across every resume and refinement.

- [ ] Write tests test_session_round_trip_restores_profiles_plan_and_results, test_save_updates_timestamp_atomically, test_list_recent_is_ordered_and_limited, test_unknown_session_has_clear_error, and test_artifact_paths_must_stay_inside_session_root.
- [ ] Run the session-store test file and verify each test fails for the missing repository behavior.
- [ ] Implement schema version 1 migration and SessionRepository CRUD operations using sqlite3 transactions.
- [ ] Implement SessionService operations; serialize ExperimentPlan and ExperimentResults with their existing to_dict/from_dict APIs and validate results against the loaded plan.
- [ ] Store per-session profile JSON snapshots and generated artifacts outside SQLite; calculate and save source-profile hashes at selection time.
- [ ] Run the session-store tests plus the existing profile and experiment unit tests.
- [ ] Commit as feat: persist calibration sessions.

## Task 3: Build Orca Setup, Profile Import, and Profile Selection

**Files:**
- Create: src/calibrate3dp/app/services/profile_service.py
- Create: src/calibrate3dp/app/pages/setup_page.py
- Create: src/calibrate3dp/app/pages/profile_selection_page.py
- Create: src/calibrate3dp/app/widgets/profile_card.py
- Create: tests/test_profile_selection_ui.py

**Interfaces:**
- ProfileService wraps ProfileDocument.from_json_file, ProfileCatalog.add, ProfileCatalog.resolve, and the versioned Orca discovery/import adapters.
- Each selected preset is displayed with kind, name, scope, source path, source hash, inheritance chain, and per-setting provenance.
- The continue action emits a complete ProfileSelection only when all three profiles resolve and all module-required values are present.

- [ ] Write tests test_missing_orca_shows_browse_and_diagnostics_actions, test_malformed_json_shows_field_level_error, test_missing_parent_blocks_continue, test_ambiguous_name_requires_scope, and test_effective_values_show_provenance.
- [ ] Run the profile UI test file and verify the tests fail before implementation.
- [ ] Implement setup state for detected executable, detected config roots, Orca version, last check, compatibility status, browse actions, recheck, and offline diagnostics export.
- [ ] Implement JSON and supported bundle import without writing into Orca's config directory; make unsupported bundles display an actionable adapter message.
- [ ] Implement the three profile cards, inherited-value inspector, and a baseline summary that reads imported values rather than asking users to transcribe them.
- [ ] Run profile UI tests and existing profile resolver/export tests.
- [ ] Commit as feat: add Orca profile setup and selection.

## Task 4: Add Module Choice, Baseline Summary, and Experiment Review

**Files:**
- Create: src/calibrate3dp/app/services/experiment_service.py
- Create: src/calibrate3dp/app/pages/module_page.py
- Create: src/calibrate3dp/app/pages/experiment_review_page.py
- Create: src/calibrate3dp/app/widgets/candidate_table.py
- Create: tests/test_experiment_review_ui.py

**Interfaces:**
- ExperimentService exposes create_initial(module_id, profiles, options) -> ExperimentPlan and propose_refinement(plan, results, limits) -> ExperimentPlan.
- For Ironing Finish, create_initial calls create_initial_ironing_experiment with the imported effective process settings.
- The default first-stage ironing plan is a 3x3 grid centered on the imported baseline: flow values are 0.8, 1.0, and 1.2 times the imported ironing_flow; speed values are 2/3, 1.0, and 4/3 times imported ironing_speed. Preserve the profile value's numeric representation and round to its precision. For the README example baseline of flow 10 and speed 30, the exact matrix is flow 8/10/12 by speed 20/30/40.
- Clamp only to explicit limits supplied by the active Orca schema adapter. If a clamp creates duplicate values, if a required setting is absent, or if numeric precision cannot represent three distinct values, block generation and ask the user to edit bounds.
- The review view shows the full parameter matrix, fixed settings, candidate IDs, the exact generated plate map, assumptions, estimates if verified, and all warnings before Generate is enabled.

- [ ] Write tests test_ironing_defaults_use_imported_profile_and_match_documented_matrix, test_missing_baseline_blocks_plan, test_duplicate_after_schema_clamp_requires_edit, test_fixed_settings_are_visible, and test_unavailable_module_cannot_start.
- [ ] Run the experiment review test file and verify it fails before implementation.
- [ ] Implement deterministic baseline-relative sweep options and call the existing ironing planner; keep the 3x3 matrix editable before creating the final plan.
- [ ] Implement module cards for Ironing Finish, Bridge Quality, and Support Interface/Removal; disable cards until their experiment planners and geometry adapters are available.
- [ ] Implement the table model and review page; allow bound/step edits and parameter locking only where the module declares the parameter.
- [ ] Show a clear explanation of how each proposed range was calculated and what values are held fixed.
- [ ] Run UI review tests and existing ironing/experiment tests.
- [ ] Commit as feat: add calibration setup and experiment review.

## Task 5: Connect Preview, Slicing Progress, Cancellation, and Validation

**Files:**
- Create: src/calibrate3dp/app/services/generation_port.py
- Create: src/calibrate3dp/app/pages/generation_page.py
- Create: src/calibrate3dp/app/widgets/job_log_panel.py
- Create: tests/test_generation_ui.py

**Interfaces:**
- GenerationService is injected; this task implements the GUI adapter and state mapping, not another Orca CLI or geometry engine. No Orca worker may call Dear PyGui APIs; progress events are applied from the render loop.
- The real service must be supplied by the original plan's Phase 0/Phase 1 Orca adapter before real generation is enabled.
- The UI observes GenerationEvent values, shows logs as they arrive, and enables Results only after state=succeeded and validation state is valid.
- A canceled or failed job has no ready-to-print state. Partial outputs and logs remain attached to the session and are visibly labeled invalid.

- [ ] Write tests test_generation_page_disables_start_when_orca_unavailable, test_running_job_keeps_window_responsive, test_generation_ui_applies_worker_events_on_ui_loop, test_cancel_preserves_logs_and_marks_partial_output_invalid, test_failure_shows_recovery_action, and test_results_step_requires_successful_validation.
- [ ] Run the generation UI test file and verify it fails before implementation.
- [ ] Implement preview rendering from ExperimentPreview, including candidate map, plate count, and verified estimates.
- [ ] Implement the job progress view with phase text, indeterminate progress when needed, expandable stdout/stderr, cancel action, and validation summary.
- [ ] Implement event subscription/unsubscription and close behavior so no orphaned UI callback can update a closed page.
- [ ] Run UI tests and the original integration smoke test with the fake service.
- [ ] Commit as feat: add calibration job progress UI.

## Task 6: Implement Result Entry, Attachments, and Explicit Outcome States

**Files:**
- Modify: src/calibrate3dp/experiments.py
- Modify: src/calibrate3dp/storage/session_store.py
- Create: src/calibrate3dp/app/pages/results_page.py
- Create: src/calibrate3dp/app/widgets/assessment_editor.py
- Create: tests/test_result_contract.py
- Create: tests/test_results_ui.py

**Interfaces:**
- CandidateAssessment retains candidate_id, ratings, defect_tags, and notes; add an optional verdict with values pass, fail, uncertain, or missing, plus zero or more session-relative photo paths.
- ExperimentResults retains selected_candidate_id and accepted; add tied_candidate_ids while retaining the existing single selected candidate for adaptive refinement.
- New results serialize as schema version 2. from_dict continues to read existing version 1 payloads with verdict=None, no photographs, and no tied candidates.
- Missing means the physical specimen was unavailable; uncertain means it was present but could not be judged. Neither is converted to a score.
- Photos are copied into the session evidence directory before their relative paths are saved.

- [ ] Write tests test_version_1_results_still_load, test_version_2_round_trip_preserves_verdict_tie_and_photos, test_missing_specimen_cannot_have_numeric_ratings, test_tie_candidates_must_belong_to_plan, and test_photo_paths_cannot_escape_session_root.
- [ ] Run the result-contract test file and verify it fails before implementation.
- [ ] Extend CandidateAssessment and ExperimentResults validation/serialization compatibly; update existing tests for version 2 writes and version 1 reads.
- [ ] Implement per-candidate editors for 1–5 ratings, pass/fail/uncertain/missing, defect tags, note, and local photograph attachments.
- [ ] Persist each edit with a 300 ms debounce for text entry and immediately after rating, verdict, or attachment changes; show Saved, Saving, or Save Failed with retry.
- [ ] Implement explicit winner and tie selection; incomplete or uncertain assessments remain visible and cannot silently pass the acceptance gate.
- [ ] Run result-contract, results UI, session-store, and full existing unit tests.
- [ ] Commit as feat: add resumable print result entry.

## Task 7: Add Explainable Refinement and Final Confirmation

**Files:**
- Create: src/calibrate3dp/app/pages/recommendation_page.py
- Create: src/calibrate3dp/app/services/acceptance_service.py
- Create: tests/test_recommendation_ui.py

**Interfaces:**
- AcceptanceService evaluates the selected module's acceptance policy and returns an explicit decision plus reasons; it does not write profile files.
- For ironing, refinement calls propose_ironing_refinement(plan, results, next_plan_id, limits).
- A tied result requires the user to choose a tie-break candidate or explicitly start a confirmation comparison. The system never averages a tie into a new setting.
- The final result cannot be accepted until the required confirmation run has succeeded and passed validation, except when the user selects a documented opt-out and the session report records it.

- [ ] Write tests test_boundary_winner_explains_extension, test_interior_winner_explains_narrowing, test_tie_requires_explicit_resolution, test_acceptance_waits_for_confirmation_run, and test_opt_out_is_recorded.
- [ ] Run the recommendation UI test file and verify it fails before implementation.
- [ ] Implement recommendation cards showing previous/current ranges, selected candidate, next range, changed dimensions, fixed values, limits, and exact rationale.
- [ ] Implement explicit Accept, Refine, Extend Boundary, and Mark Inconclusive actions; hide actions that the current module policy cannot support.
- [ ] Implement confirmation-run state and ensure failed/canceled confirmation jobs do not unlock profile export.
- [ ] Run recommendation, ironing, results, and session-resume tests.
- [ ] Commit as feat: add explainable calibration refinement.

## Task 8: Add Reviewed Profile Export

**Files:**
- Create: src/calibrate3dp/app/pages/export_page.py
- Create: src/calibrate3dp/app/services/export_service.py
- Create: tests/test_export_ui.py

**Interfaces:**
- ExportService builds a patch only from module-declared calibrated settings and the accepted candidate; it uses clone_profile_with_patch on the source process ProfileDocument.
- ExportDraft contains source profile name/hash, new profile name, changed setting keys, old/new values, supporting run and candidate IDs, compatibility warnings, and report paths.
- ExportResult contains the new profile path, machine-readable session manifest, human-readable Markdown report, and hashes. Writing the draft must not mutate the source profile or Orca config directory.

- [ ] Write tests test_diff_contains_only_accepted_ironing_keys, test_original_profile_bytes_remain_unchanged, test_export_requires_accepted_confirmation_result, test_destination_is_user_selected, and test_manifest_and_report_include_evidence_scope.
- [ ] Run the export UI test file and verify it fails before implementation.
- [ ] Implement a review page with source and destination names, exact old/new values, source hash, evidence link, known trade-offs, confirmation status, and a browsable destination.
- [ ] Implement JSON preset and Markdown report export; enable import-bundle export only for an adapter version already verified by the Orca integration tests.
- [ ] Implement exact import instructions for the detected Orca version; do not auto-import or activate the exported preset.
- [ ] Run export UI, profile export, and full unit tests.
- [ ] Commit as feat: add reviewed profile export workflow.

## Task 9: Complete Home, Session Management, Accessibility, and Acceptance

**Files:**
- Create: src/calibrate3dp/app/pages/home_page.py
- Create: src/calibrate3dp/app/pages/sessions_page.py
- Create: src/calibrate3dp/app/pages/settings_page.py
- Create: tests/test_home_sessions_ui.py
- Modify: docs/IMPLEMENTATION_STATUS.md
- Modify: README.md

**Interfaces:**
- Home lists recent sessions and their real states: setup, ready to print, awaiting results, refinement ready, confirmation required, export ready, or completed.
- Sessions can be opened and resumed; archival hides a session from Home but never deletes its files.
- Settings contain only application-level choices in v1: workspace root, default export root, diagnostics inclusion choices, and Orca executable/config-root overrides.

- [ ] Write tests test_home_status_matches_persisted_session, test_resume_opens_saved_step, test_archive_preserves_artifacts, and test_settings_survive_restart.
- [ ] Run the Home/session UI test file and verify it fails before implementation.
- [ ] Implement recent sessions, resume, archive, settings, and clean recovery for a missing/moved artifact.
- [ ] Add keyboard traversal, visible focus, accessible names, scaling checks at 100/150/200 percent, and Windows/Linux file-dialog checks.
- [ ] Run PYTHONPATH=src python -m unittest discover -s tests -v and confirm the headless suite also passes without the gui extra installed.
- [ ] Run the acceptance workflow with an actual supported Orca version: import three profiles, generate and validate a 3x3 ironing run, resume after closing, record results, complete refinement and confirmation, and export a new process profile.
- [ ] Update README.md with installation, launch, setup, saved-session location, and recovery steps; update docs/IMPLEMENTATION_STATUS.md with completed and blocked gates.
- [ ] Commit as feat: complete calibration workbench GUI.

## Acceptance Gate

The GUI is ready for review when all of the following are demonstrated on Windows and Linux:

- A new user can select or import Orca printer, filament, and process profiles without transcribing their settings.
- Effective values and inheritance provenance are visible, and unresolved required values block test generation.
- The first ironing grid is generated from the imported baseline and displays the exact candidate-to-pad map before slicing.
- Orca runs in the background with logs, cancellation, output validation, and no source-profile mutation.
- The user can close and resume every workflow stage, enter ratings/defects/verdicts/photos, and resolve ties explicitly.
- The next range is explained from recorded evidence; final export waits for an accepted confirmation result or a recorded opt-out.
- The export contains only accepted module settings, a readable diff, a reproducibility manifest, and a report.
- Core imports and headless tests still work without Dear PyGui installed.

## References

- Repository implementation plan: IMPLEMENTATION_PLAN.md.
- Current implementation status: docs/IMPLEMENTATION_STATUS.md.
- Dear PyGui documentation: https://dearpygui.readthedocs.io/en/latest/.
- Dear PyGui callback queue and manual callback management: https://dearpygui.readthedocs.io/en/latest/documentation/item-callbacks.html.
- Dear PyGui tables and themes: https://dearpygui.readthedocs.io/en/latest/documentation/tables.html and https://dearpygui.readthedocs.io/en/latest/documentation/themes.html.
- Dear PyGui 2.3.1 Python wheels and platform tags: https://pypi.org/project/dearpygui/.
