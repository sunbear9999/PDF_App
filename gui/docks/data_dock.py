from __future__ import annotations

import uuid
from typing import List

from PySide6.QtCore import Qt, QRectF
from PySide6.QtGui import QColor, QPainter, QPen, QBrush, QShortcut, QKeySequence
from PySide6.QtWidgets import (
    QApplication,
    QComboBox,
    QDialog,
    QFormLayout,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from core.events.domains.data_dock_events import DataDockEvent, DataDockEventPayload, DataDockIntent, DataDockPayload
from core.events.domains.document_events import DocumentIntent, DocumentPayload
from core.models.data_dock_models import ChartConfig, DataGridState
from core.models.ontology_model import EntityType, RelationType
from core.models.workspace_models import NodeModel, WorkspaceModel
from gui.base.core import BaseDock, UnifiedThemedMixin


class DataChartPreview(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.state = None
        self.config = None
        self.palette = ["#2f80ed", "#27ae60", "#f2994a", "#eb5757", "#9b51e0"]
        self.setMinimumHeight(150)

    def set_chart(self, state: DataGridState | None, config: ChartConfig | None, palette: List[str]):
        self.state = state
        self.config = config
        self.palette = palette or self.palette
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.fillRect(self.rect(), QColor("#1f2933"))
        painter.setPen(QPen(QColor("#d7dde5")))
        if not self.state or not self.config:
            painter.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, "Chart preview")
            return
        headers = self.state.headers
        try:
            x_idx = headers.index(self.config.x_field) if self.config.x_field in headers else 0
            y_idx = headers.index(self.config.y_field) if self.config.y_field in headers else min(1, len(headers) - 1)
        except Exception:
            painter.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, "Choose chart fields")
            return
        points = []
        for row_num, row in enumerate(self.state.rows[:40]):
            if self.config.x_field == "__row_header__":
                label = self.state.row_headers[row_num] if row_num < len(self.state.row_headers) else str(row_num + 1)
            else:
                label = str(row[x_idx]) if x_idx < len(row) else ""
            try:
                value = float(str(row[y_idx]).replace(",", "").replace("$", "").replace("%", "")) if y_idx < len(row) else 0.0
            except Exception:
                continue
            points.append((label, value))
        if not points:
            painter.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, "No numeric values to chart")
            return
        area = self.rect().adjusted(34, 12, -16, -28)
        max_val = max(abs(v) for _, v in points) or 1.0
        chart_type = self.config.chart_type
        painter.setPen(QPen(QColor("#5b6572"), 1))
        painter.drawLine(area.bottomLeft(), area.bottomRight())
        painter.drawLine(area.bottomLeft(), area.topLeft())
        if chart_type == "line":
            self._paint_line(painter, area, points, max_val)
        elif chart_type == "scatter":
            self._paint_scatter(painter, area, points, max_val)
        elif chart_type == "pie":
            self._paint_pie(painter, area, points)
        else:
            self._paint_bar(painter, area, points, max_val)
        painter.setPen(QPen(QColor("#d7dde5")))
        painter.drawText(QRectF(area.left(), area.bottom() + 4, area.width(), 20), Qt.AlignmentFlag.AlignCenter, self.config.x_title or self.config.x_field)

    def _paint_bar(self, painter, area, points, max_val):
        bar_w = max(4, area.width() / max(1, len(points)) - 4)
        for idx, (_, value) in enumerate(points):
            h = (abs(value) / max_val) * area.height()
            x = area.left() + idx * (bar_w + 4)
            rect = QRectF(x, area.bottom() - h, bar_w, h)
            painter.setBrush(QBrush(QColor(self.palette[idx % len(self.palette)])))
            painter.setPen(Qt.PenStyle.NoPen)
            painter.drawRect(rect)

    def _paint_line(self, painter, area, points, max_val):
        prev = None
        pen = QPen(QColor(self.palette[0]), 2)
        painter.setPen(pen)
        for idx, (_, value) in enumerate(points):
            x = area.left() + (idx / max(1, len(points) - 1)) * area.width()
            y = area.bottom() - (abs(value) / max_val) * area.height()
            if prev:
                painter.drawLine(prev[0], prev[1], x, y)
            painter.setBrush(QBrush(QColor(self.palette[0])))
            painter.drawEllipse(QRectF(x - 3, y - 3, 6, 6))
            prev = (x, y)

    def _paint_scatter(self, painter, area, points, max_val):
        painter.setPen(Qt.PenStyle.NoPen)
        for idx, (_, value) in enumerate(points):
            x = area.left() + (idx / max(1, len(points) - 1)) * area.width()
            y = area.bottom() - (abs(value) / max_val) * area.height()
            painter.setBrush(QBrush(QColor(self.palette[idx % len(self.palette)])))
            painter.drawEllipse(QRectF(x - 4, y - 4, 8, 8))

    def _paint_pie(self, painter, area, points):
        total = sum(abs(v) for _, v in points) or 1.0
        size = min(area.width(), area.height())
        rect = QRectF(area.center().x() - size / 2, area.center().y() - size / 2, size, size)
        start = 0
        for idx, (_, value) in enumerate(points[:10]):
            span = int((abs(value) / total) * 360 * 16)
            painter.setBrush(QBrush(QColor(self.palette[idx % len(self.palette)])))
            painter.setPen(Qt.PenStyle.NoPen)
            painter.drawPie(rect, start, span)
            start += span


class DataDockView(BaseDock, UnifiedThemedMixin):
    def __init__(self, app_context, parent=None):
        super().__init__(app_context, parent)
        self.service = app_context.data_dock_service
        self._loading_grid = False
        self._chart_config = None
        self._build_ui()
        self._bind_events()
        self.refresh_library()

    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(8, 8, 8, 8)
        root.setSpacing(6)

        top = QHBoxLayout()
        self.dataset_combo = QComboBox()
        self.dataset_combo.currentIndexChanged.connect(self._on_dataset_selected)
        top.addWidget(QLabel("Dataset"))
        top.addWidget(self.dataset_combo, 1)
        self.btn_new = QPushButton("New")
        self.btn_new.clicked.connect(self.new_dataset)
        self.btn_save = QPushButton("Save")
        self.btn_save.clicked.connect(self.save_dataset)
        self.btn_save_as = QPushButton("Save As")
        self.btn_save_as.clicked.connect(self.save_dataset_as)
        self.btn_extract_pdf = QPushButton("Extract Open PDF")
        self.btn_extract_pdf.clicked.connect(self.extract_open_pdf)
        self.btn_jump_pdf = QPushButton("Jump to Table")
        self.btn_jump_pdf.setToolTip("Scroll the PDF viewer to the source region for this dataset")
        self.btn_jump_pdf.setEnabled(False)
        self.btn_jump_pdf.clicked.connect(self.jump_to_source)
        self.btn_more = QPushButton("Manage")
        self.btn_more.clicked.connect(self.manage_dataset)
        for btn in (self.btn_new, self.btn_save, self.btn_save_as, self.btn_extract_pdf, self.btn_jump_pdf, self.btn_more):
            top.addWidget(btn)
        root.addLayout(top)

        tools = QHBoxLayout()
        self.btn_transpose = QPushButton("Transpose")
        self.btn_transpose.clicked.connect(self.transpose)
        self.btn_drop_empty = QPushButton("Drop Empty")
        self.btn_drop_empty.clicked.connect(self.drop_empty)
        self.btn_add_row = QPushButton("+ Row")
        self.btn_add_row.clicked.connect(self.add_row)
        self.btn_add_col = QPushButton("+ Column")
        self.btn_add_col.clicked.connect(self.add_column)
        self.btn_del_row = QPushButton("- Row")
        self.btn_del_row.clicked.connect(self.delete_row)
        self.btn_del_col = QPushButton("- Column")
        self.btn_del_col.clicked.connect(self.delete_column)
        self.btn_rename_title = QPushButton("Rename Title")
        self.btn_rename_title.clicked.connect(self.rename_dataset_title)
        self.type_combo = QComboBox()
        self.type_combo.addItems(["Text", "Currency", "Percentage", "Date"])
        self.type_combo.currentTextChanged.connect(self.apply_column_type)
        tools.addWidget(self.btn_rename_title)
        tools.addWidget(self.btn_add_row)
        tools.addWidget(self.btn_add_col)
        tools.addWidget(self.btn_del_row)
        tools.addWidget(self.btn_del_col)
        tools.addWidget(self.btn_transpose)
        tools.addWidget(self.btn_drop_empty)
        tools.addWidget(QLabel("Column Type"))
        tools.addWidget(self.type_combo)
        tools.addStretch()
        root.addLayout(tools)

        self.table = QTableWidget(0, 0)
        self.table.setSelectionMode(QTableWidget.SelectionMode.ExtendedSelection)
        self.table.itemChanged.connect(self._on_item_changed)
        self.table.itemSelectionChanged.connect(self._update_summary)
        self.table.horizontalHeader().sectionDoubleClicked.connect(self.rename_column)
        self.table.verticalHeader().sectionDoubleClicked.connect(self.rename_row)
        root.addWidget(self.table, 1)

        self.summary_label = QLabel("SUM 0    AVERAGE 0    COUNT 0")
        root.addWidget(self.summary_label)

        chart_row = QHBoxLayout()
        self.chart_type = QComboBox()
        self.chart_type.addItems(["bar", "line", "pie", "scatter"])
        self.chart_type.currentTextChanged.connect(self.update_chart_preview)
        self.x_field = QComboBox()
        self.y_field = QComboBox()
        self.palette_combo = QComboBox()
        self.palette_combo.addItems(sorted((self.app_context.data_provider_registry or self.service.provider_registry).palettes().keys()))
        for widget in (self.x_field, self.y_field, self.palette_combo):
            widget.currentTextChanged.connect(self.update_chart_preview)
        self.btn_data_node = QPushButton("Create Data Node")
        self.btn_data_node.clicked.connect(self.create_data_node)
        self.btn_chart_node = QPushButton("Create Chart Node")
        self.btn_chart_node.clicked.connect(self.create_chart_node)
        chart_row.addWidget(QLabel("Chart"))
        chart_row.addWidget(self.chart_type)
        chart_row.addWidget(QLabel("X"))
        chart_row.addWidget(self.x_field)
        chart_row.addWidget(QLabel("Y"))
        chart_row.addWidget(self.y_field)
        chart_row.addWidget(self.palette_combo)
        chart_row.addWidget(self.btn_data_node)
        chart_row.addWidget(self.btn_chart_node)
        root.addLayout(chart_row)

        self.chart_preview = DataChartPreview()
        root.addWidget(self.chart_preview)
        self._register_keybindings()

    def _bind_events(self):
        self.bus.data_dock_action_requested.connect(self._handle_intent)

    def _register_keybindings(self):
        kr = getattr(self.app_context, "keybinding_registry", None)
        if not kr:
            return
        kr.bind("data_dock.clear_grid", self.clear_active_grid, self)
        kr.bind("data_dock.generate_chart", self.update_chart_preview, self)
        kr.bind("data_dock.save_dataset", self.save_dataset, self)

    def _handle_intent(self, intent, payload):
        if intent == DataDockIntent.OPEN:
            self.show()
            return
        if intent == DataDockIntent.LOAD_SELECTION:
            self.show()
            self.raise_()
            state = self.service.dataset_from_selection(getattr(payload, "selection", payload))
            self.refresh_library(select_id=state.dataset_id)
            self.bus.data_dock_state_changed.emit(DataDockEvent.DATASET_LOADED, DataDockEventPayload(dataset_id=state.dataset_id, dataset_state=state))
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
        self._refresh_jump_button(state)

    def _refresh_jump_button(self, state):
        has_location = (
            state is not None
            and state.provenance is not None
            and state.provenance.page_number is not None
        )
        self.btn_jump_pdf.setEnabled(has_location)

    def render_state(self, state: DataGridState | None):
        self._loading_grid = True
        try:
            self.table.clear()
            if not state:
                self.table.setRowCount(0)
                self.table.setColumnCount(0)
                return
            self.table.setColumnCount(len(state.headers))
            self.table.setHorizontalHeaderLabels(state.headers)
            self.table.setRowCount(len(state.rows))
            row_headers = list(state.row_headers or [])
            if len(row_headers) < len(state.rows):
                row_headers.extend(str(i + 1) for i in range(len(row_headers), len(state.rows)))
            self.table.setVerticalHeaderLabels(row_headers)
            for r, row in enumerate(state.rows):
                for c in range(len(state.headers)):
                    self.table.setItem(r, c, QTableWidgetItem(str(row[c]) if c < len(row) else ""))
            self._refresh_chart_fields(state)
            self.update_chart_preview()
        finally:
            self._loading_grid = False

    def _grid_from_table(self):
        headers = [self.table.horizontalHeaderItem(c).text() if self.table.horizontalHeaderItem(c) else f"Column {c + 1}" for c in range(self.table.columnCount())]
        rows = []
        for r in range(self.table.rowCount()):
            rows.append([
                self.table.item(r, c).text() if self.table.item(r, c) else ""
                for c in range(self.table.columnCount())
            ])
        row_headers = [self.table.verticalHeaderItem(r).text() if self.table.verticalHeaderItem(r) else str(r + 1) for r in range(self.table.rowCount())]
        return headers, rows, row_headers

    def _on_item_changed(self, item):
        if self._loading_grid:
            return
        state = self.current_state()
        if not state:
            return
        headers, rows, row_headers = self._grid_from_table()
        self.service.update_grid(state.dataset_id, headers, rows, state.column_types, row_headers=row_headers)
        self.refresh_library(select_id=state.dataset_id)
        self._emit_dataset_changed(state.dataset_id)

    def keyPressEvent(self, event):
        if event.matches(QKeySequence.StandardKey.Copy):
            self.copy_selection()
            return
        if event.matches(QKeySequence.StandardKey.Paste):
            self.paste_selection()
            return
        super().keyPressEvent(event)

    def copy_selection(self):
        ranges = self.table.selectedRanges()
        if not ranges:
            return
        rng = ranges[0]
        lines = []
        for r in range(rng.topRow(), rng.bottomRow() + 1):
            vals = []
            for c in range(rng.leftColumn(), rng.rightColumn() + 1):
                vals.append(self.table.item(r, c).text() if self.table.item(r, c) else "")
            lines.append("\t".join(vals))
        QApplication.clipboard().setText("\n".join(lines))

    def paste_selection(self):
        text = QApplication.clipboard().text()
        if not text:
            return
        start_r = self.table.currentRow() if self.table.currentRow() >= 0 else 0
        start_c = self.table.currentColumn() if self.table.currentColumn() >= 0 else 0
        rows = [line.split("\t") for line in text.splitlines()]
        self.table.setRowCount(max(self.table.rowCount(), start_r + len(rows)))
        self.table.setColumnCount(max(self.table.columnCount(), start_c + max((len(r) for r in rows), default=0)))
        for r, row in enumerate(rows):
            for c, value in enumerate(row):
                self.table.setItem(start_r + r, start_c + c, QTableWidgetItem(value))
        self._on_item_changed(None)

    def new_dataset(self, *args):
        state = self.service.new_dataset()
        self.refresh_library(select_id=state.dataset_id)

    def save_dataset(self, *args):
        state = self.current_state()
        if not state:
            return
        headers, rows, row_headers = self._grid_from_table()
        self.service.update_grid(state.dataset_id, headers, rows, state.column_types, row_headers=row_headers)
        saved = self.service.save_dataset(state.dataset_id)
        self.refresh_library(select_id=saved.dataset_id if saved else state.dataset_id)

    def rename_dataset_title(self, *args):
        state = self.current_state()
        if not state:
            return
        name, ok = QInputDialog.getText(self, "Rename Dataset", "Dataset title:", text=state.name)
        if ok and name:
            state.name = name
            state.dirty = True
            self.refresh_library(select_id=state.dataset_id)
            self._emit_dataset_changed(state.dataset_id)

    def add_row(self, *args):
        state = self.current_state()
        if not state:
            return
        row = self.table.currentRow()
        insert_at = row + 1 if row >= 0 else self.table.rowCount()
        self.table.insertRow(insert_at)
        self.table.setVerticalHeaderItem(insert_at, QTableWidgetItem(str(insert_at + 1)))
        for c in range(self.table.columnCount()):
            self.table.setItem(insert_at, c, QTableWidgetItem(""))
        self._renumber_blank_row_headers()
        self._on_item_changed(None)

    def add_column(self, *args):
        state = self.current_state()
        if not state:
            return
        col = self.table.currentColumn()
        insert_at = col + 1 if col >= 0 else self.table.columnCount()
        self.table.insertColumn(insert_at)
        self.table.setHorizontalHeaderItem(insert_at, QTableWidgetItem(f"Column {insert_at + 1}"))
        for r in range(self.table.rowCount()):
            self.table.setItem(r, insert_at, QTableWidgetItem(""))
        self._on_item_changed(None)

    def delete_row(self, *args):
        row = self.table.currentRow()
        if row < 0 and self.table.rowCount() > 0:
            row = self.table.rowCount() - 1
        if row >= 0:
            self.table.removeRow(row)
            self._on_item_changed(None)

    def delete_column(self, *args):
        col = self.table.currentColumn()
        if col < 0 and self.table.columnCount() > 0:
            col = self.table.columnCount() - 1
        if col >= 0:
            self.table.removeColumn(col)
            self._on_item_changed(None)

    def rename_column(self, section: int):
        current = self.table.horizontalHeaderItem(section).text() if self.table.horizontalHeaderItem(section) else f"Column {section + 1}"
        name, ok = QInputDialog.getText(self, "Rename Column", "Column title:", text=current)
        if ok and name:
            self.table.setHorizontalHeaderItem(section, QTableWidgetItem(name))
            self._on_item_changed(None)

    def rename_row(self, section: int):
        current = self.table.verticalHeaderItem(section).text() if self.table.verticalHeaderItem(section) else str(section + 1)
        name, ok = QInputDialog.getText(self, "Rename Row", "Row title:", text=current)
        if ok and name:
            self.table.setVerticalHeaderItem(section, QTableWidgetItem(name))
            self._on_item_changed(None)

    def _renumber_blank_row_headers(self):
        for r in range(self.table.rowCount()):
            item = self.table.verticalHeaderItem(r)
            if item is None or not item.text().strip():
                self.table.setVerticalHeaderItem(r, QTableWidgetItem(str(r + 1)))

    def save_dataset_as(self, *args):
        state = self.current_state()
        if not state:
            return
        name, ok = QInputDialog.getText(self, "Save Dataset As", "Dataset name:", text=f"{state.name} Copy")
        if ok and name:
            saved = self.service.save_dataset_as(state.dataset_id, name)
            self.refresh_library(select_id=saved.dataset_id if saved else state.dataset_id)

    def extract_open_pdf(self, *args):
        viewer = getattr(self.app_context, "viewer", None)
        doc = getattr(viewer, "doc", None)
        if doc is None:
            QMessageBox.information(self, "No Open PDF", "Open a PDF first, then extract data.")
            return
        pdf_path = getattr(viewer, "pdf_path", "") or ""
        states = self.service.extract_document(doc, pdf_path)
        if not states:
            QMessageBox.information(self, "No Tables Found", "No selectable tables or chart/image regions were detected in the open PDF.")
            return
        self.refresh_library(select_id=states[0].dataset_id)
        QMessageBox.information(self, "Data Extracted", f"Extracted {len(states)} dataset(s) from the open PDF.")

    def jump_to_source(self, *args):
        state = self.current_state()
        if not state or state.provenance is None or state.provenance.page_number is None:
            return
        prov = state.provenance
        viewer = getattr(self.app_context, "viewer", None)
        if viewer and prov.pdf_path and getattr(viewer, "pdf_path", None) != prov.pdf_path:
            from PySide6.QtWidgets import QMessageBox
            QMessageBox.information(self, "Different PDF", "This dataset was extracted from a different PDF than the one currently open.")
            return
        bbox = prov.bounding_box_coordinates[0] if prov.bounding_box_coordinates else None
        self.bus.document_action_requested.emit(
            DocumentIntent.JUMP_TO_LOCATION,
            DocumentPayload(page_num=prov.page_number, rects=[bbox] if bbox else None),
        )

    def manage_dataset(self, *args):
        state = self.current_state()
        if not state:
            return
        action, ok = QInputDialog.getItem(self, "Manage Dataset", "Action:", ["Rename", "Duplicate", "Delete", "Revert Saved", "Close Working Dataset"], 0, False)
        if not ok:
            return
        if action == "Rename":
            name, name_ok = QInputDialog.getText(self, "Rename Dataset", "Dataset name:", text=state.name)
            if name_ok and name:
                state.name = name
                state.dirty = True
                if state.is_persisted:
                    self.service.save_dataset(state.dataset_id)
        elif action == "Duplicate":
            clone = self.service.duplicate_dataset(state.dataset_id)
            self.refresh_library(select_id=clone.dataset_id if clone else state.dataset_id)
            return
        elif action == "Delete":
            if QMessageBox.question(self, "Delete Dataset", f"Delete '{state.name}'?") == QMessageBox.StandardButton.Yes:
                self.service.delete_dataset(state.dataset_id)
        elif action == "Revert Saved" and state.is_persisted:
            self.service._open.pop(state.dataset_id, None)
            self.service.load_dataset(state.dataset_id)
        elif action == "Close Working Dataset":
            self.service._open.pop(state.dataset_id, None)
        self.refresh_library()

    def clear_active_grid(self, *args):
        state = self.current_state()
        if not state:
            return
        self.service.update_grid(state.dataset_id, state.headers, [["" for _ in state.headers] for _ in state.rows], state.column_types, row_headers=state.row_headers)
        self.render_state(state)
        self._emit_dataset_changed(state.dataset_id)

    def transpose(self, *args):
        state = self.current_state()
        if state:
            self.service.transpose(state.dataset_id)
            self.render_state(state)
            self.refresh_library(select_id=state.dataset_id)
            self._emit_dataset_changed(state.dataset_id)

    def drop_empty(self, *args):
        state = self.current_state()
        if state:
            self.service.drop_empty(state.dataset_id)
            self.render_state(state)
            self.refresh_library(select_id=state.dataset_id)
            self._emit_dataset_changed(state.dataset_id)

    def apply_column_type(self, type_name):
        state = self.current_state()
        col = self.table.currentColumn()
        if not state or col < 0 or col >= len(state.headers):
            return
        state.column_types[state.headers[col]] = type_name
        state.dirty = True
        self.refresh_library(select_id=state.dataset_id)

    def _update_summary(self):
        values = []
        for item in self.table.selectedItems():
            values.append(item.text())
        summary = self.service.selection_summary(values)
        self.summary_label.setText(f"SUM {summary['sum']:.3g}    AVERAGE {summary['average']:.3g}    COUNT {summary['count']}")

    def _refresh_chart_fields(self, state):
        current_x = self.x_field.currentText()
        current_y = self.y_field.currentText()
        for combo, current in ((self.x_field, current_x), (self.y_field, current_y)):
            combo.blockSignals(True)
            combo.clear()
            if combo is self.x_field:
                combo.addItem("Row Title", "__row_header__")
            combo.addItems(state.headers)
            idx = combo.findText(current)
            if idx >= 0:
                combo.setCurrentIndex(idx)
            combo.blockSignals(False)

    def update_chart_preview(self, *args):
        state = self.current_state()
        if not state:
            self.chart_preview.set_chart(None, None, [])
            return
        self._chart_config = ChartConfig(
            chart_id=f"chart_{uuid.uuid4()}",
            name=f"{state.name} Chart",
            chart_type=self.chart_type.currentText(),
            dataset_id=state.dataset_id if state.is_persisted else None,
            x_field=self.x_field.currentData() or self.x_field.currentText(),
            y_field=self.y_field.currentText(),
            x_title=self.x_field.currentText(),
            y_title=self.y_field.currentText(),
            palette_id=self.palette_combo.currentText(),
        )
        registry = self.app_context.data_provider_registry or self.service.provider_registry
        self.chart_preview.set_chart(state, self._chart_config, registry.palette(self._chart_config.palette_id))

    def create_data_node(self, *args):
        state = self.current_state()
        if not state:
            return
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
        title = state.name if kind == "data" else (config.name if config else f"{state.name} Chart")
        props = {
            "title": title,
            "text": title,
            "note_text": title,
            "preview_text": self._preview_text(state, config),
            "data_dock": {
                "dataset_id": state.dataset_id if state.is_persisted else None,
                "snapshot": snapshot,
                "chart_config": config.to_dict() if config else None,
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
            width=280,
            height=160,
            workspace_id=workspace_id,
            node_type_id="workspace.node.data" if kind == "data" else "workspace.node.chart",
            entity_type=EntityType.DATA_TABLE.value if kind == "data" else EntityType.CHART.value,
            entity_properties=props,
            entity_state={"is_verified": True, "ai_generated": False, "origin": "human"},
        )
        model = WorkspaceModel(workspace_id=workspace_id, nodes=[node])
        self.app_context.workspace_service.sync_delta(model)

    def _preview_text(self, state: DataGridState, config: ChartConfig | None = None) -> str:
        if config:
            return f"{config.chart_type.title()} chart: {config.x_field} -> {config.y_field}"
        lines = [" | ".join(state.headers[:6])]
        for row in state.rows[:3]:
            lines.append(" | ".join(str((row + [''] * len(state.headers))[i]) for i in range(min(len(state.headers), 6))))
        return "\n".join(lines)

    def _emit_dataset_changed(self, dataset_id: str):
        state = self.service._open.get(dataset_id)
        if state:
            self._sync_workspace_dataset_snapshots(state)
        self.bus.data_dock_state_changed.emit(
            DataDockEvent.DATASET_CHANGED,
            DataDockEventPayload(dataset_id=dataset_id, dataset_state=state),
        )

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
            props["data_dock"] = dock
            props["preview_text"] = self._preview_text(state, ChartConfig.from_dict(dock["chart_config"]) if dock.get("chart_config") else None)
            node.entity_properties = props
            node.note = props.get("title") or node.note
            changed.append(node)
        if changed:
            service.sync_delta(WorkspaceModel(workspace_id=1, nodes=changed))

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

    def apply_theme(self, theme: dict) -> None:
        self._theme = theme
        self.setStyleSheet(
            f"DataDockView {{ background: {self._t('bg_main')}; color: {self._t('text_main')}; }}"
            f"QTableWidget {{ background: {self._t('bg_input')}; color: {self._t('text_main')}; gridline-color: {self._t('border')}; }}"
            f"QHeaderView::section {{ background: {self._t('bg_panel')}; color: {self._t('text_main')}; border: 1px solid {self._t('border')}; }}"
        )

    def on_project_loaded(self) -> None:
        self.service.clear_memory()
        self.refresh_library()

    def on_project_cleared(self) -> None:
        self.service.clear_memory()
        self.refresh_library()
