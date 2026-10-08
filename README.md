# Calibrate-3DP

Local-first, semi-automatic calibration workbench for FDM printers using OrcaSlicer as the slicing engine. The application will keep experiment generation, manual result entry, adaptive iteration, and profile export under its own control.

The current implementation is a headless calibration core. It imports Orca profile JSON and ZIP-based bundles without discarding unknown fields, resolves inheritance with per-setting provenance, and builds flattened candidate process profiles. It also creates deterministic experiment grids, records manual candidate assessments, and proposes narrower ironing flow/speed tests when a result is rejected. Plans and results have versioned JSON representations.

The ironing runner consumes an explicit resolved machine, process, and filament baseline. It generates one flat top-surface coupon per candidate, invokes Orca headlessly with isolated data and output folders, and saves candidate profiles, G-code, logs, hashes, and a run manifest. If the selected profile disables ironing, callers must explicitly enable it in the process baseline patch and record that value in the plan; the runner does not turn it on silently. Separate candidate plates are the current slicing path; per-object 3MF overrides are still unverified. The Windows integration spike passed with the stock Creality Ender-3 V2 profile set. The CLI wrapper uses no GUI or printer-control integration.

## Headless ironing workflow

```python
from calibrate3dp.experiments import CandidateAssessment, ExperimentResults
from calibrate3dp.ironing import (
    create_initial_ironing_experiment,
    propose_ironing_refinement,
)

# In the eventual profile workflow, pass the explicitly resolved Orca settings.
baseline = {
    "ironing_flow": "10",
    "ironing_speed": "30",
    "ironing_pattern": "rectilinear",
    "ironing_spacing": "0.1",
}
plan = create_initial_ironing_experiment(
    plan_id="ironing-001",
    baseline_settings=baseline,
    flow_values=(8, 10, 12),
    speed_values=(20, 30, 40),
)

# Save this payload with json.dump. With resolved Orca profiles and an
# OrcaCli instance, slice_ironing_experiment generates one candidate job per
# coupon and records the results in a run manifest.
plan_payload = plan.to_dict()

# After printing and judging candidate I005 manually:
results = ExperimentResults(
    plan_id=plan.plan_id,
    assessments=(CandidateAssessment("I005", ratings={"finish": 4}),),
    selected_candidate_id="I005",
    accepted=False,
)
results_payload = results.to_dict()

# A rejected result narrows around an interior winner and halves both steps.
next_plan = propose_ironing_refinement(
    plan,
    results,
    next_plan_id="ironing-002",
    limits={"ironing_flow": (0, 25), "ironing_speed": (1, 100)},
)
```

`ExperimentPlan.settings_for(candidate_id)` returns the complete setting map for a print candidate. Other settings remain at that plan's baseline. Refinements require a manually assessed selected candidate and are only proposed while `accepted=False`; accepting a result closes that calibration loop.

## Development

```sh
PYTHONPATH=src python -m unittest discover -s tests -v
```

The real Orca integration test is skipped unless `ORCA_SLICER_EXE` and `ORCA_PROFILE_ROOT` point to an installed slicer and a profile tree. See `tests/test_orca_slicer_integration.py` and the Windows spike report for the tested profile names.
