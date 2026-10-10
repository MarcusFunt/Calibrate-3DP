# Calibrate-3DP — build123d Geometry and Physical Labeling Implementation Plan

**Status:** Proposed implementation plan; no code changes implied by this document  
**Repository:** https://github.com/MarcusFunt/Calibrate-3DP  
**Verified baseline:** `origin/main` at `373f0f04e5558a13014b280581a4235800f3ba2c` (2026-10-10)  
**Primary decision:** Introduce a versioned `build123d` geometry backend while preserving the existing experiment compiler, Orca-specific 3MF settings packaging, history, and print-readiness gates.  
**Product scope:** Local-first, **manual** FDM calibration V1; no camera/ML, printer control, direct printing, or changes to imported Orca source profiles.

> **Implementation rule:** Read `AGENTS.md`, `docs/V1_GOAL.md`, `IMPLEMENTATION_PLAN.md`, `docs/IMPLEMENTATION_STATUS.md`, `docs/PROGRESS_HISTORY.md`, and the relevant source/test files on **current `origin/main` before coding**. The baseline hash above describes the repository when this plan was authored; do not assume it remains HEAD. Update the plan if current code conflicts with a stated detail. Implement in reviewable increments and record tested evidence rather than marking unverified behavior complete.

## 1. Goal and non-negotiable outcomes

Replace the limitations of the current 0.4 mm standard-library voxel generator with a real parametric CAD backend, focusing on reproducible specimens, better physical text, and geometry reuse across the eight V1 calibration families.

The required physical identity design is:

1. **Each eligible sample gets a readable, raised/embossed label on its *underside*.** The sample's functional top face, especially for ironing, remains flat and uninterrupted. Labels are actual CAD geometry, not slicer annotations or text stored only in a manifest.
2. **The allocated six-character plate ID is on a separate, flat plaque at one corner of the connected grid frame.** The plaque lies flat on the bed and is physically attached to the frame by designed breakaway links. Its ID is visible/readable on the plaque's *top* side; it must not be on the old vertical frame rail. Default corner: front-left; other corners selectable to accommodate bed/keep-outs.
3. **Nine ironing samples remain independently addressable objects** (`Sample-A` through `Sample-I`), the frame remains a distinct `Plate-Frame`, and the new identifier plaque becomes a distinct `Plate-Identifier` object. A nine-sample plate will therefore have **11 named 3MF objects**, not the current 10. Object identity and per-sample Orca overrides must survive slicing.
4. All generated shapes and their metadata are associated with one immutable experiment/run revision. The code, A-I label map, source Orca profile hashes, font hash, CAD/kernel versions, backend and recipe versions, settings, and exported artifact hashes remain recoverable after restart and upgrades.
5. A geometry/modeling success or Orca CLI success **does not** set `print_ready=true`. Existing preflight gaps, real-printer test requirements, and version-support gates remain open until separately satisfied.

### Definition of “embossed underside text” and printability constraint

An ordinary positive-relief glyph hanging *below* a sample whose other underside starts at Z=0 would place part of the model below the print bed. Moving the sample up so only letters touch Z=0 leaves large portions of its first layers unsupported. **Do not implement that naive configuration.**

Use a **pocketed underside relief** as the primary design:

- The sample body and an outer footprint/rim have a flat bed-contact plane at **Z=0**.
- In a deliberately small, nonfunctional area of the bed-facing underside, subtract a shallow rectangular/rounded identification pocket, nominally **0.8 mm deep** (prototype setting, to be physically qualified).
- Place actual **raised letters protruding *from the recessed pocket ceiling toward the bed***. The letter tips terminate at **Z=0** (coplanar with the sample's ordinary bed-contact surface). They are embossed relative to the surrounding recessed background, without extending below the build plate.
- Letters must connect to the pocket ceiling through real solid material, and the resulting sample must be one watertight solid. The pocket has an outer land/rim at Z=0 for first-layer adhesion.
- Since the pocket ceiling bridges over gaps between letters, restrict pocket span, unsupported local spans, text dimensions, and first-layer islands; verify with Orca toolpaths and **real physical prints**. Do not describe a CAD-only check as proof that this underside feature prints well.
- Implement the underside text in a coordinate frame oriented so it reads correctly **when the specimen is flipped over and viewed from below**, not mirrored. Use asymmetric test strings (e.g. `R2F`) to verify the transformation.
- When an experiment's geometry cannot safely host underside relief (thin walls, load-bearing surfaces, engineered bridging areas, open lattices, support contact, insufficient thickness, etc.), its recipe **declares underside labels unsupported** and selects an explicit alternative: attached flat identification tab or engraved/nonfunctional side label. **Never silently place text on a measurement surface.**

**Terminology:** This design is *positive/embossed text inside a recessed bottom pocket*, rather than letters protruding below the overall bed-contact plane. Keep this distinction in GUI and documentation. If user testing finds the pocket impractical, record the result and obtain a new explicit design decision before changing the default to plain engraved/debossed text.

### Example section, schematic only

```text
                 sample body, functional top surface uninterrupted
        +-------------------------------------------------------+
        |                       SOLID                           |
        |                                                       |
Z=0.8   |      pocket ceiling _________________________          |
        |       |             |     |           |     |          |
        |       |     VOID    |GLYPH|   VOID    |GLYPH|          |
Z=0.0   +-------+-------------+-----+-----------+-----+----------+  print bed
                 ^ recessed   ^ raised letters flush with Z=0
                   background
```

Do not take schematic dimensions as a printability guarantee. A letter touching the bed on layer 1 might be an island until joined to the main body. Verify first-layer paths, bridging above the pocket, bed adhesion, and visual readability.

## 2. Current architecture and exact integration points

| Existing component at baseline | Current behavior | Required change |
|---|---|---|
| `src/calibrate3dp/domain/experiment_config.py` | Frozen schema v1, `stdlib-voxel` v1 layout settings; frozen 3x3 flow/speed | Introduce **schema v2** or explicit versioned extension for backend, recipe, font, underside labels, plaque geometry/corner; load v1 unchanged |
| `src/calibrate3dp/app/services/experiment_configuration_service.py` | Creates/reviews frozen ironing config; preview uses `XXXXXX` placeholder | Backend-neutral geometry preview and validation, no permanent code allocation until run |
| `src/calibrate3dp/app/services/grouped_layout.py` | Converts plan and Orca bed polygon to `PlateLayoutRequest` | Expand layout request for plaque, bottom-label feature regions, and recipe; maintain deterministic mappings |
| `src/calibrate3dp/geometry/layout.py` | Static connected grid, frame rails, eight tabs per sample; code plaque on vertical frame face | Introduce a corner plaque footprint and orientation, collision/keep-out-aware placement, label pockets; preserve sample order |
| `src/calibrate3dp/geometry/specimens.py` | `PlateGeometryBackend` builds watertight voxel meshes, `TriangleMesh`, `PlateObject` | Split shared data contracts from voxel implementation; introduce build123d implementation and versioned dispatch |
| `src/calibrate3dp/grouped_plate.py` | Streams separate-object 3MF with required Orca per-part flow/speed metadata; old count 10 | Retain writer but support an eleventh `Plate-Identifier` object without per-sample overrides; preserve exact ID mapping |
| `src/calibrate3dp/app/services/grouped_orca_service.py` | Generates STL, `plate.3mf`, Orca G-code, validation and manifests | Backend selection, geometry labels/provenance, new object set, validation and meaningful error handling |
| `src/calibrate3dp/orca_cli.py` | Slices with `--arrange 0 --orient 0` | Retain unchanged behavior; do not let Orca detach or rotate linked parts |
| `src/calibrate3dp/app/qt/experiment_review.py` | True-layout 2D preview; displays sample labels and old front code rail | Show corner ID plaque, underside-text marks/option, backend information and warnings; optional detailed mesh viewer later |
| `tests/test_plate_geometry_backend.py`, `test_plate_layout.py`, `test_grouped_plate.py` | Assert voxel-specific mesh and old 10-object contract | Preserve legacy fixtures and add backend-independent contract tests and new build123d/label/plaque tests |

The existing `PlateGeometryBackend.validate()` compares a generated mesh against a regenerated *voxel mesh*. That is incompatible with a CAD backend: update validation to inspect geometry contracts, **not triangulation equality across engines**. Keep strict legacy voxel-output assertions inside its own tests.

### Do not replace these proven systems

- Typed/frozen experiment plans, allocated code uniqueness and linked configuration/run IDs.
- Profile import/resolution/hashes and unmodified source profiles.
- The first-party **Orca-specific 3MF configuration writer**, where resource object ID and `Metadata/model_settings.config` part ID match. A generic build123d 3MF export is **not** a substitute unless explicitly proven to preserve the required Orca metadata.
- Real-Orca negative tests for a deliberately missing per-object override and the positive per-object flow/speed G-code check.
- Sliced XY-object-layout proof (one common translation only), bed limits, keep-outs, artifact recording, review/export flow, and the conservative `print_ready: false` state.

## 3. Dependency, backend, and packaging decisions

### Required architecture

Introduce a small, headless, Python geometry adapter contract:

```python
class GeometryBackend(Protocol):
    backend_id: str
    backend_version: str

    def build(self, request: PlateLayoutRequest) -> PlateGeometry: ...
    def validate(self, geometry: PlateGeometry) -> GeometryValidation: ...
```

Suggested file layout (adjust only when current repository organization warrants it):

```text
src/calibrate3dp/geometry/
    __init__.py
    contracts.py                 # PlateGeometry, PlateObject, Connection, Validation, TriangleMesh
    layout.py                    # pure, backend-neutral placement and occupied-feature bounds
    registry.py                  # explicit versioned backend selection; no silent fallback
    voxel_backend.py             # legacy stdlib-voxel v1 implementation
    build123d_backend.py         # new backend, imports build123d lazily
    cad_mesh.py                  # CAD Shape -> TriangleMesh tessellation adapter
    text_geometry.py             # labels, font resolution, fitting, underside orientation
    identifier_plaque.py         # corner plaque shape and breakaway attachment
    recipes/
        __init__.py
        ironing_flat.py          # first migrated geometry recipe
        # later: bridges.py, support_interface.py, temperature_tower.py, etc.
```

Migrate imports incrementally to avoid breaking old consumers of `calibrate3dp.geometry.specimens.PlateGeometryBackend`; a compatibility import/re-export can preserve that legacy name. Do **not** duplicate record models or Orca ID mapping in each backend.

### Installation strategy

- First benchmark the exact isolated build123d version already recorded in the repo's successful Windows/Python 3.11 probe: **build123d 0.13.0**. The repository recorded about **688.5 MiB** under `Lib/site-packages` for that isolated environment (45 distributions). That is installed dependency footprint, not a final bundled app measurement.
- For the spike, place CAD behind an optional dependency, for example `cad = ["build123d==0.13.0"]` in `pyproject.toml`; keep PySide6 as the existing `gui` extra. Pin native versions in a repeatable environment/lock or constraints file once compatibility is verified.
- Avoid importing build123d at GUI startup when no CAD generation is in progress. A missing CAD runtime must produce an explicit “CAD backend unavailable” state, **never** an unannounced output from the voxel backend.
- Record Python version, OS, CPU, OpenCascade/OCCT binding version, build123d version, wheel sizes, installed size, binary-bundling impact, cold/warm startup time, generation time, and peak RSS.
- Decide whether build123d belongs in the default packaged desktop distribution only after measuring clean-environment installation and application-bundling behavior. Packaging is a release gate, not a reason to give up on parametric CAD prematurely.
- Keep the upstream Apache-2.0 license notices and review the dependencies for distribution obligations. Any font bundled later needs its own compatible redistributable license.

**Build123d documentation used when preparing this plan:**
- Text and font path: https://build123d.readthedocs.io/en/v0.13.0/objects.html
- BREP tessellation: https://build123d.readthedocs.io/en/v0.13.0/direct_api_reference.html
- STL export and tolerances: https://build123d.readthedocs.io/en/v0.13.0/import_export.html

Pin or probe method signatures against the installed version; examples below are *design pseudocode*, not a claim that they have been run.

## 4. Typed geometry recipe and versioned configuration

Introduce a **geometry recipe** separate from calibration factor definitions and separate from layout strategy:

```text
ExperimentConfiguration v2
  ├─ printer / material / profile snapshot and hashes
  ├─ calibration plan (which factors, values, sample-to-candidate mapping)
  ├─ geometry_recipe
  │    ├─ id: ironing.flat_coupon
  │    ├─ revision: 1
  │    ├─ parameters: specimen thickness/size, edge treatment, etc.
  │    ├─ sample_label: pocketed_underside_emboss
  │    │    ├─ letter format: A-I
  │    │    ├─ font asset ID/hash
  │    │    ├─ nominal font size, stroke/feature rules
  │    │    ├─ pocket depth, edge clearance, relief height
  │    │    └─ underside-view orientation
  │    └─ identifier_plaque
  │         ├─ enabled: true
  │         ├─ default corner: front_left
  │         ├─ width/depth/thickness, margins and attachment mode
  │         ├─ top-side embossed code typography
  │         └─ two deliberate frame links
  ├─ plate layout: connected-grid / dimensions / keep-outs
  └─ backend id + version, compiler version, mesh tolerances
```

Schema and validation requirements:

1. A v2 configuration owns an explicit backend+recipe+version and all numerical parameters affecting geometry. It must not rely on implicit OS-default fonts or ambient geometry-library defaults.
2. All dimensions finite, positive where applicable, and checked against nozzle/layer-height capability. Do **not** round CAD dimensions to 0.4 mm voxels; geometric accuracy is one motivation for this migration.
3. Default v2 profiles may be derived from printer/nozzle context. Record the effective values, and explain when imported Orca settings are missing or inconsistent.
4. The sample label mode is declared per recipe (or per face when needed). The app may choose an explicit alternative for unsupported specimens, but **not silently**.
5. The unique plate code is allocated only when creating a real run; preview may use `XXXXXX`. It must not be derived from a hash without collision checks.
6. Preserve old schema v1 JSON and its interpretation. Do not overwrite archived configurations, manifests, or legacy geometry. New outputs get new generator, layout and font identity. Historical plate-code lookup must still work.
7. A different font file, relief dimension, plaque corner, or tessellation tolerance produces a different recipe/configuration revision and traceable hash.
8. Accept one to nine samples where existing follow-up/confirmation plans permit; nine-sample ironing is still the first complete target. A one-sample confirmation must receive its own flat identifier plaque connected to its own small frame.

**Suggested new dataclasses:** `GeometryRecipeSpec`, `SampleLabelSpec`, `UndersidePocketSpec`, `IdentifierPlaqueSpec`, `TessellationSpec`, `GeometryObjectRole`, `GeometryPlacement`. Names may change; contracts and versioning must not.

## 5. Parametric 3D specimen implementation

### First recipe: `ironing.flat_coupon@1`

Implement the current 30 × 30 mm flat ironing specimens in build123d. Keep the top surface a single uninterrupted planar region except where explicitly required by the test recipe. Preserve configured sample height (currently 6.4 mm by default), positions, 8 mm gaps, and independent sample identities.

A sample CAD recipe should:

1. Create a solid base with exact mm dimensions and a fixed local coordinate system (`x/y` on bed, `z=0` bed-contact plane).
2. Select the underside identification location in a reserved region **not used as the measured/calibrated surface**; leave structural wall thickness and corner clearances.
3. Create a defined shallow underside pocket and add raised, readable text *inside* it per Section 6.
4. Integrate eight intended breakaway links to the shared frame, preserving the existing contact/width/height behavior initially so only one class of geometry changes per step.
5. Generate the frame independently (the frame remains one settings-neutral named object).
6. Create the identifier plaque independently (settings-neutral named object) and attach it to an outer corner of the frame.
7. Validate all geometric contracts before tessellation; create individual meshes only after valid BREP construction.

Use algebra or Builder mode consistently for each module. Prefer functional recipes with typed inputs and no hidden global state. Avoid making OpenCascade selectors depend on unstable face indices; build named geometric regions from layout coordinates wherever possible.

### Mesh adapter

Convert each final build123d BREP shape to the existing `TriangleMesh` representation using a controlled tessellation interface, e.g. `shape.tessellate(linear_tolerance, angular_tolerance)`; normalize vertex and triangle orientation only when justified and verified. The adapter must:

- Produce one `PlateObject` per named sample and one per non-sample object.
- Verify finite coordinates, nondegenerate triangles, closed oriented surfaces, bounds, and the expected solid component count.
- Preserve the object name/candidate ID/settings/physical marking *outside* the STL format.
- Explicitly pin linear/angular meshing tolerances and record them in metadata. Apply finer tessellation where geometry needs it without needlessly exploding triangle counts on flat faces.
- Fail on invalid/empty/wrongly oriented/fragmented geometry instead of trying a silent BREP “repair” that changes the part.
- Prevent uncontrolled face-count and memory growth; measure mesh count, runtime and RSS in benchmarks.
- Keep expected *geometric* equality separate from expected *byte-level artifact* equality; exact bytes are guaranteed only for retained artifacts and their hashes, not across arbitrary OCCT versions.

Optional STEP/BREP diagnostic exports may be added for developers, but the normal production path remains individual meshes + the existing special 3MF assembler + Orca.

## 6. Dedicated text engine — underside sample labels

### Font policy

- Bundle or explicitly acquire one **license-reviewed, redistributable, semibold sans-serif** font with visibly distinguishable `0/O`, `1/I`, and `5/S`; register it with build123d using an explicit `font_path`.
- Before adding any font binary to the repository, review its license and obtain the file from an authorized source. Record attribution and license; do not rely on system-installed Arial or font substitution.
- Store `font_asset_id`, filename (relative to the app's asset directory), file SHA-256, font-face/style, and relevant text geometry settings in the immutable run manifest.
- Initial supported glyph set: `A-Z`, `0-9`, `-` and the deliberate labels/code. Reject unsupported characters with a helpful error rather than rendering a question mark or a substituted font.
- Cache outline sketches and/or generated text solids using stable keys including font hash, text, size, and backend/kernel version, but do not let caching affect correctness or provenance.

### Underside pocketed emboss algorithm

1. Given a sample local solid with bottom `Z=0`, choose an underside label rectangle away from connectors, measurement-sensitive regions, and thin walls.
2. Reserve an outer first-layer adhesion land. A pocket may be rounded, but its depth and boundaries must not compromise the part's strength or functional top surface.
3. Use build123d `Text` with explicit font asset and alignment; measure the **actual resulting glyph bounding box** rather than estimating width from character count.
4. Scale/reflow within the label rectangle according to declared minimum font size and minimum stroke widths. A-I can usually be substantially larger than the old bitmap lettering. **Never scale a font below a proven printable limit merely to fit.**
5. Define a local plane whose positive viewing direction is **from beneath the sample (-Z)**. Verify correct reading order; a face on the underside must not accidentally produce mirrored letters.
6. Subtract a pocket from the sample's underside (`0 <= Z <= pocket_depth`). Add text solids to protrude from the pocket ceiling toward the bed (`0 <= Z <= pocket_depth`), possibly extending a small *internal* Boolean-union overlap into the parent body (above pocket depth). The text's lowest point must remain `Z=0`, not negative.
7. Union all glyph solids to the parent body and confirm there is exactly one contiguous watertight solid. A positive-volume overlap with the pocket ceiling helps avoid mere coplanar-face contact.
8. Validate local pocket span, minimum roof thickness, glyph minimum feature widths, expected first-layer islands and anticipated bridging. Record a `needs_physical_validation` warning if geometry is mechanically valid but printability is not qualified.
9. Provide geometry metadata for text bounding box, underside orientation matrix/plane, pocket dimensions, text/glyph Z limits and font hash.

**Starting prototype dimensions, *not* certified universal defaults:** pocket depth 0.6–0.8 mm, label height 4–6 mm, raised text depth matching pocket depth, minimum unbroken bed-contact perimeter 2 mm, and feature strokes checked against the selected nozzle/line width. Parameterize and prove values on 0.4 mm and 0.6 mm nozzle cases; don't assume either succeeds without printing.

### Printability alternatives and failure behavior

- If there is inadequate pocket depth, roof thickness, nonfunctional surface area, adhesion or toolpath support, do not generate a risky sample. Produce an actionable warning and require an alternative `attached_id_tab` label mode defined by that recipe.
- The fallback tab must also be **flat against the bed**, not a vertical plaque, if its raised top text would otherwise be the safest readable option.
- Plain underside engraving/debossing is a separate *opt-in mode*, not a silent replacement for the requested embossed letters.
- Samples whose test properties depend on their underside (e.g. certain support interfaces, bridge undersides, bed adhesion/first-layer tests) must refuse the underside modification unless the recipe reserves a region proven nonfunctional.
- Validate Orca's first-layer toolpaths around small isolated glyph islands and across the pocket ceiling. The slicer may bridge or suppress details; actual G-code must be inspected, then printed samples checked physically.

### Unit and geometry tests for text

- `A`, `I`, `R2F`, `012345`, `O0I1S5`, and every permitted code glyph render without dropped contours.
- Rendered text is not mirrored in an underside view; use an actual underside projection/test render and a printed fixture.
- Lowest Z is never negative; pocket depth, relief height and surface clearance are within tolerance.
- Every glyph is fused to its owning sample; sample remains a valid single solid and watertight tessellated mesh.
- Text stays inside its pocket; no contact with connectors, pocket perimeter, critical holes/measurement features, or neighbor meshes.
- Reject absent/wrong-license-unreviewed font asset, changed hash, unsupported glyph, too-small font, too-thin wall, zero-height lettering, and malformed rotation.
- Two runs with the same input and *pinned installed environment* match intended geometry and mappings; retain exact generated artifacts/hashes for archival identity.

## 7. Flat corner-mounted plate-identifier plaque

### Physical design

The frame keeps the grid mechanically together. The **plate identifier is a distinct horizontal plaque on the bed**, attached to the frame at a corner. Do not emboss the code on the existing front-facing vertical rail and do not place the code on an ironing test surface.

Proposed first-pass values to refine with real-print trials:

| Feature | Prototype | Verification requirement |
|---|---|---|
| Plaque size | ~42 × 14 mm (auto-size to fit code) | Full code legible, full geometry within bed |
| Plaque thickness | ~1.6 mm | Stable adhesion without curling or excessive material |
| Code format | Existing six uppercase alphanumeric characters | Exact match to allocated database plate code |
| Text | Top-side raised outline-font text, nominal 4–5 mm tall | Fit checked from actual glyph bounds, not text length |
| Relief | ~0.4–0.6 mm upward from plaque top | Sliced features remain visible and printable |
| Corners | `front_left`, `front_right`, `back_left`, `back_right` | Chosen corner saved; default front-left |
| Attachment | Two slender intentional breakaway tabs to nearby frame rails | No incidental collision; physical handling/separation test |
| Orientation | Top-side text readable from front of bed by default | Preview and STL/3MF orientation consistent |

Avoid a nominal corner position that produces negative machine coordinates or overlaps the 3×3 sample area. **Recompute the entire plate assembly bounding box including the plaque**, then choose a deterministic translation within the resolved bed polygon and outside keep-outs. If a preferred corner cannot fit, report the failure and allow another corner or explicitly reviewed layout revision. Do not silently rearrange samples or mutate an already frozen run.

### Geometry and contract

- New name `Plate-Identifier`; role `identifier`, with **no candidate ID and no ironing flow/speed override**.
- `Plate-Frame` remains a separate object; its primary function is holding specimens together, not carrying the code. The identifier has a separate geometry record and physical marking in its own object.
- The plaque's two connectors are modeled as deliberate bridge tabs **owned by the `Plate-Identifier` mesh** and touching/fusing into the frame at the intended locations. Use the same contact convention as sample links, but permit a distinct parameter set.
- Connection records should name source object, target `Plate-Frame`, contact regions, and physical separation type, rather than requiring every connection to have a sample A-I label. Version the data model as necessary.
- Record explicit print orientation; plaque's **bottom is flat at Z=0** and code letters are on **top**, not on the bed-contact side.
- Use the same font-generation service as sample labels, but with top-facing text orientation and independent type size/depth.
- Fit every geometry feature (sample bodies, sample tabs, frame, plaque, plaque tabs, label pockets and label strokes) against machine bed polygon and keep-out areas.
- No accidental intersections between sample settings objects and the identifier; intended physical contact between matching object-boundary faces is allowed only at declared connectors.
- The 3MF will contain all eleven separately named objects; the frame and identifier receive no sample-specific overrides.

### Visual preview expectations

The Qt preview should depict an actual corner plaque with its six-character placeholder/code, outline and tabs, and distinguish it from the nine parameter-testing cells. It must not display the old rail code location as though it were still printed. Indicate underside labels using a small underside glyph/annotation and offer a flipped underside view or detail preview; don't clutter the top-down test surface view with labels that physically belong below it.

## 8. Geometry validation contracts

Refactor old validation into reusable **backend-neutral checks** plus engine-specific model checks.

**Required invariant categories:**

- **Identity:** ordered `Sample-A` … `Sample-I` as applicable; `Plate-Frame` and exactly one `Plate-Identifier`; unique candidate IDs; no missing physical label; actual plaque code matches run code.
- **Object boundaries:** all mesh objects are individually watertight and geometrically valid; one intended solid per part; no unexpected disjoint text islands or missing walls.
- **Geometry:** each sample retains expected envelope and top measurement geometry; no undersized walls; pocket within allocated nonfunctional region; exact dimensions within documented tolerances.
- **Contacts:** sample/identifier links terminate on intended frame surfaces with declared contact areas; no accidental overlap of nonconnectors; connectors remain separable by design.
- **Build plane:** bed-contacting objects have `min_z >= 0` within numerical tolerance; no negative-Z raised lettering; identifier flat underside at Z=0; no unsupported or floating body introduced solely to print underside letters.
- **Bed constraints:** complete feature bounds and actual geometry inside printable polygon and outside keep-outs; do not check only sample rectangles or the old frame bounds.
- **Slicer identity:** one frame and one ID plaque receive no sample candidate overrides; each sample retains its exact candidate mapping before/after 3MF and in G-code. No unexpected extra objects.
- **Reproducibility:** input/config/backend/recipe/font/OCCT/tessellation metadata recorded, artifact hashes and physical code unique. Same inputs give equivalent models under pinned environment.

**Important:** triangulated mesh vertices need not match legacy voxel vertices exactly. Tests for parity should compare bounds, volumes within tolerance, named features, sample positions, and printing behavior. Keep exact legacy fixtures for historical reproducibility only.

## 9. 3MF and Orca integration

Update `write_plate_geometry_3mf()` and related manifests without replacing the standard-library 3MF XML assembler.

**For nine-sample ironing:**

```text
3MF object IDs (example):
  1–9   Sample-A … Sample-I     -> matching per-part ironing overrides
  10    Plate-Frame            -> no sample overrides
  11    Plate-Identifier       -> no sample overrides
```

Maintain the existing Orca-specific model-settings object/part-ID invariant for all eleven objects. Preserve identity transforms (`--arrange 0 --orient 0`) so objects stay in a shared coordinate system. Define a stable sorted order and test it.

### G-code proof requirements

- The generated G-code contains all intended named objects, including `Plate-Identifier`, within one common XY translation from CAD geometry.
- Real-Orca integration must confirm all nine sample ironing speeds and normalized flow values; a deliberately deleted/mis-mapped sample override must fail closed.
- Confirm label-pocket toolpaths occur on the underside of samples without modifying the **functional top ironing region**. Inspect first layers separately: glyph islands, pocket bridging, extrusion continuity, and missing features.
- Confirm code plaque prints in the correct corner/plane and that top text is sliced. The frame and plaque may inherit normal process settings, but their presence must not distort sample-specific measurements.
- Bed/keep-out and movement checks must consider all eleven objects and the translated toolpaths. Keep the existing bounded preflight warnings and `print_ready=false` until separately qualified.
- Retain the exact Orca executable fingerprint, reported identity/banner/header, arguments, stdout/stderr, effective profiles, G-code, project/mesh hashes and test outcomes. Do not infer full compatibility from a single Windows Orca run.

Test positive and negative cases for plaque missing, duplicate object IDs, missing label geometry, remapped settings, disconnected plaque, auto-arrange damage, moved plaque, and a too-small machine area.

## 10. Storage, migrations, and provenance

- Keep existing run/assessment/export records and six-character lookup behavior. The identifier plaque is a physical representation of the same code, **not** a new identifier authority.
- Existing frozen schema-v1 configurations continue to dispatch to `stdlib-voxel@1`, including their original old 10-object geometry and rail-code location. Do not rewrite or re-export historical files unless explicitly requested.
- New v2 configurations dispatch to `build123d@1` and a declared recipe/label/plaque design revision. Freeze the entire normalized spec and its canonical serialization in the configuration snapshot.
- Persist installed backend version, OCCT/native binding versions, Python version, OS, recipe and layout versions, font asset ID/SHA-256, mesh tolerances, full plate/sample object names, per-feature label positions, ID plaque corner, and validation warnings.
- Store SHA-256 and size for individual STL files, the 3MF file, `geometry.json`, `plan.json`, `sample-map.json`, G-code and relevant evidence/logs.
- Update manifest schema version and implement a backward reader that does not confuse v1 `Plate-Frame` with v2 `Plate-Identifier` for the printed code.
- No silent migration of historical evidence or permissive schema parsing. Unknown backend/recipe version should be displayed as unsupported for regeneration, while still allowing read-only plate-code history lookup.
- Generation of a new run from an unchanged configuration gets a **new allocated code**; compare equal non-code geometry only under a normalized code-independent fixture, not by expecting identical entire-file SHA-256.
- Retain interrupted/failed-run evidence and existing restart behavior; never delete a partially written run when doing so would lose provenance.

## 11. Qt user experience changes

Update the existing experiment review and run detail views, **not** create a separate replacement GUI.

### Experiment configuration dialog

- Geometry backend: `build123d` selected for new eligible experiments; show backend version and availability. Legacy backend available only when opening/replaying old configurations or explicitly selected for controlled comparisons.
- Labels: state that specimens receive **raised underside labels in recessed pockets** where the recipe allows it. Show effective font, nominal text size, pocket depth and printability warnings.
- Identifier: corner chooser (default front-left) and plaque outline dimensions. Show `XXXXXX` before code allocation.
- Preview: top-down arrangement with physically separate corner plaque, connectors and readable code; optional toggle to underside/mesh view; show all occupied feature regions, bed polygon and excluded areas.
- Validation: block Save/Generate on unsupported backend, missing font, illegal corner, collisions, too-small text, bad wall thickness, and out-of-bed geometry. If geometry is valid but printability unproven, show a clear warning, not a ready-to-print badge.

### Run detail / history

- Display the allocated code and image/mesh preview of the actual `Plate-Identifier` plaque.
- Display sample A-I settings along with label placement mode and font/geometry-version provenance.
- List all generated STL/3MF/G-code artifacts and their hashes.
- Explain pending physical readability, adhesion, bridge, breakaway and safety checks. Do not collapse `settings_validated` into `print_ready`.

Keep headless services independent of Qt. GUI tests should work offscreen where supported and real visual layout/DPI tests should be recorded separately.

## 12. Physical validation matrix and test fixtures

A CAD engine change is not accepted only on synthetic geometry tests. **Print and inspect** at least the following artifacts with exact settings recorded.

| Fixture | Goal | Evidence |
|---|---|---|
| `underside_text_fixture` | Distinguishable `R2F`, `O0I1S5` and A-I, all on underside | Top/bottom photos, measured letter dimensions, failed strokes/islands |
| `label_pocket_depth_sweep` | Compare 0.4, 0.6 and 0.8 mm pocket depths, practical line widths | G-code layer view, first-layer adhesion, readability after detachment |
| `identifier_corner_fixture` | Four corner placements, two attachment tabs, plaque text | Bed fit, orientation, breakaway force/handling, no detached first layers |
| `ironing_connected_9` | Full nine-sample experiment and physical labels | Valid flow/speed toolpaths, uninterrupted ironing surfaces, top/bottom photos |
| `small_bed_or_keepout` | Validate rejection rather than unwanted auto-arrange | Exact rejection code and UI message |
| `font_missing_or_changed` | Font reproducibility safety | Explicit failure, no font substitution |
| `followup_one_sample` | Confirmation geometry and identifier | Sample-A mapping, frame/plaque, unique code |

Record printer, nozzle diameter, layer height, material/brand, bed surface and temperature, Orca version/identity, profile snapshots, orientation, photographs, observed text quality, and tab/handling notes.

**Minimum physical acceptance:** all sample labels read correctly without magnification at normal inspection distance agreed by the tester, no ambiguous plate code characters, no unintentional detachment on ordinary removal, deliberate tab separation with hand tool, no significant first-layer failures attributable to underside text, and no altered functional ironing geometry. If that is not achieved, iterate on the pocket/typography/connector design and preserve the failed run evidence.

## 13. Phased implementation tasks — coding-agent checklist

Each phase should be a small, testable branch or PR rather than a single broad rewrite. Implement against freshly fetched `origin/main` and preserve evidence per phase.

### Phase 0 — Baseline, tests and CAD feasibility spike

**Files:** `pyproject.toml` (optional extra only), new `tests/test_build123d_smoke.py`, `docs/REUSE_RESEARCH.md` (after actual measurements), `docs/PROGRESS_HISTORY.md` (append).

- [ ] Confirm HEAD, clean baseline, current status/plan, Python and Orca install identity; inventory code references to `PlateGeometryBackend`, 10-object assumptions and rail-code semantics.
- [ ] Establish a pinned Python 3.11 test environment with build123d 0.13.0; record exact versions and installed native libraries. Don't change the default installer yet.
- [ ] Test a basic box, text from an explicit font asset, subtractive pocket, positive relief union, `Shape.tessellate()`, and mesh export.
- [ ] Test all allowed code glyphs for failure and measure memory/runtime on nine samples plus plaque.
- [ ] Benchmark build123d import time, model construction, Booleans, tessellation, output size, and isolated deployment footprint.
- [ ] Add no-code fallback behavior for missing CAD runtime. Record whether a clean Windows install is viable.

**Gate:** A build123d prototype produces one geometrically valid labeled sample, and measured packaging/runtime are recorded. If it fails, report the measured blocker and do not migrate the main workflow blindly.

### Phase 1 — Shared geometry contract, versioned dispatcher and schema v2

**Files:** `geometry/contracts.py`, `geometry/registry.py`, `geometry/specimens.py` compatibility layer, `geometry/layout.py`, `domain/experiment_config.py`, `app/services/grouped_layout.py`, schema/storage migration tests.

- [ ] Extract reusable contracts without changing legacy behavior.
- [ ] Make backend selection explicit (`stdlib-voxel@1`, `build123d@1`); no silent fallback.
- [ ] Add typed label/plaque/tessellation specs and versioned new configuration, preserving every v1 reader and archive.
- [ ] Replace voxel-mesh equality checks with backend-neutral geometric validation in the shared layer; keep strict voxel fixtures locally.
- [ ] Test canonical input serialization/version hashing and legacy config/run loading after app restart.

**Gate:** Existing non-CAD suite remains green and old 10-object runs retain their exact interpretation; new schema v2 round-trips without losing settings.

### Phase 2 — Underbody labels and parameterized sample recipe

**Files:** `geometry/text_geometry.py`, `geometry/recipes/ironing_flat.py`, `geometry/build123d_backend.py`, `geometry/cad_mesh.py`; unit tests including glyph orientation and numeric invariants.

- [ ] Implement normalized sample solids and typed placement regions.
- [ ] Implement explicit font loading/hash validation and repeatable text outline generation.
- [ ] Implement pocketed underside embossed labels with Z=0 floor constraint, orientation and positive-volume Boolean union.
- [ ] Confirm text does not modify the ironing upper surface or test-sensitive underside regions.
- [ ] Tessellate and validate one to nine separate sample objects.
- [ ] Add geometry preview fixtures/images for underside orientation; verify `R2F` is not mirrored.

**Gate:** Tests establish one valid solid per sample, readable planned geometry, accurate positioning, and no negative Z. Physical-print status remains unverified.

### Phase 3 — Corner identifier plaque and connected frame

**Files:** `geometry/identifier_plaque.py`, `geometry/layout.py`, `geometry/build123d_backend.py`, `geometry/contracts.py`, layout/geometry tests.

- [ ] Generate one flat separate `Plate-Identifier` with raised top-side six-character code.
- [ ] Add two explicit frame links, connector measurements and identifier-owned geometry.
- [ ] Recalculate entire envelope, deterministic origin translation, four corners and keep-out collisions.
- [ ] Preserve eight links per sample and intended physical sample-to-frame connectivity.
- [ ] Validate positive-volume connections where intended and no accidental overlap elsewhere.
- [ ] Test nine-sample and one-sample confirmation cases, and narrow/irregular printer bed rejection.

**Gate:** Connected plate produces valid A-I meshes, a frame mesh, and one named identifier mesh; all are within the exact selected machine region.

### Phase 4 — Preserve 3MF mapping and prove actual Orca behavior

**Files:** `grouped_plate.py`, `app/services/grouped_orca_service.py`, `tests/test_grouped_plate.py`, `tests/test_grouped_orca_service.py`, `tests/test_orca_slicer_integration.py`.

- [ ] Extend 3MF writer/manifest to the 11-object layout and remove old rail-code assumptions for v2 only.
- [ ] Preserve each sample resource ID == settings-part ID; add the frame and identifier without sample overrides.
- [ ] Extend G-code spatial validator to require all eleven named toolpaths and common translation. Keep negative cases.
- [ ] Slice with recorded Orca binary and profiles using disabled auto-arrange/orient.
- [ ] Prove nine flow/speed toolpath variations, identifier position/top text, and underside-label first-layer/toolpath presence; preserve positive and intentionally corrupted negative fixtures.
- [ ] Preserve all audit inputs, CLI outputs, artifacts/hashes and `print_ready=false` while current broader qualification remains incomplete.

**Gate:** Real-Orca positive/negative integration passes for the exact tested environment, or generation remains blocked for that environment. Do not treat this as general printer safety approval.

### Phase 5 — UI, provenance, historical compatibility and performance

**Files:** `app/qt/experiment_review.py`, `app/qt/experiment_detail.py`, associated services/models/tests, `domain/experiment_config.py`, storage/manifest readers.

- [ ] Preview the separate corner identifier and correct sample underside label positions.
- [ ] Surface backend/font/pocket/label-mode version and any generator support warnings.
- [ ] Make the selection of plaque corner and approved typography/layout parameters reviewable before saving a new config revision.
- [ ] Show immutable code, sample mapping and physical-label design in run/history lookup.
- [ ] Verify old runs are readable with original v1 geometry semantics.
- [ ] Benchmark build time, memory, STL/3MF/G-code size, cold startup, and installation footprint; diagnose pathological Boolean or glyph performance.
- [ ] Exercise interrupted generation, cancellation and restart to ensure no missing or falsely completed run records.

**Gate:** End-to-end app flow produces and retrieves a v2 labeled connected plate without mutating the source profiles or historical records.

### Phase 6 — Physical trial, iteration and handoff to other calibration families

**Files:** new physical-test record(s) and source fixture scripts, `docs/IMPLEMENTATION_STATUS.md`, `IMPLEMENTATION_PLAN.md`, `docs/EXPERIMENT_CONFIGURATION_PROPOSAL.md`, append-only `docs/PROGRESS_HISTORY.md`.

- [ ] Run first-layer and underside-label print trials with actual material/printer context recorded.
- [ ] Verify visually and physically that the embossed pocket is readable, connected and printable; tune parameters through new recipe revisions, not silent code changes.
- [ ] Validate flat plaque code readability, placement, normal handling and deliberate separation.
- [ ] Record top ironing uniformity, first layer/bridging, G-code evidence, printer/profile versions and photo evidence.
- [ ] Make a bounded release/packaging decision using actual measurements.
- [ ] Introduce a documented recipe interface for bridge/support/temperature/retraction/flow/etc. **Do not claim all V1 families implemented merely because build123d now supports their geometry.**
- [ ] Update only those progress checkboxes that passed and append exact commands/results, environment and remaining blockers.

**Gate:** At least one physical nine-sample plate demonstrates readable underside A-I, a readable flat corner identifier, intended connectivity and successful deliberate separation, without compromising the ironing top surfaces. Keep broader print readiness and support-matrix gates independently qualified.

## 14. Proposed tests and commands

Suggested new test files:

```text
tests/test_build123d_backend.py
tests/test_build123d_mesh_adapter.py
tests/test_underside_text_geometry.py
tests/test_font_asset_provenance.py
tests/test_identifier_plaque.py
tests/test_geometry_contract_v2.py
tests/test_geometry_schema_migration.py
tests/test_build123d_performance.py          # opt-in benchmark, not CI pass/fail without thresholds
```

Retain and revise existing tests:

```text
tests/test_plate_geometry_backend.py
tests/test_plate_layout.py
tests/test_grouped_plate.py
tests/test_grouped_orca_service.py
tests/test_experiment_config.py
tests/test_experiment_review_ui.py
tests/test_orca_slicer_integration.py
```

Adapt command syntax to the current environment:

```bash
python -m pip install -e '.[gui,cad]'
python -m unittest tests.test_build123d_backend tests.test_underside_text_geometry tests.test_identifier_plaque -v
python -m unittest tests.test_geometry_contract_v2 tests.test_geometry_schema_migration -v
python -m unittest tests.test_plate_layout tests.test_grouped_plate tests.test_grouped_orca_service -v
python -m unittest discover -s tests -v
python -m compileall -q src/calibrate3dp tests
git diff --check
```

Real Orca tests must be opted in with the project's established environment setup and an exact binary/profile/evidence directory; do not invent success claims or require Orca in ordinary unit CI. Run Qt tests with `QT_QPA_PLATFORM=offscreen` where supported, plus separate real-window visual/DPI inspection.

### Mandatory negative tests

1. Imported machine polygon fits original grid but **not** the corner plaque: generation fails and identifies the plaque.
2. Keep-out intersects plaque or tabs but not samples: generation fails closed.
3. Missing/changed font SHA: no silent fallback or replacement glyphs.
4. Underside text includes an unsupported glyph, has too-small strokes, inverted reading direction or protrudes below Z=0: geometry rejected.
5. Pocket breaches minimum wall/roof thickness or the ironing top face: rejected.
6. A text Boolean leaves one floating glyph disconnected from body: rejected.
7. Sample F loses its 3MF ironing override: actual G-code validator fails.
8. Orca moves identifier or sample independently, despite per-object settings still being applied: layout gate fails.
9. Old schema v1 plate code formerly on `Plate-Frame` is read as a v2 ID plaque: must **not** happen.
10. CAD missing/unavailable during generation: explicit block, not silently falling back to voxel geometry.
11. A canceled/interrupted run has files but no successful manifest: not treated as complete or print-ready.

## 15. Completion criteria and out-of-scope work

The CAD migration is **complete** when:

- [ ] build123d is selectable/versioned and produces validated parametric geometry from a frozen configuration.
- [ ] Every eligible ironing sample has actual **bed-facing embossed lettering inside a recessed pocket**, correctly readable from the underside and located outside the functional test area.
- [ ] The six-character plate ID exists on a **separate horizontal, physically attached, corner-mounted, top-labeled plaque**, not a vertical frame rail.
- [ ] A nine-sample run exports **11 named objects**, with nine correctly mapped Orca sample overrides; real G-code layout/settings checks pass.
- [ ] Fonts, geometry recipe, backend/native versions, mesh tolerances and artifacts are recorded for reproducibility.
- [ ] Historical stdlib-voxel runs/configurations remain readable without changing their interpretation.
- [ ] The Qt preview and run detail accurately represent underside labels and the corner plaque.
- [ ] A physical test confirms label/code readability, adhesion, intact handling, deliberate separation and unchanged measured ironing surface.
- [ ] The project documents exact test results, supported environments and unresolved blockers. `print_ready` remains truthful and conservative.

**Explicitly not in this migration:** building automatic optical inspection, sensor ingestion, printer control, a new slicer, automatic profile activation, all eight calibration families at once, full G-code safety qualification, or an unsupported claim of production readiness. Future sensor/ML analysis should consume the same structured run/provenance data but is outside this V1 CAD work.

## 16. Source references

**Authoritative project sources at the verified baseline:**

- [V1 scope and acceptance criteria](https://github.com/MarcusFunt/Calibrate-3DP/blob/373f0f04e5558a13014b280581a4235800f3ba2c/docs/V1_GOAL.md)
- [Experiment configuration proposal](https://github.com/MarcusFunt/Calibrate-3DP/blob/373f0f04e5558a13014b280581a4235800f3ba2c/docs/EXPERIMENT_CONFIGURATION_PROPOSAL.md)
- [Previous library spike and packaging measurements](https://github.com/MarcusFunt/Calibrate-3DP/blob/373f0f04e5558a13014b280581a4235800f3ba2c/docs/REUSE_RESEARCH.md)
- [Current schema/layout options](https://github.com/MarcusFunt/Calibrate-3DP/blob/373f0f04e5558a13014b280581a4235800f3ba2c/src/calibrate3dp/domain/experiment_config.py)
- [Current geometry layout](https://github.com/MarcusFunt/Calibrate-3DP/blob/373f0f04e5558a13014b280581a4235800f3ba2c/src/calibrate3dp/geometry/layout.py)
- [Current voxel backend](https://github.com/MarcusFunt/Calibrate-3DP/blob/373f0f04e5558a13014b280581a4235800f3ba2c/src/calibrate3dp/geometry/specimens.py)
- [Existing Orca-specific 3MF writer](https://github.com/MarcusFunt/Calibrate-3DP/blob/373f0f04e5558a13014b280581a4235800f3ba2c/src/calibrate3dp/grouped_plate.py)
- [Current generation orchestration](https://github.com/MarcusFunt/Calibrate-3DP/blob/373f0f04e5558a13014b280581a4235800f3ba2c/src/calibrate3dp/app/services/grouped_orca_service.py)
- [Existing connected-plate test plan](https://github.com/MarcusFunt/Calibrate-3DP/blob/373f0f04e5558a13014b280581a4235800f3ba2c/docs/superpowers/plans/2026-10-09-connected-labeled-plate.md)

**External API references:**

- [build123d project](https://github.com/gumyr/build123d)
- [build123d 0.13.0 Text/font docs](https://build123d.readthedocs.io/en/v0.13.0/objects.html)
- [build123d 0.13.0 shape and tessellation API](https://build123d.readthedocs.io/en/v0.13.0/direct_api_reference.html)
- [build123d 0.13.0 import/export](https://build123d.readthedocs.io/en/v0.13.0/import_export.html)

---

**Decision summary:** Make build123d the preferred generator for new parametric calibration recipes. Do not throw away the proven Orca 3MF/settings pipeline. Use **pocketed embossed underside sample lettering** and **a separate flat top-labeled corner identifier plaque** with explicit printability tests. Treat run history and full safety validation as first-class responsibilities, not afterthoughts.
