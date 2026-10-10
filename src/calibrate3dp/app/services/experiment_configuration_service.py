"""Review, revise, and persist frozen grouped ironing configurations."""

from __future__ import annotations

from dataclasses import dataclass, replace
from types import MappingProxyType
from typing import Any, Mapping, Sequence
from uuid import uuid4

from calibrate3dp.app.models import ProfileSelection
from calibrate3dp.app.services.experiment_service import ExperimentOptions, ExperimentReview, ExperimentService
from calibrate3dp.app.services.library_service import LibraryService
from calibrate3dp.domain.experiment_config import SavedExperimentConfiguration, default_layout_options
from calibrate3dp.domain.records import utc_now
from calibrate3dp.geometry.layout import PlateLayout, PlateLayoutError, layout_plate
from calibrate3dp.app.services.grouped_layout import grouped_ironing_layout_request


class ConfigurationServiceError(ValueError):
    """Raised when an ironing configuration cannot be reviewed or saved."""


@dataclass(frozen=True)
class ExperimentConfigurationReview:
    configuration: SavedExperimentConfiguration
    experiment_review: ExperimentReview
    layout: PlateLayout | None
    layout_error: str | None = None


class ExperimentConfigurationService:
    """Create immutable revisions while keeping review logic headless."""

    def __init__(
        self,
        library: LibraryService,
        *,
        experiment_service: ExperimentService | None = None,
    ) -> None:
        if not isinstance(library, LibraryService):
            raise TypeError("library must be a LibraryService")
        self.library = library
        self.repository = library.repository
        self.experiments = experiment_service or ExperimentService()

    def prepare_ironing(
        self,
        printer_id: str,
        material_id: str,
        options: ExperimentOptions | Mapping[str, Any] | None = None,
    ) -> ExperimentConfigurationReview:
        """Build an editable review; this never allocates a plate code or run."""
        selection = _enable_top_ironing(self.library.resolve_selection(printer_id, material_id))
        plan = self.experiments.create_initial("ironing", selection, options)
        configuration = SavedExperimentConfiguration(
            config_id=f"config-{uuid4().hex}",
            experiment_id=f"experiment-{uuid4().hex}",
            revision_no=1,
            printer_id=printer_id,
            material_id=material_id,
            profile_selection=selection,
            plan=plan,
            layout_options=default_layout_options(),
            created_at_utc=utc_now(),
        )
        return self.review(configuration)

    def revise_ironing(
        self,
        configuration: SavedExperimentConfiguration,
        *,
        flow_values: Sequence[Any],
        speed_values: Sequence[Any],
    ) -> ExperimentConfigurationReview:
        """Return a new immutable config revision with edited sweep values."""
        if not isinstance(configuration, SavedExperimentConfiguration):
            raise TypeError("configuration must be a SavedExperimentConfiguration")
        if configuration.relation_type != "initial":
            raise ConfigurationServiceError("follow-up configurations cannot be edited as an initial sweep")
        plan_id = f"{configuration.plan.plan_id}-r{configuration.revision_no + 1}-{uuid4().hex[:8]}"
        plan = self.experiments.create_initial(
            "ironing",
            configuration.profile_selection,
            ExperimentOptions(flow_values=flow_values, speed_values=speed_values, plan_id=plan_id),
        )
        revised = SavedExperimentConfiguration(
            config_id=f"config-{uuid4().hex}",
            experiment_id=configuration.experiment_id,
            revision_no=configuration.revision_no + 1,
            printer_id=configuration.printer_id,
            material_id=configuration.material_id,
            profile_selection=configuration.profile_selection,
            plan=plan,
            layout_options=configuration.layout_options,
            created_at_utc=utc_now(),
        )
        return self.review(revised)

    def review(self, configuration: SavedExperimentConfiguration) -> ExperimentConfigurationReview:
        """Reopen an exact saved configuration and calculate its true layout."""
        plan_review = self.experiments.review(configuration.plan, configuration.profile_selection)
        try:
            request = grouped_ironing_layout_request(
                configuration.plan,
                "XXXXXX",
                configuration.profile_selection.printer.settings,
                configuration.layout_options,
            )
            layout = layout_plate(request)
        except (PlateLayoutError, ValueError) as exc:
            return ExperimentConfigurationReview(configuration, plan_review, None, str(exc))
        return ExperimentConfigurationReview(configuration, plan_review, layout)

    def save(self, configuration: SavedExperimentConfiguration) -> SavedExperimentConfiguration:
        self.repository.save_configuration(configuration)
        return configuration

    def load(self, config_id: str) -> SavedExperimentConfiguration:
        return self.repository.get_configuration(config_id)


def _enable_top_ironing(selection: ProfileSelection) -> ProfileSelection:
    process_settings = dict(selection.process.settings)
    process_settings["ironing_type"] = "top"
    return replace(
        selection,
        process=replace(selection.process, settings=MappingProxyType(process_settings)),
    )
