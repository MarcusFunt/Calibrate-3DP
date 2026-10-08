# Calibrate-3DP

Local-first, semi-automatic calibration workbench for FDM printers using OrcaSlicer as the slicing engine. The application will keep experiment generation, manual result entry, adaptive iteration, and profile export under its own control.

The current implementation is the non-graphical calibration core. It loads Orca-style JSON presets, resolves inherited values with per-setting provenance, and creates source-preserving candidate profiles with explicit settings patches. It also builds deterministic experiment grids, records manual candidate assessments, and proposes narrower ironing flow/speed tests when the user rejects the current result. Plans and results have versioned JSON representations so a future interface can save and resume a calibration session.

The ironing planner consumes an explicit baseline and sweep values. It does not choose hidden slicer defaults. It produces candidate settings and plans, but it does not generate coupon geometry or G-code yet. Orca CLI integration, printer control, and all graphical UI work remain out of scope for this slice.

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

# Save this payload with json.dump, or use each candidate's settings for
# candidate-profile/G-code generation when the Orca adapter is implemented.
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
