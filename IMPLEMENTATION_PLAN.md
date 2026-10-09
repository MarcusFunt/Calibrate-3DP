# Calibrate-3DP V1 Implementation Plan

> For coding agents: use superpowers:executing-plans and complete tasks in order. Keep each task independently reviewable. Update its checkbox and append to docs/PROGRESS_HISTORY.md when work is complete.

**Goal:** Deliver the manual, local-first V1 defined in docs/V1_GOAL.md.

**Architecture:** Keep profile resolution, dependency evaluation, experiment compilation, plate construction, and assessment rules in a headless Python core. PySide6 is a presentation layer over typed services. A version-aware Orca adapter creates final G-code, while SQLite stores versioned records and immutable artifact folders retain evidence.

**Tech Stack:** Python >=3.11; PySide6/Qt for the desktop app; SQLite through the standard library; OrcaSlicer CLI as an external slicing engine; standard library testing unless a new dependency has a demonstrated need.

**Spec:** docs/V1_GOAL.md

## Global constraints

- V1 is manual: no camera workflow, automatic measurements, computer vision, automatic scoring, cloud service, printer control, or firmware writes.
- OrcaSlicer remains the only supported final G-code slicer in V1.
- Never modify source Orca presets. Import read-only and export new, reviewed profile files.
- Preserve unknown profile keys, inheritance provenance, source fingerprints, exact experiment settings, and artifact hashes.
- Every physical plate receives a unique six-character code. Every sample receives a compact printed label.
- A test cannot generate printable output unless the adapter can validate the planned setting changes in the resulting G-code.
- Record the actual Orca executable and version for every integration run. Claim support only for tested matrix entries.
- Keep calibration/domain logic independent of PySide6 and Orca process management.
- Persist versioned schemas and write explicit SQLite migrations. Existing sessions must remain readable or have an explicit migration path.
- Append to docs/PROGRESS_HISTORY.md for each meaningful task, experiment, test, assumption change, or deviation.

## Review focus

- A selected profile is inherited, ambiguous, malformed, or missing a required setting; show provenance and block only actions that need that value.
- Orca accepts a project but ignores an object override; block generation unless G-code checks prove the override was applied.
- A profile, nozzle, material, firmware context, or slicer capability changes; mark only dependent calibration results stale and explain why.
- A plate exceeds the usable bed, crosses a keep-out, or has an invalid connector; prevent slicing and preserve the rejected layout and reason.
- A run fails, times out, or is canceled; keep the UI responsive, retain logs, and mark partial artifacts invalid.
- A code is mistyped, duplicated, or looked up after a schema upgrade; resolve unambiguously or return an actionable error without guessing.
- An assessment omits a sample or is uncertain; preserve that uncertainty and prevent silent acceptance or export.

## Existing foundation

At origin/main commit 5ef7c4cf04a9b5da84bb570c9276cca98d1427d9, the repository already has profile import and inheritance resolution, provenance, candidate JSON generation, deterministic plan and result objects, an ironing planner, one STL per ironing candidate, isolated Orca CLI jobs, manifests and hashes, a SQLite session index, manual result/export screens, and a Dear PyGui shell.

The implementation has one active experiment planner: ironing flow × speed. Coupons are identical 30 × 30 × 4 mm boxes generated as separate STLs. Candidate plates are sliced independently. The application does not yet have a generic calibration catalog, dependency graph, sample-per-plate compiler, plate code, full V1 module set, printer history model, or PySide6 UI. docs/IMPLEMENTATION_STATUS.md is the current inventory.

---

## Phase 0 — Resolve the slicing and plate risk

### Task 0B: Evaluate the experiment configuration and geometry proposal

**Status:** Design decision pending. The user requested an evaluation and explicitly identified this as a suggestion. Keep implementation choices open until the prototype evidence and a later decision.

**Files:**
- Read/update: docs/EXPERIMENT_CONFIGURATION_PROPOSAL.md
- Update after evidence: docs/V1_GOAL.md, docs/REUSE_RESEARCH.md, and docs/PROGRESS_HISTORY.md
- Prototype location: tools/geometry_prototype/ or an isolated test package (choose after checking the repository layout)

**Interfaces to evaluate:**
- ExperimentConfig: typed, versioned request created from GUI values; no executable CAD code.
- CompiledExperiment: immutable expanded candidates, settings, dependency/profile snapshots, plate/sample mapping, seed, and generator versions.
- GeometryBackend: Python adapter that builds a layout from a compiled experiment.
- OrcaAdapter: separate project/settings application, slicing, and G-code validation boundary.

- [ ] Decide whether canonical configuration is a versioned JSON snapshot stored in SQLite, with relational indexes for lookup, and optional JSON import/export. Document how existing records migrate.
- [ ] Decide whether large mesh/G-code artifacts remain in an app-managed immutable artifact store referenced by hash, rather than SQLite BLOBs. Preserve complete structured plate configuration and sample maps in the database either way.
- [ ] Prototype a connected 3 × 3 grid with robust hand-tool-separable links, A–I labels, and a six-character plate code in Python. Prototype build123d first; compare CadQuery if installation, geometry, or packaging evidence calls for it.
- [ ] Verify valid geometry, exact bounds, sample separation, readable labels, connector dimensions, and repeat-generation behavior on the proposed platform matrix.
- [ ] Import the generated project into supported Orca versions, assign distinct settings to at least three samples, slice, and prove the requested changes reached their intended toolpaths. Include a negative case.
- [ ] Evaluate standard 3MF packaging support separately from Orca project metadata; add lib3mf only if a reproducible packaging prototype justifies its platform/dependency cost.
- [ ] Record evidence, decision, alternatives, and user impact in docs/PROGRESS_HISTORY.md; update this plan and goal only after the architecture choice is accepted.

**Verification:** The prototype emits the same normalized sample order, settings, layout, and expected geometry for identical versioned inputs; the Orca integration check demonstrates or blocks independent sample settings in G-code; packaging installs and runs on each claimed platform. A documentation review or mock test alone does not close this gate.


### Task 0: Validate a multi-sample Orca project

**Files:**
- Create or update: tests/integration/test_orca_plate_overrides.py
- Create or update: tests/fixtures/orca/plate_override_reference.3mf and expected G-code checks
- Update: docs/IMPLEMENTATION_STATUS.md and docs/PROGRESS_HISTORY.md

**Interfaces:**
- Consumes: current ProfileCatalog, OrcaProfileAdapter, OrcaCli, and installed Orca profiles.
- Produces: a recorded capability result by Orca version and profile family.

- [ ] Build a project with at least three adjacent sample objects using different ironing settings.
- [ ] Slice with the exact installed Orca binary, isolated data directory, and resolved printer/filament/process profiles.
- [ ] Parse the output and prove each object's requested settings affect its toolpath. Include a negative fixture where a setting is intentionally absent.
- [ ] Check start/end code, temperature commands, bed bounds, output hashes, and version identity.
- [ ] Repeat on every proposed support-matrix entry and record raw commands, logs, and artifact hashes.
- [ ] If overrides do not work, stop the single-plate implementation and document a version-specific safe alternative. Do not silently mark one-candidate-per-plate output as the requested grouped plate workflow.

**Verification:** The real-Orca integration test passes for every claimed matrix entry. Mocked tests remain the normal unit-test path.

---

## Phase 1 — Data and Qt foundation

### Task 1: Add versioned printer, material, calibration, plate, and artifact records

**Files:**
- Create: src/calibrate3dp/domain/records.py
- Create: src/calibrate3dp/storage/database.py
- Create: src/calibrate3dp/storage/migrations.py
- Create: src/calibrate3dp/storage/repositories.py
- Modify: src/calibrate3dp/storage/session_store.py
- Test: tests/test_database_migrations.py and tests/test_domain_records.py

**Interfaces:**
- PrinterRecord identifies a saved machine and its profile snapshots.
- MaterialRecord identifies a filament/material profile and context such as nozzle and toolhead.
- CalibrationRecord stores module/version, applicability context, status, accepted values, and the input fingerprint.
- ExperimentRecord, PlateRecord, SampleRecord, ArtifactRecord, and AssessmentRecord store immutable lineage and references.
- Database.migrate() upgrades schema transactionally; repositories expose typed create/get/list/update operations.

- [ ] Write migration tests from the existing session schema, including preserved session IDs and payloads.
- [ ] Implement typed records with validation and versioned JSON serialization.
- [ ] Add foreign keys, uniqueness constraints for plate codes, transaction boundaries, and explicit migration versions.
- [ ] Keep large files in session/run artifact folders; store relative paths, media types, sizes, and SHA-256 values in SQLite.
- [ ] Test interrupted migration rollback, duplicate identifiers, missing artifact files, and recovery messages.
- [ ] Run the full current headless suite and the new storage tests.

### Task 2: Establish the PySide6 application shell early

**Files:**
- Create: src/calibrate3dp/app/qt/main.py
- Create: src/calibrate3dp/app/qt/main_window.py
- Create: src/calibrate3dp/app/qt/navigation.py
- Create: tests/test_qt_app_shell.py
- Modify: pyproject.toml and src/calibrate3dp/app/__main__.py

**Interfaces:**
- create_application(argv) returns QApplication.
- MainWindow receives typed service interfaces; widgets do not open SQLite or invoke Orca directly.
- Navigation pages: Printer Library, Printer Workspace, New Calibration, Runs/History, and Settings.

- [ ] Add PySide6 to the optional GUI dependency group and retain headless imports without PySide6.
- [ ] Implement a clean Qt shell with application startup, navigation, empty/loading/error states, and keyboard focus visibility.
- [ ] Add an offscreen Qt smoke test for application startup and page navigation.
- [ ] Show saved printer entries from a fake repository in a view-model test.
- [ ] Keep the existing Dear PyGui entry point only as a temporary migration path; remove it after Qt reaches the V1 feature gate.
- [ ] Verify that a headless import and all non-GUI tests pass when the GUI extra is not installed.

### Task 3: Make profile intake a saved printer/material library

**Files:**
- Modify: src/calibrate3dp/app/services/profile_service.py
- Modify: src/calibrate3dp/profiles.py and src/calibrate3dp/orca_profiles.py
- Create: src/calibrate3dp/app/services/library_service.py
- Test: tests/test_profile_library.py and existing profile adapter tests

**Interfaces:**
- LibraryService.import_printer(source) returns PrinterRecord.
- LibraryService.import_material(source, printer_id, nozzle_context) returns MaterialRecord.
- LibraryService.resolve_selection(printer_id, material_id, process_profile_id) returns ProfileSelection.
- Every import is read-only and preserves source path, raw document, effective values, provenance, and content hash.

- [ ] Import single Orca JSON presets and supported ZIP bundles into app-managed records.
- [ ] Support saved printers from earlier app sessions and explicit profile re-import/versioning.
- [ ] Show inheritance, compatibility, missing values, and unknown settings in the profile review.
- [ ] Test ambiguous names, parent cycles, deleted source files, renamed files, and changed profile hashes.
- [ ] Confirm no service writes to Orca's profile directories.

---

## Phase 2 — Dependency graph and compiler

### Task 4: Implement contextual dependencies and stale-result propagation

**Files:**
- Create: src/calibrate3dp/calibration/dependencies.py
- Create: src/calibrate3dp/calibration/state.py
- Create: tests/test_calibration_dependencies.py

**Interfaces:**
- DependencyRule names an input/result, scope, predicate, severity, and invalidation keys.
- CalibrationStatus values are untested, blocked, ready, in_progress, needs_review, accepted, and stale.
- DependencyEvaluator.evaluate(context, records) returns a tuple of CalibrationState.
- DependencyEvaluator.affected_by_change(changed_keys, graph) returns affected calibration IDs.

- [ ] Model requirements as typed rules, not hard-coded UI ordering.
- [ ] Record exact profile values and prior results assumed by each run.
- [ ] Implement explicit prerequisites, recommendations, optional overrides with a required reason, and transitive invalidation.
- [ ] Test that changing nozzle size invalidates the relevant records, while changing an unrelated setting leaves records current.
- [ ] Test cycles, unknown prerequisites, missing context, and an explicit user override.
- [ ] Use Orca and firmware guidance as initial defaults; version and qualify each rule by context.

### Task 5: Define the in-process calibration module contract

**Files:**
- Create: src/calibrate3dp/calibration/definitions.py
- Create: src/calibrate3dp/calibration/registry.py
- Create: src/calibrate3dp/calibration/settings.py
- Create: tests/test_calibration_registry.py and tests/test_setting_specs.py

**Interfaces:**
- SettingSpec contains canonical key, profile role, value type, unit, supported choices/range, and slicer-version capability.
- CalibrationDefinition contains module_id, definition_version, applicability scope, dependency rules, factor definitions, plan builder, specimen builder, assessment schema, refinement policy, and export mapping.
- CalibrationRegistry.register(definition) rejects duplicate IDs/versions and invalid references.
- V1 definitions are first-party Python objects; do not load arbitrary executable plugins from user directories.

- [ ] Separate test definitions from UI labels and Orca CLI-specific spelling.
- [ ] Validate units, ranges, factor interactions, prerequisites, and target profile roles before compiling.
- [ ] Preserve versioned definitions so an old run remains interpretable.
- [ ] Test duplicate registration, missing setting mappings, invalid units, unsupported slicer capability, and definition upgrades.

### Task 6: Compile requests into an immutable experiment representation

**Files:**
- Create: src/calibrate3dp/calibration/compiler.py
- Create: src/calibrate3dp/calibration/compiled.py
- Create: tests/test_calibration_compiler.py

**Interfaces:**
- CompilerRequest includes printer/material/process IDs, module_id, user options, layout constraints, and explicit dependency overrides.
- CompiledExperiment includes definition version, baseline snapshot/hashes, dependency snapshot, fixed settings, factors, candidates, sample IDs, plate plans, validation requirements, and a human-readable rationale.
- ExperimentCompiler.compile(request, repository_snapshot) returns CompiledExperiment.
- Compilation is pure: it does not write files, invoke Orca, or mutate profiles.

- [ ] Compile an ironing 3 × 3 request and verify deterministic ordering, exact baseline preservation, and sample mapping.
- [ ] Reject incompatible profiles, missing required settings, invalid ranges, excessive candidates, and unresolved dependencies.
- [ ] Record held-constant parameters and intentional interactions for every candidate.
- [ ] Ensure the same request, baseline, definition version, and seed produce the same plan hash.

---

## Phase 3 — Plate generation and Orca adapter

### Task 7: Build a plate and sample layout compiler

**Files:**
- Create: src/calibrate3dp/geometry/layout.py
- Create: src/calibrate3dp/geometry/specimens.py
- Create: src/calibrate3dp/geometry/three_mf.py
- Create: tests/test_plate_layout.py and tests/test_three_mf_project.py

**Interfaces:**
- PlateLayoutRequest contains bed polygon/keep-outs, margins, sample dimensions, connector dimensions, and layout strategy.
- PlateLayout contains plate code, coordinate transforms, labeled sample placements, connector geometry, and bounds.
- PlateCompiler.layout(compiled_experiment, printer_geometry) returns one or more PlateLayout values.
- PlateCompiler.write(layout, output_dir) returns generated artifact records.

- [ ] Generate unique six-character codes using an alphabet without visually ambiguous characters; enforce a database uniqueness check and collision retry.
- [ ] Assign short labels deterministically, starting A through I for nine samples; store label-to-candidate mapping.
- [ ] Generate a tiny physical code coupon and ensure the code is readable after slicing.
- [ ] Support grouped flat grids/zones and a distinct tower strategy through the backend selected at Task 0B. Preserve grouping when the experiment must span multiple physical plates. Preserve grouping when the experiment must span multiple physical plates.
- [ ] Design connectors to survive accidental plate removal and handling while allowing deliberate separation with a hand tool.
- [ ] Validate bed bounds, keep-outs, clearances, collision-free samples, minimum connector geometry, mesh validity, and deterministic output. Record geometry/backend versions and hashes.
- [ ] Test geometry properties at minimum/maximum bed size, irregular beds, narrow margins, and full plate capacity.
- [ ] Do not depend on a general-purpose 3MF library for Orca metadata; validate the exact package contents and adapter extension separately.

### Task 8: Implement the versioned Orca project and slicing adapter

**Files:**
- Modify: src/calibrate3dp/orca_cli.py and src/calibrate3dp/orca_jobs.py
- Create: src/calibrate3dp/orca_adapter.py
- Create: tests/test_orca_adapter.py and tests/integration/test_orca_plate_overrides.py

**Interfaces:**
- OrcaCapabilities contains binary identity, version, CLI options, project override support, and supported operations.
- OrcaAdapter.compile_job(compiled_plate, profile_snapshot) returns OrcaJob.
- OrcaAdapter.slice(job, progress, cancel) returns SliceResult.
- OrcaAdapter.validate_output(job, result) returns ValidationReport.

- [ ] Keep argument-vector invocation, isolated data/output directories, timeout, cancellation, streaming logs, and partial-output retention.
- [ ] Create resolved profile inputs without changing the user's Orca configuration.
- [ ] Add version capability checks for object overrides and layer-change temperature events.
- [ ] Parse G-code and compare expected setting comments/commands to the compiled per-sample plan.
- [ ] Check start/end sequences, temperatures, work envelope, required toolpath markers, empty output, and CLI exit status.
- [ ] Refuse to label output print-ready if any check is unknown, failed, or unsupported.
- [ ] Store the exact executable identity, CLI arguments, logs, effective profiles, settings hashes, and output hashes in the run manifest.

---

## Phase 4 — V1 calibration definitions

### Task 9: Implement temperature and maximum volumetric speed

**Files:**
- Create: src/calibrate3dp/calibration/modules/temperature.py
- Create: src/calibrate3dp/calibration/modules/volumetric_speed.py
- Create: tests/test_temperature_definition.py and tests/test_volumetric_speed_definition.py

- [ ] Generate a temperature tower or validated temperature-zone plate with a deterministic value-to-height/zone map.
- [ ] Validate the G-code temperature command at each layer/zone against the map.
- [ ] Generate maximum volumetric speed candidates with fixed geometry and explicit speed/flow context.
- [ ] Record manual rubric criteria, selected range, and accepted material-profile patch.
- [ ] Test candidate bounds, unsupported material ranges, and G-code mapping errors.

### Task 10: Implement flow ratio and pressure advance

**Files:**
- Create: src/calibrate3dp/calibration/modules/flow.py
- Create: src/calibrate3dp/calibration/modules/pressure_advance.py
- Create: tests/test_flow_definition.py and tests/test_pressure_advance_definition.py

- [ ] Define flow ratio candidates and geometry with explicit reference flow, wall/top criteria, and profile role.
- [ ] Define PA methods and supported firmware/profile parameter mappings without automatic scoring.
- [ ] Store PA test speed, layer height, line width, temperature, nozzle, extruder, flow, and acceleration as experiment context.
- [ ] Add contextual dependencies and stale-result rules rather than assuming one universal order.
- [ ] Validate every candidate's actual G-code commands or metadata before results can be entered.
- [ ] Test profile patch role, value units, candidate labeling, and context changes.

### Task 11: Implement retraction, bridge, and support calibration

**Files:**
- Create: src/calibrate3dp/calibration/modules/retraction.py
- Create: src/calibrate3dp/calibration/modules/bridges.py
- Create: src/calibrate3dp/calibration/modules/supports.py
- Create: tests/test_retraction_definition.py, tests/test_bridge_definition.py, and tests/test_support_definition.py

- [ ] Give each module a dedicated geometry recipe, factors, held-constant settings, specimen labels, and manual defect rubric.
- [ ] Cover bridge flow/speed/cooling factors in staged experiments with explicit interaction context.
- [ ] Cover support interface and removal factors with a manual release/damage rubric.
- [ ] Verify required profile settings against the Orca version adapter.
- [ ] Test unsupported settings, geometry limits, incomplete sample results, and export target mapping.

### Task 12: Implement complete staged ironing calibration

**Files:**
- Create: src/calibrate3dp/calibration/modules/ironing.py
- Modify: src/calibrate3dp/ironing.py and current ironing tests
- Create: tests/test_ironing_definition.py

- [ ] Include type, pattern, flow, spacing, inset, offset angle, fixed-angle behavior, and speed in the setting catalog.
- [ ] Implement staged designs that allow one or more declared factor interactions without generating an uncontrolled full-factorial plate.
- [ ] Declare the required top-surface geometry and fixed process inputs.
- [ ] Use a manual finish rubric for coverage, ridges, gloss, edge behavior, and defects.
- [ ] Make a follow-up plan that narrows or extends only factors with supporting manual results.
- [ ] Test all setting combinations supported by the adapter and reject unsupported values before geometry generation.

---

## Phase 5 — History, UI completion, and release

### Task 13: Persist manual assessment, dependency status, and profile export

**Files:**
- Create: src/calibrate3dp/app/services/assessment_service.py
- Modify: src/calibrate3dp/app/services/acceptance_service.py and export_service.py
- Modify: storage repositories and Qt pages
- Create: tests/test_assessment_service.py and tests/test_profile_export.py

- [ ] Save each sample's manual assessment and uncertainty state incrementally.
- [ ] Show evidence, assumptions, candidate-to-sample map, and dependency state in run detail.
- [ ] Permit accept, reject, uncertain, tie, and missing-sample outcomes with explicit reasons.
- [ ] Create follow-up experiments from accepted/rejected manual results without mutating the original plan.
- [ ] Export a new role-correct Orca profile with a reviewed diff, evidence links, compatibility report, and no source-profile writes.
- [ ] Test reopening and resuming at each saved workflow stage and code lookup after app restart.

### Task 14: Implement the printer workspace and calibration graph

**Files:**
- Create or complete: Qt Printer Library, Printer Workspace, Calibration Graph, plate-code lookup, and run-detail pages.
- Test: tests/test_qt_printer_workspace.py, tests/test_qt_calibration_graph.py, and tests/test_qt_code_lookup.py

- [ ] Let the user select a saved printer, then inspect its history, materials, calibration statuses, and prerequisites.
- [ ] Render the graph from DependencyEvaluator output. The UI does not calculate prerequisite logic itself.
- [ ] Allow lookup by six-character plate code and show all samples, exact settings, artifacts, validation, and manual results.
- [ ] Keep the run generation service cancellable and responsive.
- [ ] Test status transitions and graph rendering with fake repositories and fake slicer services.

### Task 15: Complete acceptance matrix and release evidence

**Files:**
- Update: docs/IMPLEMENTATION_STATUS.md, README.md, docs/PROGRESS_HISTORY.md, support matrix, and packaging configuration.
- Test: full headless suite, offscreen Qt suite, and opt-in Orca integration suite.

- [ ] Run the full test suite in a clean environment with and without the GUI extra.
- [ ] Run the complete import → plan → compile → plate → slice → validate → manual assess → refine → export flow on each supported OS/Orca/profile combination.
- [ ] Physically print representative plates for each layout family. Record connector revision, material, handling result, and label/code readability.
- [ ] Verify source profiles remain unchanged and output exports can be imported into each supported Orca version.
- [ ] Verify accessibility basics, keyboard navigation, display scaling, native file dialogs, backup, and recovery.
- [ ] Record exact test commands, outcomes, logs, artifact hashes, assumptions, and deviations in docs/PROGRESS_HISTORY.md.
- [ ] Mark V1 complete only when every acceptance criterion in docs/V1_GOAL.md has evidence.

## Suggested verification commands

Run these after the corresponding tasks; adapt only when the test runner or package scripts change and record the change.

    python -m unittest discover -s tests -v
    QT_QPA_PLATFORM=offscreen python -m unittest tests.test_qt_app_shell -v
    python -m unittest tests.integration.test_orca_plate_overrides -v

Real-Orca tests are opt-in and require a supported Orca executable and profile root. A skipped integration test is not evidence that an Orca version is supported.

## Plan change control

Update this plan when evidence changes the design. Do not silently delete a requirement. Record the changed decision, evidence, user impact, and affected task IDs in docs/PROGRESS_HISTORY.md; then update docs/V1_GOAL.md or this file as needed. Keep completed historical checkboxes and experiment entries intact.
