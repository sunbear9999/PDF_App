from __future__ import annotations

import uuid
from typing import Any, Iterable, List, Tuple

from PySide6.QtCore import QAbstractTableModel, QModelIndex, Qt, QThread, Signal, QSize, QTimer
from PySide6.QtGui import QColor, QKeySequence, QPen
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QFileDialog,
    QFrame,
    QGridLayout,
    QHeaderView,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QMenu,
    QMessageBox,
    QPushButton,
    QSizePolicy,
    QTableView,
    QTextEdit,
    QStyledItemDelegate,
    QStyleOptionViewItem,
    QVBoxLayout,
    QWidget,
)

from core.events.domains.data_dock_events import DataDockEvent, DataDockEventPayload, DataDockIntent
from core.events.domains.document_events import DocumentIntent, DocumentPayload
from core.models.data_dock_models import ChartConfig, DataGridState
from core.models.ontology_model import EntityType
from core.models.workspace_models import NodeModel, WorkspaceModel
from gui.base import BaseDialog
from gui.base.core import BaseDock, UnifiedThemedMixin
from gui.docks.data_dock_chart import DataChartWidget


class SpreadsheetModel(QAbstractTableModel):
    changed = Signal()
    before_change = Signal()

    def __init__(self, service, parent=None):
        super().__init__(parent)
        self.service = service
        self.state: DataGridState | None = None

    def set_state(self, state: DataGridState | None) -> None:
        self.beginResetModel()
        self.state = state
        self.endResetModel()

    def refresh(self) -> None:
        if not self.state:
            return
        top = self.index(0, 0)
        bottom = self.index(max(0, self.rowCount() - 1), max(0, self.columnCount() - 1))
        if top.isValid() and bottom.isValid():
            self.dataChanged.emit(top, bottom, [Qt.ItemDataRole.DisplayRole, Qt.ItemDataRole.EditRole])
        self.headerDataChanged.emit(Qt.Orientation.Horizontal, 0, max(0, self.columnCount() - 1))
        self.headerDataChanged.emit(Qt.Orientation.Vertical, 0, max(0, self.rowCount() - 1))

    def rowCount(self, parent=QModelIndex()) -> int:
        return 0 if parent.isValid() or not self.state else len(self.state.rows) + 1

    def columnCount(self, parent=QModelIndex()) -> int:
        return 0 if parent.isValid() or not self.state else len(self.state.headers) + 1

    def data(self, index: QModelIndex, role=Qt.ItemDataRole.DisplayRole):
        if not self.state or not index.isValid():
            return None
        if role == Qt.ItemDataRole.BackgroundRole and (index.row() == 0 or index.column() == 0):
            return QColor("#263241")
        if role == Qt.ItemDataRole.ForegroundRole and (index.row() == 0 or index.column() == 0):
            return QColor("#f3f6fb")
        if role == Qt.ItemDataRole.TextAlignmentRole and (index.row() == 0 or index.column() == 0):
            return Qt.AlignmentFlag.AlignCenter
        if role in (Qt.ItemDataRole.DisplayRole, Qt.ItemDataRole.EditRole):
            row = index.row()
            col = index.column()
            if row == 0 and col == 0:
                return "Row titles"
            if row == 0:
                data_col = col - 1
                return self.state.headers[data_col] if data_col < len(self.state.headers) else f"Column {data_col + 1}"
            if col == 0:
                data_row = row - 1
                return self.state.row_headers[data_row] if data_row < len(self.state.row_headers) else str(data_row + 1)
            data_row = row - 1
            data_col = col - 1
            source_row = self.state.rows[data_row] if data_row < len(self.state.rows) else []
            return source_row[data_col] if data_col < len(source_row) else ""
        return None

    def setData(self, index: QModelIndex, value: Any, role=Qt.ItemDataRole.EditRole) -> bool:
        if not self.state or not index.isValid() or role != Qt.ItemDataRole.EditRole:
            return False
        row = index.row()
        col = index.column()
        if row == 0 and col == 0:
            return False
        self.before_change.emit()
        if row == 0:
            self.service.update_header(self.state.dataset_id, "column", col - 1, value)
        elif col == 0:
            self.service.update_header(self.state.dataset_id, "row", row - 1, value)
        else:
            self.service.update_cell(self.state.dataset_id, row - 1, col - 1, value)
        self.dataChanged.emit(index, index, [Qt.ItemDataRole.DisplayRole, Qt.ItemDataRole.EditRole])
        self.changed.emit()
        return True

    def headerData(self, section: int, orientation: Qt.Orientation, role=Qt.ItemDataRole.DisplayRole):
        if not self.state or role not in (Qt.ItemDataRole.DisplayRole, Qt.ItemDataRole.EditRole):
            return None
        if orientation == Qt.Orientation.Horizontal:
            return "" if section == 0 else self._letters(section)
        return "" if section == 0 else str(section)

    def setHeaderData(self, section: int, orientation: Qt.Orientation, value: Any, role=Qt.ItemDataRole.EditRole) -> bool:
        if not self.state or role != Qt.ItemDataRole.EditRole:
            return False
        kind = "column" if orientation == Qt.Orientation.Horizontal else "row"
        self.service.update_header(self.state.dataset_id, kind, section, value)
        self.headerDataChanged.emit(orientation, section, section)
        self.changed.emit()
        return True

    def flags(self, index: QModelIndex):
        if not index.isValid():
            return Qt.ItemFlag.ItemIsEnabled
        if index.row() == 0 and index.column() == 0:
            return Qt.ItemFlag.ItemIsSelectable | Qt.ItemFlag.ItemIsEnabled
        return Qt.ItemFlag.ItemIsSelectable | Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsEditable

    def _letters(self, number: int) -> str:
        result = ""
        while number:
            number, rem = divmod(number - 1, 26)
            result = chr(65 + rem) + result
        return result


class DataTableView(QTableView):
    def __init__(self, dock, parent=None):
        super().__init__(parent)
        self.dock = dock

    def keyPressEvent(self, event):
        if event.matches(QKeySequence.StandardKey.Copy):
            self.dock.copy_selection()
            return
        if event.matches(QKeySequence.StandardKey.Cut):
            self.dock.cut_selection()
            return
        if event.matches(QKeySequence.StandardKey.Paste):
            self.dock.paste_selection()
            return
        if event.matches(QKeySequence.StandardKey.Undo):
            self.dock.undo()
            return
        if event.matches(QKeySequence.StandardKey.Redo) or (
            event.key() == Qt.Key.Key_Y and event.modifiers() & Qt.KeyboardModifier.ControlModifier
        ):
            self.dock.redo()
            return
        super().keyPressEvent(event)


class CutHighlightDelegate(QStyledItemDelegate):
    def __init__(self, dock, parent=None):
        super().__init__(parent)
        self.dock = dock

    def paint(self, painter, option: QStyleOptionViewItem, index: QModelIndex):
        super().paint(painter, option, index)
        payload = getattr(self.dock, "_cut_payload", None) or {}
        cells = set(tuple(cell) for cell in payload.get("cells", []) if len(cell) == 2)
        if (index.row(), index.column()) not in cells:
            return
        painter.save()
        pen = QPen(QColor("#f59e0b"), 2, Qt.PenStyle.DashLine)
        painter.setPen(pen)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawRect(option.rect.adjusted(2, 2, -2, -2))
        painter.restore()


class DataExtractThread(QThread):
    finished = Signal(list)
    error = Signal(str)

    def __init__(self, service, pdf_path: str, parent=None):
        super().__init__(parent)
        self._service = service
        self._pdf_path = pdf_path

    def run(self):
        try:
            import fitz
            doc = fitz.open(self._pdf_path)
            states = self._service.extract_document(doc, self._pdf_path)
            doc.close()
            self.finished.emit(states)
        except Exception as exc:
            self.error.emit(str(exc))


class DataDockView(BaseDock, UnifiedThemedMixin):
    _dock_id = "data_dock"

    def __init__(self, app_context, parent=None):
        super().__init__(app_context, parent)
        self.service = app_context.data_dock_service
        self.registry = app_context.data_provider_registry or self.service.provider_registry
        self._chart_config: ChartConfig | None = None
        self._paste_target = "cells"
        self._cut_payload: dict | None = None
        self._undo_stack: List[dict] = []
        self._redo_stack: List[dict] = []
        self._restoring_history = False
        self._history_dataset_id: str | None = None
        self._build_ui()
        self._bind_events()
        self.refresh_library()

    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(6, 6, 6, 6)
        root.setSpacing(4)

        row1 = QHBoxLayout()
        row1.setSpacing(4)
        self.dataset_combo = QComboBox()
        self.dataset_combo.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.dataset_combo.setMinimumWidth(90)
        self.dataset_combo.currentIndexChanged.connect(self._on_dataset_selected)
        row1.addWidget(self.dataset_combo, 2)

        self.dataset_name = QLineEdit()
        self.dataset_name.setPlaceholderText("Dataset name")
        self.dataset_name.setMinimumWidth(90)
        self.dataset_name.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.dataset_name.editingFinished.connect(self._commit_dataset_name)
        row1.addWidget(self.dataset_name, 2)

        self.btn_new = QPushButton("New")
        self.btn_new.setToolTip("Create a blank working dataset.")
        self.btn_new.clicked.connect(self.new_dataset)
        self.btn_save = QPushButton("Save")
        self.btn_save.setToolTip("Save the active dataset to the project database.")
        self.btn_save.clicked.connect(self.save_dataset)
        self.btn_jump_pdf = QPushButton("Source")
        self.btn_jump_pdf.setToolTip("Open the source document and jump to this dataset's original location.")
        self.btn_jump_pdf.clicked.connect(self.jump_to_source)
        self.btn_more = QPushButton("☰")
        self.btn_more.setToolTip("Dataset management actions.")
        self.btn_more.setFixedWidth(32)
        self.btn_more.clicked.connect(self._show_more_menu)
        for btn in (self.btn_new, self.btn_save, self.btn_jump_pdf, self.btn_more):
            if btn is not self.btn_more:
                btn.setMaximumWidth(92)
            row1.addWidget(btn)
        root.addLayout(row1)

        row2 = QHBoxLayout()
        row2.setSpacing(4)
        self._edit_target = QComboBox()
        self._edit_target.addItems(["Row", "Column"])
        self._edit_target.setFixedWidth(76)
        row2.addWidget(self._edit_target)
        for label, callback, tip in (
            ("+", self._insert_current, "Insert row or column after the selection"),
            ("-", self._delete_current, "Delete selected row or column"),
        ):
            btn = QPushButton(label)
            btn.setFixedWidth(28)
            btn.setToolTip(tip)
            btn.clicked.connect(callback)
            row2.addWidget(btn)
        row2.addWidget(self._separator())
        self.actions_btn = QPushButton("Tools")
        self.actions_btn.setToolTip("Clean, reshape, and repair imported table data.")
        self.actions_btn.setMaximumWidth(82)
        self.actions_btn.clicked.connect(self._show_data_tools_menu)
        row2.addWidget(self.actions_btn)
        self.metrics_btn = QPushButton("Metrics")
        self.metrics_btn.setToolTip("Add a computed metric row or column from the selected data.")
        self.metrics_btn.setMaximumWidth(82)
        self.metrics_btn.clicked.connect(self._show_metrics_menu)
        row2.addWidget(self.metrics_btn)
        row2.addStretch()
        self.btn_extract_pdf = QPushButton("Extract PDF")
        self.btn_extract_pdf.setToolTip("Scan the open PDF for tables and import detected data into Data Dock.")
        self.btn_extract_pdf.setMaximumWidth(120)
        self.btn_extract_pdf.clicked.connect(self.extract_open_pdf)
        row2.addWidget(self.btn_extract_pdf)
        root.addLayout(row2)

        self.model = SpreadsheetModel(self.service, self)
        self.model.before_change.connect(self._push_undo)
        self.model.changed.connect(self._on_model_changed)
        self.table = DataTableView(self)
        self.table.setModel(self.model)
        self.table.setSelectionMode(QTableView.SelectionMode.ExtendedSelection)
        self.table.setSelectionBehavior(QTableView.SelectionBehavior.SelectItems)
        self.table.setHorizontalScrollMode(QTableView.ScrollMode.ScrollPerPixel)
        self.table.setVerticalScrollMode(QTableView.ScrollMode.ScrollPerPixel)
        self.table.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        self.table.horizontalHeader().setStretchLastSection(False)
        self.table.horizontalHeader().hide()
        self.table.verticalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        self.table.verticalHeader().hide()
        self.table.setColumnWidth(0, 150)
        self.table.setItemDelegate(CutHighlightDelegate(self, self.table))
        self.table.setToolTip("")
        self.table.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.table.customContextMenuRequested.connect(self._show_table_context_menu)
        self.table.horizontalHeader().sectionClicked.connect(self._select_column_header_target)
        self.table.verticalHeader().sectionClicked.connect(self._select_row_header_target)
        self.table.selectionModel().selectionChanged.connect(lambda *_: self._on_selection_changed())
        self.table.clicked.connect(lambda *_: self._set_paste_target("cells"))
        self.table.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        root.addWidget(self.table, 8)

        self.cell_view = QTextEdit()
        self.cell_view.setReadOnly(True)
        self.cell_view.setFixedHeight(46)
        root.addWidget(self.cell_view)

        self.summary_label = QLabel("-")
        self.summary_label.setAlignment(Qt.AlignmentFlag.AlignRight)
        root.addWidget(self.summary_label)

        chart_panel = QFrame()
        chart_panel.setObjectName("DataDockChartPanel")
        chart_grid = QGridLayout(chart_panel)
        chart_grid.setContentsMargins(0, 0, 0, 0)
        chart_grid.setHorizontalSpacing(4)
        chart_grid.setVerticalSpacing(3)
        self.chart_type = QComboBox()
        self.chart_type.addItems(sorted(self.registry.chart_types().keys()))
        self.x_field = QComboBox()
        self.y_field = QComboBox()
        self.palette_combo = QComboBox()
        self.palette_combo.addItems(sorted(self.registry.palettes().keys()))
        for combo in (self.chart_type, self.x_field, self.y_field, self.palette_combo):
            combo.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.chart_title = QLineEdit()
        self.chart_title.setPlaceholderText("Chart title")
        self.x_title = QLineEdit()
        self.x_title.setPlaceholderText("X title")
        self.y_title = QLineEdit()
        self.y_title.setPlaceholderText("Y axis title")
        for field in (self.chart_title, self.x_title, self.y_title):
            field.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.show_labels = QCheckBox("Labels")
        self.show_labels.setToolTip("Show values next to bars or points.")
        self.multi_series = QCheckBox("Series")
        self.multi_series.setToolTip("Use selected numeric columns as separate chart series, useful for grouped bar charts.")
        self.show_grid = QCheckBox("Grid")
        self.show_grid.setToolTip("Show chart grid lines.")
        self.show_grid.setChecked(True)
        for widget in (self.chart_type, self.x_field, self.y_field, self.palette_combo):
            widget.currentTextChanged.connect(self.update_chart_preview)
        for field in (self.chart_title, self.x_title, self.y_title):
            field.textChanged.connect(self.update_chart_preview)
        self.show_labels.stateChanged.connect(self.update_chart_preview)
        self.multi_series.stateChanged.connect(self.update_chart_preview)
        self.show_grid.stateChanged.connect(self.update_chart_preview)
        self.btn_chart_node = QPushButton("Chart Node")
        self.btn_chart_node.setToolTip("Create a workspace node that renders this chart.")
        self.btn_chart_node.clicked.connect(self.create_chart_node)
        self.btn_data_node = QPushButton("Data Node")
        self.btn_data_node.setToolTip("Create a workspace node linked to this dataset.")
        self.btn_data_node.clicked.connect(self.create_data_node)
        self.btn_export_chart = QPushButton("Export")
        self.btn_export_chart.setToolTip("Export the current chart preview as a PNG image.")
        self.btn_export_chart.clicked.connect(self.export_chart_png)
        for btn in (self.btn_data_node, self.btn_chart_node, self.btn_export_chart):
            btn.setMaximumWidth(110)

        chart_grid.addWidget(QLabel("Chart"), 0, 0)
        chart_grid.addWidget(self.chart_type, 0, 1)
        chart_grid.addWidget(QLabel("X"), 0, 2)
        chart_grid.addWidget(self.x_field, 0, 3)
        chart_grid.addWidget(QLabel("Y"), 0, 4)
        chart_grid.addWidget(self.y_field, 0, 5)
        chart_grid.addWidget(self.chart_title, 1, 0, 1, 2)
        chart_grid.addWidget(self.x_title, 1, 2, 1, 2)
        chart_grid.addWidget(self.y_title, 1, 4, 1, 2)
        chart_grid.addWidget(QLabel("Palette"), 2, 0)
        chart_grid.addWidget(self.palette_combo, 2, 1)
        chart_grid.addWidget(self.show_labels, 2, 2)
        chart_grid.addWidget(self.multi_series, 2, 3)
        chart_grid.addWidget(self.show_grid, 2, 4)
        chart_grid.addWidget(self.btn_export_chart, 2, 5)
        chart_grid.addWidget(self.btn_data_node, 3, 0, 1, 3)
        chart_grid.addWidget(self.btn_chart_node, 3, 3, 1, 3)
        for col in range(6):
            chart_grid.setColumnStretch(col, 1 if col in (1, 3, 5) else 0)
        root.addWidget(chart_panel, 0)

        self.chart_preview = DataChartWidget()
        self.chart_preview.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        self.chart_preview.setMaximumHeight(280)
        root.addWidget(self.chart_preview, 2)

        self.provenance_label = QLabel("")
        self.provenance_label.setWordWrap(True)
        root.addWidget(self.provenance_label)

        from gui.components.workflow_panel import WorkflowPanel
        self._workflow_panel = WorkflowPanel(
            app_context=self.app_context,
            mount_point="data_dock",
            output_target_id="data_dock_workflow",
            label="Workflow",
            parent=self,
        )
        root.addWidget(self._workflow_panel)
        self._register_keybindings()

    def _separator(self):
        sep = QFrame()
        sep.setFrameShape(QFrame.Shape.VLine)
        sep.setFrameShadow(QFrame.Shadow.Sunken)
        return sep

    def _build_data_tools_menu(self, parent) -> QMenu:
        menu = QMenu(parent)
        menu.setToolTipsVisible(True)
        self._add_menu_action(menu, "Trim whitespace", "Remove leading/trailing spaces from labels and cells.", self.trim_whitespace)
        self._add_menu_action(menu, "Normalize numbers", "Convert selected numeric-looking cells to plain numbers.", self.normalize_numbers)
        self._add_menu_action(menu, "Drop empty rows/columns", "Remove rows and columns with no useful content.", self.drop_empty)
        self._add_menu_action(menu, "Infer column types", "Detect text, number, and date-like columns.", self.infer_types)
        self._add_menu_action(menu, "Fill down", "Fill blank cells in selected columns using the value above.", self.fill_down)
        self._add_menu_action(menu, "Transpose", "Swap rows and columns.", self.transpose)
        menu.addSeparator()
        self._add_menu_action(menu, "Promote selected row to headers", "Use the selected row as column titles.", self.promote_selected_row)
        self._add_menu_action(menu, "Promote selected column to row titles", "Use the selected column as row titles.", self.promote_selected_column)
        self._add_menu_action(menu, "Split selected column...", "Split one selected column by a delimiter.", self.split_selected_column)
        self._add_menu_action(menu, "Merge selected columns", "Merge selected columns into one text column.", self.merge_selected_columns)
        self._add_menu_action(menu, "Replace text...", "Find and replace text in selected columns or the whole table.", self.replace_text)
        plugin_cleaners = self.registry.cleaners()
        if plugin_cleaners:
            menu.addSeparator()
            for spec in plugin_cleaners.values():
                if spec.callback:
                    self._add_menu_action(menu, spec.label, spec.description or "Plugin-provided data cleaner.",
                                          lambda checked=False, s=spec: self._apply_transform(lambda state: s.callback(state, {})))
        plugin_actions = self.registry.grid_actions()
        if plugin_actions:
            menu.addSeparator()
            for spec in plugin_actions.values():
                if spec.callback:
                    self._add_menu_action(menu, spec.label, spec.description or "Plugin-provided table action.",
                                          lambda checked=False, s=spec: self._run_plugin_grid_action(s))
        return menu

    def _build_metrics_menu(self, parent) -> QMenu:
        menu = QMenu(parent)
        menu.setToolTipsVisible(True)
        for metric in ("average", "sum", "min", "max", "count"):
            title = metric.title()
            self._add_menu_action(
                menu,
                f"Add {title} column",
                f"Create a new column to the right of the selection; only selected rows are filled.",
                lambda checked=False, m=metric: self.add_metric_column(m),
            )
            self._add_menu_action(
                menu,
                f"Add {title} row",
                f"Create a new row below the selection; only selected columns are filled.",
                lambda checked=False, m=metric: self.add_metric_row(m),
            )
            menu.addSeparator()
        return menu

    def _show_data_tools_menu(self, *args):
        menu = self._build_data_tools_menu(self.actions_btn)
        menu.exec(self.actions_btn.mapToGlobal(self.actions_btn.rect().bottomLeft()))

    def _show_metrics_menu(self, *args):
        menu = self._build_metrics_menu(self.metrics_btn)
        menu.exec(self.metrics_btn.mapToGlobal(self.metrics_btn.rect().bottomLeft()))

    def _add_menu_action(self, menu: QMenu, label: str, tooltip: str, callback):
        action = menu.addAction(label)
        action.setToolTip(tooltip)
        action.setStatusTip(tooltip)
        action.triggered.connect(lambda checked=False, cb=callback: cb())
        return action

    def _inject_dock_actions_cell(self, menu: QMenu, pos) -> None:
        """Append DockActionSpec actions for DATA_DOCK_CONTEXT_MENU_CELL mount."""
        from core.engine.dock_mounts import DATA_DOCK_CONTEXT_MENU_CELL
        index = self.table.indexAt(pos)
        row = index.row() if index.isValid() else -1
        col = index.column() if index.isValid() else -1
        value = self.table.model().data(index) or "" if index.isValid() else ""
        self.inject_dock_actions(
            menu, DATA_DOCK_CONTEXT_MENU_CELL, "context_menu:cell",
            extra={"row": row, "col": col, "value": value},
        )

    def _run_plugin_grid_action(self, spec):
        state = self.current_state()
        if not state or not spec.callback:
            return
        self._push_undo()
        result = spec.callback(state, {"cells": self._selected_data_cells()})
        updated = result if isinstance(result, DataGridState) else state
        if updated:
            self.model.set_state(updated)
            self.refresh_library(select_id=updated.dataset_id)
            self._emit_dataset_changed(updated.dataset_id, DataDockEvent.CLEANER_APPLIED)

    def _bind_events(self):
        self.bus.data_dock_action_requested.connect(self._handle_intent)

    def _register_keybindings(self):
        kr = getattr(self.app_context, "keybinding_registry", None)
        if not kr:
            return
        kr.bind("data_dock.clear_grid", self.clear_active_grid, self)
        kr.bind("data_dock.generate_chart", self.update_chart_preview, self)
        kr.bind("data_dock.save_dataset", self.save_dataset, self)
        kr.bind("data_dock.extract_pdf", self.extract_open_pdf, self)
        kr.bind("data_dock.jump_to_source", self.jump_to_source, self)
        kr.bind("data_dock.copy", self.copy_selection, self)
        kr.bind("data_dock.cut", self.cut_selection, self)
        kr.bind("data_dock.paste", self.paste_selection, self)
        kr.bind("data_dock.undo", self.undo, self)
        kr.bind("data_dock.redo", self.redo, self)
        kr.bind("data_dock.add_average_column", lambda: self.add_metric_column("average"), self)
        kr.bind("data_dock.add_sum_column", lambda: self.add_metric_column("sum"), self)

    def _handle_intent(self, intent, payload):
        if intent == DataDockIntent.OPEN:
            self.show()
            return
        if intent == DataDockIntent.LOAD_SELECTION:
            self.show()
            self.raise_()
            state = self.service.dataset_from_selection(getattr(payload, "selection", payload))
            self.refresh_library(select_id=state.dataset_id)
            self._emit_dataset_changed(state.dataset_id, DataDockEvent.DATASET_LOADED)
        elif intent == DataDockIntent.LOAD_DATASET:
            state = self._state_from_payload(payload)
            if state:
                self.service._track(state)
                self.refresh_library(select_id=state.dataset_id)
            elif getattr(payload, "dataset_id", None):
                self.load_dataset(payload.dataset_id)
        elif intent == DataDockIntent.NEW_DATASET:
            self.new_dataset()

    def refresh_library(self, select_id: str | None = None):
        current = select_id or self.current_dataset_id()
        self.dataset_combo.blockSignals(True)
        self.dataset_combo.clear()
        for item in self.service.list_datasets():
            status = "*" if item.get("dirty") else ""
            memory = "memory" if not item.get("is_persisted") else "saved"
            self.dataset_combo.addItem(f"{status}{item.get('name', 'Dataset')} ({memory})", item.get("dataset_id"))
        self.dataset_combo.blockSignals(False)
        if self.dataset_combo.count() == 0:
            state = self.service.new_dataset()
            self.dataset_combo.addItem(f"{state.name} (memory)", state.dataset_id)
            current = state.dataset_id
        if current:
            idx = self.dataset_combo.findData(current)
            if idx >= 0:
                self.dataset_combo.setCurrentIndex(idx)
        self._on_dataset_selected()

    def current_dataset_id(self):
        return self.dataset_combo.currentData()

    def current_state(self):
        dataset_id = self.current_dataset_id()
        return self.service.set_active_dataset(dataset_id) if dataset_id else None

    def _on_dataset_selected(self, *args):
        state = self.current_state()
        self.render_state(state)

    def render_state(self, state: DataGridState | None):
        self.model.set_state(state)
        dataset_id = state.dataset_id if state else None
        if dataset_id != self._history_dataset_id:
            self._undo_stack.clear()
            self._redo_stack.clear()
            self._history_dataset_id = dataset_id
        self.dataset_name.blockSignals(True)
        self.dataset_name.setText(state.name if state else "")
        self.dataset_name.blockSignals(False)
        self._refresh_chart_fields(state)
        self._refresh_provenance(state)
        self._update_summary()
        self.update_chart_preview()

    def _commit_dataset_name(self):
        state = self.current_state()
        if not state:
            return
        new_name = self.dataset_name.text().strip()
        if new_name and new_name != state.name:
            self._push_undo()
            self.service.rename_dataset(state.dataset_id, new_name, save=state.is_persisted)
            self.refresh_library(select_id=state.dataset_id)
            self._emit_dataset_changed(state.dataset_id, DataDockEvent.DATASET_RENAMED)

    def _set_paste_target(self, target: str):
        self._paste_target = target

    def _select_column_header_target(self, section: int):
        self.table.selectColumn(section)
        self._set_paste_target("cells")

    def _select_row_header_target(self, section: int):
        self.table.selectRow(section)
        self._set_paste_target("cells")

    def _push_undo(self):
        state = self.current_state()
        if not state or self._restoring_history:
            return
        snapshot = state.to_dict()
        if self._undo_stack and self._undo_stack[-1] == snapshot:
            return
        self._undo_stack.append(snapshot)
        self._undo_stack = self._undo_stack[-100:]
        self._redo_stack.clear()

    def undo(self):
        state = self.current_state()
        if not state or not self._undo_stack:
            return
        self._redo_stack.append(state.to_dict())
        self._restore_snapshot(self._undo_stack.pop())

    def redo(self):
        state = self.current_state()
        if not state or not self._redo_stack:
            return
        self._undo_stack.append(state.to_dict())
        self._restore_snapshot(self._redo_stack.pop())

    def _restore_snapshot(self, snapshot: dict):
        restored = DataGridState.from_dict(snapshot)
        current = self.current_state()
        if current:
            restored.is_persisted = current.is_persisted
        self._restoring_history = True
        try:
            self.service._track(restored)
            self.model.set_state(restored)
            self.refresh_library(select_id=restored.dataset_id)
            self._emit_dataset_changed(restored.dataset_id)
        finally:
            self._restoring_history = False

    def _selected_cells(self) -> List[Tuple[int, int]]:
        indexes = self.table.selectionModel().selectedIndexes() if self.table.selectionModel() else []
        return sorted({(idx.row(), idx.column()) for idx in indexes if idx.isValid()})

    def _selected_columns(self) -> List[int]:
        return sorted({col - 1 for _, col in self._selected_cells() if col > 0})

    def _selected_rows(self) -> List[int]:
        return sorted({row - 1 for row, _ in self._selected_cells() if row > 0})

    def _selected_data_cells(self) -> List[Tuple[int, int]]:
        return sorted((row - 1, col - 1) for row, col in self._selected_cells() if row > 0 and col > 0)

    def _show_table_context_menu(self, pos):
        menu = QMenu(self.table)
        menu.setToolTipsVisible(True)
        self._add_menu_action(menu, "Copy", "Copy selected labels and data cells.", self.copy_selection)
        self._add_menu_action(menu, "Cut", "Cut selected labels and data cells; dashed cells clear after a successful paste.", self.cut_selection)
        self._add_menu_action(menu, "Paste", "Paste clipboard values into the selected starting cell or title cell.", self.paste_selection)
        menu.addSeparator()
        for metric in ("average", "sum", "min", "max", "count"):
            title = metric.title()
            self._add_menu_action(
                menu,
                f"Add {title} Column",
                f"Add a {metric} column beside the selected data and leave unselected rows blank.",
                lambda checked=False, m=metric: self.add_metric_column(m),
            )
            self._add_menu_action(
                menu,
                f"Add {title} Row",
                f"Add a {metric} row below the selected data and leave unselected columns blank.",
                lambda checked=False, m=metric: self.add_metric_row(m),
            )
        menu.addSeparator()
        self._add_menu_action(menu, "Promote Row to Column Titles", "Use the first selected row as editable column titles.", self.promote_selected_row)
        self._add_menu_action(menu, "Promote Column to Row Titles", "Use the first selected column as editable row titles.", self.promote_selected_column)
        self._inject_dock_actions_cell(menu, pos)
        menu.exec(self.table.viewport().mapToGlobal(pos))

    def _selection_matrix(self) -> List[List[str]]:
        cells = self._selected_cells()
        if not cells:
            return []
        rows = range(min(r for r, _ in cells), max(r for r, _ in cells) + 1)
        cols = range(min(c for _, c in cells), max(c for _, c in cells) + 1)
        selected = set(cells)
        matrix = []
        for row in rows:
            values = []
            for col in cols:
                value = self.model.data(self.model.index(row, col), Qt.ItemDataRole.DisplayRole) if (row, col) in selected else ""
                values.append("" if value is None else str(value))
            matrix.append(values)
        return matrix

    def copy_selection(self):
        matrix = self._selection_matrix()
        if not matrix:
            return
        QApplication.clipboard().setText("\n".join("\t".join(row) for row in matrix))
        self._cut_payload = None
        self.model.refresh()

    def cut_selection(self):
        matrix = self._selection_matrix()
        cells = self._selected_cells()
        if not matrix or not cells:
            return
        QApplication.clipboard().setText("\n".join("\t".join(row) for row in matrix))
        self._cut_payload = {"dataset_id": self.current_dataset_id(), "cells": cells}
        self.model.refresh()

    def paste_selection(self):
        text = QApplication.clipboard().text()
        if not text:
            return
        matrix = [line.split("\t") for line in text.splitlines()]
        if not matrix:
            return
        state = self.current_state()
        if not state:
            return
        self._push_undo()
        changed = self._paste_cells(matrix)
        if changed:
            self._clear_cut_source_if_needed()
            self.model.set_state(state)
            self._on_model_changed()

    def _flatten_for_headers(self, matrix: List[List[str]]) -> List[str]:
        if len(matrix) == 1:
            return list(matrix[0])
        if all(len(row) <= 1 for row in matrix):
            return [row[0] if row else "" for row in matrix]
        return [row[0] if row else "" for row in matrix]

    def _paste_headers(self, kind: str, indexes: List[int], values: List[str]) -> bool:
        state = self.current_state()
        if not state or not indexes:
            return False
        if len(indexes) == 1 and len(values) > 1:
            targets = list(range(indexes[0], indexes[0] + len(values)))
        else:
            targets = indexes
        for offset, target in enumerate(targets):
            value = values[offset] if offset < len(values) else values[-1]
            self.service.update_header(state.dataset_id, "column" if kind == "column" else "row", target, value)
        return True

    def _paste_cells(self, matrix: List[List[str]]) -> bool:
        state = self.current_state()
        if not state:
            return False
        selected = self._selected_cells()
        current = self.table.currentIndex()
        start_r = min((row for row, _ in selected), default=current.row() if current.isValid() else 0)
        start_c = min((col for _, col in selected), default=current.column() if current.isValid() else 0)
        for r, row in enumerate(matrix):
            for c, value in enumerate(row):
                self._set_view_cell(start_r + r, start_c + c, value)
        return True

    def _set_view_cell(self, row: int, col: int, value: Any) -> bool:
        state = self.current_state()
        if not state or row < 0 or col < 0 or (row == 0 and col == 0):
            return False
        if row == 0:
            self.service.update_header(state.dataset_id, "column", col - 1, value)
        elif col == 0:
            self.service.update_header(state.dataset_id, "row", row - 1, value)
        else:
            self.service.update_cell(state.dataset_id, row - 1, col - 1, value)
        return True

    def _clear_cut_source_if_needed(self):
        payload = self._cut_payload
        if not payload or payload.get("dataset_id") != self.current_dataset_id():
            self._cut_payload = None
            return
        state = self.current_state()
        if not state:
            return
        for row, col in payload.get("cells") or []:
            self._set_view_cell(row, col, "")
        self._cut_payload = None
        self.model.refresh()

    def _on_selection_changed(self):
        state = self.current_state()
        cells = self._selected_cells()
        if cells and state:
            row, col = cells[-1]
            value = self.model.data(self.model.index(row, col), Qt.ItemDataRole.DisplayRole)
            self.cell_view.setPlainText(str(value))
        self._update_summary()
        self.update_chart_preview()
        self.bus.data_dock_state_changed.emit(
            DataDockEvent.GRID_SELECTION_CHANGED,
            DataDockEventPayload(dataset_id=self.current_dataset_id(), changes={"cells": cells}),
        )

    def _update_summary(self):
        state = self.current_state()
        if not state:
            self.summary_label.setText("-")
            return
        values = self.service.selected_values(state.dataset_id, self._selected_data_cells())
        if not values:
            self.summary_label.setText("-")
            return
        summary = self.service.selection_summary(values)
        parts = [f"COUNT {summary['count']}", f"NUM {summary['numeric_count']}"]
        if summary["numeric_count"]:
            parts.extend([
                f"SUM {self.service.format_number(summary['sum'])}",
                f"AVG {self.service.format_number(summary['average'])}",
                f"MIN {self.service.format_number(summary['min'])}",
                f"MAX {self.service.format_number(summary['max'])}",
            ])
        self.summary_label.setText("   ".join(parts))

    def _on_model_changed(self):
        state = self.current_state()
        if state:
            self.refresh_library(select_id=state.dataset_id)
            self._emit_dataset_changed(state.dataset_id)

    def new_dataset(self, *args):
        state = self.service.new_dataset()
        self.refresh_library(select_id=state.dataset_id)

    def save_dataset(self, *args):
        state = self.current_state()
        if not state:
            return
        if self.dataset_name.text().strip():
            state.name = self.dataset_name.text().strip()
        saved = self.service.save_dataset(state.dataset_id)
        self.refresh_library(select_id=saved.dataset_id if saved else state.dataset_id)

    def save_state(self):
        self._commit_dataset_name()
        saved = self.service.save_all_open_datasets()
        current = self.current_dataset_id()
        self.refresh_library(select_id=current or (saved[0].dataset_id if saved else None))

    def load_dataset(self, dataset_id: str):
        state = self.service.set_active_dataset(dataset_id)
        self.refresh_library(select_id=state.dataset_id if state else dataset_id)

    def _insert_current(self, *args):
        self.add_column() if self._edit_target.currentText() == "Column" else self.add_row()

    def _delete_current(self, *args):
        self.delete_column() if self._edit_target.currentText() == "Column" else self.delete_row()

    def add_row(self, *args):
        state = self.current_state()
        if not state:
            return
        self._push_undo()
        row = self.table.currentIndex().row()
        insert_at = row if row > 0 else len(state.rows)
        state.rows.insert(insert_at, ["" for _ in state.headers])
        state.row_headers.insert(insert_at, str(insert_at + 1))
        self.service.update_grid(state.dataset_id, state.headers, state.rows, state.column_types, row_headers=state.row_headers)
        self.model.set_state(state)
        self._on_model_changed()

    def add_column(self, *args):
        state = self.current_state()
        if not state:
            return
        self._push_undo()
        col = self.table.currentIndex().column()
        insert_at = col if col > 0 else len(state.headers)
        state.headers.insert(insert_at, f"Column {insert_at + 1}")
        for row in state.rows:
            row.insert(insert_at, "")
        self.service.update_grid(state.dataset_id, state.headers, state.rows, state.column_types, row_headers=state.row_headers)
        self.model.set_state(state)
        self._on_model_changed()

    def delete_row(self, *args):
        state = self.current_state()
        rows = self._selected_rows()
        if not state or not rows:
            return
        self._push_undo()
        for row in reversed(rows):
            if 0 <= row < len(state.rows):
                state.rows.pop(row)
                if row < len(state.row_headers):
                    state.row_headers.pop(row)
        self.service.update_grid(state.dataset_id, state.headers, state.rows, state.column_types, row_headers=state.row_headers)
        self.model.set_state(state)
        self._on_model_changed()

    def delete_column(self, *args):
        state = self.current_state()
        cols = self._selected_columns()
        if not state or not cols:
            return
        self._push_undo()
        for col in reversed(cols):
            if 0 <= col < len(state.headers):
                state.headers.pop(col)
                for row in state.rows:
                    if col < len(row):
                        row.pop(col)
        self.service.update_grid(state.dataset_id, state.headers, state.rows, {}, row_headers=state.row_headers)
        self.model.set_state(state)
        self._on_model_changed()

    def clear_active_grid(self, *args):
        state = self.current_state()
        if state:
            self._push_undo()
            self.service.update_grid(state.dataset_id, state.headers, [["" for _ in state.headers] for _ in state.rows], state.column_types, row_headers=state.row_headers)
            self.model.refresh()
            self._on_model_changed()

    def trim_whitespace(self, *args):
        self._apply_transform(lambda state: self.service.trim_whitespace(state.dataset_id))

    def normalize_numbers(self, *args):
        columns = self._selected_columns() or None
        self._apply_transform(lambda state: self.service.normalize_numbers(state.dataset_id, columns))

    def drop_empty(self, *args):
        self._apply_transform(lambda state: self.service.drop_empty(state.dataset_id))

    def infer_types(self, *args):
        self._apply_transform(lambda state: self.service.infer_column_types(state.dataset_id))

    def fill_down(self, *args):
        columns = self._selected_columns() or None
        self._apply_transform(lambda state: self.service.fill_down(state.dataset_id, columns))

    def transpose(self, *args):
        self._apply_transform(lambda state: self.service.transpose(state.dataset_id))

    def promote_selected_row(self):
        rows = self._selected_rows()
        if rows:
            self._apply_transform(lambda state: self.service.promote_row_to_headers(state.dataset_id, rows[0]))

    def promote_selected_column(self):
        cols = self._selected_columns()
        if cols:
            self._apply_transform(lambda state: self.service.promote_column_to_row_headers(state.dataset_id, cols[0]))

    def split_selected_column(self):
        cols = self._selected_columns()
        if not cols:
            return
        delim, ok = QInputDialog.getText(self, "Split Column", "Delimiter:", text=",")
        if ok:
            self._apply_transform(lambda state: self.service.split_column(state.dataset_id, cols[0], delim))

    def merge_selected_columns(self):
        cols = self._selected_columns()
        if len(cols) >= 2:
            self._apply_transform(lambda state: self.service.merge_columns(state.dataset_id, cols, " "))

    def replace_text(self):
        find, ok = QInputDialog.getText(self, "Replace Text", "Find:")
        if not ok:
            return
        repl, ok = QInputDialog.getText(self, "Replace Text", "Replace with:")
        if ok:
            cols = self._selected_columns() or None
            self._apply_transform(lambda state: self.service.replace_text(state.dataset_id, find, repl, cols))

    def add_metric_column(self, metric: str):
        cols = self._selected_columns()
        rows = self._selected_rows()
        label = f"{metric.title()} ({', '.join(str(c + 1) for c in cols)})" if cols else metric.title()
        self._apply_transform(lambda state: self.service.add_metric_for_selection(state.dataset_id, "column", metric, rows, cols, label))

    def add_metric_row(self, metric: str):
        rows = self._selected_rows()
        cols = self._selected_columns()
        label = f"{metric.title()} ({', '.join(str(r + 1) for r in rows)})" if rows else metric.title()
        self._apply_transform(lambda state: self.service.add_metric_for_selection(state.dataset_id, "row", metric, rows, cols, label))

    def _apply_transform(self, callback):
        state = self.current_state()
        if not state:
            return
        self._push_undo()
        updated = callback(state)
        if updated:
            self.model.set_state(updated)
            self.refresh_library(select_id=updated.dataset_id)
            self._emit_dataset_changed(updated.dataset_id, DataDockEvent.CLEANER_APPLIED)

    def _show_clean_menu(self):
        menu = QMenu(self)
        menu.setToolTipsVisible(True)
        menu.addAction("Promote Selected Row to Headers", self.promote_selected_row)
        menu.addAction("Promote Selected Column to Row Titles", self.promote_selected_column)
        menu.addSeparator()
        menu.addAction("Split Selected Column...", self.split_selected_column)
        menu.addAction("Merge Selected Columns", self.merge_selected_columns)
        menu.addAction("Replace Text...", self.replace_text)
        plugin_cleaners = self.registry.cleaners()
        if plugin_cleaners:
            menu.addSeparator()
            for spec in plugin_cleaners.values():
                if spec.callback:
                    menu.addAction(spec.label, lambda checked=False, s=spec: self._apply_transform(lambda state: s.callback(state, {})))
        menu.exec(self.mapToGlobal(self.rect().center()))

    def _show_more_menu(self, *args):
        menu = QMenu(self)
        menu.setToolTipsVisible(True)
        menu.addAction("Duplicate Dataset", self.duplicate_dataset)
        menu.addAction("Save As...", self.save_dataset_as)
        menu.addAction("Delete Dataset", self.delete_dataset)
        menu.exec(self.btn_more.mapToGlobal(self.btn_more.rect().bottomLeft()))

    def duplicate_dataset(self):
        state = self.current_state()
        if state:
            clone = self.service.duplicate_dataset(state.dataset_id)
            self.refresh_library(select_id=clone.dataset_id if clone else state.dataset_id)

    def save_dataset_as(self):
        state = self.current_state()
        if not state:
            return
        name, ok = QInputDialog.getText(self, "Save Dataset As", "Dataset name:", text=f"{state.name} Copy")
        if ok and name:
            saved = self.service.save_dataset_as(state.dataset_id, name)
            self.refresh_library(select_id=saved.dataset_id if saved else state.dataset_id)

    def delete_dataset(self):
        state = self.current_state()
        if not state:
            return
        if QMessageBox.question(self, "Delete Dataset", f"Delete '{state.name}'?") == QMessageBox.StandardButton.Yes:
            self.service.delete_dataset(state.dataset_id)
            self.refresh_library()

    def extract_open_pdf(self, *args):
        viewer = getattr(self.app_context, "viewer", None)
        doc = getattr(viewer, "doc", None)
        if doc is None:
            self._show_info("No Open PDF", "Open a PDF first, then extract data.")
            return
        pdf_path = getattr(viewer, "pdf_path", "") or ""
        if not pdf_path:
            self._show_info("No File Path", "Cannot extract: the open PDF has no file path on disk.")
            return
        self.btn_extract_pdf.setEnabled(False)
        self.btn_extract_pdf.setText("Extracting...")
        self._extract_thread = DataExtractThread(self.service, pdf_path, self)
        self._extract_thread.finished.connect(self._on_extraction_done)
        self._extract_thread.error.connect(self._on_extraction_error)
        self._extract_thread.start()

    def _on_extraction_done(self, states):
        self.btn_extract_pdf.setEnabled(True)
        self.btn_extract_pdf.setText("Extract PDF")
        if not states:
            self._show_info("No Tables Found", "No selectable tables were detected in the open PDF.")
            return
        self.refresh_library(select_id=states[0].dataset_id)
        self._show_info("Data Extracted", f"Extracted {len(states)} dataset(s) from the open PDF.")

    def _on_extraction_error(self, msg):
        self.btn_extract_pdf.setEnabled(True)
        self.btn_extract_pdf.setText("Extract PDF")
        self._show_info("Extraction Failed", f"Error during extraction:\n{msg}")

    def jump_to_source(self, *args):
        state = self.current_state()
        if not state or state.provenance is None or state.provenance.page_number is None:
            return
        prov = state.provenance
        viewer = getattr(self.app_context, "viewer", None)
        source_path = prov.source_path or prov.pdf_path
        if viewer and source_path and getattr(viewer, "pdf_path", None) != source_path:
            self.bus.document_action_requested.emit(
                DocumentIntent.OPEN,
                DocumentPayload(path=source_path, source_id=prov.source_id, source_type=prov.source_type or "pdf"),
            )
            QTimer.singleShot(300, lambda p=prov: self._emit_source_jump(p))
        else:
            self._emit_source_jump(prov)
        self.bus.data_dock_state_changed.emit(
            DataDockEvent.PROVENANCE_JUMP_REQUESTED,
            DataDockEventPayload(dataset_id=state.dataset_id),
        )

    def _emit_source_jump(self, prov):
        bbox = prov.bounding_box_coordinates[0] if prov.bounding_box_coordinates else None
        self.bus.document_action_requested.emit(
            DocumentIntent.JUMP_TO_LOCATION,
            DocumentPayload(page_num=prov.page_number, rects=[bbox] if bbox else None),
        )

    def _refresh_chart_fields(self, state: DataGridState | None):
        for combo in (self.x_field, self.y_field):
            current = combo.currentData() or combo.currentText()
            combo.blockSignals(True)
            combo.clear()
            if combo is self.x_field:
                combo.addItem("Row Title", "__row_header__")
            if state:
                for header in state.headers:
                    combo.addItem(header, header)
            idx = combo.findData(current)
            if idx < 0:
                idx = combo.findText(str(current))
            if idx >= 0:
                combo.setCurrentIndex(idx)
            combo.blockSignals(False)

    def update_chart_preview(self, *args):
        state = self.current_state()
        if not state:
            self.chart_preview.set_chart(None, None, [])
            return
        cells = self._selected_data_cells()
        config = self.service.chart_config_for_selection(state.dataset_id, cells, self.chart_type.currentText())
        if config is None:
            return
        config.x_field = self.x_field.currentData() or self.x_field.currentText()
        config.y_field = self.y_field.currentData() or self.y_field.currentText()
        config.palette_id = self.palette_combo.currentText()
        config.title = self.chart_title.text().strip() or config.title
        config.name = config.title
        config.x_title = self.x_title.text().strip() or self.x_field.currentText()
        config.y_title = self.y_title.text().strip() or self.y_field.currentText()
        config.show_data_labels = self.show_labels.isChecked()
        config.show_grid_lines = self.show_grid.isChecked()
        config.series = self._chart_series_from_selection(state, config) if self.multi_series.isChecked() else []
        self._chart_config = config
        self.chart_preview.set_chart(state, config, self.registry.palette(config.palette_id))

    def _chart_series_from_selection(self, state: DataGridState, config: ChartConfig) -> List[dict]:
        selected_cols = self._selected_columns()
        try:
            x_idx = state.headers.index(config.x_field) if config.x_field in state.headers else -1
        except Exception:
            x_idx = -1
        y_cols = [col for col in selected_cols if col != x_idx and 0 <= col < len(state.headers)]
        if not y_cols and config.y_field in state.headers:
            y_cols = [state.headers.index(config.y_field)]
        return [{"name": state.headers[col], "y_field": state.headers[col]} for col in y_cols]

    def export_chart_png(self):
        state = self.current_state()
        if not state:
            return
        self.update_chart_preview()
        if not self._chart_config:
            return
        path, _ = QFileDialog.getSaveFileName(self, "Export Chart", f"{self._chart_config.name}.png", "PNG Images (*.png)")
        if not path:
            return
        image = self.chart_preview.render_image(QSize(1200, 800))
        image.save(path, "PNG")
        self.bus.data_dock_state_changed.emit(
            DataDockEvent.CHART_EXPORTED,
            DataDockEventPayload(dataset_id=state.dataset_id, chart_config=self._chart_config, changes={"path": path}),
        )

    def create_data_node(self, *args):
        state = self.current_state()
        if state:
            self._create_workspace_node("data", state, None)

    def create_chart_node(self, *args):
        state = self.current_state()
        if not state:
            return
        self.update_chart_preview()
        self._create_workspace_node("chart", state, self._chart_config)

    def _create_workspace_node(self, kind: str, state: DataGridState, config: ChartConfig | None):
        workspace_id = 1
        snapshot = state.to_dict()
        title = state.name if kind == "data" else (config.title if config else f"{state.name} Chart")
        props = {
            "title": title,
            "text": title,
            "note_text": title,
            "preview_text": self._preview_text(state, config),
            "data_dock": {
                "dataset_id": state.dataset_id if state.is_persisted else None,
                "snapshot": snapshot,
                "chart_config": config.to_dict() if config else None,
                "provenance": state.provenance.to_dict(),
            },
        }
        node = NodeModel(
            id=f"{kind}_{uuid.uuid4()}",
            quote="",
            note=title,
            color="#155e75" if kind == "data" else "#6d28d9",
            is_custom=True,
            x=80,
            y=80,
            width=460 if kind == "chart" else 280,
            height=300 if kind == "chart" else 160,
            workspace_id=workspace_id,
            node_type_id="workspace.node.data" if kind == "data" else "workspace.node.chart",
            entity_type=EntityType.DATA_TABLE.value if kind == "data" else EntityType.CHART.value,
            entity_properties=props,
            entity_state={"is_verified": True, "ai_generated": False, "origin": "human"},
        )
        self.app_context.workspace_service.sync_delta(WorkspaceModel(workspace_id=workspace_id, nodes=[node]))
        self.bus.status_message_requested.emit(f"Added {title} to the workspace.", 3000)

    def _preview_text(self, state: DataGridState, config: ChartConfig | None = None) -> str:
        if config:
            return f"{config.chart_type.title()} chart: {config.x_field} -> {config.y_field}"
        lines = [" | ".join(state.headers[:6])]
        for row in state.rows[:3]:
            padded = row + [""] * len(state.headers)
            lines.append(" | ".join(str(padded[i]) for i in range(min(len(state.headers), 6))))
        return "\n".join(lines)

    def _sync_workspace_dataset_snapshots(self, state: DataGridState):
        if not state.is_persisted:
            return
        service = getattr(self.app_context, "workspace_service", None)
        if not service:
            return
        try:
            model = service.load_workspace(1)
        except Exception:
            return
        changed = []
        for node in model.nodes:
            props = dict(getattr(node, "entity_properties", {}) or {})
            dock = dict(props.get("data_dock") or {})
            if dock.get("dataset_id") != state.dataset_id:
                continue
            dock["snapshot"] = state.to_dict()
            dock["provenance"] = state.provenance.to_dict()
            props["data_dock"] = dock
            config = ChartConfig.from_dict(dock["chart_config"]) if dock.get("chart_config") else None
            props["preview_text"] = self._preview_text(state, config)
            node.entity_properties = props
            node.note = props.get("title") or node.note
            changed.append(node)
        if changed:
            service.sync_delta(WorkspaceModel(workspace_id=1, nodes=changed))

    def _emit_dataset_changed(self, dataset_id: str, event=DataDockEvent.DATASET_CHANGED):
        state = self.service._open.get(dataset_id)
        if state:
            self._sync_workspace_dataset_snapshots(state)
        self.bus.data_dock_state_changed.emit(event, DataDockEventPayload(dataset_id=dataset_id, dataset_state=state))

    def _refresh_provenance(self, state: DataGridState | None):
        if not state or not state.provenance:
            self.provenance_label.setText("")
            self.btn_jump_pdf.setEnabled(False)
            return
        prov = state.provenance
        source = prov.source_path or prov.pdf_path or "Unknown source"
        location = f"page {prov.page_number + 1}" if prov.page_number is not None else "unknown location"
        self.provenance_label.setText(f"Source: {source} ({location})")
        self.btn_jump_pdf.setEnabled(prov.page_number is not None)

    def _state_from_payload(self, payload):
        raw = getattr(payload, "dataset_state", None)
        if isinstance(raw, DataGridState):
            return raw
        if isinstance(raw, dict):
            return DataGridState.from_dict(raw)
        snapshot = (getattr(payload, "extra", {}) or {}).get("snapshot")
        if isinstance(snapshot, dict):
            return DataGridState.from_dict(snapshot)
        return None

    def _show_info(self, title: str, message: str) -> None:
        dm = getattr(self.app_context, "dialog_manager", None)
        if not dm:
            QMessageBox.information(self, title, message)
            return
        dialog = BaseDialog(title, parent=self)
        layout = QVBoxLayout(dialog)
        label = QLabel(message)
        label.setWordWrap(True)
        layout.addWidget(label)
        row = QHBoxLayout()
        row.addStretch()
        btn = QPushButton("OK")
        btn.clicked.connect(dialog.accept)
        row.addWidget(btn)
        layout.addLayout(row)
        dm.show_instance(dialog)

    def apply_theme(self, theme: dict) -> None:
        self._theme = theme
        self.setStyleSheet(
            f"DataDockView {{ background: {self._t('bg_main')}; color: {self._t('text_main')}; }}"
            f"QTableView {{ background: {self._t('bg_input')}; color: {self._t('text_main')}; gridline-color: {self._t('border')}; }}"
            f"QHeaderView::section {{ background: {self._t('bg_panel')}; color: {self._t('text_main')}; border: 1px solid {self._t('border')}; }}"
            f"QTextEdit, QLineEdit {{ background: {self._t('bg_input')}; color: {self._t('text_main')}; border: 1px solid {self._t('border')}; }}"
        )

    def on_project_loaded(self) -> None:
        self.service.clear_memory()
        self.refresh_library()

    def on_project_cleared(self) -> None:
        self.service.clear_memory()
        self.refresh_library()
