# Grouped Orca gate and Qt library workflow

## Goal

Verify grouped sample settings in real Orca output, then connect saved local printer/material profiles and a grouped ironing run/history workflow to the PySide6 application.

## Decisions in scope

- Keep the first grouped 3MF implementation in Python's standard library. It demonstrates Orca's object/part metadata mapping for simple separate coupons; it does not settle the final connected plate CAD backend.
- Store typed printer, material, and calibration-run records in SQLite, keeping the current session rows readable through an explicit schema migration. Keep 3MF, profiles used for slicing, logs, G-code, and manifests as workspace files referenced by relative path, size, and SHA-256.
- Snapshot resolved profiles and provenance when records/runs are created. Preserve plans, plate codes, and sample maps after generation. A code collision is rejected by a database uniqueness constraint.
- The first Qt workflow supports local Orca profile import/selection, printer/material saving, a grouped ironing sweep, and run-history inspection. It records whether sample settings were validated; the current coupon package has no printed labels/connectors and does not qualify a plate as ready to print.

## Work sequence

1. Turn the successful three-object Orca probe into a reusable 3MF builder, G-code validator, and opt-in real-Orca test with a real negative case.
2. Add immutable profile snapshots and typed printer/material/run records, with transactional SQLite migration from the existing session schema.
3. Add a headless library and grouped-generation service. Retain exact profiles, CLI identity/arguments/logs, output hashes, and per-sample G-code evidence.
4. Replace the Qt empty/placeholder flow with profile intake, saved printer/material selection, run generation, and run history. Keep Orca work off the UI thread.
5. Run focused storage, service, grouped-plate, and offscreen Qt tests, the opt-in installed-Orca gate, then the full relevant suite. Update implementation status and append exact outcomes to the progress history.

## Verification evidence required

- A standard-library fixture confirms each 3MF settings part refers to its intended mesh object.
- Real Orca output shows the requested per-sample ironing speeds and flow-proportional extrusion for all samples. Omitting one sample's override produces output that the validator rejects.
- SQLite migration leaves existing session rows intact; duplicate plate codes and attempts to rewrite finalized run inputs are rejected.
- A Qt offscreen interaction exercises profile saving and navigation/run workflow boundaries without opening SQLite or Orca from widget code.
- The full test suite and the exact local Orca binary/profile gate are rerun; skipped checks are reported as skipped.
