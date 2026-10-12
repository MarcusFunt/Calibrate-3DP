# OrcaSlicer 3MF format and CLI notes

Updated 2026-10-11 from the installed OrcaSlicer 2.3.0 CLI and its tagged source. This guide records observed behavior; Orca's metadata is version-specific and a different export path can contain a different set of parts.

## The short version

The `.3mf` extension identifies a ZIP-based 3MF document. Its core model and package relationships follow the open 3MF/OPC standards. Orca adds its own metadata conventions for settings, plates, object overrides, sliced G-code, statistics, and previews. Those additions are readable XML, JSON, and ordinary files, but their meaning and import behavior are Orca-specific. The format is not one opaque proprietary binary, and a generic 3MF importer is not expected to understand every Orca setting.

The [3MF Core Specification](https://github.com/3MFConsortium/spec_core/blob/1.3.0/3MF%20Core%20Specification.md) defines the ZIP package, parts, relationships, model resources, and build items. Orca's [Import and Export guide](https://github.com/OrcaSlicer/OrcaSlicer/wiki/import_export) distinguishes a generic model import, an Orca project, and a sliced `Gcode.3MF`. Its tagged [2.3.0 writer](https://github.com/OrcaSlicer/OrcaSlicer/blob/v2.3.0/src/libslic3r/Format/bbs_3mf.cpp) lists the extra package parts Orca knows how to read and write.

## Three files that are easy to confuse

| File kind | What it contains | What the app currently exports |
| --- | --- | --- |
| Generic model `.3mf` | Core 3D geometry, object/build relationships, and optional application metadata | **Yes.** Calibrate-3DP writes this for importing a sample into a slicer with the user-selected printer, material, and process. |
| Orca project `.3mf` | A model plus Orca project state such as plate/object organization and settings; the exact members depend on how the project was saved | **No.** Calibrate-3DP does not claim to produce an Orca Save Project snapshot. |
| Sliced `.gcode.3mf` | A sliced plate package; the observed CLI export contains the G-code, effective project settings, object settings, plate metadata, slice statistics, and a G-code MD5 sidecar | **No.** This was produced for the CLI format study only. |

Orca's official guide says a plain imported G-code file has toolpaths but does not carry printer, material, or process information. It recommends `Gcode.3MF` when the sliced job needs to travel with that information. The package examined below is a *sliced* output from `--slice 0 --export-3mf`; this experiment did not use Orca's GUI Save Project action, so it does not establish that every project-save variant has these exact members.

## Package anatomy from the real CLI output

The inspected output was `sample-a.gcode.3mf`, created from the exported Sample A model and the captured Creality machine, process, and filament profiles. It is a ZIP archive with 12 members:

```text
[Content_Types].xml
_rels/.rels
3D/3dmodel.model
3D/_rels/3dmodel.model.rels
3D/Objects/Sample-A_1.model
Metadata/plate_1.json
Metadata/project_settings.config
Metadata/model_settings.config
Metadata/_rels/model_settings.config.rels
Metadata/slice_info.config
Metadata/plate_1.gcode
Metadata/plate_1.gcode.md5
```

### Standard 3MF / OPC pieces

- `[Content_Types].xml` declares the MIME type for `.model`, `.rels`, `.png`, and `.gcode` parts.
- `_rels/.rels` identifies `/3D/3dmodel.model` as the primary 3D payload. The 3MF specification uses package relationships so consumers find the model without guessing a filename.
- `3D/3dmodel.model` is core 3MF XML. It declares millimetres, model resources, and a build item. In this output a root object refers to a component in `3D/Objects/Sample-A_1.model`; the build item places it on the plate with a translation.
- `3D/_rels/3dmodel.model.rels` links that component model. Object IDs and paths are references across package parts, so they must stay consistent when a package is edited.

The production namespace `p` and `p:UUID` / `p:path` values in the model are part of the 3MF Production extension used by Orca's multi-part model representation. They complement the standard core resource/build relationship; they do not replace it.

### Orca metadata pieces

| Member | Observed role |
| --- | --- |
| `Metadata/project_settings.config` | JSON with 501 effective setting keys in this output. This is a large flattened settings snapshot. The tested export did not contain separate `machine_settings_*`, `process_settings_*`, or `filament_settings_*` files, so do not assume this particular file is the original profile documents verbatim. |
| `Metadata/model_settings.config` | Orca XML associating setting metadata with an object and part ID. The captured `Sample-A` part carries `ironing_flow=12%` and `ironing_speed=10`. It also records the plate and its G-code path. |
| `Metadata/plate_1.json` | Per-plate summary, including the bounding box, object name/ID, bed type, filament IDs/colors, and nozzle diameter. |
| `Metadata/slice_info.config` | XML slice summary: filament used, estimated time, warning records, and plate attributes. This sample contains a temperature warning. Preserve and review these messages instead of treating a successful slice as approval. |
| `Metadata/plate_1.gcode` | The actual plain-text machine instructions produced by Orca for the plate. It is the data to inspect or import for a toolpath preview. |
| `Metadata/plate_1.gcode.md5` | MD5 sidecar for the embedded G-code. Orca's observed value matched the file. This is an archive-integrity convention, not a security hash or print-safety check. |
| `Metadata/_rels/model_settings.config.rels` | Additional relationship part associated with model settings. |

The tagged writer also defines names for `Metadata/print_profile.config`, embedded preset members such as `machine_settings_*`, `process_settings_*`, `filament_settings_*`, thumbnails, layer-height profiles, and other optional parts. Their presence depends on the kind of package and save/export path. The list of constants in source is not a promise that every `.3mf` contains every file.

In the observed XML, the root 3D model's object ID is `2`, and its component points to object ID `1` in `3D/Objects/Sample-A_1.model`. Orca's settings XML uses `<object id="2">` and `<part id="1">` and puts the ironing values on that part. The same settings file includes a `<plate>` record whose `gcode_file` points to `Metadata/plate_1.gcode`. This explains why appending only a `<plate>` tag to a generic model package was not enough to create a valid Orca project: plate settings participate in a larger mapping across model IDs, object/part IDs, transforms, profile settings, and artifacts.

The 3MF model's `Application` string in this captured file says `BambuStudio-2.3.0`, while the executable banner and embedded G-code identify `OrcaSlicer 2.3.0`. Orca and Bambu Studio share code and package conventions. Treat that metadata string as an application field, not proof of which executable sliced a file; use captured executable identity, CLI output, and the G-code header together.

## Orca CLI experiment

The installed executable was `C:\Program Files\OrcaSlicer\orca-slicer.exe`, SHA-256 `7055199cc377278f68ccd48f330e8f4dbf873fd8eb9f4c556ad390962265634a`, with banner `OrcaSlicer-01.10.01.50:`. The source model was the previously validated standalone Sample A export; profiles were copied into an isolated evidence folder and passed as read-only input files. Orca's documented `--info` action reported the model dimensions, facet count, part count, and manifold status.

The equivalent argument shape was:

```text
orca-slicer.exe
  --slice 0 --arrange 0 --orient 0
  --outputdir <fresh-output-folder>
  --datadir <fresh-data-folder>
  --load-settings <machine.json;process.json>
  --load-filaments <filament.json>
  --info --export-3mf sample-a.gcode.3mf
  <sample-a.3mf>
```

Orca resolves `--export-3mf`'s filename under `--outputdir`. The first attempt passed an absolute destination; this build prefixed the output directory again and failed to write the archive (exit `4294967283`). Passing the checked basename fixed the issue. The Calibrate-3DP adapter now exposes this as an optional `run_slice(..., inspect_model=True, export_3mf_filename=...)` operation. It rejects paths outside the fresh output folder, captures the command and logs, and reports the export only if the file exists. Normal slicing behavior is unchanged when those optional arguments are omitted.

The successful isolated rerun and opt-in Orca integration test both returned code 0. On the saved test artifact, ZIP CRC validation passed, the Orca JSON/XML parts parsed, the embedded G-code matched the separately written G-code, and its MD5 sidecar matched. The existing Sample A settings validator found 836 ironing moves at 10 mm/s and verified the 12% flow signal. Those checks establish this sample's serialization and setting propagation, not full print readiness.

There is also a known local export defect worth keeping visible: Orca's root relationships refer to `Metadata/plate_1.png` (twice) and `Metadata/plate_1_small.png`, but those images were absent from the captured archive. Its log reports missing rendering shaders and `can not get shader for rendering thumbnail`, yet the process still returns 0 and emits the package. The installed CLI also emits `PartPlate::calc_exclude_triangles: Unable to create exclude triangles` and a parse error for its installed `nozzle_info.json`; the latter warnings appeared on earlier successful generic-import runs too. Therefore a zero exit code is only one signal. The current app's independent settings/layout validators and `print_ready: false` boundary remain in place.

Evidence folder:

```text
%LOCALAPPDATA%\Calibrate-3DP\gate-evidence\2026-10-11\gcode3mf-integration-suite\standalone-sample-import-20261011T180326210972Z\
```

It contains `schema-evidence.json`, the exact argument vector, the package, the G-code, the isolated profile copies, and captured stdout/stderr. The package SHA-256 is `c28b515ffc452628ee2ecfa8198057ef57bebb05735ab2ac207d068d1429b21b`; its G-code SHA-256 is `b1007a500c844a223bf8582465f5bd0a7c6a80d3615ae4585eab55b05f31c137`.

The supplemental relationship-target audit is retained at `%LOCALAPPDATA%\Calibrate-3DP\gate-evidence\2026-10-11\gcode3mf-format-study-20261011T195526\schema-evidence.json`; it records the ZIP/XML checks and the three unresolved thumbnail references. That earlier slice was the same 2.3.0 binary/profile setup, with different timestamps and consequently different G-code/archive hashes.

## Viewing G-code

The 2026-10-11 decision to leave preview in Orca's desktop UI was superseded by the user's 2026-10-12 request for an in-app viewer. The Qt Inspect tab now has a **Preview G-code** action for saved `.gcode` and `.gcode.3mf` artifacts. It bundles Chestnut Labs' existing Vue adapter, parser, and renderers as local files and sends the artifact to a browser worker in chunks after a second SHA-256 check. No G-code goes to the online demo or any network service.

The selected upstream is `@chestnutlabs/gcode-preview-vue` 0.20.1 (MIT), with an adapter for `.gcode`, Orca/Bambu metadata, and `.gcode.3mf`. The package set includes a worker parser, 3D renderer, and a 2D layer renderer. The viewer offers a layer cutoff and travel-move toggle, displays parser layer/segment/coverage summaries, chooses 2D automatically when WebGL is unavailable, and uses 3D when available. The desktop shell already depends on PySide6; the WebEngine is supported from PySide6 6.8, matching the GUI extra's declared minimum. An older installed PySide6 shows a clear upgrade message instead of opening WebEngine.

The library has a small adoption base, so the integration was checked against retained actual output. With PySide6 6.12.0 in an offscreen run, the library reached `Preview ready` for both a local `.gcode` fixture and the retained 2.3.0 `sample-a.gcode.3mf` artifact (package SHA-256 `c28b515ffc452628ee2ecfa8198057ef57bebb05735ab2ac207d068d1429b21b`). The offscreen host has no usable WebGL context and therefore checked the library's 2D layer fallback; a normal desktop GPU's 3D rendering still needs a visual check. The viewer is display-only: it is not an Orca-format validator or a safety check and does not change the run's validation or `print_ready: false` state. Exact package provenance, license and dependency review, test commands, and limits are recorded in [REUSE_RESEARCH.md](REUSE_RESEARCH.md) and [PROGRESS_HISTORY.md](PROGRESS_HISTORY.md).

Alternatives reviewed were the Alpha PyPI `gcode-viewer` GUI (PyVista/NumPy/PyQt6 dependency mismatch) and Polar3D's viewer (its license requires a visible linked attribution). The standalone `@chestnutlabs/gcode-preview-element` tarball lagged its Vue package at 0.5.0, so the app uses the Vue 0.20.1 adapter to keep parser/render package versions consistent. Orca's own **File → Import → Import Sliced G-code** route remains a local alternate.

## What Calibrate-3DP writes

The sample-export service writes a generic model-import 3MF with the standard root package relationship, a `3D/3dmodel.model`, one selected mesh/build item, and optional `Metadata/model_settings.config` whose object and part IDs match the model. App provenance lives in `Metadata/calibrate3dp-sample.json`. It does not include G-code, Orca project settings, plate records, or embedded profiles. Orca uses the profiles selected when the model is imported. See [ORCA_3MF_COMPATIBILITY.md](ORCA_3MF_COMPATIBILITY.md) for the recorded import, grouped-override, and rejected project-metadata experiments.

Do not copy native Orca project tags into a generic export without a versioned fixture and a real import/slice gate. Our 2.3.0 CLI experiments found that adding `<plate>` project records to a generic model caused a crash; removing them restored the generic model-import route. No compatibility or hardware-safety claim is made for untested versions, printer profiles, or physical prints.
