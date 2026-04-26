import os
from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QBrush
from PySide6.QtWidgets import (
    QCheckBox, QColorDialog, QDialog, QDialogButtonBox,
    QHBoxLayout, QLabel, QLineEdit, QListWidget,
    QListWidgetItem, QMessageBox, QPushButton, QScrollArea,
    QVBoxLayout, QWidget
)

# --- NEW: Dialog for Mass Assigning Tags ---
class MassAssignDialog(QDialog):
    def __init__(self, project_manager, tag_id, tag_name, parent=None):
        super().__init__(parent)
        self.project_manager = project_manager
        self.tag_id = tag_id
        
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
        
        # Get currently assigned docs so we can pre-check them
        assigned_docs = {d.get("doc_id") for d in project_manager.get_docs_for_tag(tag_id)}
        
        for doc_path in project_manager.pdfs:
            doc_name = os.path.basename(doc_path)
            cb = QCheckBox(doc_name)
            cb.setProperty("doc_path", doc_path) # Store the full path safely
            if doc_path in assigned_docs:
                cb.setChecked(True)
            self.scroll_layout.addWidget(cb)
            self.checkboxes.append(cb)
            
        self.scroll_layout.addStretch()
        
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.save_mass_assign)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        
    def save_mass_assign(self):
        for cb in self.checkboxes:
            doc_path = cb.property("doc_path")
            if cb.isChecked():
                self.project_manager.assign_tag_to_doc(doc_path, self.tag_id)
            else:
                self.project_manager.remove_tag_from_doc(doc_path, self.tag_id)
        self.accept()
# -------------------------------------------

class TagManagerDialog(QDialog):
    def __init__(self, project_manager, parent=None):
        super().__init__(parent)
        self.project_manager = project_manager
        self.selected_color = "#4CAF50"

        self.setWindowTitle("Tag Manager")
        self.setMinimumSize(720, 420)

        main_layout = QVBoxLayout(self)

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
        
        # 🔥 UPDATED: Mass Tag UI replaces the Combo Box
        assign_layout = QHBoxLayout()
        self.btn_mass_assign = QPushButton("🏷️ Mass Assign / Remove Docs...")
        self.btn_mass_assign.clicked.connect(self.open_mass_assign_dialog)
        self.btn_mass_assign.setEnabled(False) # Disabled until a tag is selected
        assign_layout.addWidget(self.btn_mass_assign)

        details_layout.addWidget(self.docs_label)
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

        self.load_tags()

    def _update_color_button_style(self):
        self.color_button.setStyleSheet(
            f"background-color: {self.selected_color}; color: white; border: 1px solid #666; "
            "padding: 6px 10px; border-radius: 4px;"
        )

    def pick_color(self):
        chosen = QColorDialog.getColor(QColor(self.selected_color), self, "Select Tag Color")
        if chosen.isValid():
            self.selected_color = chosen.name()
            self._update_color_button_style()

    def load_tags(self):
        self.tag_list.clear()
        self.doc_list.clear()
        self.btn_mass_assign.setEnabled(False)

        tags = self.project_manager.get_all_tags() if self.project_manager else []
        for tag in tags:
            name = tag.get("name", "")
            color_hex = tag.get("color") or "#808080"
            tag_id = tag.get("id")

            item = QListWidgetItem(name)
            item.setData(Qt.ItemDataRole.UserRole, tag_id)

            color = QColor(color_hex)
            if color.isValid():
                item.setBackground(QBrush(color))
                item.setForeground(QBrush(Qt.GlobalColor.white))

            self.tag_list.addItem(item)

        if self.tag_list.count() > 0:
            self.tag_list.setCurrentRow(0)
        else:
            self.docs_label.setText("Documents with selected tag")
            self.doc_list.addItem("No tags created yet.")

    def update_tag_details(self):
        self.doc_list.clear()
        
        item = self.tag_list.currentItem()
        if not item or not self.project_manager:
            self.docs_label.setText("Documents with selected tag")
            self.btn_mass_assign.setEnabled(False)
            return

        tag_name = item.text().strip() or "Selected Tag"
        tag_id = item.data(Qt.ItemDataRole.UserRole)
        self.docs_label.setText(f"Documents with '{tag_name}'")
        self.btn_mass_assign.setEnabled(True)

        docs = self.project_manager.get_docs_for_tag(tag_id)
        if not docs:
            self.doc_list.addItem("No documents use this tag.")
            return

        for doc in docs:
            doc_name = doc.get("doc_name") or "Unknown Document"
            doc_id = doc.get("doc_id") or ""
            entry = QListWidgetItem(doc_name)
            if doc_id:
                entry.setToolTip(doc_id)
                entry.setData(Qt.ItemDataRole.UserRole, doc_id)
            self.doc_list.addItem(entry)

    def open_mass_assign_dialog(self):
        item = self.tag_list.currentItem()
        if not item or not self.project_manager: return
        
        tag_id = item.data(Qt.ItemDataRole.UserRole)
        tag_name = item.text().strip()
        
        dialog = MassAssignDialog(self.project_manager, tag_id, tag_name, self)
        if dialog.exec():
            self.update_tag_details() # Refresh the list immediately after mass assigning!

    def add_tag(self):
        name = self.tag_name_input.text().strip()
        if not name:
            QMessageBox.warning(self, "Missing Tag Name", "Please enter a tag name.")
            return

        tag_id = self.project_manager.create_tag(name, self.selected_color)
        if tag_id is None:
            QMessageBox.warning(self, "Tag Error", "Could not create tag.")
            return

        self.tag_name_input.clear()
        self.load_tags()

    def delete_tag(self):
        item = self.tag_list.currentItem()
        if not item:
            QMessageBox.information(self, "No Selection", "Please select a tag to delete.")
            return

        tag_id = item.data(Qt.ItemDataRole.UserRole)
        if tag_id is None:
            return

        self.project_manager.delete_tag(tag_id)
        self.load_tags()

class TagAssignmentDialog(QDialog):
    # ... (Keep the rest of TagAssignmentDialog exactly as you had it, no changes needed) ...
    def __init__(self, project_manager, target_id, target_type, parent=None):
        super().__init__(parent)
        self.project_manager = project_manager
        self.target_id = target_id
        self.target_type = target_type
        self.checkboxes_by_tag_id = {}
        self.new_tag_color = "#4CAF50"

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
        self.scroll_layout.setContentsMargins(8, 8, 8, 8)
        self.scroll_layout.setSpacing(8)
        self.scroll.setWidget(self.scroll_widget)
        layout.addWidget(self.scroll)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self.save_assignments)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

        self.load_tag_checkboxes()

    def _update_new_tag_color_button_style(self):
        self.new_tag_color_button.setStyleSheet(
            f"background-color: {self.new_tag_color}; color: white; border: 1px solid #666; "
            "padding: 4px 8px; border-radius: 4px;"
        )

    def pick_new_tag_color(self):
        chosen = QColorDialog.getColor(QColor(self.new_tag_color), self, "Select Tag Color")
        if chosen.isValid():
            self.new_tag_color = chosen.name()
            self._update_new_tag_color_button_style()

    def create_tag_in_dialog(self):
        if not self.project_manager:
            return

        name = self.new_tag_name_input.text().strip()
        if not name:
            QMessageBox.warning(self, "Missing Tag Name", "Please enter a tag name.")
            return

        new_tag_id = self.project_manager.create_tag(name, self.new_tag_color)
        if new_tag_id is None:
            QMessageBox.warning(self, "Tag Error", "Could not create tag.")
            return

        self.new_tag_name_input.clear()
        self.load_tag_checkboxes()

        created_cb = self.checkboxes_by_tag_id.get(new_tag_id)
        if created_cb is not None:
            created_cb.setChecked(True)

    def load_tag_checkboxes(self):
        while self.scroll_layout.count():
            item = self.scroll_layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()

        self.checkboxes_by_tag_id.clear()

        all_tags = self.project_manager.get_all_tags() if self.project_manager else []
        assigned = []
        if self.project_manager:
            if self.target_type == "node":
                assigned = self.project_manager.get_tags_for_node(self.target_id)
            else:
                assigned = self.project_manager.get_tags_for_doc(self.target_id)

        assigned_ids = {t.get("id") for t in assigned}

        for tag in all_tags:
            tag_id = tag.get("id")
            tag_name = tag.get("name", "")
            tag_color = tag.get("color") or "#808080"

            cb = QCheckBox(tag_name)
            cb.setChecked(tag_id in assigned_ids)
            cb.setStyleSheet(f"QCheckBox {{ color: {tag_color}; font-weight: 600; }}")
            self.scroll_layout.addWidget(cb)
            self.checkboxes_by_tag_id[tag_id] = cb

        self.scroll_layout.addStretch()

    def save_assignments(self):
        if not self.project_manager:
            self.reject()
            return

        currently_assigned = []
        if self.target_type == "node":
            currently_assigned = self.project_manager.get_tags_for_node(self.target_id)
        else:
            currently_assigned = self.project_manager.get_tags_for_doc(self.target_id)

        current_ids = {t.get("id") for t in currently_assigned}

        for tag_id, checkbox in self.checkboxes_by_tag_id.items():
            should_have = checkbox.isChecked()
            has_now = tag_id in current_ids

            if should_have and not has_now:
                if self.target_type == "node":
                    self.project_manager.assign_tag_to_node(self.target_id, tag_id)
                else:
                    self.project_manager.assign_tag_to_doc(self.target_id, tag_id)
            elif not should_have and has_now:
                if self.target_type == "node":
                    self.project_manager.remove_tag_from_node(self.target_id, tag_id)
                else:
                    self.project_manager.remove_tag_from_doc(self.target_id, tag_id)

        self.accept()