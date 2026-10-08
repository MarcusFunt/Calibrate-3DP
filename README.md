# Calibrate-3DP

Local-first, semi-automatic calibration workbench for FDM printers using OrcaSlicer as the slicing engine. The application will keep experiment generation, manual result entry, adaptive iteration, and profile export under its own control.

The first implementation slice is the non-graphical profile-resolution core. It resolves inherited preset values with per-setting provenance and fails explicitly for missing, ambiguous, duplicate, or cyclic references. No GUI or printer-control integration is included yet.

## Development

```sh
PYTHONPATH=src python -m unittest discover -s tests -v
```
