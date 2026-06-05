# gui/components/workspace_widgets.py
from PySide6.QtWidgets import (QMenu, QPushButton, QFrame, QHBoxLayout, QLabel, 
                               QComboBox, QDialog, QVBoxLayout, QListWidget, QListWidgetItem)
from PySide6.QtGui import QPainterPath, QPainterPathStroker, QStandardItemModel, QStandardItem, QCursor
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QGraphicsLineItem
from core.utils.workspace_utils import format_highlight_item_label

class GhostLineItem(QGraphicsLineItem):
    def __init__(self, x1, y1, x2, y2, source_id, target_id, sim_score, workspace_view):
        super().__init__(x1, y1, x2, y2)
        self.source_id = source_id
        self.target_id = target_id
        self.sim_score = sim_score
        self.workspace_view = workspace_view
        self.setAcceptedMouseButtons(Qt.MouseButton.RightButton)

    def shape(self):
        p = QPainterPath()
        p.moveTo(self.line().p1())
        p.lineTo(self.line().p2())
        stroker = QPainterPathStroker()
        stroker.setWidth(12)
        return stroker.createStroke(p)

    def contextMenuEvent(self, event):
        menu = QMenu()
        pct = int(self.sim_score * 100)
        convert_action = menu.addAction(f"🔗 Convert to Edge  (similarity {pct}%)")
        action = menu.exec(event.screenPos())
        if action == convert_action:
            self.workspace_view._convert_ghost_to_edge(self.source_id, self.target_id, self.sim_score)
        event.accept()

class CollapsingButton(QPushButton):
    _COLLAPSED_WIDTH = 36
    def __init__(self, icon_text, full_text, parent=None):
        super().__init__(icon_text, parent)
        self._icon_text = icon_text
        self._full_text = full_text
        self.setFixedWidth(self._COLLAPSED_WIDTH)

    def _sync_toolbar(self):
        w = self.parentWidget()
        while w is not None:
            if isinstance(w, QFrame) and w.objectName() == "WorkspaceToolbar":
                w.adjustSize()
                w.move(15, 15)
                return
            w = w.parentWidget()

    def enterEvent(self, event):
        self.setText(self._full_text)
        self.setFixedWidth(self.sizeHint().width() + 8)
        self._sync_toolbar()
        super().enterEvent(event)

    def leaveEvent(self, event):
        self.setText(self._icon_text)
        self.setFixedWidth(self._COLLAPSED_WIDTH)
        self._sync_toolbar()
        super().leaveEvent(event)

class CollapsingSection(QFrame):
    def __init__(self, icon_text, content_widget, parent=None):
        super().__init__(parent)
        self._content = content_widget
        layout = QHBoxLayout(self)
        layout.setContentsMargins(4, 0, 4, 0)
        layout.setSpacing(4)
        self._icon_label = QLabel(icon_text)
        self._icon_label.setObjectName("CollapsingIcon")
        layout.addWidget(self._icon_label)
        layout.addWidget(content_widget)
        content_widget.hide()

    def _sync_toolbar(self):
        w = self.parentWidget()
        while w is not None:
            if isinstance(w, QFrame) and w.objectName() == "WorkspaceToolbar":
                w.adjustSize()
                w.move(15, 15)
                return
            w = w.parentWidget()

    def enterEvent(self, event):
        self._content.show()
        self.adjustSize()
        self._sync_toolbar()
        super().enterEvent(event)

    def leaveEvent(self, event):
        local_pos = self.mapFromGlobal(QCursor.pos())
        if not self.rect().contains(local_pos):
            self._content.hide()
            self.adjustSize()
            self._sync_toolbar()
        super().leaveEvent(event)

class CheckableComboBox(QComboBox):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.view().clicked.connect(self.handle_item_clicked)
        self._changed = False

    def handle_item_clicked(self, index):
        item = self.model().itemFromIndex(index)
        if not item: return

        clicked_data = item.data(Qt.ItemDataRole.UserRole)
        current_state = item.checkState()
        all_item = self.model().item(0)

        self.model().blockSignals(True)

        if clicked_data == "ALL":
            item.setCheckState(Qt.CheckState.Checked)
            for i in range(1, self.model().rowCount()):
                self.model().item(i).setCheckState(Qt.CheckState.Unchecked)
        else:
            if current_state == Qt.CheckState.Checked:
                if all_item and all_item.checkState() == Qt.CheckState.Checked:
                    all_item.setCheckState(Qt.CheckState.Unchecked)
                    for i in range(1, self.model().rowCount()):
                        other_item = self.model().item(i)
                        if other_item != item:
                            other_item.setCheckState(Qt.CheckState.Unchecked)
            else:
                any_checked = any(self.model().item(i).checkState() == Qt.CheckState.Checked for i in range(1, self.model().rowCount()))
                if not any_checked and all_item:
                    all_item.setCheckState(Qt.CheckState.Checked)

        self._changed = True
        self.model().blockSignals(False)

        top_left = self.model().index(0, 0)
        bottom_right = self.model().index(self.model().rowCount() - 1, 0)
        self.model().dataChanged.emit(top_left, bottom_right)

    def hidePopup(self):
        if not self._changed:
            super().hidePopup()
        self._changed = False

    def addItem(self, text, userData=None, checked=False):
        if not isinstance(self.model(), QStandardItemModel):
            self.setModel(QStandardItemModel(self))
            
        item = QStandardItem(text)
        item.setFlags(Qt.ItemFlag.ItemIsUserCheckable | Qt.ItemFlag.ItemIsEnabled)
        item.setData(Qt.CheckState.Checked if checked else Qt.CheckState.Unchecked, Qt.ItemDataRole.CheckStateRole)
        item.setData(userData, Qt.ItemDataRole.UserRole)
        self.model().appendRow(item)

    def get_checked_items(self):
        checked = []
        model = self.model()
        if isinstance(model, QStandardItemModel):
            for i in range(model.rowCount()):
                item = model.item(i)
                if item and item.checkState() == Qt.CheckState.Checked:
                    checked.append(item.data(Qt.ItemDataRole.UserRole))
        return checked

    def clear(self):
        model = self.model()
        if isinstance(model, QStandardItemModel):
            model.clear()
            model.setColumnCount(1)
        else:
            super().clear()

class UnusedHighlightsDialog(QDialog):
    def __init__(self, highlights, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Unused Highlights")
        self.resize(520, 380)

        layout = QVBoxLayout(self)
        layout.addWidget(QLabel("Highlights in the database that are not in this workspace:"))

        self.list_widget = QListWidget()
        self.list_widget.setSelectionMode(QListWidget.SelectionMode.ExtendedSelection)
        for highlight in highlights:
            label = format_highlight_item_label(highlight)
            text_content = (highlight.get("text_content") or "[Empty Highlight]").strip()
            item = QListWidgetItem(label)
            item.setData(Qt.ItemDataRole.UserRole, highlight.get("id"))
            item.setToolTip(text_content)
            self.list_widget.addItem(item)

        layout.addWidget(self.list_widget)

        btn_layout = QHBoxLayout()
        btn_layout.addStretch()
        self.btn_add = QPushButton("Add to Workspace")
        self.btn_cancel = QPushButton("Cancel")
        self.btn_add.clicked.connect(self.accept)
        self.btn_cancel.clicked.connect(self.reject)
        btn_layout.addWidget(self.btn_add)
        btn_layout.addWidget(self.btn_cancel)
        layout.addLayout(btn_layout)

        if self.list_widget.count() > 0:
            self.list_widget.setCurrentRow(0)
        self.list_widget.itemDoubleClicked.connect(lambda _: self.accept())

    def get_selected_highlight_ids(self):
        return [item.data(Qt.ItemDataRole.UserRole) for item in self.list_widget.selectedItems()]