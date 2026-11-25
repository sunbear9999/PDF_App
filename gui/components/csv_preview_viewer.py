"""
gui/components/csv_preview_viewer.py

Read-only CSV/TSV/XLSX preview shown in SourceViewerHost, with an "Open in
Data Dock" button that promotes the same parsed DataGridState into the
editable Data Dock. Reuses SpreadsheetModel (gui/docks/data_dock.py) in a
read-only QTableView rather than building a new grid widget, and loads via
the same DataSourceLoaderSpec the Data Dock itself uses — both surfaces
show identical parsed data.
"""
from __future__ import annotations

from PySide6.QtGui import QColor
from PySide6.QtWidgets import QHBoxLayout, QLabel, QPushButton, QTableView, QVBoxLayout, QWidget

from core.events.domains.data_dock_events import DataDockIntent, DataDockPayload
from core.events.domains.document_events import SourceEvent
from core.events.domains.gui_events import GuiEvent
from core.events.event_bus import EventBus
from gui.docks.data_dock import SpreadsheetModel


class CsvPreviewViewer(QWidget):
    """Read-only data-file preview for one registered source_type (csv/tsv/xlsx)."""

    def __init__(self, app_context=None, parent=None, source_type: str = ""):
        super().__init__(parent)
        self._ctx = app_context
        self._source_type = source_type
        self.bus = EventBus.get_instance()
        self.current_source_path = None
        self._state = None

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        toolbar = QHBoxLayout()
        self._title_label = QLabel("")
        self._title_label.setStyleSheet("font-weight: bold; padding: 4px;")
        toolbar.addWidget(self._title_label, 1)
        self._open_in_data_dock_btn = QPushButton("Open in Data Dock")
        self._open_in_data_dock_btn.setToolTip(
            "Load this file into the Data Dock for editing (this preview stays read-only)."
        )
        self._open_in_data_dock_btn.clicked.connect(self._open_in_data_dock)
        self._open_in_data_dock_btn.setEnabled(False)
        toolbar.addWidget(self._open_in_data_dock_btn)
        layout.addLayout(toolbar)

        self.table_view = QTableView()
        self.model = SpreadsheetModel(service=None)
        self.table_view.setModel(self.model)
        self.table_view.setEditTriggers(QTableView.EditTrigger.NoEditTriggers)

        # Apply theme-aware styling to ensure readability across themes
        self._apply_table_stylesheet()

        layout.addWidget(self.table_view)

        self.bus.source_opened.connect(self._on_source_opened)
        self.bus.gui_action_requested.connect(self._on_gui_action)

        kr = getattr(app_context, "keybinding_registry", None)
        if kr is not None:
            kr.bind("csv_preview.open_in_data_dock", self._open_in_data_dock, self)

    def _on_source_opened(self, event, payload):
        if event != SourceEvent.OPENED or payload.source_type != self._source_type:
            return
        self._load(payload.path)

    def _load(self, path: str) -> None:
        self.current_source_path = path
        self._title_label.setText(path.rsplit("/", 1)[-1].rsplit("\\", 1)[-1] if path else "")
        registry = getattr(self._ctx, "source_format_registry", None)
        data_provider_registry = getattr(self._ctx, "data_provider_registry", None)
        spec = registry.get(self._source_type) if registry else None
        loader = (
            data_provider_registry.get_data_source_loader(spec.data_loader_id)
            if data_provider_registry and spec and spec.data_loader_id
            else None
        )
        self._state = loader.callback(path) if loader and loader.callback else None
        self.model.set_state(self._state)
        self._open_in_data_dock_btn.setEnabled(self._state is not None)

    def _on_gui_action(self, event, payload):
        """Re-theme table when display settings or theme changes."""
        if event in (GuiEvent.SOURCE_VIEWER_SETTINGS_CHANGED, GuiEvent.THEME_CHANGED):
            self._apply_table_stylesheet()

    def _apply_table_stylesheet(self) -> None:
        """Apply theme-aware colors to the table for readability."""
        # Get colors from theme or settings
        theme_manager = getattr(self._ctx, "theme_manager", None)
        bg_color = "#FFFFFF"
        text_color = "#000000"
        header_color = "#F5F5F5"
        border_color = "#CCCCCC"

        if theme_manager and hasattr(theme_manager, "get_current_theme"):
            theme = theme_manager.get_current_theme()
            if theme:
                # Extract colors from theme dict if available
                bg_color = theme.get("colors", {}).get("background", bg_color)
                text_color = theme.get("colors", {}).get("text", text_color)
                header_color = theme.get("colors", {}).get("header_bg", header_color)
                border_color = theme.get("colors", {}).get("border", border_color)

        # Apply stylesheet ensuring text is always readable
        stylesheet = f"""
            QTableView {{
                background-color: {bg_color};
                color: {text_color};
                border: 1px solid {border_color};
            }}
            QTableView::item {{
                padding: 4px;
                border: 1px solid {border_color};
            }}
            QHeaderView::section {{
                background-color: {header_color};
                color: {text_color};
                padding: 4px;
                border: 1px solid {border_color};
            }}
        """
        self.table_view.setStyleSheet(stylesheet)

    def _open_in_data_dock(self) -> None:
        if self._state is None:
            return
        self.bus.data_dock_action_requested.emit(
            DataDockIntent.LOAD_DATASET,
            DataDockPayload(dataset_state=self._state, dataset_id=self._state.dataset_id),
        )

    # ------------------------------------------------------------ zoom (no-op)
    def zoom_in(self):
        pass

    def zoom_out(self):
        pass

    def zoom_reset(self):
        pass
