# Experiment Configuration and Generation Proposal

**Status: evaluated proposal, not an adopted V1 requirement.** This records a design recommendation for the experiment configurator/compiler. The user asked that it be evaluated and explicitly said it is only a suggestion. Do not implement these choices as settled product requirements without a later decision.

## Assessment

The core idea is strong: save a complete, versioned experiment description and generate geometry and slicer inputs from that description. This gives the project one auditable source for what a plate was meant to test, makes code lookup useful after printing, and lets the UI, compiler, and future import/export tools share the same data contract.

The main adjustment I recommend is to avoid treating “the configuration file”, database row, CAD model, Orca project, and G-code as one artifact. They have different roles and lifetimes. Use one typed configuration model, persist its canonical snapshot and the compiled sample map in the database, and treat geometry, Orca project, G-code, logs, and previews as immutable output artifacts linked by hash. A portable JSON representation can be exported/imported, but it should serialize the same schema rather than become a second source of truth.

This approach also avoids saving large mesh and G-code binaries inside SQLite. The database still contains the full structured plate definition, generated sample mapping, versions, profile snapshots/fingerprints, code, and artifact hashes/locations. The actual generated files can live in an app-managed artifact directory, with checksums and recovery checks in the database.

## Recommended separation of responsibilities

1. **Experiment configurator:** PySide6 views and forms gather module choice, factors, candidate ranges, fixed context, plate layout, sample labels, and applicable dependency overrides. It emits typed data; it does not generate CAD scripts or write Orca files itself.
2. **Experiment configuration:** A versioned, schema-validated value object captures the user's request: printer/material/profile snapshot IDs, calibration definition/version, chosen factors and options, layout strategy and dimensions, candidate count/order, deterministic seed, and explicit overrides.
3. **Compiler:** A headless service validates dependencies and capabilities, expands factors into concrete candidate samples, resolves held-constant settings, assigns sample mappings, and returns an immutable compiled experiment. The same compiler contract serves both the GUI and any future command-line or import/export path.
4. **Geometry backend:** A replaceable Python geometry adapter turns the compiled plate layout into connected grids, zones, towers, labels, and deliberate separation features. A “connected: on/off” toggle alone is too weak: connector shape, width, thickness, clearance, and separation mode affect handling and must be typed and validated.
5. **Orca adapter:** Separately packages/imports the geometry, associates samples with supported per-object or per-height setting changes, invokes Orca, then validates the actual G-code against the compiled sample map. Geometry generation must not imply that Orca applied the requested settings.
6. **Experiment history/inspect view:** A searchable Experiments workspace accepts a six-character plate code and displays the immutable configuration, compiled candidates, profiles, geometry and G-code artifacts, slicer validation, manual observations, and later refinement links. A revised configuration creates a new experiment/plate revision and code; it does not overwrite prior evidence.

The logical flow is:

`ExperimentConfig` → dependency/capability validation → `CompiledExperiment` → plate layout → Python geometry backend → Orca adapter and slice → output validation → immutable run/artifacts → manual assessment.

## Determinism and record identity

“Deterministic” should mean that the same normalized configuration, input profile snapshots, calibration-definition version, compiler version, geometry-backend version, and seed produce the same candidate order, sample labels, settings, and layout. Store those inputs and the compiled manifest. Retain the generated artifact and its SHA-256; future library versions must not be assumed to recreate byte-identical meshes or G-code.

Allocate the six-character physical plate code once, store it as unique indexed data, and include it in the saved config before geometry is emitted. Recompilation of that saved revision preserves the same code. An edited configuration creates a new immutable revision and a new code. Do not derive the short code from a truncated hash without collision checking.

The canonical schema should be versioned. A migration can transform an older config into a newer in-memory representation, but old experiment snapshots and artifacts stay available in their original form. Reject unknown major schema versions with a useful message rather than silently discarding fields.

## Python model-generation options

A Python CAD library is a better fit than an OpenSCAD subprocess for the requested desktop-integrated configurator, but a short packaging and Orca round-trip prototype should choose the dependency. Keep a narrow backend interface so the choice does not leak into calibration modules.

- **build123d** is the first candidate to prototype: it is a Python parametric BREP framework over OpenCascade and documents STL and STEP export. Its project is Apache-2.0. Sources: [project](https://github.com/gumyr/build123d), [import/export documentation](https://build123d.readthedocs.io/en/latest/import_export.html), [license](https://github.com/gumyr/build123d/blob/dev/LICENSE).
- **CadQuery** is a close alternative: Python parametric CAD over OCP/OpenCascade, with STL/STEP export and Apache-2.0 licensing. Sources: [project](https://github.com/CadQuery/cadquery), [import/export documentation](https://cadquery.readthedocs.io/en/latest/importexport.html), [installation guidance](https://cadquery.readthedocs.io/en/stable/installation.html), [license](https://github.com/CadQuery/cadquery/blob/master/LICENSE).
- **lib3mf** may help package generated meshes into a standard 3MF file; it is not the CAD modeler and does not supply Orca-specific project settings. Its official project uses BSD-2-Clause. Sources: [project](https://github.com/3MFConsortium/lib3mf), [license](https://github.com/3MFConsortium/lib3mf/blob/master/LICENSE).

I recommend prototyping build123d first because the geometry is simple but benefits from explicit solids/unions for connectors and labels. Keep CadQuery as the comparison if its packaging or topology handling proves better. Do not settle this from feature lists: test installation/bundling on supported desktop platforms, stable mesh export, text labels, connected coupon geometry, and actual Orca import. The CAD library decision is still open.

Orca's published import/export and CLI guidance describes splitting a 3MF into separate parts for per-part print-setting changes and exposes per-object settings in its CLI. Those references justify a prototype, but do not prove the exact independent setting behavior needed by this application. A real-slicer test and G-code checks remain a release gate. Sources: [Orca import/export](https://www.orcaslicer.com/wiki/general_settings/import_export), [Orca CLI mode](https://www.orcaslicer.com/wiki/cli/cli_mode).

## Required architecture spike before committing to a backend

1. Create a small typed config and compiler prototype for a connected 3 × 3 coupon plate with A–I labels and a six-character code.
2. Generate a simple specimen grid, intentionally robust breakaway links, and readable labels with build123d; compare CadQuery if installation, mesh topology, or export is a problem.
3. Validate bed bounds, distinct sample meshes/names, connector geometry, label placement, watertightness, and repeat-generation stability.
4. Package/import the output in the exact Orca versions and profiles under consideration. Assign different supported settings to at least three samples, slice, and prove the changes reached their intended toolpaths. Include an intentionally invalid case that must be blocked.
5. Record commands, environment, input config, tool versions, hashes, Orca logs, G-code findings, platform packaging impact, and the decision in `docs/PROGRESS_HISTORY.md`.
6. After the evidence, choose the CAD backend, persistence representation, artifact storage policy, 3MF packaging approach, and supported layout/settings capability matrix. Keep unresolved decisions visible.

## Recommendation

Proceed with the architecture concept as a design candidate: one typed/versioned experiment configuration, persisted and searchable through the database; a GUI configurator that creates that data; one headless deterministic compiler; a Python geometry backend; a separate Orca application/validation adapter; and a searchable experiment history view keyed by the plate code.

Adopt the separation and prototype gate above. Keep the exact CAD library, physical DB-vs-artifact storage boundary, configuration file import/export, and tab naming open until the spike and user review. This evaluation does not authorize camera workflows, automatic assessment, or any other excluded V1 feature.
