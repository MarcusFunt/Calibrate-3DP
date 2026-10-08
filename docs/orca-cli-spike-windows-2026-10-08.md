# OrcaSlicer Windows CLI spike

Date: 2026-10-08
Repository baseline: `origin/main` at `8f275d801afebc24f430c5aa4396bf21614bfd4b`
Environment: Marcus's Windows computer; installed OrcaSlicer and stock Creality profile resources

## Result

The headless flow can resolve stock machine, process, and filament inheritance, create fully flattened candidate JSON, generate one coupon per candidate, and slice each candidate independently with Orca CLI. This validates the separate-plate fallback for the tested Creality profile set.

The integrated run used the stock `Creality Ender-3 V2 0.4 nozzle`, `0.20mm Standard @Creality Ender3V2`, and `Creality Generic PLA` profiles. The selected process resolved through three-level inheritance. Its `ironing_flow` baseline was `15%` and `ironing_speed` was `15`; the stock preset had ironing disabled, so the run explicitly set `ironing_type` to `top` in both the plan baseline and the CLI candidate profiles. A 3 × 3 grid varied flow over `12%, 15%, 18%` and speed over `10, 15, 20`.

All nine candidate jobs returned exit status 0 and produced G-code. Each output included an ironing section, the candidate's expected `ironing_flow` and `ironing_speed` values in Orca's G-code config comments, and a separate coupon, process JSON, output directory, and isolated data directory. The run manifest records the profile inheritance chains, effective-settings hashes, argv, exit status, logs, and output G-code hashes.

The integration probe checked that generated moves stayed within X=2.0–124.8 mm and Y=10.0–176.0 mm on the stock 220 × 220 mm bed. It found the resolved start sequence and the resulting G-code commands for homing, bed/nozzle temperatures, and the end sequence (`M140 S0`, `M104 S0`, `M107`, `M84 X Y E`). These checks apply only to this test profile and generated coupon; they are not a general G-code safety validator.

## Orca identity and behavior notes

The executable's `--help` banner reported `OrcaSlicer-01.10.01.50:`; generated G-code said `OrcaSlicer 2.3.0`. The relationship between these version labels is unresolved. No broader release compatibility claim follows from this run.

The installed CLI rejected `--logfile`, so the runner captures process stdout and stderr instead. In an earlier closed-cube smoke slice Orca printed `PartPlate::calc_exclude_triangles: Unable to create exclude triangles` while returning 0 and producing G-code. The candidate coupon run also returned success, but its logs should be reviewed if this warning appears for another geometry.

A Bambu A1 profile run failed Orca layer-G-code validation, including after machine, process, and filament inheritance had been flattened. That profile set is not currently supported by evidence from this spike.

## Scope not verified

- Per-object/per-pad overrides in a generated 3MF project.
- Equivalence against effective configuration exported from Orca's UI.
- Other printer profiles, Orca releases, or non-Windows platforms.
- A complete G-code validator for arbitrary beds, keep-outs, temperatures, or start/end macros.

The implementation therefore slices independent candidate plates. It does not depend on 3MF overrides.

To repeat the local integration test on this Windows installation, set `ORCA_SLICER_EXE` to the installed executable and `ORCA_PROFILE_ROOT` to Orca's profile tree with `machine\`, `process\`, and `filament\` directories, then run `D:\projects\Test-Calibrate-3DP.ps1 -ProjectPath D:\projects\Calibrate-3DP-worktrees\orca-integration`. The default profile names are the three stock Creality presets listed above; the test accepts alternate names through `ORCA_MACHINE_PROFILE`, `ORCA_PROCESS_PROFILE`, and `ORCA_FILAMENT_PROFILE`.
