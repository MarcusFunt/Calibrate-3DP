# Calibrate-3DP — Next Implementation Pass

**Plan ID:** `C3DP-NEXT-2026-10-10`  
**Prepared:** 2026-10-10  
**Repository:** https://github.com/MarcusFunt/Calibrate-3DP  
**Source of truth:** `origin/main` at `5570dafabb44c9adc77089d2053e194600fc7850` (`feat: validate connected grouped plate output`)  
**Plan status:** Proposed; **no code changes or commits are implied by this document.**  
**Suggested working branch:** `feat/qt-ironing-assess-refine-export` created from a freshly fetched `origin/main`

> **Instruction for the implementing agent:** Re-fetch and inspect `origin/main` before changing code. If HEAD has moved, compare the new commits with this plan and record necessary changes. Work on the explicitly authorized machine/checkout; do not assume a particular desktop is available. Keep changes in small, independently reviewed commits/PRs; do not force-push or overwrite existing worktrees. Treat `docs/V1_GOAL.md`, `IMPLEMENTATION_PLAN.md`, `AGENTS.md`, and `docs/PROGRESS_HISTORY.md` as governing project documents.

## 1. Executive decision

**Next milestone: make the existing nine-sample *ironing flow × speed* experiment usable from configuration review through manually entered results, explainable refinement/confirmation, and reviewed Orca process-profile export — in the Qt application.**

The critical path is no longer inventing the grouped plate: the latest `main` includes connected labeled meshes, ten-object 3MF packaging, saved run records, an asynchronous Qt→Orca workflow, and a real-Orca check that the nine sample overrides reach the intended toolpaths without rearranging the connected layout. The missing user-facing half is **assess → decide → refine/confirm → export**.

This pass must also make the existing **not-print-ready** status understandable and strengthen automated preflight checks. **Do not declare any physical plate or printer/profile combination print-ready without the required safety, human inspection, and physical acceptance evidence.** A complete *software* walkthrough may use clearly labeled synthetic manual results; it is not evidence that a physical print occurred.

### Pass priorities

| Tier | Outcome | Required for this pass? |
|---|---|---|
| P0 | Baseline/docs correction, frozen editable experiment draft, preview, and safe run linkage | Yes |
| P0 | Persistent per-sample manual assessment in the **current Qt workflow** | Yes |
| P0 | Explainable recommendation, new linked refinement/confirmation run, and reviewed standalone Orca JSON export | Yes, within existing ironing scope |
| P0 | Reopen by plate code after app restart; preserve old evidence and protect original Orca profiles | Yes |
| P1 | Better G-code print-readiness preflight and explicitly recorded remaining blockers | Yes as a bounded validator/report; no unsupported blanket safety claim |
| P1 | Visual sliced-label/connector inspection workflow and physical trial protocol | Inspection workflow yes; *physical acceptance is a human/external gate* |
| P2 | Full G-code dialect support, cross-platform Orca support matrix, generic calibration compiler, other calibration modules, cameras/printer control | No; subsequent milestones |

### Non-goals

No new calibration families; no comprehensive dependency graph; no camera scoring or automation; no automatic printer upload; no firmware modification; no implicit source-profile edits; no new OpenCascade/build123d/CadQuery dependency; no return to Dear PyGui; no claim of general Orca/OS/printer support from one Windows integration sample. Do not spend this pass redesigning every screen or building a full 3D renderer.

## 2. Verified baseline and specific gaps

| Area | Present on reviewed `main` | Gap to close |
|---|---|---|
| Headless experiment logic | `src/calibrate3dp/experiments.py`; `app/services/experiment_service.py` | `create_initial()` accepts custom sweep options, but Qt's grouped generation does not expose a reviewed immutable configuration. `ExperimentService.review()` still describes **separate** plates; this is outdated for the verified grouped path. |
| Geometry + slicing | `geometry/layout.py`, `geometry/specimens.py`, `grouped_plate.py`, `app/services/grouped_orca_service.py`, `orca_cli.py` | Connected geometry and grouped settings/layout validated in the tested Orca setup; full movement/temperature/start/end checks, physical mark readability, and connector trial remain open. |
| Persistence | `domain/records.py`, `storage/library_store.py`, `storage/session_store.py` | Current `CalibrationRunRecord` stores immutable generation inputs and final generation status, but no linked new-generation assessment/revision/export records. `SessionSnapshot` supports older results/export separately. |
| Existing assessment/refinement/export machinery | `CandidateAssessment`, `ExperimentResults`, `AcceptanceService`, `ExportService`, and transitional `app/pages/*` | Reuse **headless domain/service rules**, not Dear PyGui widgets. Bridge carefully to the current saved-run model and Qt flow. |
| Qt | `app/qt/main_window.py`, `workflow_widgets.py`, `navigation.py` | Printer library/workspace/history/settings exist. New Calibration currently redirects to printer selection; History presents textual lookup, not editable experiment assessment. |
| Test evidence | 2026-10-10 recorded full suite: **184 run, 181 passed, 3 skipped**; opt-in real Orca gate passed separately | Add durable round-trip tests and meaningful failure cases; do not count skipped integration checks as passes. |
| Documentation | `README.md`, `docs/IMPLEMENTATION_STATUS.md`, `IMPLEMENTATION_PLAN.md`, `docs/PROGRESS_HISTORY.md` | Status/plan still claim work was uncommitted with `main` at `4a16ccf`; current remote HEAD is `5570daf`. Update *current* snapshot while preserving append-only historical evidence. |

**Existing risk to preserve:** the previous Orca result was falsely interpreted as a connected layout because Orca auto-arranged the objects. Never remove `--arrange 0 --orient 0`, the named-object shared-translation check, or its negative regression. A passing per-sample setting test alone is insufficient.

## 3. Target user journey

1. Choose a saved printer and matching material in **Printer Workspace**.
2. Open **Calibrations → Ironing → Configure**. Show the resolved source profiles, provenance/compatibility summary, fixed ironing settings, editable flow/speed triples, and the exact nine A–I candidates.
3. Show a **2D geometry/layout preview**, code/label locations, machine bounds/keep-outs, and an explicit preflight checklist; save the versioned configuration before generation.
4. Confirm **Generate**. The app freezes configuration/profile snapshots, allocates a unique plate code, generates connected 3MF/STLs, invokes Orca, and retains logs, G-code and hashes. Show what validation passed and what remains unverified.
5. User prints externally (outside application). In **Printer Workspace → Experiments**, find the run directly or by six-character code. Open **Experiment Details**.
6. Enter manual A–I verdicts (`pass`/`fail`/`uncertain`/`missing`), 1–5 quality ratings, defect tags, notes, and optionally photos. Explicitly mark whether a physical print was performed; do not infer this from G-code generation.
7. Select a winning candidate or unresolved tie. Save a resumable **draft** independently of result acceptance.
8. Show explanation: insufficient evidence, tie resolution needed, boundary extension, interior refinement, or eligible confirmation/recorded opt-out. Generate a **new immutable** linked experiment with its own plate code if refining or confirming.
9. When existing acceptance gates are satisfied, show an **Export Review** with exact source/destination values, profile provenance, supporting run IDs, confirmation/opt-out status, compatibility warnings, and final destination path. Write a **new Orca process JSON** plus manifest/report, never modify the source profile.
10. Close and reopen. By plate code, recover configuration, printer/material, sample map, generated artifacts, manual result revisions, parent/child runs, and export history.

**UI placement:** Keep Printer Library and Settings as primary destinations. Under **Printer Workspace**, use contextual subtabs or clearly integrated sections for **Overview**, **Calibrations**, **Experiments**, and **History**. Do **not** create a second disconnected global overview. A global Runs/History shortcut may remain but must open the same saved experiment detail view; migrate/redirect the existing New Calibration entry rather than maintaining duplicate workflows.

## 4. Architectural contracts (decide before UI wiring)

### 4.1 Separate facts that have different lifecycles

- **Experiment configuration**: frozen *inputs* (schema/version, experiment/revision ID, module, printer/material refs, source hashes, resolved settings, sweep values, layout/options, generator/adapter versions). Editable drafts are separate from frozen revisions.
- **Calibration run**: immutable plate code, plan and sample mappings, captured profiles, slicer/G-code/geometry evidence, generation outcome. Do not rewrite the historic run to record a later observation.
- **Assessment revision**: human-entered results and optional photos, parent `run_id`, timestamps, revision number, reviewer-entered print attestation; saved drafts are not automatically accepted.
- **Decision**: acceptance-policy outcome and rationale tied to an **exact assessment revision** and plan revision; refinement/confirmation parent-child links.
- **Export**: immutable review snapshot (source profile hash, accepted candidate, exact patch, Orca setup identity, evidence IDs), destination paths, output hashes, status.
- **Readiness**: distinguish **settings validated**, **layout validated**, **G-code safety checked**, **physical handling accepted**, **assessment complete**, and **export accepted**. `print_ready` must remain a separately derived, fail-closed decision, not a synonym for `settings_validated`.

### 4.2 Suggested versioned storage shape

Implement the smallest normalized schema that fulfills the above; do not blindly adopt names if a better existing repository contract is available. Suggested SQLite v2→v3 additive migration:

```text
experiment_configs(id PK, experiment_id, revision_no, schema_version, created_at,
                   config_json, input_sha256, UNIQUE(experiment_id, revision_no))
run_config_links(run_id PK REFERENCES calibration_runs(run_id), config_id REFERENCES experiment_configs(id),
                 parent_run_id NULL, relation_type NULL)
run_assessment_revisions(id PK, run_id REFERENCES calibration_runs(run_id),
                         revision_no, created_at, assessment_json, print_attestation_json,
                         UNIQUE(run_id, revision_no))
run_decisions(id PK, run_id, assessment_revision_id, decision_json, created_at)
run_exports(id PK, run_id, assessment_revision_id, export_json, created_at)
```

Use SQLite foreign keys, transactional migrations, explicit schema/version validation and path-safe artifact references. Index plate-code → run → linked config/assessment/export. Preserve v1 sessions and existing v2 printer/material/calibration rows; test migration on populated copies and rollback on failure. Do not migrate old accepted v1 results into a trusted accepted state without explicit verdict evidence.

**Potentially simpler design:** store immutable JSON records in a versioned `run_events` table plus typed accessors. Choose based on existing service patterns, but preserve revision history and avoid mutable, untyped catch-all blobs for safety-critical decisions.

### 4.3 Configuration snapshot contract

Provide `to_dict` / `from_dict`, schema validation and canonical normalized hashing for a `SavedExperimentConfiguration` (name is illustrative). Suggested logical fields:

```json
{
  "schema_version": 1,
  "module_id": "ironing",
  "experiment_id": "<stable logical identifier>",
  "revision": 1,
  "printer_id": "<saved printer ref>",
  "material_id": "<saved material ref>",
  "source_profile_sha256": {
    "printer": "<sha256>", "process": "<sha256>", "filament": "<sha256>"
  },
  "sweep": {
    "ironing_flow": ["<low>", "<mid>", "<high>"],
    "ironing_speed": ["<low>", "<mid>", "<high>"]
  },
  "fixed_settings": {"ironing_type": "top"},
  "layout": {
    "strategy": "connected-grid", "rows": 3, "columns": 3,
    "geometry_backend": "stdlib-voxel", "geometry_backend_version": 1
  },
  "parent_run_id": null
}
```

The values above are **a proposed wire contract**, not proof those exact fields/types already exist. Derive settings from real `ProfileSelection` and `ExperimentPlan`; preserve original formatting/precision where required by Orca. Store any added settings that materially change generated output (e.g. actual connector sizes, frame dimensions, voxel resolution, label style) in the immutable snapshot. Reject incompatible major versions and unknown/unvalidated layout strategies. The **preview must never allocate a permanent plate code**; allocate on committed run creation and retry a UNIQUE constraint conflict safely. Stable equivalent inputs must yield equivalent candidate settings and geometry; generated ZIP/3MF byte-identical outputs need not be promised until explicitly qualified.

### 4.4 Compatibility layer for existing services

`ExperimentService.create_initial()` already accepts `ExperimentOptions`; `GroupedOrcaGenerationService.generate_ironing()` currently builds its own plan. Introduce a typed service boundary such as:

```python
prepare_ironing(printer_id, material_id, options) -> ExperimentDraftReview
save_configuration(review) -> SavedExperimentConfiguration
generate_from_configuration(config_id, *, cancel_event=None) -> CalibrationRunRecord
save_assessment(run_id, expected_revision, results, print_attestation, photos) -> AssessmentRevision
recommend_next(run_id, assessment_revision_id) -> Decision
create_followup(decision_id, kind="refinement|confirmation") -> SavedExperimentConfiguration
build_export_review(run_id, decision_id, new_profile_name) -> ExportDraft
write_reviewed_export(export_draft_id, destination) -> ExportRecord
```

These are **proposed service contracts**; adapt names to the codebase, not the invariants. In particular, do **not** fabricate a `SessionSnapshot` or a successful confirmation just to satisfy `ExportService`. Refactor or add a narrowly scoped adapter so existing `AcceptanceService` / `ExportService` rules operate on authenticated, saved grouped-run evidence. Preserve the existing legacy session APIs and their tests until the new path is verified.

## 5. Implementation sequence — small reviewable changes

### Task 0 — Baseline hygiene and red/green gates (**P0**, first)

**Work:**

- Fetch `origin/main`; verify `5570daf` or review differences before proceeding. Check for dirty worktrees and preserve any unrelated work.
- Update `README.md`, `docs/IMPLEMENTATION_STATUS.md`, and the *current* baseline paragraph of `IMPLEMENTATION_PLAN.md` to reflect committed connected-plate functionality; leave historical dated entries in `docs/PROGRESS_HISTORY.md` intact.
- Add focused regression cases for the observed fail modes: auto-arranged samples; missing object override; corrupt/missing artifact; duplicate plate code; incorrect profile hash; cancellation and failed slice remain non-ready.
- Audit inconsistent copy: `ExperimentService.review()` still describes separate plates and Qt says users can review values before launch even though the current generation button offers no full candidate review. Fix copy alongside the new review screen.

**Files:** Docs above; `tests/test_grouped_plate.py`, `tests/test_grouped_orca_service.py`, `tests/test_qt_printer_workflow.py`, any small relevant source files.

**Done when:** documentation names actual HEAD and current capabilities; regressions protect the grouped-layout and immutable-evidence gates.

### Task 1 — Saved configuration + pre-generation review (**P0**)

**Work:**

- Introduce typed versioned config and safe persistence. Build review data from existing `ProfileSelection` + `ExperimentService` and the actual connected `PlateLayout`; eliminate the legacy separate-plate assumption for this execution path.
- Add configurable flow/speed low/mid/high triples; validate uniqueness, ordering, profile schema limits, positive finite values, expected nine candidates, correct A–I mapping, machine polygon and explicit keep-outs. Show fixed settings and which values are inherited versus overridden.
- Build 2D layout preview from the **same geometry/layout data used for generation**, not a separately coded approximate grid; annotate plate code as **allocated at generation**. Include model dimensions, tab/rail dimensions, labels, bounding box, and candidate legend.
- Freeze the saved configuration prior to generation. Link new `run_id` to exact config revision/hash. Refuse if the selected printer/material/profile context changed since review, or require explicit re-review.
- Allow generation to accept the frozen reviewed plan/options, rather than silently re-planning from a new profile baseline. Ensure terminal failures still preserve the config link.

**Likely files:** new `domain/experiment_config.py`; `app/services/experiment_service.py`; `app/services/grouped_orca_service.py`; `storage/library_store.py`; `storage/session_store.py`; `app/qt/workflow_widgets.py` or new `qt/experiment_review.py`.

**Tests:** config serialization/version rejection; source hash change during review; deterministic candidate mapping; invalid values; oversized/irregular bed; stale config; saved-config reopening; failed-run linkage; UI preview parity with generated specimen coordinates.

**Done when:** a user can review/edit/save a versioned nine-sample ironing configuration, then generate and reopen exactly that revision without hidden settings changes.

### Task 2 — Per-run assessment persistence and Qt experiment detail (**P0**)

**Work:**

- Add assessment persistence (drafts, immutable revisions, optimistic concurrency/expected revision). Existing `CandidateAssessment` and `ExperimentResults` v2 remain the validation contract; do not invent competing verdict semantics.
- Link `Sample-A..I`/physical `A..I` to the correct persisted candidate ID. Expose all nine cards with exact original settings, quality ratings, pass/fail/uncertain/missing, defect tags, free-text notes, winner/ties, photos, and save feedback.
- Add optional local evidence photos via a safe **copy-to-run** API: reject absolute/traversal/symlink escapes when reopening, avoid overwriting names, validate extension/content as far as practical, and record SHA-256/size. Never refer to fragile external image paths as immutable evidence.
- Explicitly ask user whether the plate was physically printed and reviewed; record an attestation/timestamp (not automatic certification). A software-only test may mark result input as **synthetic** without pretending a print occurred.
- Make history/table row and plate-code lookup open the same Experiment Details screen. Display generation status, validation state, artifacts, assessment revision, and remaining safety blockers. On reopening, populate exact saved values.
- Permit incomplete/uncertain draft saves. Require explicit resolved outcomes only for actions that truly need them. Clearly distinguish `assessment_complete` from `print_ready`.

**Likely files:** new `app/services/assessment_service.py` and Qt detail/editor widgets; `domain/records.py` (or separate model), `storage/library_store.py`, `app/qt/main_window.py`, `app/qt/workflow_widgets.py`.

**Tests:** save/restart/reopen; tie and uncertainty; selected candidate must exist; mismatched plan/run; concurrent edits; no physical attestation; corrupt/absent photos; protected paths; old v1/v2 data readable; Qt offscreen editing smoke.

**Done when:** a real or explicitly simulated manual result can be saved by plate code, changed as a new revision, and reconstructed after restarting without changing any generated evidence.

### Task 3 — Decision, refinement and confirmation linkage (**P0**)

**Work:**

- Wrap existing `AcceptanceService.evaluate()` and `ExperimentService.propose_refinement()` with the saved-run context. Verify all requirements of these older services with new grouped-run evidence; keep the current tie/unknown/missing/boundary behavior.
- Show a clear decision panel: **inconclusive / resolve tie / extend boundary / refine / confirmation required / accepted** with machine-readable reasons and human-readable copy.
- `Refine` must create a **new** configuration revision and subsequently a new run/plate code, linked to its parent run, winning candidate and exact assessment revision. No mutation of original plate/sample settings.
- Support confirmation as a separate linked run with the selected settings. The existing confirmation eligibility check must require the actual successful linked generation evidence and recorded human outcome, not merely an object with a plausible `run_id`.
- If retaining the existing reasoned confirmation **opt-out** route, display its consequences prominently and persist the reason and policy version; never treat opt-out as validation of the physical print or slicer safety.

**Likely files:** new `app/services/run_decision_service.py`; `app/services/acceptance_service.py` (only if necessary); `storage/library_store.py`; `app/qt/experiment_detail.py`; related tests.

**Tests:** boundary winner triggers extension; interior winner proposes narrower sweep; tie blocks; missing/uncertain blocks; failed G-code blocks confirmation; invalid parent relation blocked; old run still resolves; distinct code per follow-up; opt-out reason retained.

**Done when:** selected sample → explained next action → linked new config/run works without manual database edits and never marks an unverified run accepted automatically.

### Task 4 — Evidence-gated reviewed Orca export in Qt (**P0**)

**Work:**

- Adapt `ExportService.build_draft()` and `export()` to the new immutable run/assessment/decision records. Reuse its protected-root, source-profile copy, setting diff, new-name and three-file atomic-write behavior. Do not import Dear PyGui widget code into Qt.
- Display source process profile (name + SHA), source and proposed values, effective provenance, exact selected candidate, supported settings (`ironing_flow`, `ironing_speed` only this pass), supporting runs/confirmation or opt-out, version/capability warnings, and destination.
- Reject unresolved ties, unaccepted or unreviewed results, stale profile hash/setup, unsupported setting patch, export into any Orca source/profile root, overwrite of existing destination/report/manifest, missing artifacts, or changed draft after review.
- Export a new Orca **process JSON**, plus manifest and Markdown evidence report. Preserve unknown source keys and never modify original preset. Save export record/hashes back to history; do not claim a verified importable 3MF/preset **bundle** until separately tested.
- On export review, show unresolved physical-print/slicer support warnings even if a reasoned opt-out is recorded. An opt-out is not evidence of printer safety.

**Likely files:** `app/services/export_service.py`; a new adapter or `app/services/run_export_service.py`; `app/qt/experiment_detail.py`, `app/qt/export_review.py`; persistence and tests.

**Tests:** profile source byte/hash unchanged; correct selected settings exported; source inheritance/unrelated unknown keys preserved; denied protected target; atomic failure recovery; stale Orca/profile setup; no confirmation + no opt-out denied; clear history after restart; optional verified Orca JSON import check in the existing isolated integration environment.

**Done when:** a saved, eligible grouped ironing result can produce a reviewed, independently named JSON profile and associated evidence files through Qt, with audit and negative cases.

### Task 5 — Bounded G-code preflight + label/connector inspection (**P1**, can parallelize after Task 1)

**Work:**

- Keep the existing per-object flow/speed and shared-layout checks. Add a **separate** `GcodePreflightReport` for machine-specific commanded movements, reported bed limits/keep-outs, Z limits where available, temperature setpoints and sequencing, extrusion mode, and unrecognized/unsupported commands. Capture parser coverage and explicit unverified conditions, not only boolean `passed`.
- Implement a modal G-code interpreter for the supported subset, with fixtures for `G90/G91`, `M82/M83`, `G92`, modal X/Y/Z/E/F, travel vs extrusion, and arcs `G2/G3` if encountered. Resolve full arc extents or fail closed as **unsupported**; don't silently treat a curved move as a straight segment. Handle/flag vendor macros, unknown commands, purge/start/end moves, tool changes, and changes in units rather than making unsafe assumptions.
- Machine bounds apply to **all relevant commanded movement envelopes**; do not conflate the sample extrusion bounding-box proof with travel and start/end safety. Where printer-specific sequences legitimately fall outside a modeled object region, use declared machine limits and explicit policy instead of arbitrarily filtering them out.
- Report configured temperature limits and compare them with observed commands; if profiles/limits are unavailable, report **unverified**, not pass. Avoid pretending that static G-code analysis proves hardware safety.
- Add a Qt **Inspect** section with per-gate badges, exact toolpath/slicer/geometry artifact links, basic 2D slice/layout visualization where feasible, and a physical inspection checklist for label legibility, frame adhesion and hand-tool separability. Record the physical trial separately with material/nozzle/layer/orientation and notes/photos.
- Retain `print_ready: false` unless all **declared** automated, support-matrix and physical conditions have been met and reviewed. This pass may complete the validator yet reasonably leave every existing run not print-ready.

**Likely files:** new `gcode/preflight.py`; `grouped_plate.py`; `app/services/grouped_orca_service.py`; `app/qt/experiment_detail.py`; regression fixtures/tests.

**Tests:** relative/absolute extruder modes, G92 resets, retract/unretract, out-of-bed travel, non-extrusion move outside limits, keep-out crossing including segment intersection, negative temperature, missing temperature limits, unknown arc/macro, invalid start/end, auto-arranged negative retained. Run against recorded real-Orca G-code when local artifact paths are available.

**Done when:** every supported preflight rule has explicit observed evidence and a negative fixture; unsupported cases block readiness rather than being waved through.

### Task 6 — Recovery, end-to-end verification, docs, handoff (**P0**, final)

**Work:**

- Exercise the complete new Qt path using a deterministic fake Orca runner and **synthetic labeled** assessment input. Also run opt-in real Orca for the **generation** segment on the explicitly tested machine/profile pair; no physical print is inferred.
- Restart the application between generate, assess, refine and export steps. Verify exact plate-code lookup, config hash, parent/child links, evidence hashes, and JSON output changes. Confirm failed/canceled/missing artifacts cannot become accepted/exportable via UI bypass.
- Test SQLite v1→v2→v3 migration with preserved historical sessions/runs, mid-migration failure rollback, duplicate IDs, concurrent writes, missing/corrupt artifacts, and schema-version rejection.
- Run Qt offscreen smoke; review actual rendered windows manually at normal and 125%/150% DPI when available. Keyboard navigation, long labels, error states, and dynamic resizing must be checked.
- Run the default full suite; run opt-in real-Orca integration separately, record actual *run/pass/skip/fail* counts, executable/profile hashes, platform, commands, logs and artifact paths. Never copy outdated test counts into new documentation.
- Update `README.md`, `docs/IMPLEMENTATION_STATUS.md`, the appropriate `IMPLEMENTATION_PLAN.md` task checkboxes, and **append** a dated record to `docs/PROGRESS_HISTORY.md`; document remaining V1 gaps and physical tests.

**Done when:** an independently reproducible software walkthrough runs from imported saved profiles to a reviewed new process JSON; all safety limitations remain truthfully represented and there are no failing relevant regressions.

## 6. Suggested PR/review cuts

| PR | Scope | Depends on | Merge gate |
|---|---|---|---|
| PR-A | Baseline correction + config model/persistence + frozen plan, candidate review/preview | Current `main` | Config→run integrity and 3×3 preview parity tests |
| PR-B | Assessment revisions, photo evidence, Qt Experiment Details + plate-code navigation | PR-A | Restart/reopen, unknown/tie/missing/unsafe-path regressions |
| PR-C | Decisions/refinement/confirmation, linked run revisions | PR-B | Existing acceptance rules preserved and follow-up immutability tests |
| PR-D | Reviewed Orca process JSON export + saved export history | PR-C | Source profile unchanged, guarded destinations, true supporting evidence |
| PR-E | Bounded G-code preflight + Inspect + physical trial protocol | PR-A; can develop in parallel | Parser/negative fixtures, explicit unverified state |
| PR-F | Integrated regression, Qt visual review, documentation, handoff | All above | Default + opt-in suites, historical data migration, final demos |

If PR-E requires more work than expected, **do not block PR-B/C/D software completion** waiting for a falsely broad print-readiness claim. Keep a clearly visible preflight blocker and return to the validator as a separate focused milestone. Conversely, **do not mark PR-F complete as a physical-print acceptance** if no actual specimen has been printed and assessed.

## 7. Acceptance matrix (explicit, falsifiable)

| ID | Scenario | Expected evidence / behavior |
|---|---|---|
| AC-01 | Import saved Orca machine/process/filament and configure ironing | Review shows real resolved baseline, source fingerprints, valid A–I flow/speed matrix, preserved unrelated settings |
| AC-02 | Edit one sweep value, save config, reopen | Versioned stored config unchanged; expected candidate changed; old revision still resolves |
| AC-03 | Generate grouped plate | New unique code; nine labeled samples + frame in saved geometry/3MF; per-sample config + profile hashes retained |
| AC-04 | Real Orca positive and missing-override negative | Positive preserves common XY layout and all nine settings; negative is rejected, with evidence retained |
| AC-05 | Orca re-arranges even one object | Generated run is `validation_failed`; `print_ready` remains false |
| AC-06 | Add A–I manual observations and photo, close/reopen | Assessment revision and hash-checked copied photo round-trip; exact plate code still resolves |
| AC-07 | Missing/uncertain/tied candidate | Draft save allowed; automatic acceptance/export denied with explanatory reason |
| AC-08 | Interior winner and boundary winner | Appropriate refinement or boundary extension; new revision/code; immutable source run retained |
| AC-09 | Confirmation/opt-out | Confirmation demands actual linked evidence; reasoned opt-out recorded separately, not misrepresented as print/safety qualification |
| AC-10 | Export valid reviewed result | New process JSON, manifest and Markdown report; only accepted settings differ; original Orca source bytes unchanged |
| AC-11 | Invalid export | Protected path, stale draft, unsupported bundle, overwrite, invalid evidence rejected without partial files |
| AC-12 | Migration/recovery | Populated v1/v2 data preserved; unrecognized future schema rejected; partial migration rolls back; wrong artifact hash flagged |
| AC-13 | G-code preflight | Supported command/mode fixtures pass; out-of-bounds/keep-out/unsupported commands fail closed; physical blockers remain visible |
| AC-14 | UI acceptance | Printer workspace → experiment details → manual assessment → recommendation → reviewed export works in offscreen Qt and manual screen review |
| AC-15 | Docs/tests | Fresh source HEAD identified; real test commands/results supplied; `docs/PROGRESS_HISTORY.md` appended; no unsourced release claims |

## 8. Verification commands and evidence protocol

Run from repository root in an isolated Python >=3.11 environment after installing optional GUI dependencies. The following commands illustrate the intended steps; adapt syntax to PowerShell/Windows where needed.

```bash
python -m pip install -e '.[gui]'
python -m compileall -q src/calibrate3dp tests
QT_QPA_PLATFORM=offscreen PYTHONPATH=src python -m unittest discover -s tests -v
```

For an **opt-in** real-Orca integration, set `ORCA_SLICER_EXE` and `ORCA_PROFILE_ROOT` to the actual executable/profile tree, with a dedicated `ORCA_EVIDENCE_DIR`, then run the relevant `tests.test_orca_slicer_integration` test(s). Do **not** run against the user's Orca preset directory as a writable output. Record:

- Git commit SHA and clean/dirty checkout status.
- OS, Python/PySide6 versions and actual Orca executable fingerprint/identity.
- Machine/process/filament profile names and hashes (avoid personal home paths in public logs unless needed).
- Exact command/arguments and complete logs; positive and negative fixture results.
- Expected vs observed sample settings, layout translation, G-code preflight coverage, retained generated artifact hashes.
- Actual tests run/passed/failed/skipped; UI offscreen versus human visual inspection status.
- Explicitly identify **synthetic** assessment data, and separately list any **actual physical print** with photos, material, layer/nozzle settings, observed label readability and connector handling.

### Evidence to retain in application storage

```text
workspace/
  runs/<run-id>/
    plan.json
    sample-map.json
    plate.3mf
    geometry/{geometry.json, *.stl}
    profiles/{machine.json, process.json, filament.json}
    output/*.gcode
    logs/{stdout.log, stderr.log, ...}
    manifest.json
    evidence/{copied-photos-and-metadata}        # proposed
    reviews/{assessment-revisions-and-decisions} # proposed
    exports/{export-review-references}           # proposed
```

The filesystem layout under `evidence/`, `reviews/`, and `exports/` is illustrative; SQLite may hold the primary normalized metadata while artifacts are stored as path/hash references. Follow existing path-safety and atomicity conventions.

## 9. Release boundary and immediate next action

**Success for this pass** is a credible **software-level** ironing workflow: choose profile → review/freeze versioned experiment → generate/validate connected plate → reopen by code → record manual results → obtain explainable follow-up/confirmation → review and export a new Orca JSON process profile, with persistence and robust negative tests.

**Not success:** merely opening a UI and generating STL/3MF, passing mocked tests while skipping the real Orca gate, treating generated output as physically safe, or exporting without an exact accepted candidate and preserved supporting evidence.

**First action for the implementing agent:** verify HEAD, correct the stale baseline, and implement Task 1's frozen configuration/preview as PR-A. Then wire persistent assessment in PR-B. Keep print-readiness claims blocked until evidence genuinely satisfies the declared gate.

---

### Current source pointers

- GitHub main snapshot: https://github.com/MarcusFunt/Calibrate-3DP/tree/5570dafabb44c9adc77089d2053e194600fc7850
- Commit introducing/fixing connected Orca placement: https://github.com/MarcusFunt/Calibrate-3DP/commit/5570dafabb44c9adc77089d2053e194600fc7850
- Project V1 requirements: `docs/V1_GOAL.md`
- Ordered baseline plan: `IMPLEMENTATION_PLAN.md`
- Current implementation inventory: `docs/IMPLEMENTATION_STATUS.md` (**baseline paragraph currently stale**)
- Test history/evidence: `docs/PROGRESS_HISTORY.md`
