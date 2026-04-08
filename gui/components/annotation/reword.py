from PyQt6.QtCore import Qt, pyqtSignal, QThread
from PyQt6.QtGui import QTextCursor
from PyQt6.QtWidgets import QDialog, QVBoxLayout, QTextEdit, QPushButton


class RewordWorker(QThread):
    token_received = pyqtSignal(str)
    finished = pyqtSignal()

    def __init__(self, llm_manager, model, text, parent=None):
        super().__init__(parent)
        self.llm_manager = llm_manager
        self.model = model
        self.text = text

    def run(self):
        system_prompt = (
            "You are an expert editor. Rewrite the following text to make it easier "
            "to understand and follow, while keeping all crucial information intact. "
            "Respond ONLY with the reworded text. Do not include introductory phrases."
        )
        try:
            def handle_chunk(chunk):
                self.token_received.emit(chunk)

            self.llm_manager.query(
                question=f"\"{self.text}\"",
                selected_model=self.model,
                allowed_docs=[],
                callback=handle_chunk,
                rag_enabled=False,
                use_agents=False,
                custom_system_prompt=system_prompt,
            )
        except Exception as e:
            self.token_received.emit(f"\n[Error: {str(e)}]")
        finally:
            self.finished.emit()


class RewordDialog(QDialog):
    def __init__(self, original_text, llm_manager, model, parent=None):
        super().__init__(parent)
        self.setWindowTitle("AI Reword")
        self.resize(450, 300)
        self.setWindowFlags(self.windowFlags() | Qt.WindowType.WindowStaysOnTopHint)

        layout = QVBoxLayout(self)

        self.text_edit = QTextEdit()
        self.text_edit.setReadOnly(True)
        self.text_edit.setStyleSheet(
            "background-color: #1e1e1e; color: #ddd; font-size: 14px; padding: 10px; border: 1px solid #444;"
        )
        layout.addWidget(self.text_edit)

        self.close_btn = QPushButton("Close")
        self.close_btn.setStyleSheet("background-color: #444; padding: 5px;")
        self.close_btn.clicked.connect(self.accept)
        layout.addWidget(self.close_btn)

        self.worker = RewordWorker(llm_manager, model, original_text, self)
        self.worker.token_received.connect(self.append_text)
        self.worker.start()

    def append_text(self, token):
        cursor = self.text_edit.textCursor()
        cursor.movePosition(QTextCursor.MoveOperation.End)
        cursor.insertText(token)
        self.text_edit.setTextCursor(cursor)

