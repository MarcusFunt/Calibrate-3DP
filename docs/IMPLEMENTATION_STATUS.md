# Implementation status

## Current slice: headless profile and experiment core

- [x] Add a source-layout Python package with no runtime dependencies.
- [x] Resolve parent profile settings and retain per-setting source provenance.
- [x] Preserve unknown settings and original raw profile documents.
- [x] Clone a source profile under a new name with only explicit, non-identity settings patched.
- [x] Reject missing parents, cycles, duplicate identities, and ambiguous name-only selection.
- [x] Require callers to declare cross-scope inheritance rules.
- [x] Build deterministic Cartesian calibration grids with stable candidate IDs.
- [x] Validate manual ratings, defect tags, winner selection, and result-to-plan matching.
- [x] Serialize plans and manual results through versioned JSON-compatible payloads.
- [x] Create an explicit-baseline ironing flow/speed sweep.
- [x] Propose a follow-up ironing sweep by halving the step, extending a boundary winner, and enforcing optional limits.
- [ ] Add JSON/profile bundle loading and versioned Orca adapters.
- [ ] Generate calibration coupons and slice candidate-specific G-code through OrcaSlicer.
- [ ] Run the Orca CLI inheritance and generated-3MF override spike against installed Orca versions.

## Current limitation

The current environment does not have an OrcaSlicer executable. The resolver, experiment contracts, and ironing refinement logic are covered with deterministic Python tests, but CLI behavior, inherited-profile equivalence to the Orca UI, candidate print generation, and per-object 3MF overrides remain unverified. Those Orca integration checks remain the first external gate from `IMPLEMENTATION_PLAN.md`.

Experiment and result payloads can be saved and resumed as JSON, but there is not yet a session store or schema migration path. Ironing candidates are numeric setting sweeps only; visual assessment is still entered manually. No graphical user interface or printer-control integration has been started.
