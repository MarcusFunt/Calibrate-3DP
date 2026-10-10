"""Shared visual tokens and QSS for the PySide6 desktop shell."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ThemeTokens:
    canvas: str = "#FBFAF7"
    surface: str = "#FFFFFF"
    surface_subtle: str = "#F5F4EE"
    ink: str = "#17201C"
    muted: str = "#5F6561"
    line: str = "#D8DCD5"
    control_border: str = "#868D88"
    green: str = "#1D6549"
    green_dark: str = "#164E38"
    green_pale: str = "#F2F7F2"
    focus: str = "#287953"
    status_untested: tuple[str, str] = ("#5B615E", "#F1F1EC")
    status_blocked: tuple[str, str] = ("#843C34", "#F8E9E7")
    status_ready: tuple[str, str] = ("#3F6556", "#EEF4F0")
    status_in_progress: tuple[str, str] = ("#234E90", "#EAF0FC")
    status_needs_review: tuple[str, str] = ("#6E4A12", "#F7EED8")
    status_accepted: tuple[str, str] = ("#245C43", "#EAF3ED")
    status_stale: tuple[str, str] = ("#5D4D7A", "#F0ECF5")
    status_neutral: tuple[str, str] = ("#3F4A45", "#EDEEEA")


TOKENS = ThemeTokens()


def application_stylesheet() -> str:
    """Return the shared stylesheet; focus rings remain visible for keyboard users."""
    t = TOKENS
    status_pairs = {
        "untested": t.status_untested,
        "blocked": t.status_blocked,
        "ready": t.status_ready,
        "in_progress": t.status_in_progress,
        "needs_review": t.status_needs_review,
        "accepted": t.status_accepted,
        "stale": t.status_stale,
        "unavailable": t.status_neutral,
    }
    status_styles = "\n".join(
        f'QFrame#calibrationLifecycleChip[state="{state}"] {{ color: {foreground}; background: {background}; }}\n'
        f'QLabel#calibrationLifecycleStatus[state="{state}"] {{ color: {foreground}; background: transparent; font-weight: 650; }}\n'
        f'QWidget#calibrationStatusIcon[state="{state}"] {{ color: {foreground}; }}'
        for state, (foreground, background) in status_pairs.items()
    )
    return f"""
        QWidget {{
            color: {t.ink};
            font-family: "Segoe UI", "Inter", sans-serif;
            font-size: 10pt;
        }}
        QMainWindow, QWidget#appCanvas {{ background: {t.canvas}; }}
        QLabel#brandName {{ color: {t.green}; font-size: 16pt; font-weight: 700; }}
        QLabel#brandCaption {{ color: {t.muted}; font-size: 8pt; letter-spacing: 2px; }}
        QLabel#pageTitle {{ color: {t.ink}; font-size: 24pt; font-weight: 700; }}
        QLabel#sectionTitle {{ color: {t.ink}; font-size: 16pt; font-weight: 650; }}
        QLabel#bodyCopy {{ color: {t.muted}; font-size: 11pt; }}
        QLabel#eyebrow {{ color: {t.muted}; font-size: 8pt; font-weight: 600; letter-spacing: 1px; }}
        QFrame#appHeader {{ background: {t.surface}; border-bottom: 1px solid {t.line}; }}
        QWidget#navigationRail {{ background: {t.surface}; border-bottom: 1px solid {t.line}; }}
        QPushButton#brandButton {{
            color: {t.green}; background: transparent; border: 0; padding: 7px 4px;
            font-size: 16pt; font-weight: 700;
        }}
        QPushButton#brandButton:hover {{ color: {t.green_dark}; }}
        QPushButton#brandButton:focus {{ border: 2px solid {t.focus}; border-radius: 6px; }}
        QFrame#contextPill {{ background: {t.surface_subtle}; border: 1px solid {t.line}; border-radius: 9px; }}
        QLabel#contextLabel {{ color: {t.muted}; font-size: 7pt; font-weight: 700; letter-spacing: 1px; }}
        QLabel#contextValue {{ color: {t.ink}; font-size: 9pt; font-weight: 600; }}
        QFrame#plateLookupControls {{ background: transparent; border: 0; }}
        QTabWidget#workspaceTabs::pane, QTabWidget#plateDetailTabs::pane {{
            background: {t.surface}; border: 1px solid {t.line}; border-radius: 9px;
            top: -1px;
        }}
        QTabWidget#workspaceTabs QTabBar::tab, QTabWidget#plateDetailTabs QTabBar::tab {{
            color: {t.ink}; background: {t.surface_subtle}; border: 1px solid {t.control_border};
            border-bottom-color: {t.line}; border-top-left-radius: 8px; border-top-right-radius: 8px;
            padding: 9px 15px; margin-right: 4px; min-height: 22px; font-weight: 550;
        }}
        QTabWidget#workspaceTabs QTabBar::tab:selected, QTabWidget#plateDetailTabs QTabBar::tab:selected {{
            color: {t.green}; background: {t.surface}; border-bottom-color: {t.surface}; font-weight: 650;
        }}
        QTabWidget#workspaceTabs QTabBar::tab:hover:!selected,
        QTabWidget#plateDetailTabs QTabBar::tab:hover:!selected {{ background: {t.green_pale}; }}
        QTabWidget#workspaceTabs QTabBar::tab:focus,
        QTabWidget#plateDetailTabs QTabBar::tab:focus {{ border: 2px solid {t.focus}; }}
        QPushButton#navButton {{
            text-align: left; padding: 8px 13px; min-height: 22px;
            color: {t.ink}; background: transparent; border: 1px solid transparent;
            border-radius: 8px; font-weight: 550;
        }}
        QPushButton#navButton:hover {{ background: {t.surface}; border-color: {t.line}; }}
        QPushButton#navButton:checked {{ color: {t.green}; background: {t.green_pale}; border-color: {t.green}; }}
        QPushButton#navButton:focus {{ border: 2px solid {t.focus}; }}
        QPushButton#settingsButton {{
            color: {t.ink}; background: {t.surface}; border: 1px solid {t.control_border};
            border-radius: 8px; min-width: 78px; min-height: 34px; padding: 6px 10px;
            font-weight: 600;
        }}
        QPushButton#settingsButton:hover {{ background: {t.green_pale}; }}
        QPushButton#settingsButton:checked {{ color: {t.green}; border-color: {t.green}; background: {t.green_pale}; }}
        QPushButton#settingsButton:focus {{ border: 2px solid {t.focus}; }}
        QLineEdit {{
            min-height: 22px; padding: 9px 12px; background: {t.surface};
            border: 1px solid {t.control_border}; border-radius: 7px;
        }}
        QLineEdit:focus {{ border: 2px solid {t.focus}; padding: 8px 11px; }}
        QComboBox, QSpinBox, QDoubleSpinBox {{
            min-height: 22px; padding: 7px 10px; background: {t.surface};
            border: 1px solid {t.control_border}; border-radius: 7px;
        }}
        QPlainTextEdit, QTextEdit, QTableWidget {{
            background: {t.surface}; border: 1px solid {t.control_border}; border-radius: 7px;
        }}
        QHeaderView::section {{
            color: {t.ink}; background: {t.surface_subtle}; border: 1px solid {t.line};
            padding: 7px 9px; font-weight: 600;
        }}
        QTableWidget::item:selected {{ color: {t.ink}; background: {t.green_pale}; }}
        QPushButton#printerRow {{
            text-align: left; background: transparent; border: 1px solid transparent;
            border-bottom-color: {t.line}; border-radius: 7px; padding: 9px 10px;
        }}
        QPushButton#printerRow:hover {{ background: {t.surface}; border-color: {t.line}; }}
        QPushButton#printerRow:checked {{ background: {t.green_pale}; border: 1px solid {t.green}; }}
        QPushButton#printerRow:focus {{ border: 2px solid {t.focus}; }}
        QPushButton#primaryAction {{
            background: {t.green}; color: {t.surface}; border: 1px solid {t.green};
            border-radius: 8px; padding: 12px 20px; min-height: 24px; font-weight: 650;
        }}
        QPushButton#primaryAction:hover {{ background: {t.green_dark}; border-color: {t.green_dark}; }}
        QPushButton#primaryAction:disabled {{ background: {t.line}; color: {t.muted}; border-color: {t.line}; }}
        QPushButton#primaryAction:focus {{ border: 2px solid {t.focus}; }}
        QPushButton#secondaryAction {{
            background: transparent; color: {t.green}; border: 1px solid {t.green};
            border-radius: 8px; padding: 11px 17px; min-height: 22px; font-weight: 600;
        }}
        QPushButton#secondaryAction:hover {{ background: {t.green_pale}; }}
        QPushButton#secondaryAction:focus {{ border: 2px solid {t.focus}; }}
        QFrame#card {{ background: {t.surface}; border: 1px solid {t.line}; border-radius: 10px; }}
        QFrame#divider {{ background: {t.line}; max-width: 1px; }}
        QLabel#mutedStatus {{ color: {t.muted}; }}
        QFrame#calibrationLifecycleChip {{ border-radius: 9px; }}
        QWidget#calibrationStatusIcon {{ background: transparent; }}
        {status_styles}
    """

