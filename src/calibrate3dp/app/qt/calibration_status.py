"""Qt presentation for the service-evaluated calibration lifecycle."""

from __future__ import annotations

from collections.abc import Iterable

from PySide6.QtWidgets import QLabel, QVBoxLayout, QWidget

from calibrate3dp.calibration.state import CalibrationState


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
        self.status_label = QLabel("Unavailable")
        self.status_label.setObjectName("calibrationLifecycleStatus")
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

        for label in (
            self.workflow_label,
            self.status_label,
            self.can_start_label,
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

        self.status_label.setText(state.status.value.replace("_", " ").title())
        self.can_start_label.setText(
            "Can start: yes" if state.can_start else "Can start: no"
        )
        lines = [*state.reasons, *(f"Recommendation: {item}" for item in state.recommendations)]
        self.reasons_label.setText("\n".join(lines) if lines else "No blocking reasons or recommendations.")

    def set_unavailable(self, reason: str) -> None:
        """Fail closed when the application cannot obtain service state."""
        self.status_label.setText("Unavailable")
        self.can_start_label.setText("Can start: no")
        self.reasons_label.setText(reason)
