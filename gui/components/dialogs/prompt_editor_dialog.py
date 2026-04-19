from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QTextEdit,
    QVBoxLayout,
)


class PromptEditorDialog(QDialog):
    def __init__(self, prompt_manager, parent=None):
        super().__init__(parent)
        self.prompt_manager = prompt_manager

        self.setWindowTitle("Custom Prompt Editor")
        self.setMinimumSize(700, 500)

        layout = QVBoxLayout(self)

        self.tool_selector = QComboBox()
        self.tool_selector.addItems(list(self.prompt_manager.DEFAULT_PROMPTS.keys()))
        layout.addWidget(self.tool_selector)

        self.warning_label = QLabel(
            "WARNING: Changing system prompts may break AI formatting. Ensure JSON instructions remain intact."
        )
        self.warning_label.setWordWrap(True)
        self.warning_label.setStyleSheet(
            "color: #b00020; background-color: rgba(176, 0, 32, 0.08);"
            "border: 1px solid rgba(176, 0, 32, 0.35); border-radius: 6px; padding: 8px;"
        )
        layout.addWidget(self.warning_label)

        self.prompt_editor = QTextEdit()
        layout.addWidget(self.prompt_editor)

        button_layout = QHBoxLayout()
        button_layout.addStretch()

        self.restore_button = QPushButton("Restore Default")
        self.restore_button.setStyleSheet(
            "background-color: #b00020; color: white; border: none; padding: 8px 12px; border-radius: 4px;"
        )
        self.restore_button.clicked.connect(self._on_restore_default)

        self.cancel_button = QPushButton("Cancel")
        self.cancel_button.clicked.connect(self.reject)

        self.save_button = QPushButton("Save")
        self.save_button.clicked.connect(self._on_save)

        button_layout.addWidget(self.restore_button)
        button_layout.addWidget(self.cancel_button)
        button_layout.addWidget(self.save_button)
        layout.addLayout(button_layout)

        self.tool_selector.currentTextChanged.connect(self._load_prompt_for_tool)

        if self.tool_selector.count() > 0:
            self._load_prompt_for_tool(self.tool_selector.currentText())

    def _load_prompt_for_tool(self, tool_name):
        self.prompt_editor.setPlainText(self.prompt_manager.get_prompt(tool_name))

    def _on_restore_default(self):
        tool_name = self.tool_selector.currentText()
        if not tool_name:
            return

        self.prompt_manager.restore_default(tool_name)
        self._load_prompt_for_tool(tool_name)

    def _on_save(self):
        tool_name = self.tool_selector.currentText()
        if not tool_name:
            QMessageBox.warning(self, "No Tool Selected", "Please select a tool before saving.")
            return

        prompt_text = self.prompt_editor.toPlainText().strip()
        self.prompt_manager.save_prompt(tool_name, prompt_text)
        QMessageBox.information(self, "Saved", f"Prompt for '{tool_name}' was saved successfully.")
        self.accept()
