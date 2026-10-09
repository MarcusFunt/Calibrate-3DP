# Calibrate-3DP

Local-first, semi-automatic calibration workbench for FDM printers using OrcaSlicer as the slicing engine. The first Dear PyGui desktop workflow now covers profile setup, plan review, durable sessions, result entry, recommendations, and reviewed profile export.

The repository includes a headless calibration core and a first desktop UI. The core imports Orca profile JSON and ZIP-based bundles without discarding unknown fields, resolves inheritance with per-setting provenance, and builds flattened candidate process profiles. It also creates deterministic experiment grids, records manual candidate assessments, and proposes narrower ironing flow/speed tests when a result is rejected. Plans, results, and sessions have versioned JSON representations.

The ironing runner consumes an explicit resolved machine, process, and filament baseline. It generates one flat top-surface coupon per candidate, invokes Orca headlessly with isolated data and output folders, and saves candidate profiles, G-code, logs, hashes, and a run manifest. If the selected profile disables ironing, callers must explicitly enable it in the process baseline patch and record that value in the plan; the runner does not turn it on silently. Separate candidate plates are the current slicing path; per-object 3MF overrides are still unverified. The Windows integration spike passed with the stock Creality Ender-3 V2 profile set. The CLI wrapper uses no GUI or printer-control integration.

## Desktop workbench

Install Python 3.11 or newer and the optional GUI dependency from the repository root:

```sh
python -m pip install -e ".[gui]"
calibrate3dp
```

On Windows PowerShell, create and activate a virtual environment first if desired:

```powershell
py -3.11 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -e ".[gui]"
calibrate3dp
```

Use **New Calibration** to check Orca setup, import or select printer, filament, and process profiles, choose Ironing Finish, and review the candidate matrix. A session is saved when the reviewed plan is accepted. Home shows recent saved statuses; Sessions can resume the saved workflow step or archive a session. Archiving only hides the session from recent lists and does not delete its directory. Settings stores the workspace and export locations, diagnostics inclusion choices, and Orca overrides. A changed workspace is applied after restarting the application. `Ctrl+1` through `Ctrl+4` navigate Home, New Calibration, Sessions, and Settings.

The default workspace is `%LOCALAPPDATA%\Calibrate-3DP\workspace` on Windows, `$XDG_DATA_HOME/Calibrate-3DP/workspace` on Linux (or `~/.local/share/Calibrate-3DP/workspace`), and `~/Library/Application Support/Calibrate-3DP/workspace` on macOS. The workspace contains `sessions.sqlite3` and a `sessions/` directory with each session's profile snapshot, evidence, and generated artifacts. Application settings are stored under the user's configuration directory.

If a saved workspace was moved, set **Workspace root** to the folder containing its database and `sessions/` directory, then restart. If an individual artifact is missing, the app keeps the session and reports the missing relative path; restore the file under that session's directory to recover the evidence. A corrupt or missing profile snapshot is reported when resuming, and the session files are left in place for recovery.

The launcher does not yet register a concrete Orca generation service. The plan and session can be reviewed and saved, but **Start generation** remains disabled until the supported-version adapter is connected. Orca UI/effective-config equivalence and a supported-version matrix are still open gates.

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
