"""Editable baseline-relative candidate matrix and review details."""

from __future__ import annotations

from typing import Any, Callable

from calibrate3dp.app.models import ProfileSelection
from calibrate3dp.experiments import ExperimentPlan
from calibrate3dp.app.services.experiment_service import (
    ExperimentOptions,
    ExperimentReview,
    ExperimentService,
    ExperimentServiceError,
)
from calibrate3dp.app.widgets.candidate_table import render_candidate_table, show_empty_candidate_table


class ExperimentReviewPage:
    """Review exact candidate values, fixed settings, assumptions, and plate labels."""

    def __init__(
        self,
        service: ExperimentService,
        dpg: Any,
        *,
        on_plan_ready: Callable[[ExperimentPlan], None] | None = None,
    ) -> None:
        self.service = service
        self.dpg = dpg
        self.on_plan_ready = on_plan_ready
        self.profiles: ProfileSelection | None = None
        self.plan: ExperimentPlan | None = None
        self.review: ExperimentReview | None = None
        self._dirty = False

    def render(self) -> None:
        dpg = self.dpg
        with dpg.group(tag="experiment_review_panel", show=False):
            dpg.add_spacer(height=14)
            dpg.add_text("REVIEW THE IRONING GRID", color=(92, 191, 178, 255))
            dpg.add_text("", tag="experiment_review_baseline", wrap=850)
            dpg.add_text(
                "Defaults: flow 0.8× / 1.0× / 1.2× baseline; speed ⅔× / 1.0× / 4/3× baseline. "
                "Values are rounded to the imported profile's precision. No unspecified limits are applied.",
                tag="experiment_review_rationale",
                wrap=850,
                color=(165, 180, 195, 255),
            )
            dpg.add_spacer(height=8)
            with dpg.group(horizontal=True):
                self._add_dimension_editor("ironing_flow", "Flow", prefix="flow")
                self._add_dimension_editor("ironing_speed", "Speed", prefix="speed")
            dpg.add_button(label="Apply values and refresh matrix", callback=self._on_apply_edits)
            dpg.add_text("", tag="experiment_review_status", wrap=850)

            dpg.add_spacer(height=8)
            dpg.add_text("CANDIDATE MATRIX AND PLATE MAP", color=(133, 149, 166, 255))
            dpg.add_child_window(tag="candidate_matrix_area", width=-1, height=250, border=True)
            show_empty_candidate_table(dpg, "candidate_matrix_area")

            dpg.add_spacer(height=8)
            with dpg.group(horizontal=True):
                with dpg.child_window(width=420, height=180, border=True):
                    dpg.add_text("HELD FIXED FROM THE PROFILE", color=(92, 191, 178, 255))
                    dpg.add_text("", tag="experiment_fixed_settings", wrap=380)
                with dpg.child_window(width=420, height=180, border=True):
                    dpg.add_text("ASSUMPTIONS AND WARNINGS", color=(225, 180, 112, 255))
                    dpg.add_text("", tag="experiment_assumptions", wrap=380)
                    dpg.add_text("", tag="experiment_warnings", wrap=380,
                                 color=(225, 180, 112, 255))
                    dpg.add_text("", tag="experiment_estimates", wrap=380,
                                 color=(133, 149, 166, 255))

            dpg.add_spacer(height=10)
            with dpg.group(horizontal=True):
                dpg.add_button(label="Back to modules", callback=self._on_back)
                dpg.add_button(
                    label="Use this candidate grid",
                    tag="experiment_use_grid",
                    width=230,
                    enabled=False,
                    callback=self._on_use_plan,
                )

    def _add_dimension_editor(self, key: str, label: str, *, prefix: str) -> None:
        dpg = self.dpg
        with dpg.child_window(width=420, height=100, border=True):
            dpg.add_text(f"{label.upper()} · LOW / CENTER / HIGH", color=(238, 244, 249, 255))
            with dpg.group(horizontal=True):
                for index, bound in enumerate(("Low", "Center", "High")):
                    dpg.add_input_text(
                        label=bound,
                        tag=f"experiment_{prefix}_{index}",
                        width=105,
                        callback=self._on_axis_changed,
                        user_data=key,
                    )

    def set_plan(self, plan: ExperimentPlan, profiles: ProfileSelection) -> None:
        self.plan = plan
        self.profiles = profiles
        self.review = self.service.review(plan, profiles)
        self._dirty = False
        self._update_values(plan)
        render_candidate_table(self.dpg, "candidate_matrix_area", self.review.plate_map)
        self.dpg.set_value(
            "experiment_review_baseline",
            f"Process baseline: {profiles.process.profile.name} · "
            f"ironing_flow={profiles.process.settings.get('ironing_flow')} · "
            f"ironing_speed={profiles.process.settings.get('ironing_speed')}",
        )
        fixed = "\n".join(
            self._fixed_setting_line(key, value)
            for key, value in sorted(self.review.fixed_settings.items())
        ) or "No additional process values were provided."
        assumptions = "\n".join(f"• {item}" for item in self.review.assumptions)
        warnings = "\n".join(f"• {item}" for item in self.review.warnings)
        self.dpg.set_value("experiment_fixed_settings", fixed)
        self.dpg.set_value("experiment_assumptions", assumptions)
        self.dpg.set_value("experiment_warnings", warnings)
        self.dpg.set_value("experiment_estimates", self.review.estimate_message)
        self.dpg.set_value("experiment_review_status", "The default 3 × 3 matrix is ready to review.")
        self.dpg.configure_item("experiment_use_grid", enabled=True)

    def _fixed_setting_line(self, key: str, value: Any) -> str:
        source = self.profiles.process.provenance.get(key) if self.profiles else None
        if source is None:
            return f"{key} = {value!r} · source unavailable"
        return f"{key} = {value!r} · {source.name} [{source.scope}]"

    def _update_values(self, plan: ExperimentPlan) -> None:
        by_key = {dimension.key: dimension.values for dimension in plan.dimensions}
        for prefix, key in (("flow", "ironing_flow"), ("speed", "ironing_speed")):
            for index, value in enumerate(by_key[key]):
                self.dpg.set_value(f"experiment_{prefix}_{index}", str(value))

    def _on_axis_changed(self, sender: Any, app_data: Any, user_data: str) -> None:
        del sender, app_data, user_data
        self._dirty = True
        self.dpg.configure_item("experiment_use_grid", enabled=False)
        self.dpg.set_value(
            "experiment_review_status",
            "Edits are not applied yet. Apply values to validate and refresh all nine candidates.",
        )

    def _on_apply_edits(self, sender: Any, app_data: Any, user_data: Any = None) -> bool:
        del sender, app_data, user_data
        if self.plan is None or self.profiles is None:
            return False
        options = ExperimentOptions(
            flow_values=tuple(self.dpg.get_value(f"experiment_flow_{index}") for index in range(3)),
            speed_values=tuple(self.dpg.get_value(f"experiment_speed_{index}") for index in range(3)),
            plan_id=self.plan.plan_id,
        )
        try:
            updated = self.service.create_initial("ironing", self.profiles, options)
        except ExperimentServiceError as exc:
            self.dpg.set_value("experiment_review_status", f"Matrix blocked: {exc}")
            self.dpg.configure_item("experiment_use_grid", enabled=False)
            return False
        self.set_plan(updated, self.profiles)
        self.dpg.set_value("experiment_review_status", "Updated matrix is valid and contains nine distinct candidates.")
        return True

    def _on_use_plan(self, sender: Any, app_data: Any, user_data: Any = None) -> None:
        del sender, app_data, user_data
        if (self._dirty or self._has_unapplied_input_changes()) and not self._on_apply_edits(None, None):
            return
        if self.plan is not None and self.on_plan_ready is not None:
            self.on_plan_ready(self.plan)
            self.dpg.set_value(
                "experiment_review_status",
                "Candidate grid accepted for this session. Generation is the next workflow step.",
            )

    def _has_unapplied_input_changes(self) -> bool:
        if self.plan is None:
            return False
        values = {dimension.key: tuple(str(value) for value in dimension.values) for dimension in self.plan.dimensions}
        current = {
            "ironing_flow": tuple(str(self.dpg.get_value(f"experiment_flow_{index}")) for index in range(3)),
            "ironing_speed": tuple(str(self.dpg.get_value(f"experiment_speed_{index}")) for index in range(3)),
        }
        return any(current[key] != values[key] for key in current)

    def _on_back(self, sender: Any, app_data: Any, user_data: Any = None) -> None:
        del sender, app_data, user_data
        self.dpg.configure_item("experiment_review_panel", show=False)
        self.dpg.configure_item("module_selection_panel", show=True)
