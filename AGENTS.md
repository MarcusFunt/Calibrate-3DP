# Agent Instructions

## Before implementation

1. Read docs/V1_GOAL.md, IMPLEMENTATION_PLAN.md, and docs/IMPLEMENTATION_STATUS.md. Read docs/EXPERIMENT_CONFIGURATION_PROPOSAL.md for context, but treat it as a proposal pending a recorded design decision.
2. Read the relevant sections of docs/PROGRESS_HISTORY.md before repeating an experiment or changing an existing decision.
3. Treat the latest user instruction as authoritative when older plans conflict. The current V1 scope is manual and local.
4. Check the actual repository state before editing. Do not infer implementation from a plan checkbox or stale README text.
5. Keep changes task-sized and update the corresponding plan checkbox only after verification.

## Product boundaries

- V1 has no camera workflow, automatic measurements, computer vision, automatic scoring, printer control, cloud service, or firmware writes.
- Use PySide6/Qt for the desktop application. The Dear PyGui application shell has been removed; its remaining page adapters are transitional and must not be exposed as a supported UI or described as the V1 UI.
- Use OrcaSlicer as the final slicer. Do not mutate source Orca profiles or activate exported profiles automatically.
- V1 assessments are entered by the user. Preserve uncertain, incomplete, tied, rejected, and accepted results explicitly.
- Every physical plate receives a unique six-character code. Every sample is labeled and mapped to exact candidate settings in saved records.
- Do not claim a generated plate is ready to print unless its Orca job and G-code pass the versioned validation contract.

## Architecture boundaries

- Keep calibration definitions, dependency evaluation, experiment compilation, geometry planning, assessments, and export policy independent from Qt widgets.
- Keep all Orca process invocation behind the slicer adapter. Use argument arrays, isolated job data, cancellation, timeouts, captured logs, and exact version identity.
- Treat profile values, slicer capabilities, printer geometry, nozzle/toolhead, material, and accepted calibration results as contextual inputs to dependency evaluation.
- Make plans and run records versioned and immutable after generation. A refinement creates a child plan/run; it does not rewrite historical data. Keep the experiment configuration, compiled sample map, geometry output, Orca project, and G-code as distinct layers; do not merge them into one opaque format. The proposed schema/backend/storage split remains undecided until the documented spike closes.
- Prefer existing standard-library code and current adapters. Add a runtime dependency only when the implementation plan explains its value, license, packaging impact, and tests.
- Do not copy external code or model assets without recording their source and exact license in docs/REUSE_RESEARCH.md.

## Documentation and experiment record

- Append to docs/PROGRESS_HISTORY.md after every meaningful implementation task, experiment, test campaign, architecture decision, assumption change, or blocker.
- Include repository baseline, plan task IDs, files changed, concise rationale, assumptions, exact commands, result summary, and links/paths to logs or artifacts.
- Distinguish tests actually rerun from results copied from earlier status reports. Record skipped tests and why.
- Do not overwrite previous history entries. Add a correction entry instead.
- Record decision-level engineering rationale and evidence. Do not attempt to store private chain-of-thought.
- Update docs/IMPLEMENTATION_STATUS.md when implementation status changes. Keep README.md factual about current code; target behavior belongs in docs/V1_GOAL.md.
- If a plan requirement changes, update the spec/plan and add an entry explaining the evidence and impact.

## Verification and handoff

- Run the narrow test for each changed behavior, then the full relevant suite.
- For Qt changes, include an offscreen test and report missing GUI dependencies clearly.
- For Orca changes, retain the exact executable/version, profiles, CLI arguments, logs, G-code checks, and artifact hashes.
- Do not infer hardware safety from a successful process exit alone.
- Before handoff, state the files changed, tests/experiments actually run, their outcomes, and remaining blockers.
