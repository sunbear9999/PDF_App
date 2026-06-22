"""
gui/components/dialogs/settings_dialog.py

Global Settings dialog.  Currently contains one tab: Shortcuts.
Add future tabs (Appearance, AI Defaults, etc.) by appending to self.tabs.

Opens via main_window._open_settings() through the DialogManager (singleton,
modeless).  Reads from and writes to AppContext.keybinding_registry.
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Dict, Optional

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QColor, QKeySequence
from PySide6.QtWidgets import (
    QAbstractItemView,
    QDialog,
    QDialogButtonBox,
    QFrame,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QSizePolicy,
    QSplitter,
    QTabWidget,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from gui.base import BaseDialog

if TYPE_CHECKING:
    from gui.app_context import AppContext
    from gui.managers.keybinding_registry import KeybindingRegistry, KeySpec


def _qt_enum_int(value) -> int:
    """Return the integer payload for PySide/PyQt enum and flag values."""
    raw_value = getattr(value, "value", value)
    return int(raw_value)


def _key_sequence_from_event(event) -> str:
    """
    Convert a Qt key event into the registry's portable key string format.

    PySide6 returns enum/flag objects for modifiers on newer versions, so
    relying on int(event.modifiers()) raises TypeError. Prefer Qt's native
    keyCombination() when available, and fall back to unwrapping enum values.
    """
    if hasattr(event, "keyCombination"):
        key_combination = event.keyCombination()
        try:
            sequence = QKeySequence(key_combination)
        except TypeError:
            sequence = QKeySequence(key_combination.toCombined())
    else:
        key_combo = _qt_enum_int(event.modifiers()) | _qt_enum_int(event.key())
        sequence = QKeySequence(key_combo)
    return sequence.toString(QKeySequence.SequenceFormat.PortableText)


# ──────────────────────────────────────────────────────────────────────────────
# KeyCaptureWidget
# ──────────────────────────────────────────────────────────────────────────────

class KeyCaptureWidget(QLineEdit):
    """
    A read-only QLineEdit that captures the next key sequence pressed while
    it has focus and emits sequence_captured(str).

    Click to activate capture; press Escape to cancel.
    """
    sequence_captured = Signal(str)

    _MODIFIER_ONLY = {
        Qt.Key.Key_Control, Qt.Key.Key_Shift, Qt.Key.Key_Alt,
        Qt.Key.Key_Meta, Qt.Key.Key_Super_L, Qt.Key.Key_Super_R,
        Qt.Key.Key_AltGr,
    }

    def __init__(self, current_key: str = "", theme: Optional[dict] = None, parent=None) -> None:
        super().__init__(parent)
        self._theme = theme or {}
        self._current_key = current_key
        self._capturing = False
        self.setReadOnly(True)
        self.setPlaceholderText("None")
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setText(current_key)
        self._apply_idle_style()

    # ------------------------------------------------------------------
    # Public interface
    # ------------------------------------------------------------------

    def get_key(self) -> str:
        return self._current_key

    def set_key(self, key_str: str) -> None:
        self._current_key = key_str
        self.setText(key_str)
        self._apply_idle_style()

    def mark_conflict(self, has_conflict: bool) -> None:
        error = self._theme.get("error", "#ff4444")
        border = self._theme.get("border", "#555")
        bg = self._theme.get("bg_input", "#3a3a3a")
        if has_conflict:
            self.setStyleSheet(
                f"QLineEdit {{ background: {bg}; border: 2px solid {error}; "
                f"border-radius: 4px; color: {error}; padding: 3px 6px; }}"
            )
        else:
            self._apply_idle_style()

    # ------------------------------------------------------------------
    # Qt event overrides
    # ------------------------------------------------------------------

    def mousePressEvent(self, event) -> None:
        self._start_capture()

    def keyPressEvent(self, event) -> None:
        if not self._capturing:
            super().keyPressEvent(event)
            return

        if event.key() == Qt.Key.Key_Escape:
            self._cancel_capture()
            event.accept()
            return

        if event.key() in self._MODIFIER_ONLY:
            event.accept()
            return

        key_str = _key_sequence_from_event(event)

        if key_str:
            self._current_key = key_str
            self.setText(key_str)
            self._end_capture()
            self.sequence_captured.emit(key_str)
        event.accept()

    def focusOutEvent(self, event) -> None:
        if self._capturing:
            self._cancel_capture()
        super().focusOutEvent(event)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _start_capture(self) -> None:
        self._capturing = True
        accent = self._theme.get("accent", "#b366ff")
        bg = self._theme.get("bg_input", "#3a3a3a")
        self.setStyleSheet(
            f"QLineEdit {{ background: {bg}; border: 2px solid {accent}; "
            f"border-radius: 4px; color: {accent}; padding: 3px 6px; }}"
        )
        self.setPlaceholderText("Press keys…")
        self.setText("")
        self.setFocus()

    def _end_capture(self) -> None:
        self._capturing = False
        self.setPlaceholderText("None")
        self._apply_idle_style()

    def _cancel_capture(self) -> None:
        self._capturing = False
        self.setText(self._current_key)
        self.setPlaceholderText("None")
        self._apply_idle_style()

    def _apply_idle_style(self) -> None:
        bg = self._theme.get("bg_input", "#3a3a3a")
        fg = self._theme.get("text_main", "#fff")
        border = self._theme.get("border", "#555")
        self.setStyleSheet(
            f"QLineEdit {{ background: {bg}; border: 1px solid {border}; "
            f"border-radius: 4px; color: {fg}; padding: 3px 6px; }}"
        )


# ──────────────────────────────────────────────────────────────────────────────
# ShortcutsTab
# ──────────────────────────────────────────────────────────────────────────────

class ShortcutsTab(QWidget):
    """
    Full shortcuts editor: scope list on the left, shortcut table on the right.

    Pending changes are collected in-memory and applied only when the parent
    dialog's Apply / OK button calls apply_changes().
    """

    def __init__(
        self,
        keybinding_registry: "KeybindingRegistry",
        theme: dict,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self._kr = keybinding_registry
        self._theme = theme
        self._pending: Dict[str, str] = {}  # action_id → new key (not yet saved)
        self._current_scope: Optional[str] = None
        self._capture_widgets: Dict[str, KeyCaptureWidget] = {}  # action_id → widget

        self._build_ui()
        self._populate_scopes()

    # ------------------------------------------------------------------
    # Public API (called by parent dialog)
    # ------------------------------------------------------------------

    def apply_changes(self) -> None:
        """Persist all pending keybinding changes."""
        for action_id, key_str in list(self._pending.items()):
            if key_str:
                self._kr.set_override(action_id, key_str)
            else:
                self._kr.clear_override(action_id)
        self._pending.clear()
        self._status_lbl.setText("")

    def has_pending(self) -> bool:
        return bool(self._pending)

    # ------------------------------------------------------------------
    # UI construction
    # ------------------------------------------------------------------

    def _build_ui(self) -> None:
        t = self._theme
        bg = t.get("bg_main", "#1e1e1e")
        bg_panel = t.get("bg_panel", "#2b2b2b")
        bg_input = t.get("bg_input", "#3a3a3a")
        fg = t.get("text_main", "#fff")
        fg_muted = t.get("text_muted", "#999")
        border = t.get("border", "#555")
        accent = t.get("accent", "#b366ff")

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        # ── search bar ──────────────────────────────────────────────
        search_bar = QFrame()
        search_bar.setStyleSheet(
            f"QFrame {{ background: {bg_panel}; border-bottom: 1px solid {border}; }}"
        )
        sb_layout = QHBoxLayout(search_bar)
        sb_layout.setContentsMargins(10, 6, 10, 6)

        self._search_edit = QLineEdit()
        self._search_edit.setPlaceholderText("Filter shortcuts…")
        self._search_edit.setStyleSheet(
            f"QLineEdit {{ background: {bg_input}; border: 1px solid {border}; "
            f"border-radius: 4px; color: {fg}; padding: 4px 8px; }}"
        )
        self._search_edit.textChanged.connect(self._on_search_changed)
        sb_layout.addWidget(self._search_edit)

        btn_reset_all = QPushButton("↺ Reset All to Defaults")
        btn_reset_all.setStyleSheet(
            f"QPushButton {{ background: transparent; border: 1px solid {border}; "
            f"border-radius: 4px; color: {fg_muted}; padding: 4px 10px; }}"
            f"QPushButton:hover {{ background: {bg_input}; color: {fg}; }}"
        )
        btn_reset_all.clicked.connect(self._on_reset_all)
        sb_layout.addWidget(btn_reset_all)
        outer.addWidget(search_bar)

        # ── scope list + table splitter ──────────────────────────────
        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.setStyleSheet(
            f"QSplitter::handle {{ background: {border}; width: 1px; }}"
        )

        # Left: scope list
        self._scope_list = QListWidget()
        self._scope_list.setFixedWidth(180)
        self._scope_list.setStyleSheet(
            f"QListWidget {{ background: {bg_panel}; border: none; color: {fg}; "
            f"font-size: 13px; outline: none; }}"
            f"QListWidget::item {{ padding: 8px 12px; border-radius: 4px; }}"
            f"QListWidget::item:selected {{ background: {accent}; color: white; }}"
            f"QListWidget::item:hover:!selected {{ background: {bg_input}; }}"
        )
        self._scope_list.currentRowChanged.connect(self._on_scope_selected)
        splitter.addWidget(self._scope_list)

        # Right: table + status
        right_widget = QWidget()
        right_widget.setStyleSheet(f"background: {bg};")
        right_layout = QVBoxLayout(right_widget)
        right_layout.setContentsMargins(0, 0, 0, 0)
        right_layout.setSpacing(0)

        # Scope title
        self._scope_title = QLabel()
        self._scope_title.setStyleSheet(
            f"color: {fg_muted}; font-size: 11px; padding: 8px 12px 4px 12px; "
            f"background: {bg}; font-weight: bold; letter-spacing: 1px; "
            f"text-transform: uppercase;"
        )
        right_layout.addWidget(self._scope_title)

        # Shortcut table
        self._table = QTableWidget()
        self._table.setColumnCount(4)
        self._table.setHorizontalHeaderLabels(["Action", "Description", "Shortcut", "Default"])
        self._table.setShowGrid(False)
        self._table.setAlternatingRowColors(False)
        self._table.setSelectionMode(QAbstractItemView.SelectionMode.NoSelection)
        self._table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self._table.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self._table.verticalHeader().hide()
        hdr = self._table.horizontalHeader()
        hdr.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        hdr.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        hdr.setSectionResizeMode(2, QHeaderView.ResizeMode.Fixed)
        hdr.setSectionResizeMode(3, QHeaderView.ResizeMode.Fixed)
        self._table.setColumnWidth(2, 160)
        self._table.setColumnWidth(3, 120)
        self._table.setStyleSheet(
            f"QTableWidget {{ background: {bg}; color: {fg}; border: none; "
            f"gridline-color: transparent; outline: none; }}"
            f"QTableWidget::item {{ padding: 4px 8px; border-bottom: 1px solid {border}; }}"
            f"QHeaderView::section {{ background: {bg_panel}; color: {fg_muted}; "
            f"border: none; border-bottom: 1px solid {border}; padding: 6px 8px; "
            f"font-size: 11px; font-weight: bold; letter-spacing: 1px; }}"
        )
        self._table.setVerticalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
        right_layout.addWidget(self._table)

        # Status bar
        self._status_lbl = QLabel("")
        self._status_lbl.setStyleSheet(
            f"color: {t.get('warning', '#ffaa00')}; font-size: 11px; "
            f"padding: 4px 12px; background: {bg_panel}; "
            f"border-top: 1px solid {border};"
        )
        self._status_lbl.hide()
        right_layout.addWidget(self._status_lbl)

        splitter.addWidget(right_widget)
        splitter.setSizes([180, 600])
        outer.addWidget(splitter)

    def _populate_scopes(self) -> None:
        self._scope_list.blockSignals(True)
        self._scope_list.clear()
        scopes = self._kr.get_scopes()
        for scope, label in scopes:
            from gui.managers.keybinding_registry import SCOPE_ICONS
            icon = SCOPE_ICONS.get(scope, "🔧")
            item = QListWidgetItem(f"{icon}  {label}")
            item.setData(Qt.ItemDataRole.UserRole, scope)
            self._scope_list.addItem(item)
        self._scope_list.blockSignals(False)
        if self._scope_list.count() > 0:
            self._scope_list.setCurrentRow(0)

    def _populate_table(self, scope: str) -> None:
        from gui.managers.keybinding_registry import SCOPE_LABELS, SCOPE_ICONS
        self._capture_widgets.clear()
        specs = self._kr.get_specs_for_scope(scope)
        icon = SCOPE_ICONS.get(scope, "🔧")
        label = SCOPE_LABELS.get(scope, scope.replace("_", " ").title())
        self._scope_title.setText(f"{icon}  {label.upper()}")

        search = self._search_edit.text().strip().lower()

        self._table.setRowCount(0)
        t = self._theme
        fg = t.get("text_main", "#fff")
        fg_muted = t.get("text_muted", "#999")
        border = t.get("border", "#555")
        accent = t.get("accent", "#b366ff")

        for spec in specs:
            if search and search not in spec.label.lower() and search not in spec.description.lower():
                continue

            row = self._table.rowCount()
            self._table.insertRow(row)
            self._table.setRowHeight(row, 38)

            # Col 0: Action label
            lbl_item = QTableWidgetItem(spec.label)
            lbl_item.setForeground(QColor(fg))
            lbl_item.setData(Qt.ItemDataRole.UserRole, spec.action_id)
            lbl_item.setFlags(Qt.ItemFlag.ItemIsEnabled)
            self._table.setItem(row, 0, lbl_item)

            # Col 1: Description
            desc_item = QTableWidgetItem(spec.description)
            desc_item.setForeground(QColor(fg_muted))
            desc_item.setFlags(Qt.ItemFlag.ItemIsEnabled)
            self._table.setItem(row, 1, desc_item)

            # Col 2: Key capture widget
            pending_key = self._pending.get(spec.action_id)
            current_key = pending_key if pending_key is not None else self._kr.get_effective_key(spec.action_id)
            capture = KeyCaptureWidget(current_key, theme=self._theme)
            capture.sequence_captured.connect(
                lambda key, aid=spec.action_id, cap=capture: self._on_key_captured(aid, key, cap)
            )
            if pending_key is not None and pending_key != spec.default_key:
                # Show pending override indicator
                pass
            self._capture_widgets[spec.action_id] = capture
            self._table.setCellWidget(row, 2, capture)

            # Col 3: Default + reset button
            default_widget = QWidget()
            default_layout = QHBoxLayout(default_widget)
            default_layout.setContentsMargins(4, 2, 4, 2)
            default_layout.setSpacing(4)

            default_lbl = QLabel(spec.default_key or "—")
            default_lbl.setStyleSheet(f"color: {fg_muted}; font-size: 11px;")
            default_layout.addWidget(default_lbl)

            has_override = self._kr.has_override(spec.action_id) or spec.action_id in self._pending
            if has_override:
                btn_reset = QPushButton("↺")
                btn_reset.setToolTip(f"Reset to default: {spec.default_key or 'None'}")
                btn_reset.setFixedSize(22, 22)
                btn_reset.setStyleSheet(
                    f"QPushButton {{ background: transparent; border: 1px solid {border}; "
                    f"border-radius: 4px; color: {fg_muted}; font-size: 11px; }}"
                    f"QPushButton:hover {{ color: {accent}; border-color: {accent}; }}"
                )
                btn_reset.clicked.connect(
                    lambda checked=False, aid=spec.action_id: self._on_reset_action(aid)
                )
                default_layout.addWidget(btn_reset)

            default_layout.addStretch()
            self._table.setCellWidget(row, 3, default_widget)

    # ------------------------------------------------------------------
    # Event handlers
    # ------------------------------------------------------------------

    def _on_scope_selected(self, row: int) -> None:
        item = self._scope_list.item(row)
        if not item:
            return
        scope = item.data(Qt.ItemDataRole.UserRole)
        self._current_scope = scope
        self._populate_table(scope)

    def _on_search_changed(self, text: str) -> None:
        if self._current_scope:
            self._populate_table(self._current_scope)

    def _on_key_captured(self, action_id: str, key_str: str, capture: KeyCaptureWidget) -> None:
        spec = self._kr._specs.get(action_id)
        scope = spec.scope if spec else ""

        # Check for conflicts in same scope first, then globally
        conflicts = self._kr.find_conflicts(key_str, exclude_id=action_id, scope=scope)
        if not conflicts:
            conflicts = self._kr.find_conflicts(key_str, exclude_id=action_id)

        # Also check pending changes
        for aid, k in self._pending.items():
            if aid != action_id and k == key_str:
                c_spec = self._kr._specs.get(aid)
                if c_spec and c_spec not in conflicts:
                    conflicts.append(c_spec)

        if conflicts:
            names = ", ".join(f"'{c.label}'" for c in conflicts[:2])
            self._status_lbl.setText(
                f"⚠️  '{key_str}' is already used by: {names}. "
                "Click Apply/OK to override, or press Escape to cancel."
            )
            self._status_lbl.show()
            capture.mark_conflict(True)
        else:
            self._status_lbl.setText("")
            self._status_lbl.hide()
            capture.mark_conflict(False)

        self._pending[action_id] = key_str

    def _on_reset_action(self, action_id: str) -> None:
        self._kr.clear_override(action_id)
        self._pending.pop(action_id, None)
        if self._current_scope:
            self._populate_table(self._current_scope)
        self._status_lbl.setText("")
        self._status_lbl.hide()

    def _on_reset_all(self) -> None:
        reply = QMessageBox.question(
            self,
            "Reset All Shortcuts",
            "Reset every shortcut to its default? This cannot be undone.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return
        self._kr.reset_all()
        self._pending.clear()
        self._status_lbl.setText("")
        self._status_lbl.hide()
        if self._current_scope:
            self._populate_table(self._current_scope)


# ──────────────────────────────────────────────────────────────────────────────
# GlobalSettingsDialog
# ──────────────────────────────────────────────────────────────────────────────

class GlobalSettingsDialog(BaseDialog):
    """
    Application-wide settings dialog, opened via the main toolbar Settings button.

    Structure
    ---------
    QTabWidget
      └─ "⌨️ Shortcuts"  →  ShortcutsTab

    To add a future tab: self.tabs.addTab(MyNewTab(...), "Tab Label")
    """

    def __init__(
        self,
        app_context: "AppContext",
        parent=None,
    ) -> None:
        kr = getattr(app_context, "keybinding_registry", None)
        theme = {}
        tm = getattr(app_context, "theme_manager", None)
        if tm:
            theme = tm.get_theme()

        super().__init__(
            title="Settings",
            theme=theme,
            parent=parent,
            modal=False,
            delete_on_close=True,
        )
        self._app_context = app_context
        self._kr = kr
        self._theme = theme

        self.setMinimumSize(820, 560)
        self.resize(960, 640)

        self._build_ui()
        self._apply_theme()

    # ------------------------------------------------------------------
    # UI
    # ------------------------------------------------------------------

    def _build_ui(self) -> None:
        t = self._theme
        bg = t.get("bg_main", "#1e1e1e")
        bg_panel = t.get("bg_panel", "#2b2b2b")
        fg = t.get("text_main", "#fff")
        border = t.get("border", "#555")
        accent = t.get("accent", "#b366ff")
        fg_muted = t.get("text_muted", "#999")

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        # Tab widget
        self.tabs = QTabWidget()
        self.tabs.setStyleSheet(
            f"QTabWidget::pane {{ border: none; background: {bg}; }}"
            f"QTabBar::tab {{ background: {bg_panel}; color: {fg_muted}; "
            f"padding: 8px 20px; border: none; border-right: 1px solid {border}; "
            f"font-size: 13px; }}"
            f"QTabBar::tab:selected {{ background: {bg}; color: {fg}; "
            f"border-bottom: 2px solid {accent}; }}"
            f"QTabBar::tab:hover:!selected {{ background: {bg}; color: {fg}; }}"
        )

        if self._kr:
            self._shortcuts_tab = ShortcutsTab(self._kr, t)
        else:
            self._shortcuts_tab = _NoRegistryPlaceholder()

        from gui.components.dialogs.ai_setup_tab import AISetupSettingsTab
        self._ai_setup_tab = AISetupSettingsTab(self._app_context, t, parent=self)
        self.tabs.addTab(self._ai_setup_tab, "🤖  AI Setup")

        # The local web companion is isolated under web/; this is its only GUI hook.
        from web.settings_tab import WebAppSettingsTab
        self._web_app_tab = WebAppSettingsTab(self._app_context, t, parent=self)
        self.tabs.addTab(self._web_app_tab, "📱  Web App")

        self.tabs.addTab(self._shortcuts_tab, "⌨️  Shortcuts")

        from gui.components.dialogs.import_export_tab import ImportExportTab
        self._import_export_tab = ImportExportTab(self._app_context, theme=t, parent=self)
        self.tabs.addTab(self._import_export_tab, "📦  Import / Export")

        outer.addWidget(self.tabs, 1)

        # Button bar
        btn_bar = QFrame()
        btn_bar.setStyleSheet(
            f"QFrame {{ background: {bg_panel}; border-top: 1px solid {border}; }}"
        )
        btn_layout = QHBoxLayout(btn_bar)
        btn_layout.setContentsMargins(12, 8, 12, 8)
        btn_layout.setSpacing(8)
        btn_layout.addStretch()

        self._btn_apply = QPushButton("Apply")
        self._btn_ok = QPushButton("OK")
        self._btn_cancel = QPushButton("Cancel")

        for btn, is_accent in [
            (self._btn_cancel, False),
            (self._btn_apply, False),
            (self._btn_ok, True),
        ]:
            if is_accent:
                btn.setStyleSheet(
                    f"QPushButton {{ background: {accent}; color: white; border: none; "
                    f"border-radius: 4px; padding: 6px 18px; font-weight: bold; }}"
                    f"QPushButton:hover {{ background: {t.get('accent_hover', accent)}; }}"
                )
            else:
                btn.setStyleSheet(
                    f"QPushButton {{ background: transparent; color: {fg}; "
                    f"border: 1px solid {border}; border-radius: 4px; padding: 6px 18px; }}"
                    f"QPushButton:hover {{ background: {t.get('bg_input', '#3a3a3a')}; }}"
                )
            btn.setFixedHeight(32)
            btn_layout.addWidget(btn)

        self._btn_apply.clicked.connect(self._on_apply)
        self._btn_ok.clicked.connect(self._on_ok)
        self._btn_cancel.clicked.connect(self.reject)
        outer.addWidget(btn_bar)

    def _apply_theme(self) -> None:
        t = self._theme
        self.setStyleSheet(
            f"QDialog {{ background: {t.get('bg_main', '#1e1e1e')}; "
            f"color: {t.get('text_main', '#fff')}; }}"
        )

    # ------------------------------------------------------------------
    # Actions
    # ------------------------------------------------------------------

    def _on_apply(self) -> None:
        tab = self.tabs.currentWidget()
        if hasattr(tab, "apply_changes"):
            tab.apply_changes()

    def _on_ok(self) -> None:
        self._on_apply()
        self.accept()


# ──────────────────────────────────────────────────────────────────────────────
# Placeholder when KeybindingRegistry is unavailable
# ──────────────────────────────────────────────────────────────────────────────

class _NoRegistryPlaceholder(QWidget):
    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        lbl = QLabel("Keybinding registry not available.")
        lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lbl.setStyleSheet("color: #888;")
        layout = QVBoxLayout(self)
        layout.addWidget(lbl)
