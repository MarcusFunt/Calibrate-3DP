# Implementation Status

## Snapshot

This status describes origin/main at commit 5ef7c4cf04a9b5da84bb570c9276cca98d1427d9, reviewed on 2026-10-09. It separates source implementation from the V1 target in docs/V1_GOAL.md.

## Implemented foundation

- Python >=3.11 package with a headless core and no required runtime dependencies.
- Orca JSON and ZIP profile bundle intake with unknown-key preservation, inheritance resolution, source provenance, compatibility warnings, and fully resolved candidate profile output.
- Deterministic experiment grids and versioned JSON plans/results.
- Manual assessment records and ironing flow/speed refinement.
- One simple, identical top-surface STL coupon per ironing candidate.
- Isolated Orca CLI jobs with profile snapshots, cancellation, logs, G-code hashes, and manifests.
- Profile setup/import, session review, result entry, recommendation, confirmation, profile export, home, sessions, and settings screens in a Dear PyGui shell.
- SQLite session index with per-session artifact directories and path validation.
- Generation service connected to the desktop workflow; Start is gated by a successful CLI capability probe.

## Not implemented for V1

- PySide6/Qt user interface and saved printer/material library.
- Printer-centric calibration workspace and status graph.
- Generic versioned calibration definitions or module registry.
- Contextual dependency graph, invalidation, and stale-result propagation.
- Compiler request and immutable compiled-plan representation.
- Grouped plate/layout generator, plate-code coupon, and sample labels.
- Verified Orca per-object/per-sample settings in a generated 3MF.
- V1 temperature, flow ratio, maximum volumetric speed, pressure advance, retraction, bridge, and support modules.
- Full ironing test over type, pattern, flow, spacing, inset, angle, fixed-angle behavior, and speed.
- Plate-code lookup, sample-level result history, broad printer/material calibration database, and evidence-backed supported-version matrix.

## Evidence recorded by main

- The status at this baseline reports 137 headless tests passed in a clean environment without Dear PyGui; one opt-in installed-Orca test was skipped.
- The 2026-10-08 Windows spike reports nine candidate ironing jobs sliced successfully using stock Creality Ender-3 V2 machine/process/filament profiles. It reports checking ironing markers and expected flow/speed values, start/end code, temperatures, and bounds for that tested setup.
- The spike found an Orca identity mismatch: the CLI help banner reported OrcaSlicer-01.10.01.50 while generated G-code identified OrcaSlicer 2.3.0. The cause is unresolved.
- A separate Bambu A1 profile probe failed Orca layer-G-code validation after both direct and flattened profile loading; Bambu support is not claimed.
- On 2026-10-09, the currently detected executable returned no text for --help, so the repeated integration attempt stopped before slicing. Start remains blocked until the capability probe succeeds.
- The real Orca support matrix, Orca UI/effective-config equivalence, per-object 3MF overrides, cross-platform GUI acceptance, and complete import-to-export acceptance remain open.

The evidence above is copied from the implementation status and spike reports at the snapshot. It was not rerun as part of the documentation refresh.

## V1 completion gate

Use the acceptance criteria in docs/V1_GOAL.md and the task sequence in IMPLEMENTATION_PLAN.md. A task is complete only when its implementation, test evidence, recorded assumptions, and progress-history entry are present. No untested Orca/profile combination may be presented as supported.
