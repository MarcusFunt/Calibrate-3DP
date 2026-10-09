# Calibrate-3DP V1 Goal

## Product goal

Calibrate-3DP V1 is a local desktop workbench for planning, generating, tracking, and manually evaluating FDM printer and filament calibration experiments. It uses imported OrcaSlicer profiles as the baseline and OrcaSlicer as the final slicing engine. It keeps calibration history tied to a printer, material, profile snapshot, experiment, plate, and individual sample.

The app should make calibration reproducible: a printed plate can be identified later, every sample maps to the exact settings used, and the interface explains what has been tested, what is ready, what is blocked by a prerequisite, and what has become stale.

## V1 user workflow

1. Open the app and select a saved printer or import its Orca machine profile.
2. Select or import a filament and process profile. The app shows resolved values, inheritance provenance, compatibility warnings, and profile fingerprints.
3. Open that printer's calibration workspace. See prior calibration results and the dependency graph, with untested, ready, blocked, in-progress, accepted, and stale states.
4. Choose a calibration that is ready, review its assumptions and fixed settings, then generate a structured experiment.
5. Review the exact candidate settings and plate layout before slicing. The app assigns each physical plate a unique six-character code and each sample a short label, initially A through I for a nine-sample layout.
6. Generate the project and G-code through OrcaSlicer. Validate the output against the planned sample settings and retain the effective profiles, application and slicer versions, logs, commands, and artifact hashes.
7. Print using the user's normal printer workflow. The app does not send the job to the printer.
8. Enter manual observations for each sample. Record acceptance, defects, notes, and the selected candidate. The app proposes a follow-up experiment and explains which earlier results remain applicable.
9. Review a profile diff and export a new Orca-compatible profile or bundle. The app never changes the source profile in place.

Entering a six-character plate code in the GUI must resolve the plate, printer, material, run, sample labels, exact tested settings, generated artifacts, and assessment history. Codes remain unique and resolvable across application upgrades.

## Calibration coverage

V1 includes an end-to-end manual workflow for:

- Nozzle temperature.
- Flow ratio / extrusion multiplier.
- Maximum volumetric speed.
- Pressure advance.
- Retraction.
- Ironing.
- Bridge quality.
- Support interface and support removal.

A calibration definition may be withheld for a printer or slicer combination when its settings cannot be applied and validated safely. The interface must explain why.

Ironing is a full calibration family, not only a flow and speed sweep. Its definition must cover the applicable Orca settings: ironing type, pattern, flow, line spacing, inset, angle offset, fixed-angle behavior, and speed. Plans use staged experiments and declared interactions so a large factorial grid does not waste material. Each plan records the held-constant settings and why the selected factors are being varied.

The compiler supports test-specific layouts. Flat specimens such as ironing and flow samples should be grouped into connected grids or zones where their settings can be applied independently. Temperature tests may use a tower or another geometry with explicit temperature changes by height. Bridge and support tests use geometry that exposes the relevant quality or removal criteria.

## Plate identity and physical handling

- Every generated physical plate has one unique, human-readable six-character code.
- Every sample has a compact printed label that maps to its settings in the app.
- The plate is designed to keep samples connected during plate removal, handling, and storage. Samples can be separated deliberately with a hand tool when inspection requires it.
- The app stores the full plate-to-sample map and never relies on the physical order alone after detachment.
- Each generated revision receives a new code. Earlier codes continue to resolve to their original immutable run.
- If an experiment needs more than one physical plate, each plate receives its own code and shares an experiment identifier.

## Experiment configuration and reproducibility

Every generated experiment must retain a versioned machine-readable plan snapshot, resolved sample-to-setting map, source profile fingerprints, tool versions, and generated artifact hashes. Plate-code lookup must recover that evidence after application upgrades. Deterministic generation must be qualified by the exact plan, compiler and geometry versions; stored artifacts and hashes remain the definitive record. The bounded SQLite snapshot/artifact split and standard-library 3MF proof path are recorded implementation decisions; the wider architecture proposal in [Experiment Configuration and Generation](EXPERIMENT_CONFIGURATION_PROPOSAL.md) remains partly open and does not change the requirements in this goal.

## Dependency and history behavior

The dependency graph is contextual rather than a universal checklist. A definition declares the profile values, prior results, slicer capabilities, and hardware/material context it assumes. The app records those inputs with every result. When a relevant input changes, dependent results become stale with an explanation; unrelated results remain current.

The graph distinguishes a prerequisite from a useful recommendation. A blocked test can be overridden only through an explicit user decision recorded with its reason. The app must never silently treat an unknown or stale result as a valid prerequisite.

## V1 boundaries

V1 is local-first and manual. It does not include a camera workflow, automatic measurements, computer vision, automatic scoring, printer control, upload/print services, cloud accounts, firmware configuration changes, or automatic profile activation. There is no requirement to build a physical marker for camera recognition in V1.

The app does not replace OrcaSlicer as a slicer. Orca remains responsible for final G-code. The app does not claim compatibility with a slicer version or printer profile until that combination has passed its recorded validation checks.

## Acceptance criteria

V1 is complete when all of the following pass on the declared supported platform and Orca version matrix:

1. A user can import or select saved printer, filament, and process profiles without modifying Orca's source files.
2. The printer workspace displays prior calibration results and correct untested, ready, blocked, accepted, and stale states.
3. Each listed calibration family can generate a versioned plan, apply its settings, produce valid output, accept manual assessments, preserve its history, and export only reviewed values.
4. The compiler can group multiple independently configured samples on a plate where the slicer path supports it. The G-code validator proves that each sample received its planned settings. If an adapter cannot prove this, generation is blocked.
5. Each physical plate has a unique six-character code; every sample has a printed label; code lookup recovers the exact plan, profiles, settings, artifacts, and results.
6. Plate connectors survive ordinary removal and handling while remaining deliberately separable with a hand tool. This is verified with physical prints and recorded material, orientation, and connector revision.
7. Failed, canceled, incomplete, or unverified slicing never produces a plate presented as ready to print.
8. The application can be closed and reopened without losing printer records, sessions, results, artifacts, or code lookup.
9. Profile export shows the source and destination values, provenance, supporting result, and compatibility status before writing a new profile.
10. Automated tests pass, real-Orca integration evidence is recorded for each supported matrix entry, and the progress history records commands, outcomes, decisions, and assumptions.
