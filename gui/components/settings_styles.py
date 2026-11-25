"""Shared styling for Settings tabs (AI Setup, Memory, …).

Moved out of ai_setup_tab so every tab uses one set of themed styles
instead of copying them (rules.md §1.4).
"""
from __future__ import annotations

from PySide6.QtWidgets import QFrame, QLabel

from gui.base.core import FALLBACK_THEME


def theme_value(theme: dict, key: str, fallback: str = "") -> str:
    return theme.get(key) or FALLBACK_THEME.get(key, fallback)


# ---------------------------------------------------------------------------
# Reusable styling helpers (functions, not methods, so helpers can use them)
# ---------------------------------------------------------------------------

def section_header_style(t: dict) -> str:
    return (
        f"color: {theme_value(t,'text_main','#fff')}; background: transparent; "
        f"font-size: 13px; font-weight: 700; margin-top: 8px; margin-bottom: 2px;"
    )


def label_style(t: dict, muted: bool = False, size: int = 13) -> str:
    col = theme_value(t, "text_muted", "#aaa") if muted else theme_value(t, "text_main", "#fff")
    return f"color: {col}; background: transparent; font-size: {size}px;"


def card_style(t: dict) -> str:
    return (
        f"background: {theme_value(t,'bg_panel','#2b2b2b')}; "
        f"border: 1px solid {theme_value(t,'border','#444')}; border-radius: 8px;"
    )


def btn_primary(t: dict) -> str:
    return (
        f"background: {theme_value(t,'accent','#0078D7')}; color: #fff; border: none; "
        f"border-radius: 5px; padding: 5px 14px; font-weight: 600; font-size: 12px;"
    )


def btn_danger(t: dict) -> str:
    return (
        f"background: {theme_value(t,'error','#cc3333')}; color: #fff; border: none; "
        f"border-radius: 5px; padding: 5px 14px; font-weight: 600; font-size: 12px;"
    )


def btn_default(t: dict) -> str:
    return (
        f"background: {theme_value(t,'bg_panel','#333')}; color: {theme_value(t,'text_main','#fff')}; "
        f"border: 1px solid {theme_value(t,'border','#555')}; "
        f"border-radius: 5px; padding: 5px 14px; font-weight: 600; font-size: 12px;"
    )


def btn_small_remove(t: dict) -> str:
    return (
        f"background: transparent; color: {theme_value(t,'error','#cc3333')}; "
        f"border: 1px solid {theme_value(t,'error','#cc3333')}; "
        f"border-radius: 4px; padding: 1px 8px; font-size: 11px;"
    )


def input_style(t: dict) -> str:
    return (
        f"background: {theme_value(t,'bg_input','#333')}; color: {theme_value(t,'text_main','#fff')}; "
        f"border: 1px solid {theme_value(t,'border','#555')}; border-radius: 5px; "
        f"padding: 5px 10px; font-size: 12px; selection-background-color: {theme_value(t,'accent','#0078D7')};"
    )


def progress_bar_style(t: dict) -> str:
    return (
        f"QProgressBar {{ border: 1px solid {theme_value(t,'border','#555')}; border-radius: 3px; "
        f"background: {theme_value(t,'bg_input','#333')}; height: 10px; text-align: center; color: transparent; }}"
        f"QProgressBar::chunk {{ background: {theme_value(t,'accent','#0078D7')}; border-radius: 2px; }}"
    )


def divider(t: dict) -> QFrame:
    line = QFrame()
    line.setFrameShape(QFrame.Shape.HLine)
    line.setFixedHeight(1)
    line.setStyleSheet(f"background: {theme_value(t, 'border', '#444')}; border: none;")
    return line


def section_header(text: str, t: dict) -> QLabel:
    label = QLabel(text)
    label.setStyleSheet(section_header_style(t))
    return label
