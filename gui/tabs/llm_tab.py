import re
from PyQt6.QtWidgets import (QWidget, QVBoxLayout, QTextEdit, QPushButton, QLabel, 
                             QComboBox, QHBoxLayout, QLineEdit, QMessageBox)
from PyQt6.QtCore import pyqtSignal, QThread
from PyQt6.QtGui import QTextCursor
from core.llm_manager import LocalLLMManager

class IndexWorker(QThread):
    progress = pyqtSignal(str)
    finished_indexing = pyqtSignal()
    
    def __init__(self, llm, filepath):
        super().__init__()
        self.llm = llm
        self.filepath = filepath
        
    def run(self):
        try:
            self.llm.index_document(self.filepath, progress_callback=lambda msg: self.progress.emit(msg))
        except Exception as e:
            self.progress.emit(f"Error: {str(e)}")
        self.finished_indexing.emit()

class ChatWorker(QThread):
    token_received = pyqtSignal(str)
    chat_completed = pyqtSignal(str)
    
    def __init__(self, llm, question, model):
        super().__init__()
        self.llm = llm
        self.question = question
        self.model = model
        
    def run(self):
        buffer = ""
        is_inside_mark = False
        full_response = ""
        
        def handle_chunk(chunk):
            nonlocal buffer, is_inside_mark, full_response
            full_response += chunk
            buffer += chunk
            
            # --- Stream Interceptor ---
            # This intercepts the raw streaming tokens and hides the `<mark>...</mark>` XML
            # so the user only sees clean conversational text and a nice UI badge.
            while True:
                if not is_inside_mark:
                    if "<mark" in buffer:
                        idx = buffer.find("<mark")
                        if idx > 0:
                            # Emit everything right up until the '<'
                            self.token_received.emit(buffer[:idx])
                        buffer = buffer[idx:]
                        is_inside_mark = True
                    else:
                        # Check if we have a partial tag at the end of the chunk (e.g. "<ma")
                        idx = buffer.rfind("<")
                        if idx != -1:
                            partial_tag = buffer[idx:]
                            if "<mark".startswith(partial_tag):
                                # It might be a mark tag starting, hold it in the buffer
                                if idx > 0:
                                    self.token_received.emit(buffer[:idx])
                                    buffer = buffer[idx:]
                                break
                            else:
                                # False alarm (just a regular '<'), emit it
                                self.token_received.emit(buffer)
                                buffer = ""
                                break
                        else:
                            # Safe to emit the whole chunk
                            self.token_received.emit(buffer)
                            buffer = ""
                            break
                else:
                    if "</mark>" in buffer:
                        # We reached the end of the hidden tag. Slice it off.
                        idx = buffer.find("</mark>") + len("</mark>")
                        buffer = buffer[idx:]
                        is_inside_mark = False
                        
                        # Output a clean, visual badge to the UI instead of the XML!
                        self.token_received.emit("\n🖍️ <i>[AI Highlight Applied]</i>\n")
                    else:
                        # Still inside the tag, wait for more streaming chunks
                        break

        self.llm.query(self.question, self.model, callback=handle_chunk)
        
        # Flush anything remaining in the buffer
        if buffer and not is_inside_mark:
            self.token_received.emit(buffer)
            
        # The full_response STILL contains the raw XML so the backend highlighting logic works!
        self.chat_completed.emit(full_response)

class LLMTab(QWidget):
    def __init__(self, parent=None, main_window=None):
        super().__init__(parent)
        self.main_window = main_window
        self.llm_manager = LocalLLMManager()
        
        layout = QVBoxLayout(self)
        
        # Top Status and Model Bar
        top_layout = QHBoxLayout()
        self.status_lbl = QLabel("🔴 Status: Unindexed")
        self.status_lbl.setStyleSheet("font-weight: bold; font-size: 14px;")
        top_layout.addWidget(self.status_lbl)
        top_layout.addStretch()
        
        self.model_combo = QComboBox()
        models = self.llm_manager.get_available_models()
        self.model_combo.addItems(models if models else ["llama3 (Local)"])
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
        self.chat_history.setStyleSheet("background-color: #1e1e1e; border: 1px solid #555; padding: 10px; font-size: 14px;")
        layout.addWidget(self.chat_history)
        
        input_layout = QHBoxLayout()
        self.chat_input = QLineEdit()
        self.chat_input.setPlaceholderText("Ask a question...")
        self.chat_input.setStyleSheet("padding: 8px; background: #333; border: 1px solid #555;")
        self.chat_input.returnPressed.connect(self.send_message)
        
        self.send_btn = QPushButton("Send")
        self.send_btn.setStyleSheet("background-color: #0078D7; padding: 8px 20px; font-weight: bold;")
        self.send_btn.clicked.connect(self.send_message)
        
        input_layout.addWidget(self.chat_input)
        input_layout.addWidget(self.send_btn)
        layout.addLayout(input_layout)
        
    def sync_file(self, filepath):
        self.current_filepath = filepath
        self.status_lbl.setText("🔴 Status: Unindexed")
        self.status_lbl.setStyleSheet("font-weight: bold; color: #ff4444;")
        self.chat_history.clear()

    def start_indexing(self):
        if not hasattr(self, 'current_filepath') or not self.current_filepath:
            QMessageBox.warning(self, "Error", "Please load a PDF document first.")
            return
            
        self.btn_index.setEnabled(False)
        self.index_worker = IndexWorker(self.llm_manager, self.current_filepath)
        self.index_worker.progress.connect(lambda msg: self.status_lbl.setText(f"🟡 {msg}"))
        self.index_worker.finished_indexing.connect(self._on_index_complete)
        self.index_worker.start()

    def _on_index_complete(self):
        self.btn_index.setEnabled(True)
        self.status_lbl.setText("🟢 Status: Ready (Indexed)")
        self.status_lbl.setStyleSheet("font-weight: bold; color: #00cc66;")

    def send_message(self):
        user_text = self.chat_input.text().strip()
        if not user_text: return
        
        self.chat_history.append(f"<b style='color:#55aaff'>You:</b> {user_text}<br><b style='color:#aaffaa'>LLM:</b> ")
        self.chat_input.clear()
        self.send_btn.setEnabled(False)
        
        model = self.model_combo.currentText()
        self.chat_worker = ChatWorker(self.llm_manager, user_text, model)
        self.chat_worker.token_received.connect(self._on_chat_token)
        self.chat_worker.chat_completed.connect(self._on_chat_complete)
        self.chat_worker.start()

    def _on_chat_token(self, token):
        # Insert text smoothly at the end of the window
        cursor = self.chat_history.textCursor()
        cursor.movePosition(QTextCursor.MoveOperation.End)
        
        # Because we passed raw HTML tags like <i> into the stream interceptor, 
        # we can safely use insertHtml to render the badges natively!
        if "<" in token and ">" in token:
            cursor.insertHtml(token)
        else:
            cursor.insertText(token)
            
        self.chat_history.setTextCursor(cursor)

    def _on_chat_complete(self, full_response):
        self.send_btn.setEnabled(True)
        self.chat_history.append("<br><br>") # Spacing for next message
        
        # Parse for AI Autonomous Actions silently in the background
        matches = re.finditer(r'<mark\s+quote="([^"]+)">([^<]+)</mark>', full_response, re.IGNORECASE)
        for match in matches:
            quote = match.group(1).strip()
            note = match.group(2).strip()
            self.main_window.add_ai_annotation(quote, note)