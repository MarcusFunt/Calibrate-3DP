# Implementation status

## Current slice: profile-resolution core

- [x] Add a source-layout Python package with no runtime dependencies.
- [x] Resolve parent profile settings and retain per-key source provenance.
- [x] Preserve unknown settings and original raw profile documents.
- [x] Reject missing parents, cycles, duplicate identities, and ambiguous name-only selection.
- [x] Require callers to declare cross-scope inheritance rules.
- [ ] Add JSON/profile bundle loading and versioned Orca adapters.
- [ ] Run the Orca CLI inheritance and generated-3MF override spike against installed Orca versions.

## Current limitation

The current environment does not have an OrcaSlicer executable. The resolver is covered with deterministic Python tests, but CLI behavior, inherited-profile equivalence to the Orca UI, and per-object 3MF overrides remain unverified and are still the first integration gate from `IMPLEMENTATION_PLAN.md`.

No graphical user interface has been started.
