from PySide6.QtWidgets import (QGraphicsView, QGraphicsScene, QMenu, QMessageBox, 
                             QInputDialog, QFrame, QLabel, QVBoxLayout,
                             QHBoxLayout, QComboBox, QPushButton, QDialog,
                             QScrollArea, QWidget, QFormLayout, QDialogButtonBox, 
                             QColorDialog, QFileDialog, QTextEdit,QCheckBox,QSlider,QLabel,QTabWidget,QListWidget,QListWidgetItem)
from PySide6.QtCore import Qt, QRectF
from PySide6.QtGui import QColor, QPen, QBrush, QFont, QPainter, QImage, QStandardItemModel, QStandardItem
import uuid
import os
from gui.components.workspace_items import Node
class OutlineDialog(QDialog):
    def __init__(self, outline_text, workspace_view, parent=None):
        super().__init__(parent or workspace_view)
        self.workspace_view = workspace_view
        self.setWindowTitle("AI Generated Outline")
        self.setMinimumSize(600, 700)
        
        layout = QVBoxLayout(self)
        
        self.text_edit = QTextEdit()
        self.text_edit.setPlainText(outline_text)
        layout.addWidget(self.text_edit)
        
        btn_layout = QHBoxLayout()
        
        self.btn_save_txt = QPushButton("💾 Save as .txt")
        self.btn_save_txt.clicked.connect(self.save_as_txt)
        
        self.btn_save_node = QPushButton("📌 Save as Workspace Node")
        self.btn_save_node.clicked.connect(self.save_as_node)
        
        self.btn_close = QPushButton("Close")
        self.btn_close.clicked.connect(self.reject)
        
        self.buttons = [self.btn_save_txt, self.btn_save_node, self.btn_close]
        
        btn_layout.addWidget(self.btn_save_txt)
        btn_layout.addWidget(self.btn_save_node)
        btn_layout.addStretch()
        btn_layout.addWidget(self.btn_close)
        
        layout.addLayout(btn_layout)
        
    def save_as_txt(self):
        file_path, _ = QFileDialog.getSaveFileName(self, "Save Outline", "outline.txt", "Text Files (*.txt)")
        if file_path:
            with open(file_path, 'w', encoding='utf-8') as f:
                f.write(self.text_edit.toPlainText())
            QMessageBox.information(self, "Success", "Outline saved successfully!")
            
    def save_as_node(self):
        text = self.text_edit.toPlainText().strip()
        if not text: return
        
        self.workspace_view.save_state_for_undo()
        
        node_id = f"custom_{uuid.uuid4()}"
        node = Node(node_id, quote="", note=text, color="#1e4034", is_custom=True, width=350, height=250)
        
        view_center = self.workspace_view.mapToScene(self.workspace_view.viewport().rect().center())
        node.setPos(view_center)
        
        self.workspace_view.scene_obj.addItem(node)
        self.workspace_view.nodes[node_id] = node
        self.workspace_view.main_window.project_manager.mark_dirty("workspace")
        
        QMessageBox.information(self, "Success", "Outline added as a new node to the workspace!")
        self.accept()

class DeclutterSettingsDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Declutter Options")
        self.setMinimumWidth(350)
        
        layout = QVBoxLayout(self)
        
        # Enable AI Checkbox
        self.cb_enable_ai = QCheckBox("Enable Semantic Clustering")
        self.cb_enable_ai.setChecked(True)
        self.cb_enable_ai.toggled.connect(self._toggle_slider)
        layout.addWidget(self.cb_enable_ai)
        
        # Strength Slider
        self.slider_layout = QVBoxLayout()
        self.lbl_strength = QLabel("Clustering Strength: Medium")
        self.slider_layout.addWidget(self.lbl_strength)
        
        self.slider = QSlider(Qt.Orientation.Horizontal)
        self.slider.setRange(0, 100)
        self.slider.setValue(50) # Default to 50%
        self.slider.valueChanged.connect(self._update_label)
        self.slider_layout.addWidget(self.slider)
        
        layout.addLayout(self.slider_layout)
        layout.addSpacing(15)
        
        # Buttons
        btn_layout = QHBoxLayout()
        self.btn_run = QPushButton("Run Declutter")
        self.btn_cancel = QPushButton("Cancel")
        self.btn_run.clicked.connect(self.accept)
        self.btn_cancel.clicked.connect(self.reject)
        
        btn_layout.addWidget(self.btn_cancel)
        btn_layout.addWidget(self.btn_run)
        layout.addLayout(btn_layout)

        # Apply theme colors if available from parent
        try:
            if hasattr(parent, 'main_window') and hasattr(parent.main_window, 'theme_manager'):
                theme = parent.main_window.theme_manager.get_theme()
                self.setStyleSheet(f"background-color: {theme['bg_main']}; color: {theme['text_main']};")
                self.btn_run.setStyleSheet(f"background-color: {theme['accent']}; color: white; border-radius: 4px; padding: 6px;")
                self.btn_cancel.setStyleSheet(f"background-color: {theme['bg_panel']}; color: {theme['text_main']}; border: 1px solid {theme['border']}; border-radius: 4px; padding: 6px;")
        except:
            pass

    def _toggle_slider(self, checked):
        self.slider.setEnabled(checked)
        self.lbl_strength.setEnabled(checked)

    def _update_label(self, value):
        if value < 30: text = "Low (Loose groupings)"
        elif value < 70: text = "Medium (Balanced)"
        else: text = "High (Tight clusters)"
        self.lbl_strength.setText(f"Clustering Strength: {text}")

    def get_settings(self):
        # Returns (use_ai: bool, strength_multiplier: float from 0.0 to 2.0)
        use_ai = self.cb_enable_ai.isChecked()
        # Map 0-100 slider to a 0.0 to 2.0 multiplier
        strength = self.slider.value() / 50.0 
        return use_ai, strength
    
class WeakpointsDialog(QDialog):
    def __init__(self, weakpoints_text, workspace_view, parent=None):
        super().__init__(parent or workspace_view)
        self.workspace_view = workspace_view
        self.setWindowTitle("AI Weakpoint Analysis")
        self.setMinimumSize(600, 700)
        
        layout = QVBoxLayout(self)
        
        self.text_edit = QTextEdit()
        self.text_edit.setPlainText(weakpoints_text)
        layout.addWidget(self.text_edit)
        
        btn_layout = QHBoxLayout()
        
        self.btn_save_txt = QPushButton("💾 Save as .txt")
        self.btn_save_txt.clicked.connect(self.save_as_txt)
        
        self.btn_save_node = QPushButton("📌 Save as Workspace Node")
        self.btn_save_node.clicked.connect(self.save_as_node)
        
        self.btn_close = QPushButton("Close")
        self.btn_close.clicked.connect(self.reject)
        
        self.buttons = [self.btn_save_txt, self.btn_save_node, self.btn_close]
        
        btn_layout.addWidget(self.btn_save_txt)
        btn_layout.addWidget(self.btn_save_node)
        btn_layout.addStretch()
        btn_layout.addWidget(self.btn_close)
        
        layout.addLayout(btn_layout)
        
    def save_as_txt(self):
        file_path, _ = QFileDialog.getSaveFileName(self, "Save Analysis", "weakpoints.txt", "Text Files (*.txt)")
        if file_path:
            with open(file_path, 'w', encoding='utf-8') as f:
                f.write(self.text_edit.toPlainText())
            QMessageBox.information(self, "Success", "Analysis saved successfully!")
            
    def save_as_node(self):
        text = self.text_edit.toPlainText().strip()
        if not text: return
        
        self.workspace_view.save_state_for_undo()
        
        node_id = f"custom_{uuid.uuid4()}"
        node = Node(node_id, quote="", note=text, color="#4a0e28", is_custom=True, width=350, height=250)
        
        view_center = self.workspace_view.mapToScene(self.workspace_view.viewport().rect().center())
        node.setPos(view_center)
        
        self.workspace_view.scene_obj.addItem(node)
        self.workspace_view.nodes[node_id] = node
        self.workspace_view.main_window.project_manager.mark_dirty("workspace")
        
        QMessageBox.information(self, "Success", "Analysis added as a new node to the workspace!")
        self.accept()




class ColorOrganizerDialog(QDialog):
    def __init__(self, pdfs, tags, current_pdf_colors, current_tag_colors, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Organize by Color")
        self.setMinimumSize(400, 450)
        
        self.pdf_colors = dict(current_pdf_colors)
        self.tag_colors = dict(current_tag_colors)
        
        layout = QVBoxLayout(self)
        
        self.tab_widget = QTabWidget()
        layout.addWidget(self.tab_widget)
        
        # --- PDF Tab ---
        self.pdf_tab = QWidget()
        pdf_layout = QVBoxLayout(self.pdf_tab)
        pdf_scroll = QScrollArea()
        pdf_scroll.setWidgetResizable(True)
        pdf_scroll_widget = QWidget()
        pdf_form = QFormLayout(pdf_scroll_widget)
        
        for pdf in pdfs:
            btn = QPushButton()
            btn.setFixedSize(80, 30)
            color = self.pdf_colors.get(pdf, "#2b2b2b")
            btn.setStyleSheet(f"background-color: {color}; border: 1px solid #aaaaaa; border-radius: 4px;")
            btn.clicked.connect(lambda checked, p=pdf, b=btn: self.pick_pdf_color(p, b))
            
            full_name = os.path.basename(pdf)
            display_name = (full_name[:16] + "\u2026") if len(full_name) > 18 else full_name
            pdf_form.addRow(display_name, btn)
            
        pdf_scroll.setWidget(pdf_scroll_widget)
        pdf_layout.addWidget(pdf_scroll)
        self.tab_widget.addTab(self.pdf_tab, "By PDF")
        
        # --- Tag Tab ---
        self.tag_tab = QWidget()
        tag_layout = QVBoxLayout(self.tag_tab)
        tag_scroll = QScrollArea()
        tag_scroll.setWidgetResizable(True)
        tag_scroll_widget = QWidget()
        tag_form = QFormLayout(tag_scroll_widget)
        
        for tag in tags:
            tag_name = tag.get("name")
            if not tag_name: continue
            
            btn = QPushButton()
            btn.setFixedSize(80, 30)
            color = self.tag_colors.get(tag_name, "#808080")
            btn.setStyleSheet(f"background-color: {color}; border: 1px solid #aaaaaa; border-radius: 4px;")
            btn.clicked.connect(lambda checked, t=tag_name, b=btn: self.pick_tag_color(t, b))
            
            tag_form.addRow(tag_name, btn)
            
        tag_scroll.setWidget(tag_scroll_widget)
        tag_layout.addWidget(tag_scroll)
        self.tab_widget.addTab(self.tag_tab, "By Tag")
        
        btns = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        btns.accepted.connect(self.accept)
        btns.rejected.connect(self.reject)
        layout.addWidget(btns)
        
    def pick_pdf_color(self, pdf, btn):
        initial = QColor(self.pdf_colors.get(pdf, "#2b2b2b"))
        color = QColorDialog.getColor(initial, self, f"Select Color for {os.path.basename(pdf)}")
        if color.isValid():
            self.pdf_colors[pdf] = color.name()
            btn.setStyleSheet(f"background-color: {color.name()}; border: 1px solid #aaaaaa; border-radius: 4px;")
            
    def pick_tag_color(self, tag_name, btn):
        initial = QColor(self.tag_colors.get(tag_name, "#808080"))
        color = QColorDialog.getColor(initial, self, f"Select Color for {tag_name}")
        if color.isValid():
            self.tag_colors[tag_name] = color.name()
            btn.setStyleSheet(f"background-color: {color.name()}; border: 1px solid #aaaaaa; border-radius: 4px;")
            
    def get_result(self):
        """Returns a tuple of (mode, colors_dict) based on which tab was active when saved."""
        if self.tab_widget.currentIndex() == 0:
            return "pdf", self.pdf_colors
        else:
            return "tag", self.tag_colors
class ContextFilterDialog(QDialog):
    def __init__(self, project_manager, target_nodes, parent=None):
        super().__init__(parent)
        self.project_manager = project_manager
        self.target_nodes = target_nodes
        self.setWindowTitle("Context Optimization Required")
        self.setMinimumSize(650, 450)
        
        layout = QVBoxLayout(self)
        
        warning_lbl = QLabel(
            "<b>⚠️ Project Size Threshold Reached</b><br>"
            "To prevent AI context bloat and hallucinations across many documents, you must link your nodes to specific tags. "
            "The AI will only search documents that share the same tags as the node it is analyzing."
        )
        warning_lbl.setWordWrap(True)
        warning_lbl.setStyleSheet("color: #e67e22; font-size: 13px; margin-bottom: 10px;")
        layout.addWidget(warning_lbl)
        
        h_layout = QHBoxLayout()
        
        # Left Panel: Selected Nodes List
        left_layout = QVBoxLayout()
        left_layout.addWidget(QLabel("<b>Target Nodes:</b>"))
        self.node_list = QListWidget()
        for n in target_nodes:
            # Display a snippet of the node text
            display_text = n.quote[:50] + "..." if n.quote else n.note[:50] + "..."
            item = QListWidgetItem(display_text)
            item.setData(Qt.ItemDataRole.UserRole, n.node_id)
            self.node_list.addItem(item)
            
        self.node_list.currentItemChanged.connect(self._on_node_selected)
        left_layout.addWidget(self.node_list)
        h_layout.addLayout(left_layout, 2)
        
        # Right Panel: Tag Assignment & Document Manager
        right_layout = QVBoxLayout()
        
        self.btn_manage_tags = QPushButton("🏷️ Open Global Tag Manager")
        self.btn_manage_tags.setToolTip("Create tags and assign them to your PDFs here.")
        self.btn_manage_tags.clicked.connect(self._open_tag_manager)
        self.btn_manage_tags.setStyleSheet("padding: 8px; font-weight: bold;")
        right_layout.addWidget(self.btn_manage_tags)
        
        right_layout.addWidget(QLabel("<b>Assign Tags to Selected Node:</b>"))
        
        self.tag_scroll = QScrollArea()
        self.tag_scroll.setWidgetResizable(True)
        self.tag_widget = QWidget()
        self.tag_layout = QVBoxLayout(self.tag_widget)
        self.tag_layout.setAlignment(Qt.AlignmentFlag.AlignTop)
        self.tag_scroll.setWidget(self.tag_widget)
        right_layout.addWidget(self.tag_scroll, 1)
        
        h_layout.addLayout(right_layout, 1)
        layout.addLayout(h_layout)
        
        btn_box = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        btn_box.accepted.connect(self.accept)
        btn_box.rejected.connect(self.reject)
        btn_box.button(QDialogButtonBox.StandardButton.Ok).setText("Proceed with Tags")
        layout.addWidget(btn_box)
        
        self.checkboxes = {}
        self._load_tags()
        
        if self.node_list.count() > 0:
            self.node_list.setCurrentRow(0)

    def _open_tag_manager(self):
        from gui.components.dialogs.tag_manager_dialog import TagManagerDialog
        dlg = TagManagerDialog(self.project_manager, self)
        dlg.exec()
        self._load_tags() # Refresh available tags in case the user created new ones

    def _load_tags(self):
        # Clear existing checkboxes
        while self.tag_layout.count():
            item = self.tag_layout.takeAt(0)
            if item.widget(): item.widget().deleteLater()
        self.checkboxes.clear()
        
        tags = self.project_manager.get_all_tags()
        for tag in tags:
            cb = QCheckBox(tag.get("name", ""))
            cb.setStyleSheet(f"color: {tag.get('color', '#ffffff')}; font-weight: bold;")
            cb.clicked.connect(self._on_tag_toggled)
            self.tag_layout.addWidget(cb)
            self.checkboxes[tag.get("id")] = cb
            
        self._on_node_selected(self.node_list.currentItem())

    def _on_node_selected(self, item):
        if not item: return
        node_id = item.data(Qt.ItemDataRole.UserRole)
        
        # Fetch current tags for this specific node
        assigned = self.project_manager.get_tags_for_node(node_id)
        assigned_ids = [t.get("id") for t in assigned]
        
        for tag_id, cb in self.checkboxes.items():
            cb.blockSignals(True) # Prevent triggering the toggle event while updating UI
            cb.setChecked(tag_id in assigned_ids)
            cb.blockSignals(False)

    def _on_tag_toggled(self):
        item = self.node_list.currentItem()
        if not item: return
        node_id = item.data(Qt.ItemDataRole.UserRole)
        
        # Save state to the database live as they click
        for tag_id, cb in self.checkboxes.items():
            if cb.isChecked():
                self.project_manager.assign_tag_to_node(node_id, tag_id)
            else:
                self.project_manager.remove_tag_from_node(node_id, tag_id)