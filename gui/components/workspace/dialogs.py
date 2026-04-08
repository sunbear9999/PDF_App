import os
import uuid

from PyQt6.QtWidgets import (
    QDialog,
    QVBoxLayout,
    QHBoxLayout,
    QPushButton,
    QMessageBox,
    QFileDialog,
    QTextEdit,
    QScrollArea,
    QWidget,
    QFormLayout,
    QDialogButtonBox,
    QColorDialog,
)
from PyQt6.QtCore import Qt
from PyQt6.QtGui import QColor

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
        file_path, _ = QFileDialog.getSaveFileName(
            self,
            "Save Outline",
            "outline.txt",
            "Text Files (*.txt)",
        )
        if file_path:
            with open(file_path, "w", encoding="utf-8") as f:
                f.write(self.text_edit.toPlainText())
            QMessageBox.information(self, "Success", "Outline saved successfully!")

    def save_as_node(self):
        text = self.text_edit.toPlainText().strip()
        if not text:
            return

        self.workspace_view.save_state_for_undo()

        node_id = f"custom_{uuid.uuid4()}"
        node = Node(
            node_id,
            quote="",
            note=text,
            color="#1e4034",
            is_custom=True,
            width=350,
            height=250,
        )

        view_center = self.workspace_view.mapToScene(
            self.workspace_view.viewport().rect().center()
        )
        node.setPos(view_center)

        self.workspace_view.scene_obj.addItem(node)
        self.workspace_view.nodes[node_id] = node
        if self.workspace_view.controller:
            self.workspace_view.controller.mark_dirty("workspace")
        else:
            self.workspace_view.main_window.persistence_controller.mark_dirty("workspace")

        QMessageBox.information(
            self,
            "Success",
            "Outline added as a new node to the workspace!",
        )
        self.accept()


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
        file_path, _ = QFileDialog.getSaveFileName(
            self,
            "Save Analysis",
            "weakpoints.txt",
            "Text Files (*.txt)",
        )
        if file_path:
            with open(file_path, "w", encoding="utf-8") as f:
                f.write(self.text_edit.toPlainText())
            QMessageBox.information(self, "Success", "Analysis saved successfully!")

    def save_as_node(self):
        text = self.text_edit.toPlainText().strip()
        if not text:
            return

        self.workspace_view.save_state_for_undo()

        node_id = f"custom_{uuid.uuid4()}"
        node = Node(
            node_id,
            quote="",
            note=text,
            color="#4a0e28",
            is_custom=True,
            width=350,
            height=250,
        )

        view_center = self.workspace_view.mapToScene(
            self.workspace_view.viewport().rect().center()
        )
        node.setPos(view_center)

        self.workspace_view.scene_obj.addItem(node)
        self.workspace_view.nodes[node_id] = node
        if self.workspace_view.controller:
            self.workspace_view.controller.mark_dirty("workspace")
        else:
            self.workspace_view.main_window.persistence_controller.mark_dirty("workspace")

        QMessageBox.information(
            self,
            "Success",
            "Analysis added as a new node to the workspace!",
        )
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
            btn.setStyleSheet(
                f"background-color: {color}; border: 1px solid #aaaaaa; border-radius: 4px;"
            )
            btn.clicked.connect(lambda checked, p=pdf, b=btn: self.pick_color(p, b))
            self.buttons[pdf] = btn
            self.form.addRow(os.path.basename(pdf), btn)

        scroll.setWidget(self.scroll_widget)
        layout.addWidget(scroll)

        btns = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        btns.accepted.connect(self.accept)
        btns.rejected.connect(self.reject)
        layout.addWidget(btns)

    def pick_color(self, pdf, btn):
        initial = QColor(self.pdf_colors.get(pdf, "#2b2b2b"))
        color = QColorDialog.getColor(
            initial, self, f"Select Color for {os.path.basename(pdf)}"
        )
        if color.isValid():
            self.pdf_colors[pdf] = color.name()
            btn.setStyleSheet(
                f"background-color: {color.name()}; border: 1px solid #aaaaaa; border-radius: 4px;"
            )

    def get_colors(self):
        return self.pdf_colors

