import os
from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QBrush
from PySide6.QtWidgets import (
    QCheckBox, QColorDialog, QDialog, QDialogButtonBox,
    QComboBox, QHBoxLayout, QLabel, QLineEdit, QListWidget,
    QListWidgetItem, QMessageBox, QPushButton, QScrollArea,
    QVBoxLayout, QWidget
)
from core.events.event_bus import EventBus
from core.events.domains.metadata_events import TagEvent, TagEventPayload, TagIntent, TagPayload
from gui.managers.dialog_manager import exec_as_modal, get_for_widget

class MassAssignDialog(QDialog):
    def __init__(self, tag_id, tag_name, all_pdfs, assigned_docs, parent=None):
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        self.tag_id = tag_id
        self.bus = EventBus.get_instance()
        self.setWindowTitle(f"Mass Assign Tag: {tag_name}")
        self.setMinimumSize(400, 350)

        layout = QVBoxLayout(self)
        layout.addWidget(QLabel(f"Select documents to tag with <b>{tag_name}</b>:"))

        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll_widget = QWidget()
        self.scroll_layout = QVBoxLayout(self.scroll_widget)
        self.scroll.setWidget(self.scroll_widget)
        layout.addWidget(self.scroll)

        self.checkboxes = []
        assigned_paths = {d.get("doc_id") for d in assigned_docs}

        for doc_path in all_pdfs:
            cb = QCheckBox(os.path.basename(doc_path))
            cb.setProperty("doc_path", doc_path)
            if doc_path in assigned_paths: cb.setChecked(True)
            self.scroll_layout.addWidget(cb)
            self.checkboxes.append(cb)

        self.scroll_layout.addStretch()

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.save_mass_assign)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def save_mass_assign(self):
        assign, remove = [], []
        for cb in self.checkboxes:
            (assign if cb.isChecked() else remove).append(cb.property("doc_path"))
        self.bus.tag_action_requested.emit(
            TagIntent.MASS_ASSIGN,
            TagPayload(tag_id=self.tag_id, assign_docs=assign, remove_docs=remove),
        )
        self.accept()

class TagManagerDialog(QDialog):
    def __init__(self, parent=None): # REMOVED PM
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        self.bus = EventBus.get_instance()
        self.tag_service = getattr(parent, "tag_app_service", None)
        self.selected_color = "#4CAF50"
        self.all_pdfs_cache = self.tag_service.list_pdf_paths() if self.tag_service else []

        self.setWindowTitle("Tag Manager")
        self.setMinimumSize(720, 420)

        main_layout = QVBoxLayout(self)
        # ... [KEEP ALL UI LAYOUT CODE EXACTLY AS IT WAS] ...
        top_row = QHBoxLayout()
        self.tag_name_input = QLineEdit()
        self.tag_name_input.setPlaceholderText("Enter tag name")
        self.color_button = QPushButton("Pick Color")
        self.color_button.clicked.connect(self.pick_color)
        self.add_button = QPushButton("Add Tag")
        self.add_button.clicked.connect(self.add_tag)
        top_row.addWidget(self.tag_name_input, 1)
        top_row.addWidget(self.color_button)
        top_row.addWidget(self.add_button)
        main_layout.addLayout(top_row)
        self._update_color_button_style()
        middle_row = QHBoxLayout()
        self.tag_list = QListWidget()
        self.tag_list.currentItemChanged.connect(self.update_tag_details)
        middle_row.addWidget(self.tag_list, 1)
        details_layout = QVBoxLayout()
        self.docs_label = QLabel("Documents with selected tag")
        self.docs_label.setWordWrap(True)
        self.doc_list = QListWidget()
        parent_row = QHBoxLayout()
        parent_row.addWidget(QLabel("Nested under"))
        self.parent_combo = QComboBox()
        self.parent_combo.setEnabled(False)
        self.btn_set_parent = QPushButton("Set Parent")
        self.btn_set_parent.setEnabled(False)
        self.btn_set_parent.clicked.connect(self.set_tag_parent)
        parent_row.addWidget(self.parent_combo, 1)
        parent_row.addWidget(self.btn_set_parent)
        assign_layout = QHBoxLayout()
        self.btn_mass_assign = QPushButton("🏷️ Mass Assign / Remove Docs...")
        self.btn_mass_assign.clicked.connect(self.open_mass_assign_dialog)
        self.btn_mass_assign.setEnabled(False)
        self.btn_auto_tag = QPushButton("⚙️ Auto-tag Sources…")
        self.btn_auto_tag.clicked.connect(self.open_auto_tag_dialog)
        self.btn_auto_tag.setEnabled(False)
        assign_layout.addWidget(self.btn_mass_assign)
        assign_layout.addWidget(self.btn_auto_tag)
        details_layout.addWidget(self.docs_label)
        details_layout.addLayout(parent_row)
        details_layout.addWidget(self.doc_list, 1)
        details_layout.addLayout(assign_layout)
        middle_row.addLayout(details_layout, 1)
        main_layout.addLayout(middle_row, 1)
        self.info_label = QLabel("Select a tag and click delete to remove it globally.")
        self.info_label.setWordWrap(True)
        main_layout.addWidget(self.info_label)
        bottom_row = QHBoxLayout()
        self.delete_button = QPushButton("Delete Selected Tag")
        self.delete_button.clicked.connect(self.delete_tag)
        self.close_button = QPushButton("Close")
        self.close_button.clicked.connect(self.accept)
        bottom_row.addWidget(self.delete_button)
        bottom_row.addStretch()
        bottom_row.addWidget(self.close_button)
        main_layout.addLayout(bottom_row)

        self.bus.tag_data_updated.connect(self._handle_tag_data)
        self.load_tags()

    def _handle_tag_data(self, event: TagEvent, payload: TagEventPayload):
        """Synchronous receiver for tag bus events."""
        if event == TagEvent.ALL_TAGS:
            selected_id = self._selected_tag_id()
            self._tags_snapshot = list(payload["tags"])
            self.tag_list.clear()
            self.doc_list.clear()
            self.btn_mass_assign.setEnabled(False)
            self.btn_auto_tag.setEnabled(False)
            self.parent_combo.setEnabled(False)
            self.btn_set_parent.setEnabled(False)
            for tag in payload["tags"]:
                item = QListWidgetItem(tag.get("name", ""))
                item.setData(Qt.ItemDataRole.UserRole, tag.get("id"))
                color = QColor(tag.get("color", "#808080"))
                if color.isValid():
                    item.setBackground(QBrush(color))
                    item.setForeground(QBrush(Qt.GlobalColor.white))
                self.tag_list.addItem(item)
            restored = self._find_tag_row(selected_id)
            if self.tag_list.count() > 0: self.tag_list.setCurrentRow(max(restored, 0))
            else: self.doc_list.addItem("No tags created yet.")

        elif event == TagEvent.TAG_DETAILS:
            self.doc_list.clear()
            docs = payload["docs"]
            if not docs:
                self.doc_list.addItem("No documents use this tag.")
                return
            for doc in docs:
                entry = QListWidgetItem(doc.get("doc_name") or "Unknown Document")
                entry.setData(Qt.ItemDataRole.UserRole, doc.get("doc_id") or "")
                self.doc_list.addItem(entry)
            self._cached_assigned_docs = docs # Cache for mass assign dialog

        elif event == TagEvent.PARENT_UPDATED and payload.get("error"):
            QMessageBox.warning(self, "Cannot Nest Tag", payload["error"])

    def _update_color_button_style(self):
        self.color_button.setStyleSheet(f"background-color: {self.selected_color}; color: white; border: 1px solid #666; padding: 6px 10px; border-radius: 4px;")

    def pick_color(self):
        chosen = QColorDialog.getColor(QColor(self.selected_color), self, "Select Tag Color")
        if chosen.isValid():
            self.selected_color = chosen.name()
            self._update_color_button_style()

    def load_tags(self):
        self.bus.tag_action_requested.emit(TagIntent.FETCH_ALL, TagPayload())

    def update_tag_details(self):
        item = self.tag_list.currentItem()
        if not item: return
        tag_id = item.data(Qt.ItemDataRole.UserRole)
        self.docs_label.setText(f"Documents with '{item.text().strip()}'")
        self.btn_mass_assign.setEnabled(True)
        self.btn_auto_tag.setEnabled(True)
        self._populate_parent_choices(tag_id)
        self.bus.tag_action_requested.emit(TagIntent.FETCH_DETAILS, TagPayload(tag_id=tag_id))

    def _populate_parent_choices(self, tag_id):
        current = next(
            (tag for tag in getattr(self, "_tags_snapshot", []) if tag.get("id") == tag_id),
            {},
        )
        self.parent_combo.clear()
        self.parent_combo.addItem("No parent (top level)", None)
        for tag in getattr(self, "_tags_snapshot", []):
            if tag.get("id") != tag_id:
                self.parent_combo.addItem(tag.get("name", ""), tag.get("id"))
        index = self.parent_combo.findData(current.get("parent_tag_id"))
        self.parent_combo.setCurrentIndex(max(index, 0))
        self.parent_combo.setEnabled(True)
        self.btn_set_parent.setEnabled(True)

    def set_tag_parent(self):
        tag_id = self._selected_tag_id()
        if tag_id is None:
            return
        self.bus.tag_action_requested.emit(
            TagIntent.SET_PARENT,
            TagPayload(tag_id=tag_id, parent_tag_id=self.parent_combo.currentData()),
        )

    def _selected_tag_id(self):
        item = self.tag_list.currentItem()
        return item.data(Qt.ItemDataRole.UserRole) if item else None

    def _find_tag_row(self, tag_id):
        for row in range(self.tag_list.count()):
            if self.tag_list.item(row).data(Qt.ItemDataRole.UserRole) == tag_id:
                return row
        return -1

    def open_mass_assign_dialog(self):
        item = self.tag_list.currentItem()
        if not item: return
        dialog = MassAssignDialog(item.data(Qt.ItemDataRole.UserRole), item.text().strip(), self.all_pdfs_cache, getattr(self, "_cached_assigned_docs", []), self)
        dialog.accepted.connect(self.update_tag_details)
        dm = get_for_widget(self)
        if dm:
            dm.show_instance(dialog)
        else:
            if exec_as_modal(dialog):
                self.update_tag_details()

    def open_auto_tag_dialog(self):
        item = self.tag_list.currentItem()
        if not item:
            return
        from gui.components.dialogs.auto_tag_dialog import AutoTagDialog
        dialog = AutoTagDialog(
            getattr(self, "_tags_snapshot", []),
            item.data(Qt.ItemDataRole.UserRole),
            self,
        )
        dialog.finished.connect(self.update_tag_details)
        manager = get_for_widget(self)
        if manager:
            manager.show_instance(dialog)
        else:
            exec_as_modal(dialog)

    def add_tag(self):
        name = self.tag_name_input.text().strip()
        if not name: return QMessageBox.warning(self, "Missing Tag Name", "Please enter a tag name.")
        self.bus.tag_action_requested.emit(TagIntent.CREATE, TagPayload(name=name, color=self.selected_color))
        self.tag_name_input.clear()

    def delete_tag(self):
        item = self.tag_list.currentItem()
        if not item: return QMessageBox.information(self, "No Selection", "Please select a tag to delete.")
        self.bus.tag_action_requested.emit(TagIntent.DELETE, TagPayload(tag_id=item.data(Qt.ItemDataRole.UserRole)))

    def closeEvent(self, event):
        try:
            self.bus.tag_data_updated.disconnect(self._handle_tag_data)
        except (RuntimeError, TypeError):
            pass
        super().closeEvent(event)


class TagAssignmentDialog(QDialog):
    def __init__(self, target_id, target_type, parent=None): # REMOVED PM
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        self.bus = EventBus.get_instance()
        self.target_id = target_id
        self.target_type = target_type
        self.checkboxes_by_tag_id = {}
        self.new_tag_color = "#4CAF50"

        # ... [KEEP UI BUILD CODE EXACTLY AS WAS] ...
        target_label = "Node" if target_type == "node" else "Document"
        self.setWindowTitle(f"Manage Tags for {target_label}")
        self.setMinimumSize(360, 420)
        layout = QVBoxLayout(self)
        create_row = QHBoxLayout()
        self.new_tag_name_input = QLineEdit()
        self.new_tag_name_input.setPlaceholderText("New tag name")
        self.new_tag_color_button = QPushButton("Pick Color")
        self.new_tag_color_button.clicked.connect(self.pick_new_tag_color)
        self.new_tag_add_button = QPushButton("Add Tag")
        self.new_tag_add_button.clicked.connect(self.create_tag_in_dialog)
        create_row.addWidget(self.new_tag_name_input, 1)
        create_row.addWidget(self.new_tag_color_button)
        create_row.addWidget(self.new_tag_add_button)
        layout.addLayout(create_row)
        self._update_new_tag_color_button_style()
        layout.addWidget(QLabel("Select tags to assign:"))
        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll_widget = QWidget()
        self.scroll_layout = QVBoxLayout(self.scroll_widget)
        self.scroll.setWidget(self.scroll_widget)
        layout.addWidget(self.scroll)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.save_assignments)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

        self.bus.tag_data_updated.connect(self._handle_data)
        self.load_tag_checkboxes()

    def _update_new_tag_color_button_style(self):
        self.new_tag_color_button.setStyleSheet(f"background-color: {self.new_tag_color}; color: white; border: 1px solid #666; padding: 4px 8px; border-radius: 4px;")

    def pick_new_tag_color(self):
        chosen = QColorDialog.getColor(QColor(self.new_tag_color), self, "Select Tag Color")
        if chosen.isValid():
            self.new_tag_color = chosen.name()
            self._update_new_tag_color_button_style()

    def create_tag_in_dialog(self):
        name = self.new_tag_name_input.text().strip()
        if not name: return QMessageBox.warning(self, "Missing Tag Name", "Please enter a tag name.")
        self.bus.tag_action_requested.emit(TagIntent.CREATE, TagPayload(name=name, color=self.new_tag_color))
        self.new_tag_name_input.clear()
        self.load_tag_checkboxes()

    def load_tag_checkboxes(self):
        self.bus.tag_action_requested.emit(
            TagIntent.FETCH_TARGET_ASSIGNMENTS,
            TagPayload(target_id=self.target_id, target_type=self.target_type),
        )

    def _handle_data(self, event: TagEvent, payload: TagEventPayload):
        if event != TagEvent.TARGET_ASSIGNMENTS: return
        while self.scroll_layout.count():
            item = self.scroll_layout.takeAt(0)
            if item.widget(): item.widget().deleteLater()

        self.checkboxes_by_tag_id.clear()
        assigned_ids = {t.get("id") for t in payload["assigned"]}
        self.original_assignments = assigned_ids # Track delta

        for tag in payload["all_tags"]:
            tag_id = tag.get("id")
            cb = QCheckBox(tag.get("name", ""))
            cb.setChecked(tag_id in assigned_ids)
            cb.setStyleSheet(f"QCheckBox {{ color: {tag.get('color', '#808080')}; font-weight: 600; }}")
            self.scroll_layout.addWidget(cb)
            self.checkboxes_by_tag_id[tag_id] = cb
        self.scroll_layout.addStretch()

    def save_assignments(self):
        assign, remove = [], []
        for tag_id, cb in self.checkboxes_by_tag_id.items():
            if cb.isChecked() and tag_id not in self.original_assignments: assign.append(tag_id)
            elif not cb.isChecked() and tag_id in self.original_assignments: remove.append(tag_id)

        self.bus.tag_action_requested.emit(
            TagIntent.UPDATE_ASSIGNMENTS,
            TagPayload(
                target_id=self.target_id,
                target_type=self.target_type,
                assign_tags=assign,
                remove_tags=remove,
            ),
        )
        self.accept()
