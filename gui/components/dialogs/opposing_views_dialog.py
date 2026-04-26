from PySide6.QtWidgets import QDialog, QVBoxLayout, QTextEdit, QPushButton, QLabel, QHBoxLayout

class OpposingViewsDialog(QDialog):
    def __init__(self, target_quote, matches, parent=None):
        super().__init__(parent)
        self.setWindowTitle("⚖️ Opposing Views Found")
        self.resize(600, 500)
        
        layout = QVBoxLayout(self)
        
        # Original Quote
        layout.addWidget(QLabel("<b>Original Statement:</b>"))
        quote_box = QTextEdit()
        quote_box.setPlainText(f'"{target_quote}"')
        quote_box.setReadOnly(True)
        quote_box.setMaximumHeight(80)
        quote_box.setStyleSheet("background: rgba(100, 100, 100, 0.1); font-style: italic;")
        layout.addWidget(quote_box)
        
        layout.addWidget(QLabel(f"<b>Top {len(matches)} Contradictory Excerpts:</b>"))
        
        # Results
        results_box = QTextEdit()
        results_box.setReadOnly(True)
        
        html = ""
        for i, match in enumerate(matches, 1):
            doc = match['doc_name']
            pg = match['page'] + 1
            text = match['text']
            html += f"<p><b>{i}. {doc} (Page {pg})</b><br>{text}</p><hr>"
            
        results_box.setHtml(html)
        layout.addWidget(results_box)
        
        # Close Button
        btn_layout = QHBoxLayout()
        btn_layout.addStretch()
        btn_close = QPushButton("Close")
        btn_close.clicked.connect(self.accept)
        btn_layout.addWidget(btn_close)
        layout.addLayout(btn_layout)