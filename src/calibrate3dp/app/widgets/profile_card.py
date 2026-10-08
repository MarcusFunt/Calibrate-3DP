"""Profile selector card with a scrollable provenance inspector."""

from __future__ import annotations

from typing import Any, Callable, Sequence

from calibrate3dp.app.services.profile_service import ProfileChoice


ROLE_LABELS = {
    "printer": "PRINTER",
    "filament": "FILAMENT",
    "process": "PROCESS",
}


def add_profile_card(
    dpg: Any,
    role: str,
    choices: Sequence[ProfileChoice],
    *,
    on_select: Callable[..., Any],
    on_import: Callable[..., Any],
) -> None:
    """Add one role card and the profile JSON/bundle picker for that card."""
    if role not in ROLE_LABELS:
        raise ValueError(f"unknown profile card role {role!r}")
    labels = [choice.label for choice in choices]
    with dpg.child_window(width=286, height=338, border=True, tag=f"profile_card_{role}"):
        dpg.add_text(ROLE_LABELS[role], color=(92, 191, 178, 255))
        dpg.add_spacer(height=5)
        dpg.add_combo(
            items=labels,
            default_value="",
            width=-1,
            tag=f"profile_choice_{role}",
            callback=on_select,
            user_data=role,
        )
        dpg.add_button(
            label="Import JSON or Orca bundle",
            width=-1,
            callback=on_import,
            user_data=role,
        )
        dpg.add_spacer(height=5)
        dpg.add_text("Choose a profile to inspect its resolved values.",
                     tag=f"profile_summary_{role}", wrap=250,
                     color=(165, 180, 195, 255))
        dpg.add_spacer(height=5)
        with dpg.child_window(height=190, width=-1, border=False,
                              tag=f"profile_provenance_panel_{role}"):
            dpg.add_text("Effective settings and their source will appear here.",
                         tag=f"profile_provenance_{role}", wrap=250,
                         color=(165, 180, 195, 255))

    with dpg.file_dialog(
        tag=f"profile_import_dialog_{role}",
        show=False,
        modal=True,
        width=760,
        height=480,
        directory_selector=False,
        callback=on_import,
        user_data={"role": role, "dialog": True},
    ):
        dpg.add_file_extension(".json", custom_text="[Orca JSON preset]")
        dpg.add_file_extension(".zip", custom_text="[ZIP profile bundle]")
        dpg.add_file_extension(".orca_printer", custom_text="[Orca printer bundle]")
        dpg.add_file_extension(".orca_filament", custom_text="[Orca filament bundle]")
        dpg.add_file_extension(".orca_filaments", custom_text="[Orca filament bundle]")
        dpg.add_file_extension(".orca_bundle", custom_text="[Orca profile bundle]")


def update_profile_choices(
    dpg: Any,
    role: str,
    choices: Sequence[ProfileChoice],
    selected: ProfileChoice | None = None,
) -> None:
    dpg.configure_item(
        f"profile_choice_{role}",
        items=[choice.label for choice in choices],
        default_value=selected.label if selected else "",
    )


def update_profile_details(dpg: Any, role: str, summary: str, provenance: str) -> None:
    dpg.set_value(f"profile_summary_{role}", summary)
    dpg.set_value(f"profile_provenance_{role}", provenance)
