# Implementation status

## Current slice: headless profile-aware ironing generation

- [x] Add a source-layout Python package with no runtime dependencies.
- [x] Resolve parent profile settings and retain per-setting source provenance.
- [x] Preserve unknown settings and original raw profile documents.
- [x] Clone a source profile under a new name with only explicit, non-identity settings patched.
- [x] Reject missing parents, cycles, duplicate identities, and ambiguous name-only selection.
- [x] Require callers to declare cross-scope inheritance rules.
- [x] Build deterministic calibration grids, validate manual results, and propose ironing refinements.
- [x] Serialize plans and manual results through versioned JSON-compatible payloads.
- [x] Import individual Orca JSON presets and ZIP-based profile bundles through adapter version 1.
- [x] Build fully resolved candidate JSON profiles without changing source profiles.
- [x] Generate one deterministic flat-top coupon and isolated Orca CLI job per ironing candidate.
- [x] Save effective profile snapshots, candidate G-code hashes, logs, and the exact invocation in a versioned run manifest.
- [x] Exercise inherited profile loading, explicit ironing activation, and candidate-specific G-code on Marcus's Windows Orca installation with the stock Creality Ender-3 V2 profiles.
- [x] Add an opt-in local integration test for installed OrcaSlicer binaries.
- [ ] Verify Orca UI/effective-config equivalence and establish a supported-version matrix.
- [ ] Test generated-3MF per-object overrides. The current implementation uses separate candidate plates as the fallback path.

## Verified integration and current limits

The Windows spike is recorded in [orca-cli-spike-windows-2026-10-08.md](orca-cli-spike-windows-2026-10-08.md). The stock process disabled ironing, so the run explicitly enabled top-surface ironing and recorded that setting in the plan baseline. Nine candidates then sliced successfully; each G-code file contains an ironing section and the expected flow and speed. The probe also checked the stock start/end sequences, bed/nozzle temperatures, and XY coordinates against the Creality 220 × 220 mm bed.

The Orca help banner reported `OrcaSlicer-01.10.01.50:`, while generated G-code identified itself as `OrcaSlicer 2.3.0`. That version identity mismatch has not been explained, so this is evidence for the tested installation only, not a support matrix. An earlier Bambu A1 profile probe failed Orca's layer-G-code validation after both direct and flattened profile loading; Bambu support is not claimed. The CLI build also rejected `--logfile`; the wrapper captures stdout and stderr directly.

Per-object 3MF overrides and comparison against the Orca UI remain unverified. Separate candidate plates are implemented and tested as the current path. The first Dear PyGui application shell now provides Home, New Calibration, Sessions, and Settings navigation; profile selection, session persistence, results entry, and export are not connected to the GUI yet. There is no printer-control integration or automated print start. The experiment assessment remains manual.
