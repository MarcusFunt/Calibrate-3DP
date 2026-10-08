"""Dear PyGui table for exact candidate settings and independent plate labels."""

from __future__ import annotations

from typing import Any, Sequence

from calibrate3dp.app.services.experiment_service import CandidatePlateEntry


def render_candidate_table(
    dpg: Any,
    parent: str,
    plate_map: Sequence[CandidatePlateEntry],
) -> None:
    """Replace the review table with the current candidate-to-plate map."""
    if dpg.does_item_exist("candidate_matrix_table"):
        dpg.delete_item("candidate_matrix_table")
    with dpg.table(
        parent=parent,
        tag="candidate_matrix_table",
        header_row=True,
        borders_innerH=True,
        borders_outerH=True,
        borders_innerV=True,
        borders_outerV=True,
        row_background=True,
        resizable=True,
        policy=dpg.mvTable_SizingStretchProp,
    ):
        for label in ("Candidate", "ironing_flow", "ironing_speed", "Plate", "Specimen label"):
            dpg.add_table_column(label=label)
        for entry in plate_map:
            with dpg.table_row():
                dpg.add_text(entry.candidate_id)
                dpg.add_text(str(entry.flow_value))
                dpg.add_text(str(entry.speed_value))
                dpg.add_text(entry.plate_label)
                dpg.add_text(entry.specimen_label)


def show_empty_candidate_table(dpg: Any, parent: str) -> None:
    """Render the table header before a module creates a plan."""
    render_candidate_table(dpg, parent, ())
