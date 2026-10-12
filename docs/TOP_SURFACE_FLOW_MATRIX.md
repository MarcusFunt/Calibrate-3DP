# Top-surface flow-ratio matrix — standalone CAD recipe

**State: Geometry and 3MF packaging prototype, not a qualified print or V1
flow-ratio experiment.** The GUI has not been wired to this recipe. Imported
filament profiles, run registration, approved recommendations, and Orca
G-code evidence remain separate work.

## What it generates

`flow_ratio.top_surface_matrix@1` creates 1–9 solid-top flat coupons (default
3×3, nine samples, each 30 × 25 × 4 mm) with A–I underside pocketed embossed
labels, a connected removable frame, and a raised top-side six-character
plate-code plaque. The CAD is built with the exact pinned `build123d@1` backend.
All samples are independent named 3MF objects.

The specimen's top face remains flat and free of text. Orca, **not** CAD,
creates its actual top-surface infill/toolpaths, so the requested top pattern
and per-object flow must be checked after slicing. The current generator does
not reproduce Orca's special Archimedean-chords flow-test toolpath.

## Candidate semantics

The target being calibrated is the **filament** flow ratio. Orca allows an
additional **object** flow multiplier, `print_flow_ratio`. With baseline
filament ratio `B`, and an additive modifier `m`:

- The absolute target candidate is `B + m`.
- The 3MF per-object multiplier is `(B + m) / B`.
- The final ratio is `B * ((B + m) / B)` **only if** no other uncontrolled
  multipliers alter the result and Orca honors the per-object setting.

Default modifiers are -0.04, -0.03, -0.02, -0.01, 0, +0.01, +0.02, +0.03,
+0.04. One through nine strictly increasing candidates can be specified.
This prototype accepts targets between 0.5 and 1.5.

**Do not apply the object multipliers directly to the filament profile.**
The selected physical sample maps to its absolute target filament value in
the sidecar manifest. Imported preset files are never modified.

## CLI

Install the pinned CAD extra in a suitable Python 3.11 environment, then:

```powershell
python -m pip install -e ".[cad]"
python tools/generate_top_surface_flow_matrix.py `
  --plate-code FLOW01 `
  --baseline-ratio 0.98 `
  --modifiers -0.04 -0.03 -0.02 -0.01 0 0.01 0.02 0.03 0.04 `
  --bed-width 220 --bed-depth 220 `
  --output artifacts/flow-FLOW01.3mf
```

Produces a multi-object `*.3mf` with Orca `Metadata/model_settings.config`
per-part `print_flow_ratio` values and a `*.json` sidecar with exact geometry,
candidate-to-label mappings, versioned provenance and the 3MF SHA-256. The
six-character code is supplied by the CLI but **not reserved or registered** in
the Calibrate-3DP database; this is a standalone sample asset, not a saved run.

The model refuses to overwrite either existing artifact. Coupon and bed
dimensions are independently adjustable; invalid dimensions, unsorted ratios,
out-of-range ratios and unfit layouts fail before export.

## Orca slicer requirements and incomplete evidence

Before a real calibration workflow can be enabled:

1. Resolve the selected printer/process/filament profiles, including the
   exact baseline `filament_flow_ratio`; reject unknown or conflicting values.
2. Explicitly disable ironing and ensure sufficiently thick, closed top
   layers (minimum 3), constant top pattern, line width and layer height.
   Control global `print_flow_ratio` and any other active flow multipliers.
3. Slice the assembled plate using Orca with auto-arrangement and orientation
   disabled. Verify all objects share the intended XY transformation, labels
   and frame are present, and no objects are dropped.
4. Parse actual, object-associated **top-surface** positive-extrusion toolpaths
   to verify each candidate's requested relative flow. Use a negative fixture
   missing one per-object override and fail closed. Merely seeing the setting
   in 3MF metadata is not sufficient.
5. Independently validate G-code bounds and conditions, capture the exact Orca
   binary/profile hashes, and retain all toolpath artifacts and logs.
6. Run a real physical print to inspect labels, plaque, breakaway connectors,
   top-surface continuity and the suitability of manual scoring.
7. Integrate a typed `flow_ratio` experiment definition, dependencies,
   recorded plate-code allocation, manual assessment and reviewed filament
   profile export with the existing Calibrate-3DP services.

Until that work completes, `print_ready = false` and no result should
enable a calibration decision or export. This prototype makes no claim of
physical printability, printer safety, or supported Orca version.

## Tests

```powershell
python -m unittest discover -s tests -p test_top_surface_flow_matrix.py -v
python -m unittest discover -s tests -p test_grouped_plate.py -v
python -m unittest discover -s tests -p test_plate_layout.py -v
```

Geometry/3MF tests explicitly skip if the pinned optional CAD extra is not
installed. They are not a substitute for a real Orca integration gate.
