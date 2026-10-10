"""Qt presentation for the service-evaluated calibration lifecycle."""

from __future__ import annotations

from collections.abc import Iterable

from PySide6.QtCore import Qt
from PySide6.QtGui import QPainter, QPainterPath, QPalette, QPen
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QSizePolicy, QVBoxLayout, QWidget

from calibrate3dp.calibration.state import CalibrationState
from calibrate3dp.calibration.state import CalibrationStatus


_STATUS_PRESENTATION: dict[CalibrationStatus, str] = {
    CalibrationStatus.UNTESTED: "Untested",
    CalibrationStatus.BLOCKED: "Blocked",
    CalibrationStatus.READY: "Can configure",
    CalibrationStatus.IN_PROGRESS: "In progress",
    CalibrationStatus.NEEDS_REVIEW: "Needs review",
    CalibrationStatus.ACCEPTED: "Accepted",
    CalibrationStatus.STALE: "Stale",
}


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


class CalibrationStatusPanel(QWidget):
    """Show the available ironing workflow and its evaluated reasons."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("calibrationStatusPanel")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 8, 0, 8)
        layout.setSpacing(5)

        self.workflow_label = QLabel("Ironing · flow × speed")
        self.workflow_label.setObjectName("calibrationWorkflow")
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
        self.can_start_label = QLabel("Can start: no")
        self.can_start_label.setObjectName("calibrationCanStart")
        self.reasons_label = QLabel("Calibration status has not been evaluated.")
        self.reasons_label.setObjectName("calibrationReasons")
        self.reasons_label.setWordWrap(True)
        self.availability_label = QLabel(
            "Other V1 calibration workflows are not yet available."
        )
        self.availability_label.setObjectName("calibrationAvailability")
        self.print_readiness_label = QLabel(
            "Calibration state does not establish print readiness. Generated plates are not print-ready."
        )
        self.print_readiness_label.setObjectName("calibrationPrintReadiness")
        self.print_readiness_label.setWordWrap(True)

        status_row = QHBoxLayout()
        status_row.setSpacing(8)
        status_row.addWidget(self.status_chip, 0, Qt.AlignmentFlag.AlignLeft)
        status_row.addWidget(self.can_start_label)
        status_row.addStretch(1)

        layout.addWidget(self.workflow_label)
        layout.addLayout(status_row)
        for label in (
            self.reasons_label,
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
        self.can_start_label.setText(
            "Can start: yes" if state.can_start else "Can start: no"
        )
        lines = [*state.reasons, *(f"Recommendation: {item}" for item in state.recommendations)]
        self.reasons_label.setText("\n".join(lines) if lines else "No blocking reasons or recommendations.")

    def set_unavailable(self, reason: str) -> None:
        """Fail closed when the application cannot obtain service state."""
        self._set_status("unavailable", "Unavailable")
        self.can_start_label.setText("Can start: no")
        self.reasons_label.setText(reason)

    def _set_status(self, state: str, text: str) -> None:
        self.status_label.setText(text)
        self.status_label.setProperty("state", state)
        self.status_label.setAccessibleName(f"Calibration status: {text}")
        self.status_chip.setProperty("state", state)
        self.status_icon.set_state(state)
        self.status_chip.style().unpolish(self.status_chip)
        self.status_chip.style().polish(self.status_chip)
