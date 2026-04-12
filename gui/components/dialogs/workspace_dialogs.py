from PyQt6.QtWidgets import (QGraphicsView, QGraphicsScene, QMenu, QMessageBox, 
                             QInputDialog, QFrame, QLabel, QVBoxLayout,
                             QHBoxLayout, QComboBox, QPushButton, QDialog,
                             QScrollArea, QWidget, QFormLayout, QDialogButtonBox, 
                             QColorDialog, QFileDialog, QTextEdit,QCheckBox,QSlider,QLabel)
from PyQt6.QtCore import Qt, QRectF
from PyQt6.QtGui import QColor, QPen, QBrush, QFont, QPainter, QImage, QStandardItemModel, QStandardItem

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


class PDFColorDialog(QDialog):
    def __init__(self, pdfs, current_colors, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Organize by PDF Colors")
        self.setMinimumSize(350, 400)
        self.pdf_colors = dict(current_colors)
        
        layout = QVBoxLayout(self)
        
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        self.scroll_widget = QWidget()
        self.form = QFormLayout(self.scroll_widget)
        
        self.buttons = {}
        for pdf in pdfs:
            btn = QPushButton()
            btn.setFixedSize(80, 30)
            color = self.pdf_colors.get(pdf, "#2b2b2b")
            btn.setStyleSheet(f"background-color: {color}; border: 1px solid #aaaaaa; border-radius: 4px;")
            btn.clicked.connect(lambda checked, p=pdf, b=btn: self.pick_color(p, b))
            self.buttons[pdf] = btn
            self.form.addRow(os.path.basename(pdf), btn)
            
        scroll.setWidget(self.scroll_widget)
        layout.addWidget(scroll)
        
        btns = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        btns.accepted.connect(self.accept)
        btns.rejected.connect(self.reject)
        layout.addWidget(btns)
        
    def pick_color(self, pdf, btn):
        initial = QColor(self.pdf_colors.get(pdf, "#2b2b2b"))
        color = QColorDialog.getColor(initial, self, f"Select Color for {os.path.basename(pdf)}")
        if color.isValid():
            self.pdf_colors[pdf] = color.name()
            btn.setStyleSheet(f"background-color: {color.name()}; border: 1px solid #aaaaaa; border-radius: 4px;")
            
    def get_colors(self):
        return self.pdf_colors