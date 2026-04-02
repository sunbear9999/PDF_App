# gui/tabs/llm_tab.py
import re
import os
from PyQt6.QtWidgets import (QWidget, QVBoxLayout, QTextEdit, QPushButton, QLabel, 
                             QComboBox, QHBoxLayout, QLineEdit, QMessageBox, QListWidget, QListWidgetItem)
from PyQt6.QtCore import pyqtSignal, QThread, Qt
from PyQt6.QtGui import QTextCursor
from core.llm_manager import LocalLLMManager

class IndexWorker(QThread):
    progress = pyqtSignal(str)
    finished_indexing = pyqtSignal(bool, str)
    
    def __init__(self, llm, filepaths):
        super().__init__()
        self.llm = llm
        self.filepaths = filepaths
        
    def run(self):
        try:
            self.llm.index_documents(self.filepaths, progress_callback=lambda msg: self.progress.emit(msg))
            self.finished_indexing.emit(True, "")
        except Exception as e:
            self.finished_indexing.emit(False, str(e))

class ChatWorker(QThread):
    token_received = pyqtSignal(str)
    chat_completed = pyqtSignal(str)
    
    def __init__(self, llm, question, model, allowed_docs):
        super().__init__()
        self.llm = llm
        self.question = question
        self.model = model
        self.allowed_docs = allowed_docs
        
    def run(self):
        buffer = ""
        is_inside_mark = False
        full_response = ""
        
        def handle_chunk(chunk):
            nonlocal buffer, is_inside_mark, full_response
            full_response += chunk
            buffer += chunk
            
            while True:
                if not is_inside_mark:
                    match = re.search(r'<highlight>', buffer, re.IGNORECASE)
                    if match:
                        start_idx = match.start()
                        if start_idx > 0:
                            self.token_received.emit(buffer[:start_idx])
                        buffer = buffer[start_idx:]
                        is_inside_mark = True
                    else:
                        last_less_than = buffer.rfind("<")
                        if last_less_than != -1:
                            partial = buffer[last_less_than:].lower()
                            if "<highlight".startswith(partial):
                                if last_less_than > 0:
                                    self.token_received.emit(buffer[:last_less_than])
                                    buffer = buffer[last_less_than:]
                                break
                        if buffer:
                            self.token_received.emit(buffer)
                            buffer = ""
                        break
                else:
                    match = re.search(r'</highlight>', buffer, re.IGNORECASE)
                    if match:
                        end_idx = match.end()
                        buffer = buffer[end_idx:]
                        is_inside_mark = False
                    else:
                        break

        try:
            self.llm.query(self.question, self.model, allowed_docs=self.allowed_docs, callback=handle_chunk)
        except Exception as e:
            handle_chunk(f"\n[System Error: {str(e)}]")
        
        if buffer:
            self.token_received.emit(buffer)
            
        self.chat_completed.emit(full_response)

class LLMTab(QWidget):
    def __init__(self, parent=None, main_window=None):
        super().__init__(parent)
        self.main_window = main_window
        self.llm_manager = LocalLLMManager() 
        
        layout = QVBoxLayout(self)
        
        top_layout = QHBoxLayout()
        self.status_lbl = QLabel("🔴 Status: Unindexed")
        self.status_lbl.setStyleSheet("font-weight: bold; font-size: 14px;")
        top_layout.addWidget(self.status_lbl)
        top_layout.addStretch()
        
        self.model_combo = QComboBox()
        models = self.llm_manager.get_available_models()
        self.model_combo.addItems(models if models else ["llama3 (Local)"])
        
        self.btn_refresh_models = QPushButton("🔄")
        self.btn_refresh_models.setToolTip("Refresh Model List")
        self.btn_refresh_models.setFixedWidth(35)
        self.btn_refresh_models.setStyleSheet("background-color: #444; font-weight: bold;")
        self.btn_refresh_models.clicked.connect(self.refresh_models)

        top_layout.addWidget(QLabel("Model:"))
        top_layout.addWidget(self.model_combo)
        top_layout.addWidget(self.btn_refresh_models)
        layout.addLayout(top_layout)

        layout.addWidget(QLabel("Select PDFs to Include in AI Search:"))
        self.pdf_list = QListWidget()
        self.pdf_list.setFixedHeight(100)
        self.pdf_list.setStyleSheet("background-color: #333; border: 1px solid #555; padding: 5px;")
        layout.addWidget(self.pdf_list)
        
        self.btn_index = QPushButton("Build / Rebuild Search Index")
        self.btn_index.setStyleSheet("background-color: #444; padding: 6px; font-weight: bold; margin-bottom: 10px;")
        self.btn_index.clicked.connect(self.start_indexing)
        layout.addWidget(self.btn_index)
        
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
        
    def refresh_project_ui(self):
        """Correctly initializes the Vector DB context when a project is loaded."""
        self.pdf_list.clear()
        if self.main_window.project_manager.pdfs:
            for pdf_path in self.main_window.project_manager.pdfs:
                doc_name = os.path.basename(pdf_path)
                item = QListWidgetItem(doc_name)
                item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
                item.setCheckState(Qt.CheckState.Checked) 
                self.pdf_list.addItem(item)
                
        proj_path = self.main_window.project_manager.project_filepath
        if proj_path and hasattr(self, 'llm_manager'):
            self.llm_manager.set_project_database(proj_path)
            if self.llm_manager.collection and self.llm_manager.collection.count() > 0:
                self.status_lbl.setText("🟢 Status: Ready (Vector DB Loaded)")
                self.status_lbl.setStyleSheet("font-weight: bold; color: #00cc66;")
            else:
                self.status_lbl.setText("🔴 Status: Needs Indexing")
                self.status_lbl.setStyleSheet("font-weight: bold; color: #ffaa00;")

    def refresh_models(self):
        """Re-polls the Ollama server safely using the dedicated refresh button."""
        models = self.llm_manager.get_available_models()
        if models:
            self.model_combo.clear()
            self.model_combo.addItems(models)
            
            if hasattr(self.main_window, '_trigger_background_preload'):
                self.main_window._trigger_background_preload()

    def start_indexing(self):
        paths_to_index = self.main_window.project_manager.pdfs
        if not paths_to_index:
            QMessageBox.warning(self, "Error", "No PDFs available in project to index.")
            return
            
        self.btn_index.setEnabled(False)
        self.idx_worker = IndexWorker(self.llm_manager, paths_to_index)
        self.idx_worker.progress.connect(lambda msg: self.status_lbl.setText(f"🟡 {msg}"))
        self.idx_worker.finished_indexing.connect(self._on_index_complete)
        self.idx_worker.start()

    def _on_index_complete(self, success, error_msg):
        self.btn_index.setEnabled(True)
        if success:
            self.status_lbl.setText("🟢 Status: Ready (Indexed to Vector DB)")
            self.status_lbl.setStyleSheet("font-weight: bold; color: #00cc66;")
        else:
            self.status_lbl.setText(f"❌ Indexing Failed: {error_msg}")
            self.status_lbl.setStyleSheet("font-weight: bold; color: #ff4444;")

    def send_message(self):
        user_text = self.chat_input.text().strip()
        if not user_text: return
        
        self.chat_history.append(f"<b style='color:#55aaff'>You:</b> {user_text}<br><b style='color:#aaffaa'>LLM:</b> ")
        self.chat_input.clear()
        
        self.send_btn.setText("⏳ Generating...")
        self.send_btn.setEnabled(False)
        self.chat_input.setEnabled(False)
        self.status_lbl.setText("⚙️ AI is generating response...")
        self.status_lbl.setStyleSheet("font-weight: bold; color: #ffaa00;")
        
        allowed_docs = []
        for i in range(self.pdf_list.count()):
            item = self.pdf_list.item(i)
            if item.checkState() == Qt.CheckState.Checked:
                allowed_docs.append(item.text())
        
        model = self.model_combo.currentText()
        self.chat_worker = ChatWorker(self.llm_manager, user_text, model, allowed_docs)
        self.chat_worker.token_received.connect(self._on_chat_token)
        self.chat_worker.chat_completed.connect(self._on_chat_complete)
        self.chat_worker.start()

    def _on_chat_token(self, token):
        cursor = self.chat_history.textCursor()
        cursor.movePosition(QTextCursor.MoveOperation.End)
        cursor.insertText(token)
        self.chat_history.setTextCursor(cursor)

    def _on_chat_complete(self, full_response):
        self.send_btn.setText("Send")
        self.send_btn.setEnabled(True)
        self.chat_input.setEnabled(True)
        self.status_lbl.setText("🟢 Status: Ready")
        self.status_lbl.setStyleSheet("font-weight: bold; color: #00cc66;")
        
        self.chat_history.append("<br>")
        
        blocks = []
        for match in re.finditer(r'<highlight>([\s\S]*?)(?:</highlight>|$)', full_response, re.IGNORECASE):
            inner_text = match.group(1)
            
            doc_m = re.search(r'<doc>\s*([\s\S]*?)\s*(?:</doc>|$)', inner_text, re.IGNORECASE)
            quote_m = re.search(r'<quote>\s*([\s\S]*?)\s*(?:</quote>|$)', inner_text, re.IGNORECASE)
            note_m = re.search(r'<note>\s*([\s\S]*?)\s*(?:</note>|$)', inner_text, re.IGNORECASE)
            
            if quote_m:
                blocks.append({
                    'doc': doc_m.group(1).strip() if doc_m else None,
                    'quote': quote_m.group(1).strip(),
                    'note': note_m.group(1).strip() if note_m else ""
                })
        
        if not blocks and "<highlight" in full_response.lower():
            self.chat_history.append(f"⚠️ <b style='color:#ffaa00'>[AI Formatting Warning: The AI provided malformed syntax. Raw output is shown above.]</b><br>")

        allowed_paths = []
        for i in range(self.pdf_list.count()):
            item = self.pdf_list.item(i)
            if item.checkState() == Qt.CheckState.Checked:
                doc_name = item.text()
                for p in self.main_window.project_manager.pdfs:
                    if os.path.basename(p) == doc_name:
                        allowed_paths.append(p)
                        break

        for b in blocks:
            quote = b['quote']
            note = b['note']
            target_doc = b['doc']
                
            success = self.main_window.add_ai_annotation(quote, note, target_doc_name=target_doc, allowed_paths=allowed_paths)
            display_quote = quote[:60] + "..." if len(quote) > 60 else quote
            
            doc_label = f" in {target_doc}" if target_doc else ""
            if success:
                self.chat_history.append(f"🖍️ <b style='color:#00cc66'>[AI Highlight Applied{doc_label}]</b>: <i>\"{display_quote}\"</i><br>")
            else:
                self.chat_history.append(f"⚠️ <b style='color:#ff4444'>[Failed to find exact quote{doc_label}]</b>: <i>\"{display_quote}\"</i><br>")
            
            if note:
                self.chat_history.append(f"<span style='color:#ccc'>Note: {note}</span><br><br>")
        
        self.chat_history.append("<br>")