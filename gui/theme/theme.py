"""
gui/theme/theme.py

ThemeManager: singleton theme registry with full user CRUD and plugin registration.

Theme tiers:
  - Built-in: shipped with the app, cannot be deleted, can be duplicated
  - User:     saved to QSettings, full CRUD (create/rename/edit/delete)
  - Plugin:   registered by plugins at load time, cleared on unload

Designed so that import/export of .papyrus-theme files can be added later
without changes to the storage model.
"""
from __future__ import annotations

import json
from typing import Dict, List, Optional

from PySide6.QtCore import QObject, Signal, QSettings, Qt
from PySide6.QtWidgets import (
    QApplication, QDialog, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QColorDialog, QScrollArea, QWidget, QFormLayout, QListWidget,
    QListWidgetItem, QInputDialog, QMessageBox, QSplitter, QFrame,
)
from PySide6.QtGui import QColor
from gui.managers.dialog_manager import exec_as_modal, get_for_widget
from core.events.event_bus import EventBus

# ---------------------------------------------------------------------------
# Built-in themes — never persisted, shipped with the app
# ---------------------------------------------------------------------------

_BUILTIN_THEMES: Dict[str, dict] = {
    "Dark (Default)": {
        "bg_main": "#1e1e1e", "bg_panel": "#2b2b2b", "bg_input": "#333333",
        "text_main": "#ffffff", "text_muted": "#aaaaaa", "border": "#555555",
        "accent": "#0078D7", "accent_hover": "#0055ff", "canvas": "#1a1a1a",
        "success": "#00cc66", "warning": "#ffaa00", "error": "#ff4444",
        "ai_bubble": "#2d2238", "ai_bubble_border": "#b57edc", "ai_bubble_hover": "#38274a",
        "user_bubble": "#2b2b2b", "user_bubble_border": "#444444", "user_bubble_hover": "#333333",
    },
    "Light": {
        "bg_main": "#f0f0f0", "bg_panel": "#ffffff", "bg_input": "#e0e0e0",
        "text_main": "#000000", "text_muted": "#555555", "border": "#cccccc",
        "accent": "#005a9e", "accent_hover": "#0078D7", "canvas": "#e8e8e8",
        "success": "#28a745", "warning": "#d97706", "error": "#dc3545",
        "ai_bubble": "#f3e8ff", "ai_bubble_border": "#d8b4fe", "ai_bubble_hover": "#e9d5ff",
        "user_bubble": "#ffffff", "user_bubble_border": "#cccccc", "user_bubble_hover": "#f8f9fa",
    },
    "Ocean": {
        "bg_main": "#0f172a", "bg_panel": "#1e293b", "bg_input": "#334155",
        "text_main": "#f8fafc", "text_muted": "#94a3b8", "border": "#475569",
        "accent": "#3b82f6", "accent_hover": "#2563eb", "canvas": "#020617",
        "success": "#10b981", "warning": "#f59e0b", "error": "#ef4444",
        "ai_bubble": "#2e1065", "ai_bubble_border": "#8b5cf6", "ai_bubble_hover": "#4c1d95",
        "user_bubble": "#1e293b", "user_bubble_border": "#475569", "user_bubble_hover": "#334155",
    },
    "Solarized": {
        "bg_main": "#002b36", "bg_panel": "#073642", "bg_input": "#002b36",
        "text_main": "#839496", "text_muted": "#586e75", "border": "#586e75",
        "accent": "#268bd2", "accent_hover": "#2aa198", "canvas": "#001b21",
        "success": "#859900", "warning": "#b58900", "error": "#dc322f",
        "ai_bubble": "#1a0b2e", "ai_bubble_border": "#6c71c4", "ai_bubble_hover": "#2a1b3e",
        "user_bubble": "#073642", "user_bubble_border": "#586e75", "user_bubble_hover": "#002b36",
    },
    "Bubblegum": {
        "bg_main": "#ffbe6f", "bg_panel": "#99c1f1", "bg_input": "#dc8add",
        "text_main": "#e66100", "text_muted": "#774d00", "border": "#2ec27e",
        "accent": "#0078d7", "accent_hover": "#0055ff", "canvas": "#8ff0a4",
        "success": "#00cc66", "warning": "#ffaa00", "error": "#ff4444",
        "ai_bubble": "#2d2238", "ai_bubble_border": "#b57edc", "ai_bubble_hover": "#38274a",
        "user_bubble": "#ffa348", "user_bubble_border": "#c64600", "user_bubble_hover": "#ffbe6f",
    },
}

_QSETTINGS_ORG = "PDFMultitool"
_QSETTINGS_APP = "Workspace"
_USER_THEMES_KEY = "user_themes_v2"      # dict {name: theme_dict}
_ACTIVE_THEME_KEY = "theme"


# ---------------------------------------------------------------------------
# Theme key labels (for the editor)
# ---------------------------------------------------------------------------

_KEY_LABELS: Dict[str, str] = {
    "bg_main": "Main Background",
    "bg_panel": "Panel Background",
    "bg_input": "Input Field Background",
    "text_main": "Main Text",
    "text_muted": "Muted Text",
    "border": "Borders",
    "accent": "Accent Color",
    "accent_hover": "Accent Hover",
    "canvas": "Workspace Canvas",
    "success": "Success Color",
    "warning": "Warning Color",
    "error": "Error Color",
    "ai_bubble": "AI Note Background",
    "ai_bubble_border": "AI Note Border",
    "ai_bubble_hover": "AI Note Hover",
    "user_bubble": "User Note Background",
    "user_bubble_border": "User Note Border",
    "user_bubble_hover": "User Note Hover",
}


# ---------------------------------------------------------------------------
# Single-theme color editor
# ---------------------------------------------------------------------------

class CustomThemeDialog(QDialog):
    """Edit the colors of a single theme dict. Returns updated colors on accept."""

    def __init__(self, current_theme: dict, title: str = "Theme Editor", parent=None):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.setMinimumSize(450, 650)

        self.colors = dict(current_theme)
        self._layout = QVBoxLayout(self)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll_widget = QWidget()
        self.form = QFormLayout(scroll_widget)

        self.buttons: Dict[str, QPushButton] = {}
        for key, name in _KEY_LABELS.items():
            color = self.colors.get(key, "#000000")
            btn = QPushButton(color.upper())
            btn.setFixedSize(120, 30)
            btn.setStyleSheet(self._btn_style(color))
            btn.clicked.connect(lambda checked, k=key, b=btn: self._pick_color(k, b))
            self.buttons[key] = btn
            self.form.addRow(name, btn)

        scroll.setWidget(scroll_widget)
        self._layout.addWidget(scroll)

        btn_row = QHBoxLayout()
        reset_btn = QPushButton("Reset to Dark")
        reset_btn.clicked.connect(self._reset)
        save_btn = QPushButton("Save")
        save_btn.clicked.connect(self.accept)
        cancel_btn = QPushButton("Cancel")
        cancel_btn.clicked.connect(self.reject)
        btn_row.addWidget(reset_btn)
        btn_row.addStretch()
        btn_row.addWidget(cancel_btn)
        btn_row.addWidget(save_btn)
        self._layout.addLayout(btn_row)

        self._apply_dialog_chrome()

    # ------------------------------------------------------------------

    def _apply_dialog_chrome(self):
        tm = ThemeManager()
        t = tm.get_theme()
        self.setStyleSheet(
            f"background-color: {t['bg_main']}; color: {t['text_main']};"
        )
        save_btn = self.findChild(QPushButton, "")  # handled below via btn_row
        for btn in self.findChildren(QPushButton):
            txt = btn.text()
            if txt == "Save":
                btn.setStyleSheet(
                    f"background-color: {t['accent']}; color: #fff;"
                    " padding: 6px 15px; border-radius: 4px; border: none;"
                )
            elif txt == "Cancel":
                btn.setStyleSheet(
                    f"background-color: {t['bg_input']}; color: {t['text_main']};"
                    f" padding: 6px 15px; border-radius: 4px;"
                    f" border: 1px solid {t['border']};"
                )
            elif txt == "Reset to Dark":
                btn.setStyleSheet(
                    f"background-color: {t['warning']}; color: #000;"
                    " padding: 6px 15px; border-radius: 4px; border: none;"
                )

    @staticmethod
    def _contrasting_text(hex_color: str) -> str:
        c = QColor(hex_color)
        brightness = (c.red() * 299 + c.green() * 587 + c.blue() * 114) / 1000
        return "#000000" if brightness > 128 else "#ffffff"

    def _btn_style(self, color: str) -> str:
        fg = self._contrasting_text(color)
        return (
            f"background-color: {color}; color: {fg};"
            " border: 1px solid #aaa; font-weight: bold;"
        )

    def _pick_color(self, key: str, btn: QPushButton):
        initial = QColor(self.colors.get(key, "#000000"))
        color = QColorDialog.getColor(initial, self, f"Pick color — {_KEY_LABELS.get(key, key)}")
        if color.isValid():
            hex_color = color.name()
            self.colors[key] = hex_color
            btn.setText(hex_color.upper())
            btn.setStyleSheet(self._btn_style(hex_color))

    def _reset(self):
        for key, hex_color in _BUILTIN_THEMES["Dark (Default)"].items():
            self.colors[key] = hex_color
            if key in self.buttons:
                self.buttons[key].setText(hex_color.upper())
                self.buttons[key].setStyleSheet(self._btn_style(hex_color))

    def get_colors(self) -> dict:
        return dict(self.colors)


# ---------------------------------------------------------------------------
# Theme Manager Dialog — full CRUD
# ---------------------------------------------------------------------------

class ThemeManagerDialog(QDialog):
    """
    Main UI for managing themes:
    - Lists built-in themes (view only, can duplicate)
    - Lists user themes (full edit / rename / delete)
    - Lists plugin themes (view only)
    - Lets user switch the active theme from here
    """

    def __init__(self, theme_manager: "_ThemeManager", parent=None):
        super().__init__(parent)
        self._tm = theme_manager
        self.setWindowTitle("Theme Manager")
        self.setMinimumSize(660, 500)

        root = QHBoxLayout(self)
        splitter = QSplitter(Qt.Orientation.Horizontal)
        root.addWidget(splitter)

        # --- Left: theme list ---
        left = QWidget()
        left_layout = QVBoxLayout(left)
        left_layout.setContentsMargins(0, 0, 0, 0)
        self._list = QListWidget()
        self._list.currentRowChanged.connect(self._on_selection_changed)
        left_layout.addWidget(QLabel("Themes"))
        left_layout.addWidget(self._list)

        list_btns = QHBoxLayout()
        self._btn_new = QPushButton("New")
        self._btn_new.clicked.connect(self._create_theme)
        self._btn_dup = QPushButton("Duplicate")
        self._btn_dup.clicked.connect(self._duplicate_theme)
        self._btn_rename = QPushButton("Rename")
        self._btn_rename.clicked.connect(self._rename_theme)
        self._btn_delete = QPushButton("Delete")
        self._btn_delete.clicked.connect(self._delete_theme)
        for b in (self._btn_new, self._btn_dup, self._btn_rename, self._btn_delete):
            list_btns.addWidget(b)
        left_layout.addLayout(list_btns)
        splitter.addWidget(left)

        # --- Right: preview + actions ---
        right = QWidget()
        right_layout = QVBoxLayout(right)
        self._preview_label = QLabel("Select a theme to preview")
        self._preview_label.setWordWrap(True)
        self._preview_frame = QFrame()
        self._preview_frame.setMinimumHeight(200)
        self._preview_frame.setFrameShape(QFrame.Shape.StyledPanel)
        self._preview_layout = QVBoxLayout(self._preview_frame)
        self._preview_layout.setContentsMargins(0, 0, 0, 0)
        self._preview_layout.setSpacing(0)

        right_layout.addWidget(QLabel("Preview"))
        right_layout.addWidget(self._preview_frame)
        right_layout.addWidget(self._preview_label)
        right_layout.addStretch()

        action_btns = QHBoxLayout()
        self._btn_edit = QPushButton("Edit Colors")
        self._btn_edit.clicked.connect(self._edit_theme)
        self._btn_apply = QPushButton("Apply Theme")
        self._btn_apply.clicked.connect(self._apply_theme)
        close_btn = QPushButton("Close")
        close_btn.clicked.connect(self.accept)
        action_btns.addWidget(self._btn_edit)
        action_btns.addWidget(self._btn_apply)
        action_btns.addStretch()
        action_btns.addWidget(close_btn)
        right_layout.addLayout(action_btns)
        splitter.addWidget(right)

        splitter.setSizes([240, 400])
        self._populate_list()
        self._apply_chrome()

        # Keep the list in sync if themes change while dialog is open.
        # Store the connection so we can disconnect it in closeEvent to prevent
        # the lambda from firing after the QListWidget is already deleted.
        self._themes_changed_slot = lambda: self._safe_repopulate()
        self._tm.themes_list_changed.connect(self._themes_changed_slot)

    def _safe_repopulate(self) -> None:
        try:
            self._populate_list(select_name=self._selected_name())
        except RuntimeError:
            pass

    def closeEvent(self, event) -> None:
        try:
            self._tm.themes_list_changed.disconnect(self._themes_changed_slot)
        except Exception:
            pass
        super().closeEvent(event)

    # ------------------------------------------------------------------
    # List population
    # ------------------------------------------------------------------

    def _populate_list(self, select_name: Optional[str] = None):
        self._list.blockSignals(True)
        self._list.clear()

        current = self._tm.current_theme_name

        def _add(name: str, suffix: str = ""):
            label = f"{name}{suffix}"
            if name == current:
                label += "  ✓"
            item = QListWidgetItem(label)
            item.setData(Qt.ItemDataRole.UserRole, name)
            self._list.addItem(item)

        for name in _BUILTIN_THEMES:
            _add(name, " [built-in]")
        for name in sorted(self._tm._user_themes.keys()):
            _add(name)
        for name in sorted(self._tm._plugin_themes.keys()):
            _add(name, " [plugin]")

        self._list.blockSignals(False)

        # Select the right row
        target = select_name or current
        for i in range(self._list.count()):
            item = self._list.item(i)
            if item and item.data(Qt.ItemDataRole.UserRole) == target:
                self._list.setCurrentRow(i)
                break
        else:
            if self._list.count():
                self._list.setCurrentRow(0)

    def _selected_name(self) -> Optional[str]:
        item = self._list.currentItem()
        return item.data(Qt.ItemDataRole.UserRole) if item else None

    def _is_user_theme(self, name: str) -> bool:
        return name in self._tm._user_themes

    # ------------------------------------------------------------------
    # Preview
    # ------------------------------------------------------------------

    def _on_selection_changed(self, _row: int):
        name = self._selected_name()
        if not name:
            return
        theme = self._tm.get_theme_by_name(name)
        is_user = self._is_user_theme(name)
        self._btn_edit.setEnabled(is_user)
        self._btn_rename.setEnabled(is_user)
        self._btn_delete.setEnabled(is_user)
        self._btn_apply.setEnabled(True)
        self._refresh_preview(theme, name)

    def _refresh_preview(self, theme: dict, name: str):
        # Clear old preview content
        while self._preview_layout.count():
            item = self._preview_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

        # --- Header strip (mimics the Research Assistant header) ---
        header = QFrame()
        header.setFixedHeight(26)
        header.setStyleSheet(
            f"background-color: {theme.get('bg_input', '#333')};"
            f" border-bottom: 1px solid {theme.get('border', '#555')};"
        )
        header_row = QHBoxLayout(header)
        header_row.setContentsMargins(8, 0, 8, 0)
        lbl_title = QLabel("Research Assistant")
        lbl_title.setStyleSheet(
            f"color: {theme.get('text_muted', '#aaa')}; font-size: 11px; font-weight: bold;"
            " background: transparent; border: none;"
        )
        header_row.addWidget(lbl_title)
        header_row.addStretch()
        dot = QLabel("●")
        dot.setStyleSheet(
            f"color: {theme.get('accent', '#0078d7')}; background: transparent; border: none; font-size: 10px;"
        )
        header_row.addWidget(dot)
        self._preview_layout.addWidget(header)

        # --- Chat area ---
        chat_area = QWidget()
        chat_area.setStyleSheet(f"background-color: {theme.get('bg_main', '#1e1e1e')};")
        chat_layout = QVBoxLayout(chat_area)
        chat_layout.setContentsMargins(10, 8, 10, 8)
        chat_layout.setSpacing(8)

        # AI bubble
        ai_bubble = QFrame()
        ai_bubble.setStyleSheet(
            f"background-color: {theme.get('ai_bubble', '#2d2238')};"
            f" border: 1px solid {theme.get('ai_bubble_border', '#b57edc')};"
            f" border-radius: 8px;"
        )
        ai_layout = QVBoxLayout(ai_bubble)
        ai_layout.setContentsMargins(8, 5, 8, 5)
        ai_layout.setSpacing(2)
        ai_who = QLabel("AI Assistant")
        ai_who.setStyleSheet(
            f"color: {theme.get('ai_bubble_border', '#b57edc')};"
            " font-size: 10px; font-weight: bold; background: transparent; border: none;"
        )
        ai_text = QLabel("Here is what I found in your documents…")
        ai_text.setStyleSheet(
            f"color: {theme.get('text_main', '#fff')}; font-size: 11px;"
            " background: transparent; border: none;"
        )
        ai_layout.addWidget(ai_who)
        ai_layout.addWidget(ai_text)
        chat_layout.addWidget(ai_bubble)

        # User bubble
        user_bubble = QFrame()
        user_bubble.setStyleSheet(
            f"background-color: {theme.get('user_bubble', '#2b2b2b')};"
            f" border: 1px solid {theme.get('user_bubble_border', '#444')};"
            f" border-radius: 8px;"
        )
        user_layout = QVBoxLayout(user_bubble)
        user_layout.setContentsMargins(8, 5, 8, 5)
        user_layout.setSpacing(2)
        user_who = QLabel("You")
        user_who.setStyleSheet(
            f"color: {theme.get('accent', '#0078d7')};"
            " font-size: 10px; font-weight: bold; background: transparent; border: none;"
        )
        user_text = QLabel("Summarise the key findings.")
        user_text.setStyleSheet(
            f"color: {theme.get('text_main', '#fff')}; font-size: 11px;"
            " background: transparent; border: none;"
        )
        user_layout.addWidget(user_who)
        user_layout.addWidget(user_text)
        chat_layout.addWidget(user_bubble)
        chat_layout.addStretch()
        self._preview_layout.addWidget(chat_area)

        # --- Swatch strip at bottom (accent, success, warning, error) ---
        swatch_bar = QFrame()
        swatch_bar.setFixedHeight(14)
        swatch_bar.setStyleSheet(
            f"background-color: {theme.get('bg_panel', '#2b2b2b')};"
            f" border-top: 1px solid {theme.get('border', '#555')};"
        )
        swatch_row = QHBoxLayout(swatch_bar)
        swatch_row.setContentsMargins(6, 0, 6, 0)
        swatch_row.setSpacing(4)
        for key in ("accent", "success", "warning", "error"):
            color = theme.get(key, "#888")
            dot_lbl = QLabel("■")
            dot_lbl.setStyleSheet(
                f"color: {color}; font-size: 9px; background: transparent; border: none;"
            )
            swatch_row.addWidget(dot_lbl)
        swatch_row.addStretch()
        self._preview_layout.addWidget(swatch_bar)

        is_user = self._is_user_theme(name)
        tier = "user theme" if is_user else ("built-in theme" if name in _BUILTIN_THEMES else "plugin theme")
        self._preview_label.setText(f"<b>{name}</b> — {tier}")

    # ------------------------------------------------------------------
    # CRUD actions
    # ------------------------------------------------------------------

    def _create_theme(self):
        name, ok = QInputDialog.getText(self, "New Theme", "Theme name:")
        if not ok or not name.strip():
            return
        name = name.strip()
        if self._tm.get_theme_by_name(name) is not None:
            QMessageBox.warning(self, "Duplicate Name", f"A theme named '{name}' already exists.")
            return
        base = dict(_BUILTIN_THEMES["Dark (Default)"])
        dialog = CustomThemeDialog(base, title=f"New Theme: {name}", parent=self)

        def _on_theme_created():
            try:
                self._tm.save_user_theme(name, dialog.get_colors())
                self._populate_list(select_name=name)
            except RuntimeError:
                pass

        dialog.accepted.connect(_on_theme_created)
        dm = get_for_widget(self)
        if dm:
            dm.show_instance(dialog)
        else:
            if exec_as_modal(dialog):
                _on_theme_created()

    def _duplicate_theme(self):
        name = self._selected_name()
        if not name:
            return
        source = self._tm.get_theme_by_name(name)
        if source is None:
            return
        new_name, ok = QInputDialog.getText(
            self, "Duplicate Theme", "New theme name:", text=f"{name} Copy"
        )
        if not ok or not new_name.strip():
            return
        new_name = new_name.strip()
        if self._tm.get_theme_by_name(new_name) is not None:
            QMessageBox.warning(self, "Duplicate Name", f"A theme named '{new_name}' already exists.")
            return
        self._tm.save_user_theme(new_name, dict(source))
        self._populate_list(select_name=new_name)

    def _rename_theme(self):
        name = self._selected_name()
        if not name or not self._is_user_theme(name):
            return
        new_name, ok = QInputDialog.getText(self, "Rename Theme", "New name:", text=name)
        if not ok or not new_name.strip():
            return
        new_name = new_name.strip()
        if new_name == name:
            return
        if self._tm.get_theme_by_name(new_name) is not None:
            QMessageBox.warning(self, "Duplicate Name", f"A theme named '{new_name}' already exists.")
            return
        self._tm.rename_user_theme(name, new_name)
        self._populate_list(select_name=new_name)

    def _delete_theme(self):
        name = self._selected_name()
        if not name or not self._is_user_theme(name):
            return
        reply = QMessageBox.question(
            self, "Delete Theme", f"Permanently delete '{name}'?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if reply == QMessageBox.StandardButton.Yes:
            self._tm.delete_user_theme(name)
            self._populate_list()

    def _edit_theme(self):
        name = self._selected_name()
        if not name or not self._is_user_theme(name):
            return
        current = self._tm.get_theme_by_name(name)
        if current is None:
            return
        dialog = CustomThemeDialog(current, title=f"Edit: {name}", parent=self)

        def _on_theme_edited():
            try:
                self._tm.save_user_theme(name, dialog.get_colors())
                if self._tm.current_theme_name == name:
                    self._tm.set_theme(name)
                self._on_selection_changed(self._list.currentRow())
            except RuntimeError:
                pass

        dialog.accepted.connect(_on_theme_edited)
        dm = get_for_widget(self)
        if dm:
            dm.show_instance(dialog)
        else:
            if exec_as_modal(dialog):
                _on_theme_edited()

    def _apply_theme(self):
        name = self._selected_name()
        if name:
            self._tm.set_theme(name)
            self._populate_list(select_name=name)

    # ------------------------------------------------------------------
    # Styling
    # ------------------------------------------------------------------

    def _apply_chrome(self):
        t = self._tm.get_theme()
        self.setStyleSheet(
            f"QDialog {{ background-color: {t['bg_main']}; color: {t['text_main']}; }}"
            f"QLabel {{ color: {t['text_main']}; }}"
            f"QListWidget {{ background-color: {t['bg_input']}; color: {t['text_main']};"
            f" border: 1px solid {t['border']}; }}"
            f"QListWidget::item:selected {{ background-color: {t['accent']}; color: #fff; }}"
        )
        for btn in self.findChildren(QPushButton):
            if btn.text() in ("Apply Theme", "Edit Colors"):
                btn.setStyleSheet(
                    f"background-color: {t['accent']}; color: #fff;"
                    " padding: 5px 12px; border-radius: 4px; border: none;"
                )
            elif btn.text() == "Delete":
                btn.setStyleSheet(
                    f"background-color: {t['error']}; color: #fff;"
                    " padding: 5px 12px; border-radius: 4px; border: none;"
                )
            else:
                btn.setStyleSheet(
                    f"background-color: {t['bg_panel']}; color: {t['text_main']};"
                    f" padding: 5px 12px; border-radius: 4px; border: 1px solid {t['border']};"
                )


# ---------------------------------------------------------------------------
# _ThemeManager
# ---------------------------------------------------------------------------

class _ThemeManager(QObject):
    """
    Singleton theme registry.

    Theme tiers (in lookup order):
      1. Built-in  (_BUILTIN_THEMES)   — read-only, always present
      2. User       (_user_themes)      — persisted in QSettings, full CRUD
      3. Plugin     (_plugin_themes)    — registered at runtime, never persisted

    Signals:
        theme_changed(dict)   — active theme switched; payload is the new theme dict
        themes_list_changed() — the set of available theme names changed (add/rename/delete)
    """

    theme_changed = Signal(dict)
    themes_list_changed = Signal()  # fired when themes are added, renamed, or deleted

    def __init__(self):
        super().__init__()
        self._user_themes: Dict[str, dict] = {}
        self._plugin_themes: Dict[str, dict] = {}
        self._plugin_theme_owners: Dict[str, str] = {}  # theme_name → plugin_id

        self._load_user_themes()

        saved_name = QSettings(_QSETTINGS_ORG, _QSETTINGS_APP).value(_ACTIVE_THEME_KEY, "Dark (Default)")
        self.current_theme_name: str = (
            saved_name if self.get_theme_by_name(saved_name) is not None else "Dark (Default)"
        )
        self.theme: dict = self.get_theme_by_name(self.current_theme_name) or dict(_BUILTIN_THEMES["Dark (Default)"])

        # Legacy compat — expose built-ins under self.themes for old callers
        self.themes = _BUILTIN_THEMES

    # ------------------------------------------------------------------
    # Persistence
    # ------------------------------------------------------------------

    def _load_user_themes(self):
        settings = QSettings(_QSETTINGS_ORG, _QSETTINGS_APP)

        # v2 format: flat dict {name: theme_dict}
        raw = settings.value(_USER_THEMES_KEY, None)
        if raw:
            try:
                data = json.loads(raw)
                if isinstance(data, dict):
                    self._user_themes = {
                        k: v for k, v in data.items()
                        if isinstance(k, str) and isinstance(v, dict)
                    }
                    return
            except (json.JSONDecodeError, TypeError):
                pass

        # v1 migration: single "custom_theme_json" key → migrate to "Custom"
        old_custom = settings.value("custom_theme_json", None)
        if old_custom:
            try:
                colors = json.loads(old_custom)
                if isinstance(colors, dict):
                    self._user_themes["Custom"] = colors
                    self._persist_user_themes()
            except (json.JSONDecodeError, TypeError):
                pass

    def _persist_user_themes(self):
        settings = QSettings(_QSETTINGS_ORG, _QSETTINGS_APP)
        settings.setValue(_USER_THEMES_KEY, json.dumps(self._user_themes))

    # ------------------------------------------------------------------
    # Public read API
    # ------------------------------------------------------------------

    @property
    def all_theme_names(self) -> List[str]:
        """All theme names in display order: built-ins, user, plugin."""
        names = list(_BUILTIN_THEMES.keys())
        names += sorted(k for k in self._user_themes if k not in _BUILTIN_THEMES)
        names += sorted(k for k in self._plugin_themes if k not in _BUILTIN_THEMES and k not in self._user_themes)
        return names

    def get_theme_by_name(self, name: str) -> Optional[dict]:
        """Return the theme dict for *name*, or None if unknown."""
        if name in _BUILTIN_THEMES:
            return dict(_BUILTIN_THEMES[name])
        if name in self._user_themes:
            return dict(self._user_themes[name])
        if name in self._plugin_themes:
            return dict(self._plugin_themes[name])
        return None

    def get_theme(self) -> dict:
        return dict(self.theme)

    def get_available_themes(self) -> List[str]:
        return self.all_theme_names

    # ------------------------------------------------------------------
    # Active theme
    # ------------------------------------------------------------------

    def set_theme(self, name: str) -> bool:
        """Switch to *name*. Returns True on success."""
        theme = self.get_theme_by_name(name)
        if theme is None:
            return False
        self.current_theme_name = name
        self.theme = theme
        settings = QSettings(_QSETTINGS_ORG, _QSETTINGS_APP)
        settings.setValue(_ACTIVE_THEME_KEY, name)
        app = QApplication.instance()
        if app:
            self._apply_global_style(app)
        self.theme_changed.emit(self.theme)
        EventBus.get_instance().theme_changed.emit("THEME_SWITCHED", self.theme)
        return True

    # ------------------------------------------------------------------
    # User theme CRUD
    # ------------------------------------------------------------------

    def save_user_theme(self, name: str, theme_dict: dict) -> None:
        """Create or update a user theme."""
        self._user_themes[name] = dict(theme_dict)
        self._persist_user_themes()
        self.themes_list_changed.emit()

    def delete_user_theme(self, name: str) -> None:
        if name not in self._user_themes:
            return
        self._user_themes.pop(name)
        self._persist_user_themes()
        self.themes_list_changed.emit()
        # Fall back to default if the active theme was deleted
        if self.current_theme_name == name:
            self.set_theme("Dark (Default)")

    def rename_user_theme(self, old_name: str, new_name: str) -> None:
        if old_name not in self._user_themes or not new_name.strip():
            return
        self._user_themes[new_name] = self._user_themes.pop(old_name)
        self._persist_user_themes()
        self.themes_list_changed.emit()
        if self.current_theme_name == old_name:
            self.current_theme_name = new_name
            settings = QSettings(_QSETTINGS_ORG, _QSETTINGS_APP)
            settings.setValue(_ACTIVE_THEME_KEY, new_name)

    # ------------------------------------------------------------------
    # Plugin theme API
    # ------------------------------------------------------------------

    def register_plugin_theme(self, name: str, theme_dict: dict, plugin_id: str = "") -> None:
        """Register a theme contributed by a plugin. Not persisted."""
        self._plugin_themes[name] = dict(theme_dict)
        if plugin_id:
            self._plugin_theme_owners[name] = plugin_id
        self.themes_list_changed.emit()

    def unregister_plugin_themes(self, plugin_id: str) -> None:
        """Remove all themes registered by *plugin_id*."""
        to_remove = [n for n, pid in self._plugin_theme_owners.items() if pid == plugin_id]
        for name in to_remove:
            self._plugin_themes.pop(name, None)
            self._plugin_theme_owners.pop(name, None)
        if to_remove:
            self.themes_list_changed.emit()
        if self.current_theme_name in to_remove:
            self.set_theme("Dark (Default)")

    # ------------------------------------------------------------------
    # Import / Export (structure — implementations can be added later)
    # ------------------------------------------------------------------

    def export_theme(self, name: str) -> Optional[dict]:
        """Return a serializable dict suitable for writing to a .papyrus-theme file."""
        theme = self.get_theme_by_name(name)
        if theme is None:
            return None
        return {"name": name, "version": 1, "colors": theme}

    def import_theme(self, data: dict) -> Optional[str]:
        """
        Import a theme from a .papyrus-theme dict.
        Returns the imported theme name, or None on error.
        """
        try:
            name = str(data.get("name", "Imported Theme")).strip() or "Imported Theme"
            colors = data.get("colors", {})
            if not isinstance(colors, dict) or not colors:
                return None
            # Avoid collision
            base = name
            counter = 1
            while self.get_theme_by_name(name) is not None:
                name = f"{base} ({counter})"
                counter += 1
            self.save_user_theme(name, colors)
            return name
        except Exception:
            return None

    # ------------------------------------------------------------------
    # Dialogs
    # ------------------------------------------------------------------

    def open_theme_manager(self, parent=None) -> None:
        """Open the full Theme Manager dialog."""
        dialog = ThemeManagerDialog(self, parent=parent)
        dm = get_for_widget(parent)
        if dm is not None:
            dm.show_instance(dialog, key="theme_manager")
        else:
            exec_as_modal(dialog)

    def edit_custom_theme(self, parent_widget=None) -> None:
        """Legacy entry point — opens the full Theme Manager."""
        self.open_theme_manager(parent=parent_widget)

    # ------------------------------------------------------------------
    # Global stylesheet
    # ------------------------------------------------------------------

    def apply_global_style(self, app) -> None:
        self._apply_global_style(app)

    def _apply_global_style(self, app) -> None:
        t = self.theme
        style = f"""
            QMainWindow {{ background-color: {t['bg_main']}; color: {t['text_main']}; }}
            QWidget {{ color: {t['text_main']}; font-family: Arial; }}
            QPushButton {{
                background-color: {t['bg_input']}; border-radius: 4px; padding: 6px 12px;
                font-weight: bold; border: 1px solid {t['border']}; color: {t['text_main']};
            }}
            QPushButton:hover {{ background-color: {t['bg_panel']}; }}
            QPushButton:checked {{
                background-color: {t['accent']}; border: 1px solid {t['accent_hover']};
                color: #ffffff;
            }}
            QComboBox {{
                background-color: {t['bg_input']}; border: 1px solid {t['border']};
                padding: 4px; border-radius: 4px; color: {t['text_main']};
            }}
            QComboBox QAbstractItemView {{
                background-color: {t['bg_panel']}; color: {t['text_main']};
                selection-background-color: {t['accent']}; border: 1px solid {t['border']};
            }}
            QMenu {{
                background-color: {t['bg_panel']}; color: {t['text_main']};
                border: 1px solid {t['border']};
            }}
            QMenu::item:selected {{ background-color: {t['accent']}; color: #ffffff; }}
            QMessageBox {{ background-color: {t['bg_main']}; color: {t['text_main']}; }}
            QMessageBox QLabel {{ color: {t['text_main']}; }}
            QMessageBox QPushButton {{
                background-color: {t['bg_panel']}; color: {t['text_main']};
                padding: 5px 15px; border-radius: 4px; border: 1px solid {t['border']};
            }}
            QMessageBox QPushButton:hover {{ background-color: {t['bg_input']}; }}
            QInputDialog {{ background-color: {t['bg_main']}; color: {t['text_main']}; }}
            QInputDialog QLineEdit {{
                background-color: {t['bg_input']}; color: {t['text_main']};
                border: 1px solid {t['border']}; padding: 4px;
            }}
            QTextEdit, QLineEdit {{
                background-color: {t['bg_input']}; color: {t['text_main']};
                border: 1px solid {t['border']};
            }}
            QListWidget {{
                background-color: {t['bg_input']}; color: {t['text_main']};
                border: 1px solid {t['border']};
            }}
            QScrollArea {{ border: none; background-color: transparent; }}
            QStackedWidget {{
                background-color: {t['bg_main']}; border-left: 1px solid {t['border']};
            }}
            QTabBar::tab {{
                background: {t['bg_panel']}; padding: 8px 20px;
                border: 1px solid {t['border']}; color: {t['text_main']};
            }}
            QTabBar::tab:selected {{
                background: {t['bg_input']}; font-weight: bold;
                border-bottom: 2px solid {t['accent']};
            }}
            QTabWidget::pane {{
                border: 1px solid {t['border']}; background: {t['bg_main']};
            }}
        """
        app.setStyleSheet(style)


# ---------------------------------------------------------------------------
# STRICT SINGLETON
# ---------------------------------------------------------------------------

_global_theme_manager: Optional["_ThemeManager"] = None


def ThemeManager() -> "_ThemeManager":
    """Factory returning the singleton _ThemeManager instance."""
    global _global_theme_manager
    if _global_theme_manager is None:
        _global_theme_manager = _ThemeManager()
    return _global_theme_manager
