# 3MF compatibility with OrcaSlicer

For a package walkthrough, the exact CLI command/output, and the local G-code-viewing workflow, see [OrcaSlicer 3MF format and CLI notes](ORCA_3MF_FORMAT_GUIDE.md).

Calibrate-3DP exports a **3MF model for import into a slicer**. It is not a complete OrcaSlicer **Save Project** snapshot.

## Package contents

- `[Content_Types].xml` and `_rels/.rels` connect the package to `3D/3dmodel.model`, the standard 3MF model document.
- The model document stores mesh resources and build items. Each object has one build instance on the plate.
- `Metadata/model_settings.config` stores Orca-compatible object names and per-part settings. Object and part IDs match the corresponding 3MF resource IDs; ironing flow and speed stay on the selected sample part.
- `Metadata/calibrate3dp-run.json` or `Metadata/calibrate3dp-sample.json` stores Calibrate-3DP provenance. It is not consumed as slicer configuration.

The sample export intentionally leaves out `Metadata/project_settings.config`, embedded machine/process/filament profiles, and Orca's `<plate>` / `<model_instance>` project records. Those fields belong to Orca's saved-project workflow. The sample export has no captured Orca profile set to serialize, so Orca uses the profiles selected in the slicer when the model is imported. Orca's documentation distinguishes importing a model from opening a saved project, and its writer stores the project settings and plate/object links separately from core model geometry ([Import and Export](https://github.com/OrcaSlicer/OrcaSlicer/wiki/import_export), [OrcaSlicer 3MF writer, v2.4.2](https://github.com/OrcaSlicer/OrcaSlicer/blob/v2.4.2/src/libslic3r/Format/bbs_3mf.cpp)).

## Compatibility verification

The installed OrcaSlicer 2.3.0 CLI successfully imported and sliced the standalone Sample A export. The test confirmed Sample A's 10 mm/s ironing speed and 12% relative ironing flow in generated G-code. A separate grouped-plate run verified all nine sample overrides and its negative fixture rejected the missing Sample E override.

During compatibility investigation, adding Orca's native `<plate>` records to a Calibrate-3DP model-only package caused the installed Orca 2.3.0 process to exit with `3221225477` (`0xC0000005`). The same package sliced successfully after those project-only records were removed; the result was reproduced with metadata-only plate records, without `identify_id`, without `<assemble>`, and with an empty `project_settings.config`. Calibrate-3DP therefore keeps these exports in the model-import shape and does not label them as Orca-authored projects.

The evidence applies to the exact executable and captured Creality machine, process, and filament profiles recorded in `docs/PROGRESS_HISTORY.md`. It does not establish support for every Orca release/profile combination, printing safety, or physical printability. The export continues to record `print_ready: false`.
