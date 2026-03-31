from PyQt6.QtWidgets import QWidget, QVBoxLayout, QTextEdit, QPushButton, QLabel, QComboBox, QHBoxLayout, QLineEdit

class LLMTab(QWidget):
    def __init__(self, parent=None, main_window=None):
        super().__init__(parent)
        self.main_window = main_window
        layout = QVBoxLayout(self)
        
        # Top Status and Model Bar
        top_layout = QHBoxLayout()
        self.status_lbl = QLabel("🔴 Status: Unindexed")
        self.status_lbl.setStyleSheet("font-weight: bold; font-size: 14px;")
        top_layout.addWidget(self.status_lbl)
        
        top_layout.addStretch()
        self.model_combo = QComboBox()
        self.model_combo.addItems(["llama3 (Local)", "mistral (Local)", "phi3 (Local)"])
        top_layout.addWidget(QLabel("Model:"))
        top_layout.addWidget(self.model_combo)
        layout.addLayout(top_layout)
        
        self.btn_index = QPushButton("Index Document for Search (RAG)")
        self.btn_index.setStyleSheet("background-color: #444; padding: 8px; margin-bottom: 10px;")
        self.btn_index.clicked.connect(self.start_indexing)
        layout.addWidget(self.btn_index)
        
        # Chat Interface
        self.chat_history = QTextEdit()
        self.chat_history.setReadOnly(True)
        self.chat_history.setStyleSheet("background-color: #1e1e1e; border: 1px solid #555; padding: 10px;")
        layout.addWidget(self.chat_history)
        
        input_layout = QHBoxLayout()
        self.chat_input = QLineEdit()
        self.chat_input.setPlaceholderText("Ask your local LLM a question about this PDF...")
        self.chat_input.setStyleSheet("padding: 8px; background: #333; border: 1px solid #555;")
        self.chat_input.returnPressed.connect(self.send_message)
        
        self.send_btn = QPushButton("Send")
        self.send_btn.setStyleSheet("background-color: #0078D7; padding: 8px 20px; font-weight: bold;")
        self.send_btn.clicked.connect(self.send_message)
        
        input_layout.addWidget(self.chat_input)
        input_layout.addWidget(self.send_btn)
        layout.addLayout(input_layout)
        
    def sync_file(self, filepath):
        self.status_lbl.setText("🔴 Status: Unindexed")
        self.status_lbl.setStyleSheet("font-weight: bold; color: #ff4444;")
        self.chat_history.clear()

    def start_indexing(self):
        self.status_lbl.setText("🟡 Status: Indexing...")
        self.status_lbl.setStyleSheet("font-weight: bold; color: #ffaa00;")
        # TODO: Paste your ChromaDB / LangChain backend indexing thread here!
        # Once complete, call: self.status_lbl.setText("🟢 Status: Ready")

    def send_message(self):
        user_text = self.chat_input.text()
        if not user_text.strip(): return
        
        self.chat_history.append(f"<b>You:</b> {user_text}")
        self.chat_input.clear()
        
        # TODO: Send query to Ollama and append response
        # self.chat_history.append(f"<b>LLM:</b> {response}")