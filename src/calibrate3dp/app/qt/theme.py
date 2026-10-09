"""Shared visual tokens and QSS for the PySide6 desktop shell."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ThemeTokens:
    canvas: str = "#FBFAF7"
    surface: str = "#FFFFFF"
    surface_subtle: str = "#F5F4EE"
    ink: str = "#17201C"
    muted: str = "#686E6B"
    line: str = "#D8DCD5"
    green: str = "#1D6549"
    green_dark: str = "#164E38"
    green_pale: str = "#F2F7F2"
    focus: str = "#287953"


TOKENS = ThemeTokens()


def application_stylesheet() -> str:
    """Return the shared stylesheet; focus rings remain visible for keyboard users."""
    t = TOKENS
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
        QWidget#navigationRail {{ background: {t.surface_subtle}; border-right: 1px solid {t.line}; }}
        QPushButton#navButton {{
            text-align: left; padding: 11px 13px; min-height: 22px;
            color: {t.ink}; background: transparent; border: 1px solid transparent;
            border-radius: 8px; font-weight: 550;
        }}
        QPushButton#navButton:hover {{ background: {t.surface}; border-color: {t.line}; }}
        QPushButton#navButton:checked {{ color: {t.green}; background: {t.green_pale}; border-color: {t.green}; }}
        QPushButton#navButton:focus {{ border: 2px solid {t.focus}; }}
        QLineEdit {{
            min-height: 22px; padding: 9px 12px; background: {t.surface};
            border: 1px solid {t.line}; border-radius: 7px;
        }}
        QLineEdit:focus {{ border: 2px solid {t.focus}; padding: 8px 11px; }}
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
    """

