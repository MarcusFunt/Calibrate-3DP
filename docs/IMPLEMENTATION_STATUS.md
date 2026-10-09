# Implementation status

## Current slice: saved desktop calibration workflow

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
- [x] Verify the full headless suite in an isolated environment without Dear PyGui (137 passed; one opt-in integration test skipped).
- [x] Add the Dear PyGui desktop shell with Home, New Calibration, Sessions, and Settings navigation.
- [x] Connect reviewed plans to persistent sessions, recent-session status, resume, and non-destructive archive.
- [x] Persist application settings for the workspace, default export folder, diagnostics inclusion, and Orca overrides.
- [x] Add saved-step recovery messaging for missing workspaces or moved session artifacts.
- [x] Add keyboard navigation shortcuts and a visible keyboard focus highlight.
- [x] Add reviewed result entry, recommendation, confirmation, and profile-export screens.
- [x] Pass the headless suite (137 tests; one opt-in Orca integration test skipped).
- [ ] Verify Orca UI/effective-config equivalence and establish a supported-version matrix.
- [ ] Test generated-3MF per-object overrides. The current implementation uses separate candidate plates as the fallback path.
- [x] Register the cancellable Orca generation service; Start requires a successful CLI capability probe.
- [ ] Verify keyboard navigation, screen scaling at 100/150/200 percent, and native file dialogs on Windows and Linux.
- [ ] Run the full import-to-export acceptance workflow with an installed supported Orca version.

## Verified integration and current limits

The Windows spike is recorded in [orca-cli-spike-windows-2026-10-08.md](orca-cli-spike-windows-2026-10-08.md). The stock process disabled ironing, so the run explicitly enabled top-surface ironing and recorded that setting in the plan baseline. Nine candidates then sliced successfully; each G-code file contains an ironing section and the expected flow and speed. The probe also checked the stock start/end sequences, bed/nozzle temperatures, and XY coordinates against the Creality 220 × 220 mm bed.

The Orca help banner reported `OrcaSlicer-01.10.01.50:`, while generated G-code identified itself as `OrcaSlicer 2.3.0`. That version identity mismatch has not been explained, so this is evidence for the tested installation only, not a support matrix. An earlier Bambu A1 profile probe failed Orca's layer-G-code validation after both direct and flattened profile loading; Bambu support is not claimed. The CLI build also rejected `--logfile`; the wrapper captures stdout and stderr directly.

On 2026-10-09, the headless suite passed in a clean Python virtual environment with Dear PyGui absent (137 tests passed; the opt-in Orca integration test was skipped). Dear PyGui 2.3.1 is installed and its context initializes successfully. A separate attempt to repeat the Orca integration test with the executable currently detected on this machine stopped before slicing because `--help` returned no text. Setup now prefers `orca-slicer-console` when it is available and gives a direct recovery message when a graphical launcher does not expose the CLI. The desktop launcher registers the cancellable generation service, but its Start action stays disabled until a successful CLI capability probe. A failed probe exposes setup recovery; rechecking Orca refreshes the preview gate. The slice runner streams stdout/stderr, terminates a running process on cancellation, and retains manifests, logs, and partial output in the session. The successful 2026-10-08 spike remains installation-specific evidence; an end-to-end desktop generation run and supported-version matrix are still open.

Per-object 3MF overrides and comparison against the Orca UI remain unverified. Separate candidate plates are implemented and tested as the current path. New Calibration detects local Orca setup, supports read-only JSON and profile-bundle import, and displays scoped printer, filament, and process profiles with inherited values, source hashes, and per-setting provenance. The profile step blocks on unresolved inheritance or missing ironing baseline values. Ironing Finish offers a baseline-relative 3 × 3 grid, editable values, fixed-setting review, and a separate-plate candidate map; Bridge and Support remain disabled. Home and Sessions now read the SQLite session index, show persisted workflow statuses, resume the last saved step, and archive without deleting session files. Accepting a reviewed plan creates a durable session. Settings are stored as local JSON, including workspace/export paths, diagnostics privacy options, and Orca path overrides. Results, recommendation, confirmation, and export pages are implemented and persist their state. The generation view is wired to a concrete service with capability-gated previews, background slicing, streamed stdout/stderr, cancellation, session-persisted run artifacts, and configured G-code validation; the currently detected Orca executable fails its help probe, so Start remains disabled on this machine. No printer-control integration or automated print start is available. Candidate assessment remains manual. Cross-platform accessibility, the supported Orca matrix, and the end-to-end acceptance workflow remain open.
