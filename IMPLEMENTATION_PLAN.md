# OrcaSlicer Calibration Workbench
## Coding-agent implementation plan

**Status:** implementation handoff, research snapshot 8 October 2026  
**Product goal:** a local application that imports the user's existing OrcaSlicer setup, creates controlled calibration experiments, slices them with OrcaSlicer, guides manual result entry and adaptive retesting, then exports an Orca-compatible tuned profile. Orca remains the slicer. The app replaces Orca's calibration workflow with a more controlled, extensible experiment system.

---

## 1. Product recommendation

Build a **local-first desktop application**. Use Python 3.12+ for the domain layer and Orca integration, PySide6 for the desktop UI, SQLite for experiment history, and OrcaSlicer's command-line interface as the slicing engine. Keep the domain and calibration logic independent of the UI so a local web UI can be added later if there is a clear need.

The app should open like a normal desktop tool. It scans for OrcaSlicer, reads the user's profiles without changing them, and lets the user select or import a printer, filament, and process profile. From those real settings it creates a test, launches Orca headlessly to slice it, presents the resulting G-code and a printable test map, then records the user's visual/physical assessment. The app can create another, narrower experiment or mark the result accepted. At the end, it exports a **new process profile** with just the selected calibrated values changed, plus a report and a machine-readable experiment record.

The app must not depend on Orca's built-in calibration UI or ask the user to copy dozens of values into a setup form. It may use Orca's native 3MF project format for test geometry and overrides, but Orca CLI is the authority that creates final G-code. Keep the first release offline-capable. Do not require a camera, Moonraker, a cloud vision API, or printer control to use the core product.

### Why desktop is the right first target

The central workflow needs access to local preset files, an Orca executable, a selected output directory, and potentially very large G-code/3MF files. A desktop app can discover those resources, invoke Orca safely, and export files without asking the user to configure a local server. A web UI can still be considered later, but it would need a local companion process to do the same work.

### The key engineering risk

Before building the full UI, prove the **Orca profile resolution + multi-pad slicing path** against the Orca versions the project intends to support. Orca's CLI accepts machine/process JSON with `--load-settings` and filament JSON with `--load-filaments`; however, a recent upstream issue reports that directly loading a child preset JSON may not resolve its `inherits` chain and can silently fall back to defaults. Therefore the app must resolve effective profile values itself or create temporary, fully resolved JSON inputs and verify them through Orca's exported effective settings and resulting G-code. Do not assume that passing the selected child preset file alone gives the intended settings. The other early spike is confirming that per-object/per-pad overrides embedded in generated 3MF survive headless slicing. If this fails, use the documented fallback of slicing independently generated plates with temporary resolved process profiles.

**Gate:** no full implementation begins until a reproducible fixture proves that (1) selected inherited presets resolve to the same effective values as the Orca UI, (2) one calibration parameter varies per pad/plate in the resulting G-code, (3) machine start/end G-code and temperatures remain intact, and (4) all generated moves stay inside the printer's usable area.

---

## 2. User journey and product behavior

### Primary journey

1. User opens Calibration Workbench.
2. App finds OrcaSlicer and displays its version, executable path, and profile data roots. If discovery fails, user browses to the executable and profile folder once.
3. App shows available printer, filament, and process presets. It resolves and displays effective values, including where each value came from (child or inherited parent). It supports importing Orca preset JSONs and bundles without writing to the Orca config directory.
4. User chooses **Ironing**, **Support interface**, or **Bridges**. A first-time user can run only the requested module; the app recommends prerequisites based on selected process/filament values and prior saved runs.
5. App creates a proposed experiment using imported values as the baseline. The user sees the exact varied parameters, held-constant parameters, test geometry, estimated print duration/material, run ID, and parameter-to-pad key before slicing.
6. User clicks **Generate test**. The app generates a 3MF project if supported by the validated integration path, or per-candidate 3MF plates; calls Orca CLI with the selected machine, filament, and derived process configuration; validates output; then saves G-code, the project, effective config snapshots, CLI logs, and a human-readable map in the session directory.
7. User prints in the ordinary way. The app does not start a print or send commands to a printer in v1.
8. User reopens the run, enters a result for each labeled pad/specimen, optionally adds defect tags and notes, and marks the result **acceptable**, **not acceptable**, or **uncertain**. The app provides zoomable photos/notes but does not require a camera.
9. The experiment policy recommends an improved range. The user reviews the next candidate grid and can edit/lock the range before generating another plate.
10. Once a candidate passes the acceptance rule and a confirmation run, user selects **Export profile**. App exports a new Orca process preset and a report; original profiles are unchanged.

### Non-goals for the first release

- Replacing OrcaSlicer as a slicer or editor.
- Automatically uploading/printing files through Moonraker, OctoPrint, or a vendor cloud.
- Firmware-level input-shaper, motor-current, TMC, Z-probe, toolchanger-offset, or motion-limit calibration.
- Claiming a camera score is equivalent to human judgment for gloss, support removability, or bridge sag.
- Modifying Orca's built-in presets in place.
- Optimizing every slicer setting in a single coupled search. Experiments should change a small, declared set of variables while recording the rest.
- Automatically changing hardware configuration, start G-code, or firmware settings.

---

## 3. Repository deep dive and reuse decisions

The repositories below are assessed as **implementations, test-design references, or future optional integrations**. Do not make all of them runtime dependencies. Several are licensed in ways that require source sharing, restrict commercial use, or apply different terms to models than code. Reimplementing an idea is not a substitute for checking the exact license of any copied code, data, or mesh.

### 3.1 Most directly useful app and workflow references

| Project | What it actually contributes | Decision |
|---|---|---|
| [PA-Helper](https://github.com/4o66/pa-helper) | Offline browser companion for Orca pressure advance; run persistence, manual pick/scoring, plots, and a labeled ironing grid that varies speed and flow by pad. It creates an Orca 3MF with per-pad overrides and leaves slicing to Orca. | **Study first for manual-result UX and ironing grid semantics.** Its ironing test is the closest match to this product's desired interaction. Do not depend on its code without a deliberate AGPL-3.0 compliance decision; its README attributes the base ironing model to a CC0 source. Treat its beta/unverified status as evidence to independently validate geometry and output. The repo does not provide the complete profile-import/adaptive-experiment/export workflow required here. |
| [Print-Calibration-Tool](https://github.com/piledge/Print-Calibration-Tool) | Browser tool that reads already-sliced G-code and helps select a specimen for temperature, PA, and extrusion multiplier. Its design preserves printer start code, temperatures, retraction, and cooling while transforming supported calibration sections. | **Use as a reference for G-code preservation and candidate selection, not as the slicer or application base.** Its own README describes slicer and machine limitations; it does not provide ironing, supports, bridges, or Orca profile round-trip. The application is AGPL-3.0 and bundled models have separate CC BY-NC 4.0 terms. Avoid assuming its model assets may be redistributed. |
| [PrintWise](https://github.com/distractable2/PrintWise) | A local browser app that imports Orca printer/filament/process JSONs and bundles and exports Orca-ready profile artifacts and reports. Useful examples for profile intake, naming, and export presentation. | **Study its data-flow and user-facing bundle concepts only.** It is a profile/model optimizer, not an experimental print-and-measure loop. Its stated custom noncommercial license is not an OSI-approved open-source license, so it is not a suitable code foundation absent separate permission. |
| [tower-tool](https://github.com/Knifa/tower-tool) | React single-page generator for temperature, retraction, speed, acceleration/jerk, flow, and geometry tests. Shows how calibration parameters can be expressed as generated tower variants. | **Reference test definitions and labels.** It does not import or export Orca profiles and is not a general experiment engine. Reuse only after reviewing GPL-3.0 obligations; prefer new test definitions and generated geometry if no code reuse is needed. |
| [SmartTemperatureTower](https://github.com/thbitzer/SmartTemperatureTower) | Python/OpenSCAD tower generator that inserts temperature changes at height markers after slicing. Demonstrates a config-driven generated test and post-slice transformation. | **Reference only.** Its older PrusaSlicer-oriented pipeline and G-code edits do not match the desired Orca-first workflow. Use Orca for all final slicing; only introduce G-code transformation when it has a narrow, tested, documented purpose. |

**Practical lesson:** PA-Helper demonstrates the first product loop in miniature: define pads, print, select a result, save the run. The new app should generalize that loop into versioned experiment definitions, profile-aware baselines, adaptive follow-up, validation, and export.

### 3.2 Calibration models, geometry, and methodology

| Project | Findings | Decision |
|---|---|---|
| [ScanNTune](https://github.com/jaak0b/ScanNTune) | Browser-based scanner analysis for XY scale/skew, shrinkage, flow, PA, and input shaper. Reads scanned printed coupons and turns measurements into recommendations. A flatbed scanner is required; phone images are explicitly not equivalent in its workflow. | **Future analyzer architecture reference.** It is a strong example of separating generated coupons from measurements, and may guide a later image/scanner adapter. It does not cover ironing/support/bridges or full Orca profile export. MIT license, but review dependencies and assets individually. |
| [Calibration-Shapes](https://github.com/5axes/Calibration-Shapes) | Cura plugin plus OpenSCAD-based objects and postprocessing scripts; includes bridge and support-related shapes among broader temperature/flow/speed/overhang tests. | **Geometry and test-coverage reference only.** Cura configuration and postprocessors are not directly portable to Orca. AGPL-3.0 project; review each model/script's terms before copying. Build Orca-compatible geometry from a clean parametric specification or use properly licensed models with attribution. |
| [The Ultimate Filament Tuning Guide](https://github.com/Sgail7/The-Ultimate-Filament-Tuning-Guide) | Manual calibration sequence and test methodology. Its bridge flow exercise compares bridge-flow ratios and advises setting fan and bridge speed first, then judging sag/gaps at multiple spans. | **Use as a methodology reference for staged bridge tuning.** It assumes a specific slicer/firmware context and is not a software framework. Attribute any copied text or model; write original instructions for Orca settings. |
| [parametric-calibration-objects](https://github.com/CameronBrooks11/parametric-calibration-objects) | OpenSCAD source for parameterized calibration geometries such as flow, temperature, retraction, and speed pieces. | **Study parameterized geometry patterns.** Low adoption does not establish quality; test dimensions and slicability independently. AGPL-3.0, so avoid code reuse without complying. |
| [AutoTowersGenerator](https://github.com/kartchnb/AutoTowersGenerator) | Cura-oriented tower-generation plugin for temperature, fan, flow, and speed test towers. | **Reference only for test-variable encoding and labels.** Cura integration is not reusable as an Orca integration; verify current project and exact model licensing before adopting geometry. |
| [Calistar / Fleur de Cali](https://github.com/dirtdigger/fleur_de_cali) | Parametric calibration object plus manual worksheet for XY scale/skew. Emphasizes multiple measurement points and uncertainty rather than treating one measurement as exact. | **Borrow the uncertainty and repeat-measurement approach for future dimensional tests.** It is not an app or profile workflow. Preserve attribution/license if reusing its CAD or worksheet. |
| [SuperSlicer](https://github.com/supermerill/SuperSlicer) | Related slicer fork with built-in ironing-pattern and bridge-flow calibration approaches. Its discussions illustrate interactions between bridge parameters and top skin/ironing, and the need for a stable coupon base. | **Behavioral reference only.** Do not route users through SuperSlicer or use its calibration UI. It is not Orca, and calibration assumptions must be verified against Orca's actual settings and toolpaths. |

### 3.3 Camera and laser repositories (future modules, not MVP dependencies)

| Project | What the implementation does | Decision |
|---|---|---|
| [PressureAdvanceCamera](https://github.com/undingen/PressureAdvanceCamera) | Klipper pressure-advance experiment using a close-focus USB/endoscope camera. Its pipeline captures an image, uses image segmentation, rectifies the region, analyzes line contours, and scores a PA pattern. The reviewed implementation uses fal.ai for segmentation, so it has a network/API and privacy dependency. | **Later optional PA analyzer, not the app foundation.** Define a local `Analyzer` interface now, but do not require a camera or cloud API. A future implementation should replace cloud segmentation with local CV or explicitly ask the user before any upload. It does not solve ironing/support/bridge analysis or profile importing. GPL-3.0. |
| [SkewCamera](https://github.com/undingen/SkewCamera) | Early Klipper camera workflow using a toolhead camera and ChArUco target; calibrates camera geometry and estimates XY skew using observed moves/target positions. | **Future machine-geometry plugin.** Useful for camera calibration, coordinate mapping, confidence/quality gates, and the importance of separating camera-to-nozzle offset. It calibrates XY motion/skew, not slicer finish parameters. GPL-3.0 and early-stage. |
| [Rubedo](https://github.com/furrysalamander/rubedo) | Experimental Klipper/Moonraker pressure-advance tool using a line laser and camera to capture line deformation as a 3D-like scan. Requires a focused, angled laser, close camera, setup offsets/crops, and careful print configuration. | **Do not buy or design around it for v1.** It is what the earlier discussion called the Rubedo line-laser system: an experimental DIY line-laser scanning setup, not a standard plug-and-play sensor product. Its author warns the workflow is experimental and G-code/motion need checking. Good research reference for later structured-light ideas; GPL-3.0. |

### 3.4 Firmware, motion, Z, and multi-tool repositories

| Project | What it calibrates | Decision |
|---|---|---|
| [klipper_auto_speed](https://github.com/Anonoei/klipper_auto_speed) | Klipper extra sweeps speed/acceleration, detects missed steps using stepper feedback, searches limits, and validates the result. | **Separate machine commissioning module, later.** It outputs machine limits rather than filament/process quality. Do not silently write its output into a process profile; machine motion caps may map to printer settings and require explicit review. MIT. |
| [klipper_tmc_autotune](https://github.com/andrewmcgr/klipper_tmc_autotune) | Klipper extension calculating Trinamic driver register configuration from driver/motor details and tuning choices. | **Separate hardware/firmware domain.** It does not generate slicer tests or Orca profiles. Document as an optional adjacent tool; never edit firmware config in v1. GPL-3.0. |
| [klipper_z_calibration](https://github.com/protoloft/klipper_z_calibration) | Klipper nozzle-to-bed/probe-offset calibration using a probe and a reference switch or contact method. | **Separate machine calibration.** It can be recommended as a prerequisite when first-layer geometry is unreliable, but its result belongs in Klipper config, not an Orca process export. GPL-3.0. |
| [Axiscope](https://github.com/nic335/Axiscope) | Camera-assisted XY and optionally Z tool offset calibration for Klipper toolchangers, with an upward-facing nozzle camera and host service. | **Only relevant to multi-tool printers.** Future specialized plugin; do not put toolchanger assumptions into the core app. MIT. |
| [kTAMV](https://github.com/TypQxQ/kTAMV) | Camera-based multi-tool nozzle alignment, with a Klipper extra plus separate computer-vision service. | **Only relevant to toolchangers.** Future integration candidate if requested; GPL-3.0 and requires controlled motion/camera calibration. |
| [NozzleAlign](https://github.com/viesturz/NozzleAlign) | Mechanical probe and Klipper macros for multi-tool XY/Z offset calibration. | **Only relevant to toolchangers.** Reference for explicit calibration fixtures and measurement sequences; unrelated to process-profile tuning. GPL-3.0. |

### 3.5 OrcaSlicer itself and official documentation

| Source | What it tells us | Product implication |
|---|---|---|
| [OrcaSlicer CLI mode](https://github.com/OrcaSlicer/OrcaSlicer/wiki/cli_mode) and [CLI settings/filament flags](https://github.com/OrcaSlicer/OrcaSlicer/wiki/cli_misc) | Orca can run headlessly; CLI accepts settings JSON and filament JSON files. | Use the real slicer binary as the only source of final G-code. Capture version, invocation, logs, effective config, and exit status for reproducibility. |
| [Orca profile guide](https://github.com/OrcaSlicer/OrcaSlicer/wiki/how_to_create_profiles) | A print combines machine, filament, and process presets; presets can inherit from parent profiles, and compatibility is separate from inheritance. | Build a versioned profile resolver that retains unknown keys and tracks provenance. Never flatten-and-overwrite the user's original profiles. |
| [Orca user profiles](https://github.com/OrcaSlicer/OrcaSlicer/wiki/user_profiles) | Documents where user presets live and profile migration/sync behavior. | Discover profiles read-only and support explicit file/bundle import. Do not assume one hard-coded path across operating systems or config variants. |
| [Orca ironing settings](https://github.com/OrcaSlicer/OrcaSlicer/wiki/quality_settings_ironing) | Orca exposes ironing type/pattern, flow, spacing, inset, and angle controls. | The ironing experiment must record all relevant baseline settings and vary only declared dimensions in each stage. |
| [Orca CLI inheritance issue](https://github.com/OrcaSlicer/OrcaSlicer/issues/14718) | An upstream report describes direct CLI loading of a child JSON not resolving its `inherits` chain. | Treat profile resolution as a tested compatibility layer. Use an isolated temp data directory or fully resolved config input and verify, rather than trusting CLI behavior. Track fixed upstream versions. |

---

## 4. Product requirements and acceptance criteria

### Must-have v1

1. Run on Windows and Linux; macOS is a follow-up target unless the initial team has the capacity to package it.
2. Discover Orca executable/config roots or let users choose them. Show exact detected version and a clear “not found” recovery path.
3. Read Orca machine/printer, filament, and process presets; accept a preset bundle or individual JSON; resolve inheritance with provenance; preserve unknown settings.
4. Never write to or mutate the source Orca preset directory during import or test generation.
5. Generate and slice a parameterized test with the selected baseline settings. The app saves a reproducibility package for every run.
6. Provide a manual rating/result flow that can be resumed after closing the app.
7. Implement adaptive coarse-to-fine iteration for at least ironing flow/speed; export an Orca process profile based on the selected baseline.
8. Validate the candidate G-code for expected start/end sections, temperatures, bed bounds, positive extrusion, and expected experiment changes before presenting it as ready.
9. Offer a clear diff between original effective profile and exported profile.
10. Keep all profile input, print notes, and photos local unless a future feature explicitly says otherwise.

### Acceptance test example

A user selects a printer with a 220 × 220 mm bed, Generic PLA, and a 0.20 mm process profile. The app reads the profiles and shows inherited/effective ironing settings. User starts a 3 × 3 flow/speed test. It generates a uniquely labeled test, slices with the chosen Orca version and all selected baseline settings, and exports a G-code file plus a pad map. The user records scores: four pads are acceptable, one wins, and the rest have visible defects. The app proposes a narrower second-stage range around the winner, retaining fixed settings and identifying the exact reason for the interval. User confirms, prints, marks final result acceptable, then exports a new process profile whose diff changes only ironing fields selected in this session. The original profile remains byte-for-byte unchanged.

---

## 5. UX / screens

### 5.1 Welcome and Orca connection

- Detect executable candidates using OS-specific standard locations and running process metadata only when available; never assume a path.
- Detect config roots from documented OS-specific locations; present all candidates and allow a user-selected folder.
- Show Orca version, executable, config root, last check time, and a button to recheck.
- If unsupported version: allow user to continue only after displaying that the CLI/profile adapter is unverified; persist a compatibility warning in each session.
- Add an offline diagnostics button to export logs without profile contents by default.

### 5.2 Profile selector

Three required cards: **Printer**, **Filament**, **Process**. Each card supports select-from-detected, import JSON, import preset bundle, and inspect effective settings. Display preset name, source, parent/inheritance chain, schema/version, and compatibility warnings. Show the parameter fields the module will use without asking the user to fill them in manually.

Before any test, show a “starting point” summary:

- Nozzle diameter, bed dimensions/shape/origin, extruder count, firmware flavor.
- Material name/type, nozzle/bed temperature, fan behavior, max volumetric speed, flow ratio.
- Layer height, top solid layers, top surface speed, wall/infill, support state, bridge settings.
- Calibration settings relevant to chosen module.
- A short list of missing/ambiguous values, with a safe default only where the app can justify it.

### 5.3 Calibration module picker

MVP modules:
- **Ironing finish** (first feature, polished).
- **Bridge quality** (follow-up module).
- **Support interface/removal** (follow-up module).

Each module card shows target profile scope (process vs filament), prerequisites, estimated print time/material, variables it can adjust, and an explanation of how acceptance works. Do not imply a module tunes dimensions outside its tested context.

### 5.4 Experiment review screen

- Candidate table with specimen ID, settings, prior, next, and what is held constant.
- Physical plate map: render exact pad labels/locations matching G-code test; include optional orientation arrow and origin mark.
- Estimated duration/material; export project/G-code paths.
- Buttons: Edit bounds, Edit step/count, Lock a parameter, Randomize positions, Duplicate baseline control, Generate.
- Expert panel exposes geometry and full parameter matrix. Sensible defaults keep ordinary use direct, not opaque.

### 5.5 Result entry screen

For each test cell:
- Candidate ID and complete parameter values.
- Rating scale (default 1–5) plus “pass / fail / uncertain”; criterion-specific ratings, if relevant.
- Defect tags, including under/overfill, ridges, roughness, gaps, blobs, discoloration, stringing, sag, droop, poor release, scars, and subjective gloss.
- Optional photo attachment and note.
- Winner(s), tie, rejected, and missing specimen states.

The app must save after each edit and show that state is saved. Results are not reduced to a single average that hides criteria tradeoffs. Users can use numeric scoring, pairwise preference, or pass/fail only.

### 5.6 Export screen

Show source profile names and hashes, app-changed settings, old and new values, which test/run supports each value, and any untested transfer. Allow user to choose output folder and export:

- Orca process preset JSON (selected format/version adapter).
- Optional import bundle only after bundle format is verified for that Orca version.
- Session report (HTML/Markdown/PDF later) and machine-readable JSON manifest.
- Optional generated 3MF and G-code archive.

Do not install or activate the profile automatically. Provide exact import instructions for the detected Orca version.

---

## 6. OrcaSlicer profile and CLI integration

### 6.1 Preset types and resolution

Represent printer/machine, filament, and process as separate profile objects. Parse JSON while preserving all unknown keys and original raw bytes. Resolve `inherits` recursively using exact profile names and source/bundle scope. Detect:

- Missing parents, cycles, ambiguous duplicate names, incompatible printer list.
- Child overrides versus inherited values, including array/string values.
- Version-sensitive fields or settings absent from selected profile.
- Values whose types differ from Orca schema expectations.

Profile output retains the original profile's identity metadata and compatibility unless the user chooses a new name. Default output is an independent **new child preset** or a full effective preset only if Orca's current import semantics require it; validate import in every supported version. Do not overwrite a preset by default.

Keep two representations:

1. **Raw source document** for round-trip preservation.
2. **Resolved effective map** for test generation and display, with source provenance for every key.

Never silently drop unknown settings. A JSON diff should compare raw documents, while a resolved diff should compare actual effective values.

### 6.2 Versioned profile adapters

Create `OrcaProfileAdapter` with a version capability table:

- discover config roots;
- enumerate profiles and bundles;
- read JSON/bundles;
- resolve inheritance/compatibility;
- construct temporary fully resolved machine/filament/process files;
- export an importable process preset/bundle;
- validate that exported profile can be re-imported.

Pin fixtures to multiple Orca versions. Add adapters per tested version range rather than scattering version checks through UI code. If CLI/API changes, fail with a readable “unsupported/unverified version” message, never generate silently from defaults.

### 6.3 CLI runner

A single service owns all Orca subprocess calls. Requirements:

- Use argument arrays, never shell string interpolation.
- Record executable, version, exact argv with secrets redacted, working directory, start/end timestamp, exit code, stdout/stderr, and timeout.
- Run with a disposable per-session/per-job `--datadir` so the app cannot mutate the user's active Orca profile database.
- Supply temporary resolved JSON files with `--load-settings` and `--load-filaments` as documented. Verify CLI inheritance behavior and never rely on it when the adapter can supply a fully resolved map.
- Save all scratch files in a managed job folder and clean only after successful archival or explicit user cancellation.
- Support cancel; terminate child process cleanly; preserve partial logs and label output invalid if canceled.
- Validate return code, expected output existence/nonzero size, and G-code parse before marking complete.

### 6.4 Slicing data path

Preferred integration path:

1. Create or copy a 3MF project containing all test pads and experiment metadata.
2. Add per-object/per-pad process overrides only for the varied settings.
3. Create a disposable effective configuration set from the chosen profiles.
4. Invoke Orca headlessly with the project and temporary datadir/settings.
5. Parse exported settings and G-code to verify each pad receives the intended values.

Fallback if 3MF object overrides do not slice reliably:

- Produce one plate/project per candidate or a grouped set of plates.
- Use a derived fully resolved process JSON per candidate and call Orca once per plate (parallel execution optional only if the process is deterministic and resource-bounded).
- Label objects/plates in the exported map and G-code metadata.

Avoid scalar G-code substitution for geometry-dependent settings such as ironing spacing, pattern, flow, support interface gap, or bridge paths. These must be applied before slicing. Post-slice G-code edits are permissible only for tightly scoped layer-event changes with tests proving safe preservation; do not use as the general tuning mechanism.

### 6.5 Output package

Each run directory has stable structure:

```text
runs/<run-id>/
  manifest.json
  input_profiles/{printer,filament,process}.json
  resolved_profiles/{printer,filament,process}.json
  experiment.json
  geometry/test_project.3mf
  output/test.gcode
  output/test_map.svg
  output/test_map.png
  logs/orca.stdout.log
  logs/orca.stderr.log
  results.json
  photos/
```

Use immutable run IDs; later attempts create new runs and reference parent run IDs. Manifest records application version, Orca version, schema adapter, profile hashes, effective config hash, geometry generator version, experiment definition version, seed/randomization, CLI command, and output hashes.

---

## 7. Domain model and core interfaces

### 7.1 Core entities

- `PrinterProfileRef`, `FilamentProfileRef`, `ProcessProfileRef`: source path/bundle, identity, version, raw JSON, hash.
- `ResolvedProfile`: effective values, provenance map, compatibility result, warnings.
- `CalibrationModule`: module ID/version, target settings, prerequisites, experiment planner, result schema, acceptance policy, profile patch function.
- `ExperimentDefinition`: baseline snapshot, variable definitions, candidate generator, fixed settings, constraints, geometry spec, scoring rubric, stop rule.
- `Candidate`: immutable candidate ID, parameter map, generated specimen locator and human-readable label.
- `Run`: unique ID, parent/iteration, selected profiles, Orca environment, generated files, run status.
- `Observation`: candidate ratings, defect tags, photos, user note, missing/uncertain state.
- `Recommendation`: suggested next candidate range, evidence, confidence, accepted/rejected state.
- `ProfileExport`: source profile hash, patch, new name, export path, Orca compatibility result.

Store schema migrations in source control. Store runs in SQLite and files in ordinary folders so users can back up/copy sessions. SQLite should reference file-relative paths; avoid opaque blobs for G-code and images.

### 7.2 Calibration module contract

Create a module interface resembling:

```python
class CalibrationModule(Protocol):
    module_id: str
    version: str
    def inspect_baseline(self, profiles: ResolvedProfileSet) -> PrerequisiteReport: ...
    def create_initial_experiment(self, profiles, user_options) -> ExperimentDefinition: ...
    def score_schema(self) -> ScoreSchema: ...
    def propose_next(self, experiment, observations) -> NextExperimentProposal: ...
    def acceptance(self, experiment, observations) -> AcceptanceDecision: ...
    def build_profile_patch(self, accepted_result) -> ProfilePatch: ...
```

Keep test geometry generation pluggable. The geometry layer returns a CAD/mesh or Orca-native project plus a specimen mapping. It does not decide scoring. The module owns variable selection, candidate policy, criteria, and profile patch rules.

### 7.3 Experiment policies

Implement deterministic algorithms, not an unexplainable optimizer:

- Fixed grid for initial broad search.
- Boundary-aware range extension if the best candidate is at an edge.
- Narrowing around an interior winner with configurable factor (default 0.5 of prior span).
- Optional 1D refinement after two-dimensional screening.
- Respect machine/material constraints and user-set limits.
- Preserve tied candidates; do not discard candidate-level scoring.
- Require a final confirmation print for the chosen value unless the user explicitly opts out.
- Show why each next range was proposed and allow the user to override.

---

## 8. Detailed calibration module: ironing finish

### 8.1 Scope and outcome

Tune Orca process settings affecting the top surface produced by ironing, with primary output in the **process profile**. The result is scoped to a specific printer/nozzle, filament, process baseline/layer height, and ironing mode. A profile should not imply it is optimal for every material or geometry.

Initial candidate settings:

- `ironing_flow`
- `ironing_speed`
- `ironing_spacing`

Secondary/advanced settings only after the first result or explicit expert mode:

- `ironing_type` (for example top/topmost/solid, as supported by current Orca schema)
- `ironing_pattern`
- `ironing_angle` / fixed-angle setting
- `ironing_inset`

At baseline capture top-surface speed, top layer height, line width, number of top solid layers, filament flow ratio, nozzle temperature, cooling/fan, max volumetric speed, layer height, infill/top-solid configuration, and any relevant overrides. Avoid changing these during the primary flow/speed experiment. Warn when the top surface itself is not fully solid or when the model creates a nonrepresentative short ironing path.

### 8.2 Test coupon

Create a connected plate of repeatable square/rectangular pads with:

- Stable lower layers and enough top solid layers to represent the selected profile.
- Large, flat, fully solid top area per pad so the ironing path is long enough to evaluate.
- Small separation trenches or raised separators to identify pad edges without removing the test surface.
- A unique alphanumeric ID embossed/engraved in a non-ironed side wall or adjacent legend strip; do not put microtext on the evaluated surface.
- Corner orientation marker and row/column labels matching the app's on-screen map.
- Identical geometry across candidates; no hidden geometry changes with parameters.
- Optional duplicated baseline control pads at two plate positions to reveal bed-position/temperature bias.
- Optional randomized candidate position with a recorded seed to reduce systematic position effects. Provide deterministic fixed layout by default for simple first use.

The geometry must fit within the printer's actual usable bed polygon after origin/keep-out margins, not just the rectangular bed size. If all pads do not fit, split into plates and preserve candidate IDs.

### 8.3 Stage A: coarse flow × speed grid

Default: 3 × 3 or 4 × 4 grid. Use the imported profile value as center. Derive a conservative range from the baseline and allow explicit user bounds; do not invent material-independent constants as universally safe. Suggested defaults may be relative, e.g. several flow percentages around baseline and a speed span around the imported value, with bounds shown and editable.

For each candidate:
- Set only flow and speed at per-object process scope (or per-candidate derived process JSON fallback).
- Keep pattern, spacing, inset, angle, temperatures, fan, top-surface settings, model and filament fixed.
- Label with a short candidate ID; the detailed numeric mapping remains on the app map and machine-readable manifest.
- Add control pads using baseline values if plate area allows.

App must display the exact config matrix before the user starts slicing. If the Orca schema does not permit per-object change for one setting, use separate plates rather than silently dropping that dimension.

### 8.4 Stage B: result rubric

Use a multi-criteria rubric, each 1–5 or pass/fail:

- Surface smoothness / ridge visibility.
- Coverage (missed streaks or un-ironed islands).
- Material accumulation / blobs / edge ridges.
- Pitting, scraping, or nozzle drag.
- Gloss or matte preference (explicitly subjective and user-weighted).
- Repeatability against control pad.

User picks best candidate(s), marks unacceptable defects, and can mark uncertain. Provide pairwise comparison as a quicker optional method. Keep objective defect tags separate from preference scores. If photographs are captured, they are supporting evidence only; lighting and angle can distort gloss assessment.

### 8.5 Stage C: adaptive refinement

- If winner is in center and neighboring candidates establish a trend, narrow flow and speed ranges around best score; use halved step/span by default.
- If winner is at boundary, ask whether to extend or accept a hard bound; default propose extension if allowed.
- If ratings conflict (e.g. best gloss has more blobs), show Pareto candidates and ask user to choose criterion priority. Do not collapse criteria silently.
- If baseline control differs substantially by location, flag likely thermal/bed-position effect and recommend a repeat layout or smaller per-plate grid.
- After flow/speed is stable, optional secondary experiment varies spacing (and/or pattern) while holding winner flow/speed fixed. Do not combine flow, speed, spacing, pattern, angle, and inset in one huge grid.
- Final confirmation print uses selected values plus neighboring control/baseline. Accept only after explicit user confirmation or documented opt-out.

### 8.6 Profile patch and export

Patch only selected ironing keys into the cloned process profile, unless the current Orca version stores a particular calibrated key at another scope. Validate setting names/types against the active Orca schema and generated G-code. If flow ratio is material-wide and user explicitly chose it, it belongs in a filament preset; do not place it in process by convenience.

Export report records chosen settings, baseline values, scoring rubric, candidate winning evidence, confirmation run, known trade-offs, and applicability scope. Use a descriptive new preset name with source/process/material context but preserve Orca's naming and compatibility conventions.

---

## 9. Later calibration module: bridges

### 9.1 Staged dependency order

Bridge outcomes depend on more than a single `bridge_flow_ratio`. Tune in controlled stages:

1. Confirm ordinary extrusion/flow and stable bed adhesion.
2. Establish bridge fan/cooling and bridge speed for this filament/printer, or import trusted values and lock them.
3. Sweep bridge flow ratio with fan/speed/temp held fixed.
4. Validate chosen flow at multiple bridge spans and orientations.
5. If necessary, test bridge line width, speed, fan, or temperature one dimension at a time.
6. Optionally test top skin over bridge settings separately; do not conflate bridging strands and ironing of surfaces above bridges.

### 9.2 Geometry and observations

Coupon should include several distinct bridge lengths (short, medium, long), a couple of orientations, consistent anchor geometry, stable base walls, and clear underside access. Avoid a coupon whose bottom layers or anchors are intentionally removed in a way that turns the test into an adhesion test. Use a labeled map and specimen IDs.

Record:
- Sag/deformation at center, preferably measured with a simple reference gauge if user has one; visually score otherwise.
- Strand gaps and consistency.
- Anchor adhesion, underside quality, droop, stringing.
- Bridge length/orientation and parameter candidate.
- Optional photo with scale reference.

The first bridge module should optimize the user's selected metric with candidate sets per span. A good bridge-flow setting is often a compromise across lengths, so show tradeoffs rather than one universal score. Export only the bridge settings that were varied and accepted. Describe process scope and material conditions.

---

## 10. Later calibration module: support material/interface

### 10.1 Separate support generation from interface finish

Support tests should expose the underside finish and release behavior without accidentally varying support geometry. Phase 1 locks support style, overhang geometry, support placement, and base density, then tunes interface parameters. Candidate dimensions can include:

- Top contact Z distance / interface gap.
- Interface layer count and density/spacing.
- XY separation / support expansion near walls.
- Interface speed/flow only if Orca exposes/uses them distinctly in the selected profile.

Phase 2 can compare support style/placement for users who want broader geometry optimization. If support/interface filament is selected, treat it as a second material and surface that the tool is varying two compatible filament presets or support assignment.

### 10.2 Coupon and manual result

Use a repeatable supported overhang/underside coupon with a common base, consistent supported area, and at least two overhang angles or underside orientations. Include a support removal tab or grip feature but do not overstate force measurement unless a load cell is present.

Record:
- Underside finish (sag, scars, roughness, unsupported marks).
- Removal ease (user scale and optional measured force).
- Breakage/delamination or support fused to model.
- Support waste/estimated material and print duration.

Acceptance is a user-selected balance between surface quality and removal. One setting is not necessarily best on both. Export only the declared support/interface process keys; filament selection and support-material assignment need a separate explicit profile patch review.

---

## 11. Future camera / scanner / laser extension

Plan an optional `Analyzer` interface now, but keep it unused in v1:

```python
class Analyzer(Protocol):
    analyzer_id: str
    def required_inputs(self) -> list[InputRequirement]: ...
    def analyze(self, run: Run, inputs: list[LocalArtifact]) -> AnalysisResult: ...
```

Potential later adapters:

- Local photo capture/import and ruler/marker calibration.
- Flatbed scanner analysis inspired by ScanNTune.
- Pressure-advance camera analysis inspired by PressureAdvanceCamera, implemented locally to avoid mandatory cloud transfer.
- XY skew/camera geometry inspired by SkewCamera.
- Rubedo-like line laser structured-light experiments as an advanced hardware plugin after camera/laser calibration and safe motion handling are designed.

Every analyzer must return measurements, uncertainty/quality flags, input artifact hashes, and an explanation. Never auto-accept a process profile from a low-confidence score. A generic “camera on toolhead” is not sufficient: focus, illumination, pose, lens distortion, coordinate transform, exposure, and nozzle/camera offset need a guided calibration procedure. For a first hardware product, two individual modules are lower risk than a custom sensor board; however, this app plan does not specify or require purchasing hardware.

---

## 12. Suggested code architecture and stack

### Runtime stack

- **Python 3.12+** domain, profile parsers, experiment algorithms, SQLite repository, Orca CLI orchestration.
- **PySide6** for a native desktop shell and cross-platform file dialogs, tables, and experiment maps.
- **Pydantic** or typed dataclasses for validated internal models; raw profile documents remain lossless JSON objects.
- **SQLite** for run index, scores, user settings, migrations.
- **pytest** for unit/integration tests.
- Optional geometry generator: choose a deterministic Python CAD library or parametric OpenSCAD invocation after a spike. Do not require the user to install CAD software if packaged meshes/projects can be generated reliably.
- Package with PyInstaller/Nuitka or a platform-native packager only after Orca path discovery and subprocess execution are proven. Keep Orca as an external dependency; do not redistribute Orca binaries unless its license and packaging are deliberately reviewed.

### Module boundaries

```text
src/calibration_workbench/
  app/                    # PySide UI, navigation, view models
  domain/                 # profile, run, experiment, score types
  profiles/               # import, discovery, inheritance, export adapters
  orca/                    # executable discovery, CLI runner, G-code checks
  experiments/             # shared planner, policies, constraints
    ironing/               # definition, geometry spec, scoring, profile patch
    bridges/               # later module
    supports/              # later module
  geometry/                # project/mesh generation and label mapping
  storage/                 # SQLite, files, migrations, backup/restore
  validation/              # config validation, G-code analysis, compatibility
  reporting/               # diff, manifest, HTML/Markdown output
  analyzers/               # future offline camera/scanner adapters
  resources/               # icons, licensed original test geometry/templates
```

Keep UI widgets from embedding profile schema or calibration math. Keep Orca process execution behind a mockable runner. Keep exported profile files and input profiles out of app installation directories.

### Configuration and privacy

- Store preferences in OS app-data location; store user-selected runs in a user-chosen workspace (default under Documents).
- Never log API keys or credentials; v1 has no API keys.
- No network calls in core workflow. A future update checker may be opt-in and separate from print data.
- Allow “export diagnostic bundle” to omit user profile contents unless explicitly selected.
- Back up/restore sessions and SQLite index; files are organized so a plain filesystem backup is possible.

---

## 13. Implementation phases with exit gates

### Phase 0 — repository/license and technical spike (1–2 focused engineering weeks)

Tasks:
1. Record exact upstream repos and revisions reviewed; create `THIRD_PARTY_NOTICES.md` and a dependency/license policy.
2. Build fixture printer/filament/process profiles with inheritance, arrays, unknown keys, and realistic Orca values.
3. On Windows and Linux, discover/launch at least one supported Orca release via CLI and capture version and logs.
4. Test profile loading with inherited preset and compare effective settings against Orca UI/exported effective config. Reproduce/fail the CLI inheritance issue if present; implement resolver/flattened temp JSON approach.
5. Generate a sample 3MF with at least two labeled pads and different ironing values. Slice through Orca CLI. Inspect gcode/config to verify values change as intended.
6. Verify machine start/end G-code, bed origin, custom bed polygon/keep-outs, temperatures, retraction, and output behavior.
7. Decide how to create 3MF geometry/overrides and which Orca versions can be supported.

**Exit gate:** written test report and checked-in fixtures; no UI implementation until all tests pass or the fallback design is selected.

### Phase 1 — vertical slice / ironing MVP

1. Create desktop shell with profile selector and job progress/cancel/log screen.
2. Implement read-only profile discovery/import, inheritance resolution, provenance display, and profile hash.
3. Implement profile selector for the three presets and show baseline values.
4. Implement ironing coupon generator and exact specimen map.
5. Implement one coarse grid and call Orca CLI, with temp datadir and isolated temp configs.
6. Add G-code validation and save complete run bundle/manifest.
7. Add manual result entry with autosave/resume, defect tags, photo attachment, and tie/uncertain states.
8. Implement explainable next-grid proposal (boundary extension, range narrowing, fixed settings shown).
9. Implement final confirmation and process profile export/diff.
10. Test import/export round trip in Orca on each supported platform/version.

**Exit gate:** a user can complete the acceptance example end-to-end without changing source presets; exported candidate reimports into Orca and applies the intended ironing settings.

### Phase 2 — hardening and first public-quality release

- Add settings schema compatibility matrix and upgrade tests.
- Improve bed footprint packing and split-to-multiple-plates behavior.
- Add run comparison, search, duplicate experiment, backup/restore, and report export.
- Add explicit user bounds and per-parameter locks.
- Improve error messages for unsupported profile versions, malformed JSON, missing parent, invalid G-code, and Orca failure.
- Package Windows/Linux installers and sign as applicable.
- Add localization-ready strings and accessible keyboard navigation.
- Collect user feedback only through opt-in, anonymized diagnostics.

**Exit gate:** supported OS packaging smoke tests, no data loss through close/reopen, export/reimport regression passes.

### Phase 3 — bridge module

- Implement bridge geometry and multi-span scoring.
- Require stage order and show which imported parameters are fixed versus measured.
- Use parameter sweeps on bridge flow after fan/speed prerequisites are accepted.
- Validate export scope and profile diff.

**Exit gate:** controlled sweeps vary only declared settings and result in correct G-code for each span; a run report distinguishes span-specific tradeoffs.

### Phase 4 — support/interface module

- Implement supported underside coupons and removal rubric.
- Implement a one-variable-at-a-time candidate planner for interface distance/layers/density and optional XY separation.
- Support multi-material assignment only with explicit filament selection and profile diff confirmation.

**Exit gate:** test can measure both underside quality and removability without changing support geometry unintentionally.

### Phase 5 — optional automatic analysis

- Establish analyzer plugin API and input/calibration contracts.
- Prototype local image capture/import and/or flatbed scan analysis after manual workflow is stable.
- Treat camera and laser calibration as separate hardware setup modules; return uncertainty.
- No remote image upload without an explicit user action and explanation.

---

## 14. Testing and verification plan

### Unit tests

- Inheritance resolver: multi-level parents, child overrides, arrays, absent key, unknown key preservation, cycles, missing parent, ambiguous parent.
- Profile adapter: printer/filament/process detection; source hashes; compatibility; output name/id/compatibility fields.
- Experiment algorithms: reproducible candidate IDs; bounds; boundary extension; interval narrowing; ties; uncertain/missing observations; locked parameters; acceptance stop rule.
- Geometry mapping: stable labels and candidate-to-pad/plate lookup; bed polygon packing; rotations; origin/offset.
- Export diff: only intended keys changed; untouched raw fields preserved; exported profile references source and session.
- G-code validator: expected sections, coordinates, extrusion, temperature and start/end preservation; malformed/truncated file detection.

### Integration tests

- Invoke a real supported Orca CLI against checked-in tiny profiles and test geometry in CI where licensing/runtime permits; otherwise keep a clearly labeled local integration suite that users can run.
- Verify generated G-code contains each experiment candidate's actual settings/path differences. Do not check only app's own manifest.
- Verify a 3MF with object overrides slices correctly; retain a fixture from each supported Orca version.
- Reimport exported profile into a clean Orca data directory and verify effective settings.
- Check all G-code XY bounds against printer bed/keep-out bounds, including nonrectangular beds and nonzero origins.
- Test cancellation, timeout, invalid executable, permission errors, missing output, disk-full handling, and crash recovery.

### Manual product tests

- User can locate baseline settings without entering values.
- Test map physically matches G-code labels/orientation.
- The same run can be closed and resumed without losing partial scores/photos.
- App does not change original Orca profile files or user configuration.
- User understands which values are changed and which are held constant.
- Exported profile is importable and has the intended name/scope.

### Release checks

- SPDX/license inventory includes app dependencies, bundled meshes, fonts, icons, and generated models.
- Provide reproducible sample printer/profile fixtures that contain no user data.
- Ensure no internet connection is needed for profile import, slicing, scoring, or export.

---

## 15. Reliability, safety, and user trust

- Require an explicit review before export; generated G-code must be inspected in Orca or another viewer before printing, especially on a new printer profile.
- Do not auto-send/auto-start prints. Never use printer motion for calibration in v1.
- Before marking G-code valid, check bounds, temperatures, first layer, start/end macros, selected tool, and correct nozzle/bed limits. Report checks; do not claim they guarantee a safe print.
- Keep profile inputs immutable and make all derived settings visible.
- Make every recommendation explainable: “the winner was at the upper flow boundary, so the next range extends upward by X” is preferable to “AI optimized it.”
- If settings interact, split into stages and retain the earlier values in the experiment manifest.
- Preserve uncertainty, ties, and subjectivity. A high gloss rating is a preference, not an instrument reading.
- Save all state incrementally. A crash must not erase a recorded observation.
- Use filesystem-safe output naming and never overwrite a source profile. Warn before replacing an existing export file.

---

## 16. Risks and mitigations

| Risk | Why it matters | Mitigation |
|---|---|---|
| Orca CLI inheritance differs by version | Wrong effective config can produce believable but invalid tests. | Resolve profiles explicitly, use isolated datadir, version adapters, fixture tests, effective-config verification. Treat upstream issue as a release blocker until tested. |
| 3MF object overrides are ignored or altered headlessly | The labeled grid may not actually vary its parameter. | Phase 0 spike; inspect output paths/config; fallback to separately sliced candidate plates with derived configs. |
| Test object is not representative | One flat coupon can overfit; physical finish depends on surface length, layer height, material and geometry. | State scope, include robust top surface, require confirmation print and representative part validation. |
| Multiple settings are confounded | A “best” pad cannot reveal which variable caused improvement. | Stage experiments; show fixed/varying variables; one-factor follow-up when needed. |
| Subjective gloss is lighting-dependent | Users can choose inconsistent values. | Provide user-defined weights, defect tags, optional photos; avoid pretending camera measurements are objective. |
| Export profile loses settings/compatibility | Orca may omit unknown keys or resolve inheritance differently. | Preserve raw JSON, compare diff, reimport in clean Orca, versioned exporter. |
| Bed bounds or printer origin are misread | Test could leave usable area. | Bed polygon/keep-out support, bounds validator, G-code simulation checks, conservative margins, manual preview requirement. |
| License obligations from example projects | Some are AGPL/GPL, custom noncommercial, or have model-specific terms. | Build original modules; track notices; do exact asset/license audit before copying. |
| Camera/laser analysis adds cost and calibration work | Hardware could delay the basic useful tool. | Keep hardware optional and after stable manual workflow; no required camera purchase in MVP. |

---

## 17. Coding-agent execution instructions

The implementation agent should work in this order and keep each phase reviewable:

1. Read this plan and create a short architecture decision record for profile resolution, geometry generation, and supported Orca versions.
2. Start with Phase 0 and produce a reproducible CLI/3MF spike. Do not build the complete UI until its exit gate passes.
3. Create test fixtures and a license inventory before copying any repository code or geometry.
4. Implement typed domain models and persistence before UI polish.
5. Build one end-to-end ironing experiment slice before adding bridge/support features.
6. Keep every calibration result tied to profile hashes and the exact Orca version.
7. Run unit and relevant integration tests before each phase exit; include a short verification log in the repository.
8. Do not silently use profile defaults when values fail to resolve. Stop the run with a clear missing/ambiguous setting report.
9. Do not alter, activate, upload, or install profiles automatically. User explicitly reviews export path and diff.
10. After MVP, present a working app and test artifacts for review; defer optional camera, firmware, and printer-network integrations until requested.

### Definition of done for the first usable release

- The application opens on Windows and Linux and detects or accepts a user-selected Orca installation.
- It imports the user's existing three profile types without requiring manual parameter transcription.
- It produces a labeled ironing G-code test through Orca CLI with verified candidate-specific paths/settings.
- The user can print, record manual results, resume later, and generate a second adaptive test.
- It exports a new, reimportable Orca process profile and a readable diff/report.
- It preserves source profiles and all session files; repeated runs are reproducible from the manifest.
- It offers a documented pathway to add bridge and support experiments without changing the app's core architecture.

---

## 18. Sources and repository index

Primary repository pages reviewed for this plan:

- [4o66/pa-helper](https://github.com/4o66/pa-helper)
- [piledge/Print-Calibration-Tool](https://github.com/piledge/Print-Calibration-Tool)
- [distractable2/PrintWise](https://github.com/distractable2/PrintWise)
- [Knifa/tower-tool](https://github.com/Knifa/tower-tool)
- [thbitzer/SmartTemperatureTower](https://github.com/thbitzer/SmartTemperatureTower)
- [jaak0b/ScanNTune](https://github.com/jaak0b/ScanNTune)
- [5axes/Calibration-Shapes](https://github.com/5axes/Calibration-Shapes)
- [Sgail7/The-Ultimate-Filament-Tuning-Guide](https://github.com/Sgail7/The-Ultimate-Filament-Tuning-Guide)
- [CameronBrooks11/parametric-calibration-objects](https://github.com/CameronBrooks11/parametric-calibration-objects)
- [kartchnb/AutoTowersGenerator](https://github.com/kartchnb/AutoTowersGenerator)
- [dirtdigger/fleur_de_cali](https://github.com/dirtdigger/fleur_de_cali)
- [supermerill/SuperSlicer](https://github.com/supermerill/SuperSlicer)
- [undingen/PressureAdvanceCamera](https://github.com/undingen/PressureAdvanceCamera)
- [undingen/SkewCamera](https://github.com/undingen/SkewCamera)
- [furrysalamander/rubedo](https://github.com/furrysalamander/rubedo)
- [Anonoei/klipper_auto_speed](https://github.com/Anonoei/klipper_auto_speed)
- [andrewmcgr/klipper_tmc_autotune](https://github.com/andrewmcgr/klipper_tmc_autotune)
- [protoloft/klipper_z_calibration](https://github.com/protoloft/klipper_z_calibration)
- [nic335/Axiscope](https://github.com/nic335/Axiscope)
- [TypQxQ/kTAMV](https://github.com/TypQxQ/kTAMV)
- [viesturz/NozzleAlign](https://github.com/viesturz/NozzleAlign)
- [OrcaSlicer CLI mode documentation](https://github.com/OrcaSlicer/OrcaSlicer/wiki/cli_mode)
- [OrcaSlicer CLI flags documentation](https://github.com/OrcaSlicer/OrcaSlicer/wiki/cli_misc)
- [OrcaSlicer profile guide](https://github.com/OrcaSlicer/OrcaSlicer/wiki/how_to_create_profiles)
- [OrcaSlicer user profile locations](https://github.com/OrcaSlicer/OrcaSlicer/wiki/user_profiles)
- [OrcaSlicer ironing settings](https://github.com/OrcaSlicer/OrcaSlicer/wiki/quality_settings_ironing)
- [OrcaSlicer CLI inherited-profile issue #14718](https://github.com/OrcaSlicer/OrcaSlicer/issues/14718)

Research is repository/documentation-level, not a claim that every project was installed and exercised on the user's printer. Phase 0 exists to convert the highest-risk assumptions into reproducible tests before implementation expands.
