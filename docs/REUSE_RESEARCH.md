# Reuse Research

Reviewed for Calibrate-3DP V1 planning and the grouped-settings gate on 2026-10-09. The goal is to reuse reliable pipeline components where they fit, while keeping the app's experiment compiler, dependency model, geometry, and traceability under this repository's control.

## Summary

No reviewed project supplies the complete combination required here: Orca profile intake and inheritance, dependency-aware experiment planning, grouped and labeled plates, local printer history, manual assessment, and reviewed Orca profile export.

The best reuse is narrow:
1. Keep the current Orca CLI, profile-resolution, job isolation, manifest, and session code as the pipeline base.
2. Use Orca's calibration documentation and settings as versioned reference inputs for the module catalog.
3. The first-party standard-library writer now packages nine connected, settings-bearing sample meshes and a separate code-bearing frame object. The connected 3MF passes real-Orca G-code validation for all nine samples on one recorded executable/profile combination; it does not establish physical print/handling acceptance or broad support.
4. The original schema-v1 axis-aligned plates use the first-party `stdlib-voxel` backend. The 2026-10-09 package-footprint decision kept build123d out of the default install; the scoped schema-v2 ironing migration below now makes it an optional, exactly pinned CAD extra for pocketed labels and a separate plaque. Historical schema-v1 records retain voxel semantics.
5. Use tower and coupon repositories as geometry references. Check every model's license separately before redistributing assets.

## Experiment configuration proposal

The separate [Experiment Configuration and Generation Proposal](EXPERIMENT_CONFIGURATION_PROPOSAL.md) recommends a typed, versioned configuration, an immutable compiled manifest, a replaceable Python geometry backend, and a separate Orca settings/slicing adapter. Schema-v1 runs retain the first-party 3MF and `stdlib-voxel` adapters; new schema-v2 ironing configurations select `build123d@1` while retaining the same Orca-specific settings writer. The connected Orca round-trip passes for one recorded environment, but physical readability/handling and the broader support matrix remain open.

## Python geometry candidates

- [build123d](https://github.com/gumyr/build123d) is Apache-2.0 and documents parametric Python BREP modeling with STL and STEP export ([import/export docs](https://build123d.readthedocs.io/en/stable/import_export.html)). The 2026-10-09 Windows Python 3.11 probe installed version 0.13.0 into an isolated venv and measured 721,973,890 bytes. The 2026-10-10 pinned environment recorded 721,741,362 bytes (688.3 MiB), 59 distributions, and 225.4 MB of reported wheels; `cadquery-ocp-novtk` was a 47.5 MB wheel. The geometry implementation uses the library's BREP, Boolean, explicit-font text, and tessellation APIs. Since the install footprint is large for the current default app, both pinned runtime distributions live behind optional extra `[cad]`: `build123d==0.13.0` and `cadquery-ocp-novtk==8.0.1.1.0`. No external build123d code or assets were copied into the repository.
- [CadQuery](https://github.com/CadQuery/cadquery) is Apache-2.0, Python parametric CAD over OpenCascade, with documented STL/STEP export ([docs](https://github.com/CadQuery/cadquery/blob/master/doc/importexport.rst)). It was not installed: its current official [setup.py](https://github.com/CadQuery/cadquery/blob/master/setup.py) lists `cadquery-ocp` and additional scientific, native, and visualization dependencies. Given the build123d package cost and the exact axis-aligned requirements, no evidence justified spending another large install to compare it.
- [lib3mf](https://github.com/3MFConsortium/lib3mf) is BSD-2-Clause and may help with standard 3MF packaging. It is not the geometry modeler or Orca-specific metadata adapter.

The original schema-v1 backend uses a 0.4 mm voxel grid and emits only the surface triangles of each occupied solid. Each A–I specimen is independently watertight with its bitmap label and eight matching breakaway tabs; the separate `Plate-Frame` carries the legacy code relief. Those records keep their original meanings after migration.

## Scoped build123d geometry decision (2026-10-10)

New schema-v2 grouped-ironing configurations use `build123d@1` and `ironing.flat_coupon@2`. The backend creates nine samples with a pocketed positive-relief underside label, a separate top-labeled corner plaque, two plaque links, and a distinct grid frame; a nine-sample plate contains eleven named 3MF objects. The schema-v2 serializer pins font identity/hash, native CAD versions, recipe/layout versions, and tessellation tolerances. SQLite schema v4 accepts immutable config snapshot versions 1 and 2; schema-v1 snapshot decoding and voxel geometry remain unchanged. The wider schema/backend/storage split is still undecided.

The selected IBM Plex Mono SemiBold TTF is an official upstream asset pinned to commit `763c36ef9117782905ae010056dfbe8fd2653a25`, SHA-256 `f04d7c488ddf7d1fa99f2574efc3406ea4cbe17bb1af3a1ab960f84d0c96a172`, under the SIL Open Font License 1.1. Its exact source is `packages/plex-mono/fonts/complete/ttf/IBMPlexMono-SemiBold.ttf`; the matching license and attribution ship beside it in `src/calibrate3dp/assets/fonts/`. This is the source and license record for the only copied external asset.

The opt-in benchmark script measures fresh-process imports, full nine-sample geometry construction/validation, peak Windows working set, binary STL output, and first-party 3MF output. In the recorded Python 3.11.4/Windows 10 environment it measured 4.055 s median build123d-plus-backend import, 2.447 s geometry construction, 515,461,120 byte peak working set, 15,620 STL triangles / 781,924 STL bytes, and 149,050 bytes for the 3MF. One exact local Orca run validated all eleven objects, the shared layout, nine settings overrides and sample/plaque text paths; an omitted Sample-E override failed. Its G-code and captured Orca logs also contain `Unable to create exclude triangles`; the validator results are slicer-output evidence, not physical print acceptance. No physical print, legibility, adhesion, or tab-separation test has been performed, and `print_ready` remains false.

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
