from PyQt6.QtWidgets import QWidget, QVBoxLayout, QTextEdit, QLabel
import fitz

class NotesTab(QWidget):
    def __init__(self, parent=None, viewer=None):
        super().__init__(parent)
        self.viewer = viewer
        layout = QVBoxLayout(self)
        
        layout.addWidget(QLabel("Project Notes & Highlights"))
        self.notes_display = QTextEdit()
        self.notes_display.setReadOnly(True)
        self.notes_display.setStyleSheet("background-color: #1e1e1e; border: 1px solid #555;")
        layout.addWidget(self.notes_display)
        
    def refresh_notes(self):
        """Called automatically when AnnotationManager emits note_added."""
        if not self.viewer.doc: return
        
        all_notes = ""
        for i in range(len(self.viewer.doc)):
            page = self.viewer.doc.load_page(i)
            for annot in page.annots():
                if annot.info.get("title", "").startswith("UserNote"):
                    subject = annot.info.get("subject", "")
                    content = annot.info.get("content", "")
                    all_notes += f"Page {i+1}:\n"
                    all_notes += f"Highlight: \"{subject}\"\n"
                    if content:
                        all_notes += f"Note: {content}\n"
                    all_notes += "-"*40 + "\n"
                    
        self.notes_display.setPlainText(all_notes)