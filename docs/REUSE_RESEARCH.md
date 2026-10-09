# Reuse Research

Reviewed for Calibrate-3DP V1 planning and the grouped-settings gate on 2026-10-09. The goal is to reuse reliable pipeline components where they fit, while keeping the app's experiment compiler, dependency model, geometry, and traceability under this repository's control.

## Summary

No reviewed project supplies the complete combination required here: Orca profile intake and inheritance, dependency-aware experiment planning, grouped and labeled plates, local printer history, manual assessment, and reviewed Orca profile export.

The best reuse is narrow:
1. Keep the current Orca CLI, profile-resolution, job isolation, manifest, and session code as the pipeline base.
2. Use Orca's calibration documentation and settings as versioned reference inputs for the module catalog.
3. The first-party standard-library writer now packages nine connected, settings-bearing sample meshes and a separate code-bearing frame object. The connected 3MF passes real-Orca G-code validation for all nine samples on one recorded executable/profile combination; it does not establish physical print/handling acceptance or broad support.
4. Use the first-party `stdlib-voxel` backend for the current axis-aligned connected plate. The isolated Windows build123d 0.13.0 install/import probe succeeded but occupied 721,973,890 bytes in `Lib/site-packages` (about 688.5 MiB, 45 distributions); that packaging cost is not justified for this geometry. Keep a narrow adapter so a later curved/boolean design can revisit CAD libraries. The backend exports binary STL and a separate-object 3MF package with Orca-specific settings metadata and no new runtime dependency.
5. Use tower and coupon repositories as geometry references. Check every model's license separately before redistributing assets.

## Experiment configuration proposal

The separate [Experiment Configuration and Generation Proposal](EXPERIMENT_CONFIGURATION_PROPOSAL.md) recommends a typed, versioned configuration, an immutable compiled manifest, a replaceable Python geometry backend, and a separate Orca settings/slicing adapter. The current workflow adopts the SQLite snapshot/per-run artifact split for printer, material, plan, and sample data, plus first-party 3MF and `stdlib-voxel` adapters. The connected Orca round-trip now passes for nine sample objects, but human readability/handling and the broader support matrix remain open.

## Python geometry candidates

- [build123d](https://github.com/gumyr/build123d) is Apache-2.0 and documents parametric Python BREP modeling with STL and STEP export ([import/export docs](https://build123d.readthedocs.io/en/stable/import_export.html)). The Windows Python 3.11 probe installed version 0.13.0 into an isolated venv: import and `Shape.tessellate` resolved, but `Lib/site-packages` measured 721,973,890 bytes. `cadquery-ocp-novtk` was a 47.5 MB wheel; the dependency set also included NumPy, SciPy, scikit-learn, ezdxf, `lib3mf`, and `threejs-materials`. No build123d geometry was shipped or copied into the application.
- [CadQuery](https://github.com/CadQuery/cadquery) is Apache-2.0, Python parametric CAD over OpenCascade, with documented STL/STEP export ([docs](https://github.com/CadQuery/cadquery/blob/master/doc/importexport.rst)). It was not installed: its current official [setup.py](https://github.com/CadQuery/cadquery/blob/master/setup.py) lists `cadquery-ocp` and additional scientific, native, and visualization dependencies. Given the build123d package cost and the exact axis-aligned requirements, no evidence justified spending another large install to compare it.
- [lib3mf](https://github.com/3MFConsortium/lib3mf) is BSD-2-Clause and may help with standard 3MF packaging. It is not the geometry modeler or Orca-specific metadata adapter.

The chosen first-pass backend uses a 0.4 mm voxel grid and emits only the surface triangles of each occupied solid. Each A–I specimen is independently watertight with its label and eight matching breakaway tabs; the separate Plate-Frame is watertight and includes the code relief. Tests verify deterministic meshes, bounds/keep-outs, 2.4 × 0.8 mm connector cross-sections, 2.8 mm link clearance, a minimum 1.92 mm² contact face, binary STL serialization, and per-object 3MF mapping. This is geometric evidence only; it does not establish physical legibility, handling strength, Orca slicing behavior, or cross-platform package behavior. The 3MF test checks IDs and settings metadata; the real Orca gate is still required.

## Reviewed projects

### Calibrate-3DP main

Source: https://github.com/MarcusFunt/Calibrate-3DP/tree/c899021daa38c990bcd81466b4b9a149539eaef8

At the reviewed origin/main commit, the code had Orca profile import and inheritance resolution, deterministic candidate plans, manual assessments, isolated CLI invocation, output hashes, manifests, persistence, and profile export. The implementation worktree now adds persistent printer/material/run snapshots, the connected nine-sample plate, a reconciled identity-label mapping for the tested executable, and the real Orca/Qt generation path. Preserve and generalize these boundaries. The broader compiler and module registry, end-to-end assessment/refinement/export UI, and cross-platform support remain open.

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
