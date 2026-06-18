"""
gui/utils/dialog_helpers.py

Shared helpers for styling and configuring Papyrus dialogs.
"""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor
from PySide6.QtWidgets import QDialog

# Standard Papyrus window flags for secondary windows.
# See gui/managers/dialog_manager.py for the reasoning behind Qt.Tool.
_PAPYRUS_DIALOG_FLAGS: Qt.WindowType = (
    Qt.WindowType.Tool
    | Qt.WindowType.WindowTitleHint
    | Qt.WindowType.WindowSystemMenuHint
    | Qt.WindowType.WindowCloseButtonHint
)


def style_dialog_with_theme(
    dialog: QDialog,
    theme: dict,
    extra_styles: str = "",
    *,
    modal: bool = True,
) -> None:
    """
    Apply the current Papyrus theme stylesheet to *dialog* and configure
    window flags so the dialog does not create a separate taskbar entry or
    reveal the desktop panel when Papyrus is fullscreen.

    :param dialog: QDialog to configure.
    :param theme: Theme dict (from ThemeManager.get_theme()).
    :param extra_styles: Additional CSS appended after the default stylesheet.
    :param modal: If True (default), set WindowModal modality so only the main
                  window is blocked rather than the entire application.
                  Pass False for dialogs that will be shown modelessly after
                  styling.
    """
    stylesheet = f"""
        QDialog {{ background-color: {theme['bg_main']}; color: {theme['text_main']}; }}
        QLabel {{ color: {theme['text_main']}; font-weight: bold; }}
        QGroupBox {{ color: {theme['text_main']}; border: 1px solid {theme['border']}; border-radius: 6px; margin-top: 8px; padding: 8px; }}
        QListWidget, QTabWidget::pane, QTextEdit, QPlainTextEdit, QLineEdit, QComboBox, QSpinBox, QDoubleSpinBox {{
            background-color: {theme['bg_panel']};
            color: {theme['text_main']};
            border: 1px solid {theme['border']};
            border-radius: 4px;
            padding: 5px 7px;
            selection-background-color: {theme['accent']};
            selection-color: #ffffff;
        }}
        QComboBox::drop-down {{ border-left: 1px solid {theme['border']}; width: 22px; }}
        QComboBox QAbstractItemView {{
            background-color: {theme['bg_panel']};
            color: {theme['text_main']};
            border: 1px solid {theme['border']};
            selection-background-color: {theme['accent']};
            selection-color: #ffffff;
        }}
        QListWidget::item:selected, QTabBar::tab:selected {{ background-color: {theme['accent']}; color: #ffffff; }}
        QPushButton {{ background-color: {theme['accent']}; color: #ffffff; border: none; padding: 6px 12px; border-radius: 4px; font-weight: bold; }}
        QPushButton:hover {{ background-color: {theme['accent_hover']}; }}
        QPushButton:disabled {{ background-color: {theme['bg_input']}; color: {theme['text_muted']}; border: 1px solid {theme['border']}; }}
        QMenu {{ background-color: {theme['bg_panel']}; color: {theme['text_main']}; border: 1px solid {theme['border']}; }}
    """
    if extra_styles:
        stylesheet += extra_styles
    dialog.setStyleSheet(stylesheet)

    # Apply Papyrus-standard flags: no taskbar entry, no fullscreen panel reveal.
    dialog.setWindowFlags(_PAPYRUS_DIALOG_FLAGS)
    if modal:
        dialog.setWindowModality(Qt.WindowModality.WindowModal)
