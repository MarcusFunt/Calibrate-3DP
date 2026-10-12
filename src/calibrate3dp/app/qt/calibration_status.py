"""Qt presentation for the service-evaluated calibration lifecycle."""

from __future__ import annotations

from collections.abc import Iterable

from PySide6.QtCore import QPoint, QSize, Qt
from PySide6.QtGui import QPainter, QPainterPath, QPalette, QPen
from PySide6.QtWidgets import (
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QSizePolicy,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from calibrate3dp.calibration.state import (
    CalibrationState,
    CalibrationStatus,
    DependencyRuleOutcome,
)


_STATUS_PRESENTATION: dict[CalibrationStatus, str] = {
    CalibrationStatus.UNTESTED: "Untested",
    CalibrationStatus.BLOCKED: "Blocked",
    CalibrationStatus.READY: "Can configure",
    CalibrationStatus.IN_PROGRESS: "In progress",
    CalibrationStatus.NEEDS_REVIEW: "Needs review",
    CalibrationStatus.ACCEPTED: "Accepted",
    CalibrationStatus.STALE: "Stale",
}

_REASON_PRESENTATION: dict[str, str] = {
    "saved-printer-material-resolves": (
        "Select a saved printer and a material recorded for that printer."
    ),
    "printer-material-nozzle-matches": (
        "The material's saved nozzle size does not match this printer."
    ),
    "selected-profile-context-matches": (
        "This saved configuration uses different printer or material profiles. "
        "Review it before continuing."
    ),
    "printer-profile-hash-current": (
        "The saved printer profile changed or could not be verified."
    ),
    "process-profile-hash-current": (
        "The saved process profile changed or could not be verified."
    ),
    "filament-profile-hash-current": (
        "The saved filament profile changed or could not be verified."
    ),
    "orca-slicer-available": (
        "OrcaSlicer could not be verified. Check its executable in Settings."
    ),
}

_DEPENDENCY_GROUPS: tuple[tuple[str, str, tuple[str, ...]], ...] = (
    ("printer_material", "Printer + material", ("saved-printer-material-resolves",)),
    ("nozzle", "Nozzle match", ("printer-material-nozzle-matches",)),
    (
        "profiles",
        "Profile context",
        (
            "selected-profile-context-matches",
            "printer-profile-hash-current",
            "process-profile-hash-current",
            "filament-profile-hash-current",
        ),
    ),
    ("orca", "OrcaSlicer", ("orca-slicer-available",)),
)


def _reason_summary(reason: str) -> str:
    rule_id, separator, detail = reason.partition(":")
    return _REASON_PRESENTATION.get(rule_id, detail.strip() if separator else reason)


class _CalibrationStatusIcon(QWidget):
    """Draw status marks with Qt primitives so their shape is font-independent."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.state = "unavailable"
        self.setObjectName("calibrationStatusIcon")
        self.setFixedSize(16, 16)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)

    def set_state(self, state: str) -> None:
        self.state = state
        self.setProperty("state", state)
        self.update()

    def paintEvent(self, _event) -> None:  # noqa: N802 - Qt override name
        color = self.palette().color(QPalette.ColorRole.WindowText)
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        pen = QPen(color, 1.6, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin)
        painter.setPen(pen)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        if self.state == "untested":
            pen.setStyle(Qt.PenStyle.DashLine)
            painter.setPen(pen)
            painter.drawEllipse(2, 2, 12, 12)
        elif self.state == "blocked":
            painter.drawEllipse(2, 2, 12, 12)
            painter.drawLine(5, 5, 11, 11)
        elif self.state in {"ready", "accepted"}:
            if self.state == "accepted":
                painter.drawEllipse(2, 2, 12, 12)
            painter.drawLine(4, 8, 7, 11)
            painter.drawLine(7, 11, 12, 5)
        elif self.state == "in_progress":
            path = QPainterPath()
            path.moveTo(5, 3)
            path.lineTo(13, 8)
            path.lineTo(5, 13)
            path.closeSubpath()
            painter.setBrush(color)
            painter.drawPath(path)
        elif self.state == "needs_review":
            painter.drawEllipse(2, 2, 12, 12)
            painter.drawLine(8, 4, 8, 9)
            painter.drawPoint(8, 12)
        elif self.state == "stale":
            painter.drawArc(3, 3, 10, 10, 35 * 16, 280 * 16)
            arrow = QPainterPath()
            arrow.moveTo(11, 2)
            arrow.lineTo(14, 3)
            arrow.lineTo(12, 6)
            arrow.closeSubpath()
            painter.setBrush(color)
            painter.drawPath(arrow)
        else:
            painter.drawEllipse(2, 2, 12, 12)
            painter.drawLine(5, 8, 11, 8)
        painter.end()


class _DependencyStatusNode(QFrame):
    """One grouped input node feeding the current calibration."""

    def __init__(self, key: str, title: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.key = key
        self.setObjectName("calibrationDependencyNode")
        self.setProperty("state", "unavailable")
        self.setAccessibleName(f"Dependency: {title}")
        self.state_label = QLabel("Not evaluated", self)
        self.state_label.setObjectName("calibrationDependencyState")
        self.state_label.setProperty("state", "unavailable")
        self.title_label = QLabel(title, self)
        self.title_label.setObjectName("calibrationDependencyTitle")
        self.title_label.setWordWrap(True)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 5, 7, 5)
        layout.setSpacing(1)
        layout.addWidget(self.title_label)
        layout.addWidget(self.state_label)
        self.setFixedHeight(48)
        self.setMinimumWidth(112)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.set_state("unavailable", "Not evaluated", "No current rule outcomes are available.")

    def set_outcomes(self, outcomes: tuple[DependencyRuleOutcome, ...]) -> None:
        if not outcomes:
            self.set_state("unavailable", "Not evaluated", "No current rule outcomes are available.")
            return

        required = tuple(item for item in outcomes if item.severity == "required")
        required_failures = tuple(item for item in required if not item.passed)
        recommendations = tuple(item for item in outcomes if item.severity == "recommended")
        recommendation_failures = tuple(item for item in recommendations if not item.passed)
        if required_failures:
            state, label = "blocked", "Resolve"
            count = len(required_failures)
            summary = (
                f"{count} required check needs attention."
                if count == 1
                else f"{count} required checks need attention."
            )
        elif recommendation_failures:
            state, label = "review", "Review"
            count = len(recommendation_failures)
            summary = (
                f"{count} suggested check remains."
                if count == 1
                else f"{count} suggested checks remain."
            )
        else:
            state, label = "met", "Met"
            count = len(outcomes)
            summary = f"{count} check passed." if count == 1 else f"{count} checks passed."
        self.set_state(state, label, summary)

    def set_state(self, state: str, label: str, description: str) -> None:
        self.setProperty("state", state)
        self.state_label.setProperty("state", state)
        self.state_label.setText(label)
        self.setAccessibleDescription(description)
        for widget in (self, self.state_label):
            widget.style().unpolish(widget)
            widget.style().polish(widget)


class _DependencyFlowConnector(QWidget):
    """Draw independent prerequisite edges converging on one calibration node."""

    def __init__(
        self,
        source_nodes: tuple[_DependencyStatusNode, ...],
        target_node: QFrame,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.source_nodes = source_nodes
        self.target_node = target_node
        self.input_count = len(source_nodes)
        self.setObjectName("calibrationDependencyConnector")
        self.setAccessibleName(
            f"{self.input_count} independent prerequisite groups feed the ironing calibration"
        )
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.setFixedWidth(28)
        self.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Expanding)

    def sizeHint(self) -> QSize:
        row_count = len(self.source_nodes)
        return QSize(28, row_count * 48 + max(row_count - 1, 0) * 7)

    def paintEvent(self, _event) -> None:  # noqa: N802 - Qt override name
        if not self.source_nodes:
            return
        chart = self.parentWidget()
        if chart is None:
            return

        def point_on(widget: QWidget, point: QPoint) -> QPoint:
            point_in_chart = widget.mapTo(chart, point)
            connector_in_chart = self.mapTo(chart, QPoint(0, 0))
            return point_in_chart - connector_in_chart

        centers = [
            point_on(node, QPoint(node.width(), node.height() // 2)).y()
            for node in self.source_nodes
            if not node.isHidden()
        ]
        if not centers:
            return
        target_center = point_on(
            self.target_node, QPoint(0, self.target_node.height() // 2)
        ).y()
        trunk_x = self.width() // 2
        color = self.palette().color(QPalette.ColorRole.Mid)
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(QPen(color, 1.6, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
        for center in centers:
            painter.drawLine(0, center, trunk_x, center)
        painter.drawLine(trunk_x, min(centers), trunk_x, max(centers))
        arrow_tip = self.width() - 1
        painter.drawLine(trunk_x, target_center, arrow_tip - 5, target_center)
        arrow = QPainterPath()
        arrow.moveTo(arrow_tip, target_center)
        arrow.lineTo(arrow_tip - 6, target_center - 4)
        arrow.lineTo(arrow_tip - 6, target_center + 4)
        arrow.closeSubpath()
        painter.setBrush(color)
        painter.drawPath(arrow)
        painter.end()


class CalibrationStatusPanel(QWidget):
    """Show evaluated ironing prerequisites as a flow into the calibration."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("calibrationStatusPanel")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 14, 16, 15)
        layout.setSpacing(8)

        self.status_chip = QFrame()
        self.status_chip.setObjectName("calibrationLifecycleChip")
        self.status_chip.setProperty("state", "unavailable")
        self.status_chip.setSizePolicy(QSizePolicy.Policy.Maximum, QSizePolicy.Policy.Fixed)
        chip_layout = QHBoxLayout(self.status_chip)
        chip_layout.setContentsMargins(9, 4, 10, 4)
        chip_layout.setSpacing(6)
        self.status_icon = _CalibrationStatusIcon(self.status_chip)
        self.status_label = QLabel("Unavailable", self.status_chip)
        self.status_label.setObjectName("calibrationLifecycleStatus")
        self.status_label.setProperty("state", "unavailable")
        self.status_label.setAccessibleName("Calibration status: Unavailable")
        self.status_icon.setAccessibleName("")
        chip_layout.addWidget(self.status_icon)
        chip_layout.addWidget(self.status_label)
        self.target_status_label = self.status_label

        self.target_node = QFrame()
        self.target_node.setObjectName("calibrationTargetNode")
        self.target_node.setProperty("state", "unavailable")
        self.target_node.setMinimumWidth(118)
        self.target_node.setAccessibleName("Ironing flow and speed calibration")
        self.workflow_label = QLabel("Ironing · flow × speed", self.target_node)
        self.workflow_label.setObjectName("calibrationWorkflow")
        self.workflow_label.setWordWrap(True)
        target_layout = QVBoxLayout(self.target_node)
        target_layout.setContentsMargins(9, 10, 7, 10)
        target_layout.setSpacing(7)
        target_layout.addWidget(self.workflow_label)
        target_layout.addWidget(self.status_chip, 0, Qt.AlignmentFlag.AlignLeft)

        self.dependency_nodes = {
            key: _DependencyStatusNode(key, title)
            for key, title, _rule_ids in _DEPENDENCY_GROUPS
        }
        self.flow_connector = _DependencyFlowConnector(
            tuple(self.dependency_nodes.values()), self.target_node
        )
        self.dependency_flow_chart = QWidget()
        self.dependency_flow_chart.setObjectName("calibrationDependencyFlow")
        self.dependency_flow_chart.setAccessibleName(
            "Dependency flow chart: printer and material, nozzle, profile, and OrcaSlicer checks feed into ironing flow and speed."
        )
        chart_layout = QGridLayout(self.dependency_flow_chart)
        chart_layout.setContentsMargins(0, 0, 0, 0)
        chart_layout.setHorizontalSpacing(0)
        chart_layout.setVerticalSpacing(7)
        chart_layout.setColumnStretch(0, 5)
        chart_layout.setColumnStretch(2, 4)
        for row, node in enumerate(self.dependency_nodes.values()):
            chart_layout.addWidget(node, row, 0)
        chart_layout.addWidget(self.flow_connector, 0, 1, len(self.dependency_nodes), 1)
        chart_layout.addWidget(
            self.target_node,
            0,
            2,
            len(self.dependency_nodes),
            1,
            Qt.AlignmentFlag.AlignVCenter,
        )

        self.can_start_label = QLabel("Resolve the items below to configure this calibration.")
        self.can_start_label.setObjectName("calibrationCanStart")
        self.can_start_label.setWordWrap(True)
        self.reasons_label = QLabel("Calibration status has not been evaluated.")
        self.reasons_label.setObjectName("calibrationReasons")
        self.reasons_label.setWordWrap(True)
        self.availability_label = QLabel(
            "Other V1 calibration workflows are not yet available."
        )
        self.availability_label.setObjectName("calibrationAvailability")
        self.availability_label.setWordWrap(True)
        self.print_readiness_label = QLabel(
            "Calibration state does not establish print readiness. Generated plates are not print-ready."
        )
        self.print_readiness_label.setObjectName("calibrationPrintReadiness")
        self.print_readiness_label.setWordWrap(True)
        self.details_toggle = QToolButton()
        self.details_toggle.setObjectName("subtleAction")
        self.details_toggle.setText("Show technical details")
        self.details_toggle.setAccessibleName("Show technical calibration state details")
        self.details_toggle.setCheckable(True)
        self.details_toggle.toggled.connect(self._toggle_details)
        self.technical_details = QLabel("")
        self.technical_details.setObjectName("calibrationTechnicalDetails")
        self.technical_details.setWordWrap(True)
        self.technical_details.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.technical_details.hide()
        self.details_toggle.hide()

        layout.addWidget(self.dependency_flow_chart)
        layout.addWidget(self.can_start_label)
        for label in (
            self.reasons_label,
            self.details_toggle,
            self.technical_details,
            self.availability_label,
            self.print_readiness_label,
        ):
            layout.addWidget(label)

    def set_states(self, states: Iterable[CalibrationState]) -> None:
        """Render the service states without deriving or changing their meaning."""
        state = next(
            (item for item in states if item.calibration_id == "ironing"),
            None,
        )
        if state is None:
            self.set_unavailable("The ironing calibration state was not returned by the service.")
            return

        label = _STATUS_PRESENTATION[state.status]
        self._set_status(state.status.value, label)
        outcomes_by_rule = {item.rule_id: item for item in state.rule_outcomes}
        for key, _title, rule_ids in _DEPENDENCY_GROUPS:
            group_outcomes = tuple(
                outcomes_by_rule[rule_id]
                for rule_id in rule_ids
                if rule_id in outcomes_by_rule
            )
            self.dependency_nodes[key].set_outcomes(group_outcomes)
        self.can_start_label.setText(_start_summary(state))
        blocker_lines = [_reason_summary(item) for item in state.reasons]
        recommendation_lines = [_reason_summary(item) for item in state.recommendations]
        sections: list[str] = []
        if blocker_lines:
            heading = (
                "Before you can configure this calibration:"
                if not state.can_start
                else "Status details:"
            )
            sections.append(
                "\n".join((heading, *(f"• {item}" for item in blocker_lines)))
            )
        if recommendation_lines:
            sections.append(
                "\n".join(
                    ("Suggested next steps:", *(f"• {item}" for item in recommendation_lines))
                )
            )
        self.reasons_label.setText("\n\n".join(sections) if sections else "No blocking items.")

        raw_details = (*state.reasons, *state.recommendations)
        self.technical_details.setText("\n".join(raw_details))
        self.details_toggle.setVisible(bool(raw_details))
        self.technical_details.hide()
        self.details_toggle.setChecked(False)
        self.details_toggle.setText("Show technical details")

    def set_unavailable(self, reason: str) -> None:
        """Fail closed when the application cannot obtain service state."""
        self._set_status("unavailable", "Unavailable")
        for node in self.dependency_nodes.values():
            node.set_outcomes(())
        self.can_start_label.setText("Check local setup to continue.")
        self.reasons_label.setText(reason)
        self.details_toggle.hide()
        self.technical_details.clear()
        self.technical_details.hide()

    def _toggle_details(self, expanded: bool) -> None:
        self.technical_details.setVisible(expanded)
        self.details_toggle.setText(
            "Hide technical details" if expanded else "Show technical details"
        )
        self.details_toggle.setAccessibleName(
            "Hide technical calibration state details"
            if expanded
            else "Show technical calibration state details"
        )

    def _set_status(self, state: str, text: str) -> None:
        self.status_label.setText(text)
        self.status_label.setProperty("state", state)
        self.status_label.setAccessibleName(f"Calibration status: {text}")
        self.status_chip.setProperty("state", state)
        self.target_node.setProperty("state", state)
        self.status_icon.set_state(state)
        for widget in (self.status_chip, self.target_node, self.status_icon):
            widget.style().unpolish(widget)
            widget.style().polish(widget)
        self.target_node.setAccessibleName(
            f"Ironing flow and speed calibration status: {text}"
        )


def _start_summary(state: CalibrationState) -> str:
    if state.can_start:
        return "Prerequisites are met."
    if state.status is CalibrationStatus.BLOCKED:
        return "Resolve the items below to configure this calibration."
    if state.status is CalibrationStatus.IN_PROGRESS:
        return "A calibration is already being generated."
    if state.status is CalibrationStatus.STALE:
        return "Review the saved context before continuing."
    return "This calibration cannot be configured yet."
