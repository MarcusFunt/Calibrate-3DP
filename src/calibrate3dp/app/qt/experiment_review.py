"""Qt review screen for one frozen grouped ironing configuration revision."""

from __future__ import annotations

import json
from typing import Any

from PySide6.QtCore import QPointF, Qt
from PySide6.QtGui import QBrush, QColor, QPainter, QPen, QPolygonF
from PySide6.QtWidgets import (
    QDialog,
    QComboBox,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QPlainTextEdit,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from calibrate3dp.app.services.experiment_service import ExperimentOptions
from calibrate3dp.app.services.grouped_orca_service import GroupedOrcaGenerationService
from calibrate3dp.app.services.experiment_configuration_service import ExperimentConfigurationReview
from calibrate3dp.domain.experiment_config import SavedExperimentConfiguration
from calibrate3dp.geometry.font_asset import is_pinned_font_available
from calibrate3dp.geometry.layout import PlateLayout
from calibrate3dp.geometry.registry import is_geometry_backend_available
from calibrate3dp.grouped_plate import machine_keep_out_polygons, machine_printable_polygon


class PlatePreviewWidget(QWidget):
    """Small 2D preview drawn from the exact validated PlateLayout output."""

    def __init__(
        self,
        plate_layout: PlateLayout | None,
        machine_settings: dict[str, Any],
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.plate_layout = plate_layout
        self.printable_polygon = machine_printable_polygon(machine_settings)
        self.keep_outs = machine_keep_out_polygons(machine_settings)
        self.setMinimumSize(420, 280)
        self.setAccessibleName("Connected plate layout preview")
        self.setAccessibleDescription("Sample positions, connected frame, physical labels, corner identifier plaque, and machine keep-outs.")

    def set_layout(self, plate_layout: PlateLayout | None) -> None:
        self.plate_layout = plate_layout
        self.update()

    def paintEvent(self, _event) -> None:  # noqa: N802 - Qt override name
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.fillRect(self.rect(), QColor("#FFFFFF"))
        points = self.printable_polygon
        min_x = min(x for x, _y in points)
        max_x = max(x for x, _y in points)
        min_y = min(y for _x, y in points)
        max_y = max(y for _x, y in points)
        pad = 18.0
        scale = min(
            max(1.0, self.width() - pad * 2) / max(1e-6, max_x - min_x),
            max(1.0, self.height() - pad * 2) / max(1e-6, max_y - min_y),
        )

        def point(x: float, y: float) -> QPointF:
            return QPointF(pad + (x - min_x) * scale, self.height() - pad - (y - min_y) * scale)

        bed = QPolygonF([point(x, y) for x, y in points])
        painter.setPen(QPen(QColor("#66766F"), 1.5))
        painter.setBrush(QBrush(QColor("#F3F6F4")))
        painter.drawPolygon(bed)
        painter.setBrush(QBrush(QColor(190, 95, 78, 85)))
        painter.setPen(QPen(QColor("#B85A49"), 1.0))
        for polygon in self.keep_outs:
            painter.drawPolygon(QPolygonF([point(x, y) for x, y in polygon]))
        plate_layout = self.plate_layout
        if plate_layout is not None:
            painter.setPen(QPen(QColor("#81928A"), 0.7))
            painter.setBrush(QBrush(QColor("#CBD4CF")))
            for rail in plate_layout.frame_rails:
                painter.drawPolygon(QPolygonF([point(x, y) for x, y in rail.corners]))
            painter.setBrush(QBrush(QColor("#9CB7AA")))
            for connector in plate_layout.connectors:
                painter.drawPolygon(QPolygonF([point(x, y) for x, y in connector.rectangle.corners]))
            for sample in plate_layout.sample_placements:
                rect_points = (
                    (sample.x_mm, sample.y_mm),
                    (sample.x_mm + sample.width_mm, sample.y_mm),
                    (sample.x_mm + sample.width_mm, sample.y_mm + sample.depth_mm),
                    (sample.x_mm, sample.y_mm + sample.depth_mm),
                )
                polygon = QPolygonF([point(x, y) for x, y in rect_points])
                painter.setPen(QPen(QColor("#467B62"), 1.0))
                painter.setBrush(QBrush(QColor("#DDEBE3")))
                painter.drawPolygon(polygon)
                painter.setPen(QPen(QColor("#254A38"), 1.0))
                painter.drawText(polygon.boundingRect(), Qt.AlignmentFlag.AlignCenter, sample.label)
            painter.setPen(QPen(QColor("#B87836"), 0.8))
            painter.setBrush(QBrush(QColor("#F3D7AF")))
            for label, region in plate_layout.label_regions:
                polygon = QPolygonF([point(x, y) for x, y in region.corners])
                painter.drawPolygon(polygon)
                painter.drawText(polygon.boundingRect(), Qt.AlignmentFlag.AlignCenter, label + " underside")
            if plate_layout.identifier_region is not None:
                plaque = QPolygonF([point(x, y) for x, y in plate_layout.identifier_region.corners])
                painter.setPen(QPen(QColor("#435F83"), 1.1))
                painter.setBrush(QBrush(QColor("#CFD9E5")))
                painter.drawPolygon(plaque)
                painter.drawText(plaque.boundingRect(), Qt.AlignmentFlag.AlignCenter, plate_layout.plate_code)
                painter.setBrush(QBrush(QColor("#8C9EB4")))
                for tab in plate_layout.identifier_tabs:
                    painter.drawPolygon(QPolygonF([point(x, y) for x, y in tab.corners]))
            else:
                support = QPolygonF([point(x, y) for x, y in plate_layout.code_support_region.corners])
                painter.setPen(QPen(QColor("#586C83"), 0.8))
                painter.setBrush(QBrush(QColor("#CFD9E5")))
                painter.drawPolygon(support)
                painter.drawText(support.boundingRect(), Qt.AlignmentFlag.AlignCenter, "CODE")
                painter.setBrush(QBrush(QColor("#8C9EB4")))
                for _character, region in plate_layout.code_regions:
                    painter.drawPolygon(QPolygonF([point(x, y) for x, y in region.corners]))
        painter.end()


class ExperimentConfigurationDialog(QDialog):
    """Edit sweep triples, inspect the exact layout, and save before generation."""

    def __init__(
        self,
        review: ExperimentConfigurationReview,
        generation: GroupedOrcaGenerationService,
        parent: QWidget | None = None,
        *,
        saved_configuration: SavedExperimentConfiguration | None = None,
    ) -> None:
        super().__init__(parent)
        self.generation = generation
        self._review: ExperimentConfigurationReview | None = review
        self._base_configuration = review.configuration
        self._saved_configuration: SavedExperimentConfiguration | None = saved_configuration
        self.configuration: SavedExperimentConfiguration | None = saved_configuration
        self._dirty = False
        self._backend_ready = True
        self.setWindowTitle("Review Ironing Experiment")
        self.setMinimumSize(960, 760)
        root = QVBoxLayout(self)
        root.setContentsMargins(24, 20, 24, 20)
        root.setSpacing(10)
        title = QLabel("Review the grouped ironing sweep")
        title.setObjectName("pageTitle")
        intro = QLabel(
            "Check the imported profile context, edit the flow and speed triples, and inspect the connected A–I layout before saving this revision."
        )
        intro.setObjectName("bodyCopy")
        intro.setWordWrap(True)
        root.addWidget(title)
        root.addWidget(intro)

        self.profile_context = QLabel("")
        self.profile_context.setObjectName("mutedStatus")
        self.profile_context.setWordWrap(True)
        root.addWidget(self.profile_context)

        controls = QGridLayout()
        controls.addWidget(QLabel("Ironing flow (low, mid, high)"), 0, 0)
        self.flow_values = QLineEdit()
        self.flow_values.setAccessibleName("Ironing flow values, low mid high")
        controls.addWidget(self.flow_values, 0, 1)
        controls.addWidget(QLabel("Ironing speed (low, mid, high)"), 1, 0)
        self.speed_values = QLineEdit()
        self.speed_values.setAccessibleName("Ironing speed values, low mid high")
        controls.addWidget(self.speed_values, 1, 1)
        self.corner_label = QLabel("Plate identifier plaque corner")
        controls.addWidget(self.corner_label, 2, 0)
        self.identifier_corner = QComboBox()
        self.identifier_corner.setAccessibleName("Plate identifier plaque corner")
        for value, label in (
            ("front_left", "Front left"),
            ("front_right", "Front right"),
            ("back_left", "Back left"),
            ("back_right", "Back right"),
        ):
            self.identifier_corner.addItem(label, value)
        controls.addWidget(self.identifier_corner, 2, 1)
        self.update_button = QPushButton("Update preview")
        self.update_button.setObjectName("secondaryAction")
        controls.addWidget(self.update_button, 0, 2, 3, 1)
        root.addLayout(controls)

        self.candidate_table = QTableWidget(0, 4)
        self.candidate_table.setHorizontalHeaderLabels(("Sample", "Ironing flow", "Ironing speed", "Candidate ID"))
        self.candidate_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.candidate_table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.candidate_table.horizontalHeader().setStretchLastSection(True)
        self.candidate_table.setMinimumHeight(220)
        root.addWidget(self.candidate_table)

        preview_row = QHBoxLayout()
        self.layout_preview = PlatePreviewWidget(
            review.layout,
            dict(review.configuration.profile_selection.printer.settings),
        )
        preview_row.addWidget(self.layout_preview, 3)
        right = QVBoxLayout()
        self.fixed_settings = QPlainTextEdit()
        self.fixed_settings.setReadOnly(True)
        self.fixed_settings.setMaximumBlockCount(500)
        self.fixed_settings.setAccessibleName("Inherited fixed process settings")
        right.addWidget(QLabel("Inherited / fixed settings"))
        right.addWidget(self.fixed_settings, 1)
        preview_row.addLayout(right, 2)
        root.addLayout(preview_row, 1)

        self.code_status = QLabel("The six-character plate code is allocated at generation.")
        self.code_status.setObjectName("mutedStatus")
        self.code_status.setWordWrap(True)
        root.addWidget(self.code_status)
        self.geometry_details = QLabel("")
        self.geometry_details.setObjectName("mutedStatus")
        self.geometry_details.setWordWrap(True)
        root.addWidget(self.geometry_details)
        self.status = QLabel(
            "No profile schema limits were included in the imported snapshots; review values are checked for finite, positive, increasing triples."
        )
        self.status.setObjectName("mutedStatus")
        self.status.setWordWrap(True)
        root.addWidget(self.status)

        actions = QHBoxLayout()
        actions.addStretch(1)
        self.cancel_button = QPushButton("Cancel")
        self.save_button = QPushButton("Save configuration")
        self.save_button.setObjectName("primaryAction")
        self.generate_button = QPushButton("Generate saved revision")
        self.generate_button.setObjectName("primaryAction")
        self.generate_button.setEnabled(False)
        actions.addWidget(self.cancel_button)
        actions.addWidget(self.save_button)
        actions.addWidget(self.generate_button)
        root.addLayout(actions)

        self.cancel_button.clicked.connect(self.reject)
        self.update_button.clicked.connect(self._refresh_preview)
        self.save_button.clicked.connect(self._save_configuration)
        self.generate_button.clicked.connect(self._generate_saved_configuration)
        self.flow_values.textChanged.connect(self._mark_dirty)
        self.speed_values.textChanged.connect(self._mark_dirty)
        self.identifier_corner.currentIndexChanged.connect(self._mark_dirty)
        self._load_review(review)
        if saved_configuration is not None:
            self.save_button.setText("Save new revision")
            self.save_button.setEnabled(False)
            self.generate_button.setEnabled(review.layout is not None and self._backend_ready)

    def _load_review(self, review: ExperimentConfigurationReview) -> None:
        self._review = review
        configuration = review.configuration
        flow = next(item.values for item in configuration.plan.dimensions if item.key == "ironing_flow")
        speed = next(item.values for item in configuration.plan.dimensions if item.key == "ironing_speed")
        self.flow_values.setText(", ".join(str(item) for item in flow))
        self.speed_values.setText(", ".join(str(item) for item in speed))
        corner_index = self.identifier_corner.findData(configuration.layout_options.get("identifier_corner", "front_left"))
        self.identifier_corner.setCurrentIndex(max(0, corner_index))
        has_corner_choice = configuration.schema_version >= 2
        self.corner_label.setVisible(has_corner_choice)
        self.identifier_corner.setVisible(has_corner_choice)
        profiles = configuration.profile_selection
        hashes = configuration.source_profile_hashes
        self.profile_context.setText(
            f"Machine: {profiles.printer.profile.name} · {hashes['printer'][:12]}  |  "
            f"Process: {profiles.process.profile.name} · {hashes['process'][:12]}  |  "
            f"Filament: {profiles.filament.profile.name} · {hashes['filament'][:12]}"
        )
        self.fixed_settings.setPlainText(
            json.dumps(dict(configuration.fixed_settings), ensure_ascii=False, indent=2, sort_keys=True)
        )
        self.layout_preview.set_layout(review.layout)
        if review.layout_error:
            self.status.setText("Layout preflight is blocked: " + review.layout_error)
        if review.layout is not None:
            options = configuration.layout_options
            bounds = review.layout.bounds
            self.geometry_details.setText(
                f"Plate bounds: {bounds.max_x - bounds.min_x:.1f} × {bounds.max_y - bounds.min_y:.1f} mm; "
                f"specimens: {options['specimen_width_mm']:.1f} × {options['specimen_depth_mm']:.1f} × "
                f"{options['specimen_height_mm']:.1f} mm; tabs: {options['connector_width_mm']:.1f} × "
                f"{options['connector_height_mm']:.1f} mm; frame rail: {options['frame_width_mm']:.1f} mm; "
                f"geometry: {options['geometry_backend']} {options['geometry_backend_version']}. "
                f"Raised labels face the bed in {options.get('label_pocket_depth_z_mm', 0):.1f} mm pockets; "
                f"identifier: {review.layout.identifier_corner or 'legacy frame code'}. "
                "Green shows specimens and frame; blue shows the separate plaque; red areas show keep-outs. "
                "Readability, adhesion and tab separation still need a physical print trial."
            )
        else:
            self.geometry_details.setText("Layout preview is unavailable because machine bounds or keep-outs reject this plate.")
        self._refresh_candidate_table(review)
        self._dirty = False
        self._backend_ready = _geometry_backend_ready(configuration.layout_options)
        if not self._backend_ready:
            self.save_button.setEnabled(False)
            self.generate_button.setEnabled(False)
            self.status.setText(
                "The pinned CAD backend or bundled font is unavailable. Install the optional cad extra and verify the font asset before saving or generating this schema-v2 configuration."
            )

    def _mark_dirty(self, _text: str) -> None:
        self._dirty = True
        self.save_button.setEnabled(self._backend_ready)
        self.generate_button.setEnabled(False)
        self.status.setText("The form has unsaved edits. Save will validate and freeze the values currently shown.")

    def _form_matches_review(self) -> bool:
        if self._review is None:
            return False
        try:
            flow = _triple(self.flow_values.text(), "ironing flow")
            speed = _triple(self.speed_values.text(), "ironing speed")
        except ValueError:
            return False
        current_flow = next(item.values for item in self._review.configuration.plan.dimensions if item.key == "ironing_flow")
        current_speed = next(item.values for item in self._review.configuration.plan.dimensions if item.key == "ironing_speed")
        return (
            flow == current_flow
            and speed == current_speed
            and (
                self._review.configuration.schema_version == 1
                or self.identifier_corner.currentData()
                == self._review.configuration.layout_options.get("identifier_corner", "front_left")
            )
        )

    def _refresh_preview(self) -> None:
        try:
            flow = _triple(self.flow_values.text(), "ironing flow")
            speed = _triple(self.speed_values.text(), "ironing speed")
            if self._saved_configuration is None:
                layout_options = dict(self._review.configuration.layout_options)
                if self._review.configuration.schema_version >= 2:
                    layout_options["identifier_corner"] = self.identifier_corner.currentData()
                review = self.generation.prepare_ironing_configuration(
                    self._base_configuration.printer_id,
                    self._base_configuration.material_id,
                    ExperimentOptions(flow_values=flow, speed_values=speed),
                    layout_options=layout_options,
                )
            else:
                layout_options = dict(self._review.configuration.layout_options)
                if self._review.configuration.schema_version >= 2:
                    layout_options["identifier_corner"] = self.identifier_corner.currentData()
                review = self.generation.configurations.revise_ironing(
                    self._saved_configuration,
                    flow_values=flow,
                    speed_values=speed,
                    layout_options=layout_options,
                )
        except Exception as exc:
            self._review = None
            self.candidate_table.setRowCount(0)
            self.layout_preview.set_layout(None)
            self.status.setText(f"Review could not be updated: {exc}")
            self.save_button.setEnabled(False)
            self.generate_button.setEnabled(False)
            return
        self.save_button.setEnabled(self._backend_ready)
        self._load_review(review)
        self._base_configuration = review.configuration
        self._dirty = True
        self.generate_button.setEnabled(False)
        if self._backend_ready:
            self.status.setText(
                "Preview uses the same connected-grid geometry and machine keep-outs as generation. The code is still allocated only when the saved revision is generated."
            )

    def _refresh_candidate_table(self, review: ExperimentConfigurationReview) -> None:
        entries = review.experiment_review.plate_map
        self.candidate_table.setRowCount(len(entries))
        for row, entry in enumerate(entries):
            values = (entry.specimen_label, str(entry.flow_value), str(entry.speed_value), entry.candidate_id)
            for column, value in enumerate(values):
                self.candidate_table.setItem(row, column, QTableWidgetItem(value))

    def _save_configuration(self) -> None:
        if self._review is None:
            return
        if not self._backend_ready:
            self.status.setText(
                "The pinned CAD backend or bundled font is unavailable; install the optional cad extra and verify the font asset before saving."
            )
            return
        if not self._dirty and self._saved_configuration is not None:
            return
        if not self._form_matches_review():
            self._refresh_preview()
            if self._review is None or not self._form_matches_review():
                return
        try:
            self._saved_configuration = self.generation.save_configuration(self._review.configuration)
        except Exception as exc:
            self.status.setText(f"Configuration could not be saved: {exc}")
            return
        self.configuration = self._saved_configuration
        self._dirty = False
        self.generate_button.setEnabled(self._review.layout is not None and self._backend_ready)
        self.save_button.setText(
            "Save new revision" if self._saved_configuration.revision_no > 1 else "Configuration saved"
        )
        self.save_button.setEnabled(False)
        self.status.setText(
            f"Saved immutable configuration {self.configuration.config_id} · revision {self.configuration.revision_no} · SHA-256 {self.configuration.input_sha256[:16]}."
        )

    def _generate_saved_configuration(self) -> None:
        if self._saved_configuration is None or self._dirty:
            self.status.setText("Save the current preview before generating it.")
            return
        self.configuration = self._saved_configuration
        self.accept()


def _triple(raw: str, name: str) -> tuple[str, str, str]:
    values = tuple(item.strip() for item in raw.split(","))
    if len(values) != 3 or any(not item for item in values):
        raise ValueError(f"{name} needs exactly three comma-separated values")
    return values


def _geometry_backend_ready(options: dict[str, Any]) -> bool:
    if options.get("geometry_backend") == "build123d":
        return is_geometry_backend_available("build123d", "1") and is_pinned_font_available()
    return options.get("geometry_backend") == "stdlib-voxel" and options.get("geometry_backend_version") == "1"
