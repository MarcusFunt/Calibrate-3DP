# Reuse Research

Reviewed for Calibrate-3DP V1 planning and the grouped-settings gate on 2026-10-09. The goal is to reuse reliable pipeline components where they fit, while keeping the app's experiment compiler, dependency model, geometry, and traceability under this repository's control.

## Summary

No reviewed project supplies the complete combination required here: Orca profile intake and inheritance, dependency-aware experiment planning, grouped and labeled plates, local printer history, manual assessment, and reviewed Orca profile export.

The best reuse is narrow:
1. Keep the current Orca CLI, profile-resolution, job isolation, manifest, and session code as the pipeline base.
2. Use Orca's calibration documentation and settings as versioned reference inputs for the module catalog.
3. The first per-object 3MF settings gate now passes on one installed Orca/profile combination. A first-party standard-library writer packages separate coupon objects and Orca metadata; it passed real-slicer G-code checks. It does not yet build connected breakaway geometry.
4. Keep build123d as a candidate for connected coupon CAD, with CadQuery as a comparison; keep the geometry backend replaceable. Consider lib3mf for standard 3MF mesh/package handling only if it materially reduces code and its Python packaging is supportable. It does not replace Orca-specific project settings.
5. Use tower and coupon repositories as geometry references. Check every model's license separately before redistributing assets.

## Experiment configuration proposal

The separate [Experiment Configuration and Generation Proposal](EXPERIMENT_CONFIGURATION_PROPOSAL.md) recommends a typed, versioned configuration, an immutable compiled manifest, a replaceable Python geometry backend, and a separate Orca settings/slicing adapter. The current workflow adopts the SQLite snapshot/per-run artifact split for printer, material, plan, and sample data, and uses its first-party 3MF writer for grouped settings. The full canonical schema, connected CAD backend, physical labels, and broader support matrix remain open.

## Python geometry candidates

- [build123d](https://github.com/gumyr/build123d) is Apache-2.0 and documents parametric Python BREP modeling with STL and STEP export ([import/export docs](https://build123d.readthedocs.io/en/latest/import_export.html)). Prototype it first for connected coupon geometry and text labels, but validate platform installation and Orca interchange.
- [CadQuery](https://github.com/CadQuery/cadquery) is Apache-2.0, Python parametric CAD over OpenCascade, with documented STL/STEP export ([docs](https://cadquery.readthedocs.io/en/latest/importexport.html)). It is a comparison candidate if it proves simpler or more robust in packaging or geometry tests.
- [lib3mf](https://github.com/3MFConsortium/lib3mf) is BSD-2-Clause and may help with standard 3MF packaging. It is not the geometry modeler or Orca-specific metadata adapter.

Both CAD options use an OpenCascade-based native geometry stack; packaged dependency size, wheel/platform availability, text handling, mesh reliability, and round-trip behavior remain to be measured in this project. Do not select a runtime dependency from documentation alone.

## Reviewed projects

### Calibrate-3DP main

Source: https://github.com/MarcusFunt/Calibrate-3DP/tree/c899021daa38c990bcd81466b4b9a149539eaef8

At the reviewed origin/main commit, the code has Orca profile import and inheritance resolution, deterministic candidate plans, manual assessments, isolated CLI invocation, output hashes, manifests, persistence, and profile export. The feature worktree adds persistent printer/material/run snapshots and the first grouped sample path; its manifest captures an unreconciled CLI/G-code identity mismatch. Preserve and generalize these boundaries. The broader compiler and module registry, connected plate generator, and end-to-end assessment/export UI are still missing.

### OrcaSlicer

Sources:
- https://github.com/OrcaSlicer/OrcaSlicer/wiki/calibration_guide
- https://github.com/OrcaSlicer/OrcaSlicer/wiki/cli_mode
- https://github.com/OrcaSlicer/OrcaSlicer/wiki/cli_misc
- https://www.orcaslicer.com/wiki/print_settings/quality/quality_settings_ironing

The official guide documents temperature, maximum volumetric speed, pressure advance, flow, retraction, cornering, input shaping, VFA, and tolerance. It provides a practical suggested sequence. The Klipper pressure advance guidance separately states that temperature and extrusion rate can affect PA and recommends tuning extruder rotation distance and nozzle temperature first. Treat ordering as contextual rules with provenance, not a universal hard-coded chain.

Orca is already the final slicer in the repository. Reuse its CLI and profile model through the existing adapters; do not embed its C++ slicing engine into this Python application. Orca's settings and project formats still require version-specific capability tests.

### PA-Helper

Source and license: https://github.com/4o66/pa-helper and https://github.com/4o66/pa-helper/blob/main/LICENSE

PA-Helper generates an Orca-readable 3MF with per-pad ironing flow/speed overrides and a visible grid, and supports manual pad selection/result persistence. Its README labels the software early beta. Its code is AGPL-3.0; its method notes describe reverse-engineering the Orca/Bambu-style 3MF project metadata. Its base ironing model is separately attributed CC0.

Use it as the closest behavioral and file-format reference. The project has independently implemented the required 3MF object-settings mapping with Python's standard library and proved sample flow/speed in Orca output, without copying PA-Helper code or assets. The current project still does not meet V1's connected, physically labeled, code-bearing plate requirement. The real-slicer proof applies only to its recorded Windows installation and profile set.

### lib3mf

Source and license: https://github.com/3MFConsortium/lib3mf

The official library provides cross-platform 3MF reading, writing, conversion, and validation under BSD-2-Clause. It may reduce work on the standard 3MF model/package layer. It is a native library with Python bindings, so assess wheel availability, installation size, and platform support before adding it.

A general 3MF library does not automatically create Orca's project-specific per-object settings. The app still needs its own Orca metadata adapter and end-to-end output validation. Do not add lib3mf as a runtime dependency before a small packaging and round-trip prototype proves value.

### tower-tool

Source and license: https://github.com/Knifa/tower-tool

This React project includes calibration tower concepts and shapes for temperature, retraction, acceleration/jerk, and flow. It is GPL-3.0 and does not provide Orca profile handling, printer history, or a general experiment compiler.

Use it to compare test coverage and geometry concepts. Do not use it as the application base. Review the license of individual mesh assets before redistribution.

### ScanNTune

Source and license: https://github.com/jaak0b/ScanNTune

This MIT-licensed project includes parametric coupon geometry and workflows for pressure advance and extrusion multiplier among other calibration families. Its main product uses flatbed scanner analysis. That measurement workflow is outside V1.

Use only the coupon and parameterization ideas that fit manual assessment. Do not add scanner or automatic measurement features to V1.

### FullControl

Source and license: https://github.com/FullControlXYZ/fullcontrol

FullControl is a Python toolpath/G-code design system under GPL-3.0. It directly designs printer paths and would bypass the desired Orca final-slicing path for most generated geometry.

Keep it as a reference for procedural toolpath concepts. It is not a V1 pipeline dependency.

## Reuse decision rules

- Keep the calibration compiler and dependency graph first-party and testable.
- Prefer current code, standards, and documented interfaces over copying an entire neighboring application.
- Record exact repository, revision, file path, license, attribution, and modifications for every reused code or asset.
- Do not copy AGPL/GPL code into the application without an explicit license decision.
- Do not redistribute a model just because a generator's source code is open.
- Add a third-party runtime dependency only after recording packaging impact, supported platforms, license, and a passing round-trip/integration test.
