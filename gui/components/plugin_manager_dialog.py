"""
gui/components/plugin_manager_dialog.py

Plugin Manager dialog — list all discovered plugins with enable/disable toggles.
"""
from __future__ import annotations

from typing import Dict, List, Optional

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
)


class PluginManagerDialog(QDialog):
    """
    Shows all plugins found in the plugins/ directory with their enabled/disabled
    state and toggle buttons.  Changes take effect immediately (live enable/disable).
    """

    def __init__(self, core, parent=None):
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        self.setWindowTitle("Plugin Manager")
        self.resize(680, 420)
        self._core = core
        self._rows: List[Dict] = []
        self._build_ui()
        self._populate()

    # ------------------------------------------------------------------
    # UI
    # ------------------------------------------------------------------

    def _build_ui(self):
        layout = QVBoxLayout(self)

        self._info_lbl = QLabel(
            "Plugins marked as enabled are loaded immediately. "
            "Disabled plugins are unloaded now and skipped on next startup."
        )
        self._info_lbl.setWordWrap(True)
        layout.addWidget(self._info_lbl)

        self._table = QTableWidget(0, 4)
        self._table.setHorizontalHeaderLabels(["Plugin", "Version", "Status", "Action"])
        self._table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self._table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        self._table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        self._table.horizontalHeader().setSectionResizeMode(3, QHeaderView.ResizeMode.ResizeToContents)
        self._table.setSelectionBehavior(self._table.SelectionBehavior.SelectRows)
        self._table.setEditTriggers(self._table.EditTrigger.NoEditTriggers)
        self._table.verticalHeader().setVisible(False)
        layout.addWidget(self._table)

        self._status_lbl = QLabel("")
        layout.addWidget(self._status_lbl)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _populate(self):
        from core.plugins.plugin_loader import scan_plugin_dirs
        from core.plugins.plugin_enable_registry import PluginEnableRegistry

        self._table.setRowCount(0)
        self._rows = []

        enable_reg = PluginEnableRegistry.get_instance()
        disabled_ids = enable_reg.disabled_ids()
        loaded_ids = {
            p.plugin_id for p, _ in getattr(self._core, "_loaded_plugins", [])
        }

        all_plugins = scan_plugin_dirs()
        for meta in all_plugins:
            pid = meta["plugin_id"]
            is_loaded = pid in loaded_ids
            is_disabled = pid in disabled_ids

            row = self._table.rowCount()
            self._table.insertRow(row)

            name_item = QTableWidgetItem(meta["name"])
            name_item.setData(Qt.ItemDataRole.UserRole, pid)
            if getattr(meta, "get", lambda k, d=None: None)("requires_internet"):
                name_item.setText(f"{meta['name']} [internet]")
            self._table.setItem(row, 0, name_item)
            self._table.setItem(row, 1, QTableWidgetItem(meta["version"]))

            status_text = "✅ Enabled" if is_loaded else ("⏸ Disabled" if is_disabled else "⚠ Not loaded")
            status_item = QTableWidgetItem(status_text)
            self._table.setItem(row, 2, status_item)

            btn = QPushButton("Disable" if is_loaded else "Enable")
            btn.setProperty("plugin_id", pid)
            btn.setProperty("is_loaded", is_loaded)
            btn.clicked.connect(lambda checked=False, b=btn: self._toggle_plugin(b))
            self._table.setCellWidget(row, 3, btn)

            self._rows.append({"pid": pid, "loaded": is_loaded, "row": row})

    def _toggle_plugin(self, btn: QPushButton):
        pid = btn.property("plugin_id")
        is_loaded = btn.property("is_loaded")
        if not pid or self._core is None:
            return

        if is_loaded:
            self._disable(pid, btn)
        else:
            self._enable(pid, btn)

    def _disable(self, plugin_id: str, btn: QPushButton):
        from core.plugins.plugin_loader import disable_plugin
        try:
            disable_plugin(self._core, plugin_id)
            self._update_row(plugin_id, loaded=False)
            self._status_lbl.setText(f"✓ '{plugin_id}' disabled.")
        except Exception as exc:
            self._status_lbl.setText(f"⚠ Could not disable '{plugin_id}': {exc}")

    def _enable(self, plugin_id: str, btn: QPushButton):
        from core.plugins.plugin_loader import enable_plugin
        try:
            ok = enable_plugin(self._core, plugin_id)
            if ok:
                self._update_row(plugin_id, loaded=True)
                self._status_lbl.setText(f"✓ '{plugin_id}' enabled.")
            else:
                self._status_lbl.setText(f"⚠ Could not enable '{plugin_id}' — check the console.")
        except Exception as exc:
            self._status_lbl.setText(f"⚠ Could not enable '{plugin_id}': {exc}")

    def _update_row(self, plugin_id: str, loaded: bool):
        for r in range(self._table.rowCount()):
            item = self._table.item(r, 0)
            if item and item.data(Qt.ItemDataRole.UserRole) == plugin_id:
                self._table.item(r, 2).setText("✅ Enabled" if loaded else "⏸ Disabled")
                btn = self._table.cellWidget(r, 3)
                if isinstance(btn, QPushButton):
                    btn.setText("Disable" if loaded else "Enable")
                    btn.setProperty("is_loaded", loaded)
                break

    # ------------------------------------------------------------------
    # Theme
    # ------------------------------------------------------------------

    def update_theme(self, theme: dict):
        bg = theme.get("bg_panel", theme.get("bg_main", "#1e1e1e"))
        input_bg = theme.get("bg_input", "#2b2b2b")
        text = theme.get("text_main", "#ffffff")
        muted = theme.get("text_muted", "#aaaaaa")
        border = theme.get("border", "#444444")
        accent = theme.get("accent", "#4a8cff")
        self.setStyleSheet(f"""
            QDialog {{ background-color: {bg}; color: {text}; }}
            QLabel {{ color: {muted}; }}
            QTableWidget {{
                background-color: {input_bg};
                color: {text};
                border: 1px solid {border};
                gridline-color: {border};
            }}
            QHeaderView::section {{
                background-color: {bg};
                color: {text};
                border: 1px solid {border};
                padding: 4px;
            }}
            QPushButton {{
                background-color: {input_bg};
                color: {text};
                border: 1px solid {border};
                border-radius: 4px;
                padding: 4px 10px;
            }}
            QPushButton:hover {{ background-color: {accent}; color: #fff; }}
        """)
