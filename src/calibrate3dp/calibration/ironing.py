"""Prerequisite rules for the currently implemented ironing flow × speed sweep."""

from __future__ import annotations

from calibrate3dp.calibration.dependencies import DependencyGraph, DependencyRule


IRONING_CALIBRATION_ID = "ironing"
IRONING_DEPENDENCY_RULESET_ID = "ironing-flow-speed-context"
IRONING_DEPENDENCY_RULESET_VERSION = 1

IRONING_DEPENDENCY_RULES = (
    DependencyRule(
        "saved-printer-material-resolves",
        IRONING_CALIBRATION_ID,
        "input",
        "printer_material_state",
        "equals",
        "resolved",
        invalidates=("printer_id", "material_id"),
    ),
    DependencyRule(
        "printer-material-nozzle-matches",
        IRONING_CALIBRATION_ID,
        "input",
        "nozzle_context_state",
        "equals",
        "matches",
        invalidates=("printer_nozzle", "material_nozzle_context"),
    ),
    DependencyRule(
        "selected-profile-context-matches",
        IRONING_CALIBRATION_ID,
        "input",
        "profile_context_state",
        "equals",
        "matches",
        invalidates=("profile_context_identity",),
    ),
    *(
        DependencyRule(
            f"{role}-profile-hash-current",
            IRONING_CALIBRATION_ID,
            "input",
            f"profile_hash_state.{role}",
            "equals",
            "unchanged",
            invalidates=(f"source_hashes.current.{role}",),
        )
        for role in ("printer", "process", "filament")
    ),
    DependencyRule(
        "orca-slicer-available",
        IRONING_CALIBRATION_ID,
        "input",
        "slicer_state",
        "equals",
        "available",
    ),
)

IRONING_DEPENDENCY_GRAPH = DependencyGraph.from_rules(IRONING_DEPENDENCY_RULES)
