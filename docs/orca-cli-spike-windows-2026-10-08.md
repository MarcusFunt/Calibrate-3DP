> Historical, installation-specific evidence only. This spike does not establish a supported Orca version or profile matrix. Current implementation state and later probe results are in IMPLEMENTATION_STATUS.md. Per-object 3MF overrides and V1 grouped plates were not tested here.

# OrcaSlicer Windows CLI spike

Date: 2026-10-08
Repository baseline at the time: origin/main at 8f275d801afebc24f430c5aa4396bf21614bfd4b
Environment: Marcus's Windows computer; installed OrcaSlicer and stock Creality profile resources

## Result

The headless flow resolved stock machine, process, and filament inheritance, created fully flattened candidate JSON, generated one coupon per candidate, and sliced each candidate independently with Orca CLI. This validates the separate-candidate path only for the tested Creality profile set.

The integrated run used:
- Creality Ender-3 V2 0.4 nozzle.
- 0.20mm Standard @Creality Ender3V2 process.
- Creality Generic PLA filament.

The selected process resolved through three levels of inheritance. Its ironing flow baseline was 15% and ironing speed was 15. The stock preset had ironing disabled, so the run explicitly set ironing type to top in both the plan baseline and candidate profiles. A 3 × 3 grid varied flow over 12%, 15%, 18% and speed over 10, 15, 20.

All nine candidate jobs returned exit status 0 and produced G-code. Each output included an ironing section and the expected flow and speed values in Orca's G-code configuration comments. Each job had a separate coupon, process JSON, output directory, isolated data directory, and manifest entry.

The probe checked generated moves within X=2.0–124.8 mm and Y=10.0–176.0 mm on the tested 220 × 220 mm bed. It checked the resolved start sequence, homing, bed/nozzle temperatures, and end commands M140 S0, M104 S0, M107, and M84 X Y E. These checks apply only to this test profile and coupon; they are not a general G-code safety validator.

## Identity and limitations

The executable help banner reported OrcaSlicer-01.10.01.50 while generated G-code identified OrcaSlicer 2.3.0. The relationship remains unexplained. The installed CLI rejected --logfile, so the runner captured stdout/stderr directly. A previous closed-cube smoke slice printed an exclude-triangle warning while returning success and producing G-code; inspect logs when it appears.

A Bambu A1 profile run failed Orca layer-G-code validation after direct and flattened profile loading. Bambu support was not established.

Not tested:
- Per-object/per-pad overrides in a generated 3MF.
- Equivalence with Orca UI effective settings.
- Other printer profiles, Orca releases, or non-Windows platforms.
- General G-code safety checks for arbitrary beds, keep-outs, temperatures, and start/end macros.

To repeat, set ORCA_SLICER_EXE and ORCA_PROFILE_ROOT to an installed executable and profile tree containing machine, process, and filament directories, then run the repository's opt-in Orca integration test. See the test file for profile-name overrides.
