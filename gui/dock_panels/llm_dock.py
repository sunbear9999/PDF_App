# gui/dock_panels/llm_dock.py
import re
import os
from PyQt6.QtWidgets import (QDockWidget, QWidget, QVBoxLayout, QTextEdit, QPushButton, QLabel,
                             QComboBox, QHBoxLayout, QLineEdit, QMessageBox, QListWidget, QListWidgetItem, QCheckBox)
from PyQt6.QtCore import pyqtSignal, QThread, Qt
from PyQt6.QtGui import QTextCursor
from core.llm_manager import LocalLLMManager

class IndexWorker(QThread):
    progress = pyqtSignal(str)
    finished_indexing = pyqtSignal(bool, str)

    def __init__(self, llm, filepaths, parent=None):
        super().__init__(parent)
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
    agent_update = pyqtSignal(str)

    def __init__(self, llm, question, model, allowed_docs, use_agents, rag_enabled=True, document_map=None, custom_system_prompt=None, existing_highlights=None, parent=None):
        super().__init__(parent)
        self.llm = llm
        self.question = question
        self.model = model
        self.allowed_docs = allowed_docs
        self.use_agents = use_agents
        self.rag_enabled = rag_enabled
        self.document_map = document_map
        self.custom_system_prompt = custom_system_prompt
        self.existing_highlights = existing_highlights or []

    def run(self):
        buffer = ""
        full_response = ""
        hide_future_output = False

        def handle_chunk(chunk):
            nonlocal buffer, hide_future_output, full_response

            if chunk.startswith("@@AGENT@@"):
                clean_msg = chunk.replace("@@AGENT@@", "").strip()
                self.agent_update.emit(clean_msg)
                return

            full_response += chunk

            if hide_future_output:
                return

            buffer += chunk

            if "--- HIGHLIGHTS ---" in buffer:
                hide_future_output = True
                visible_part, _ = buffer.split("--- HIGHLIGHTS ---", 1)
                buffer = visible_part

            while '\n' in buffer:
                line, buffer = buffer.split('\n', 1)
                if "%%QUOTE" not in line:
                    self.token_received.emit(line + '\n')

        try:
            if hasattr(self.llm, 'route_query_intent'):
                intent = self.llm.route_query_intent(self.model, self.question)
            else:
                intent = "LOGICAL_ANALYSIS"

            if intent == "GENERAL_CHAT":
                self.rag_enabled = False
                self.agent_update.emit("Routing query to general chat mode...")
            elif intent == "FACT_RETRIEVAL":
                self.rag_enabled = True
                self.agent_update.emit("Routing query to fact retrieval mode...")
            else:
                self.rag_enabled = True
                self.agent_update.emit("Routing query to logical analysis mode...")

            self.llm.query(
                self.question,
                self.model,
                allowed_docs=self.allowed_docs,
                callback=handle_chunk,
                rag_enabled=self.rag_enabled,
                use_agents=self.use_agents,
                custom_system_prompt=self.custom_system_prompt,
                existing_highlights=self.existing_highlights,
                document_map=self.document_map
            )
        except Exception as e:
            handle_chunk(f"\n[System Error: {str(e)}]")

        if buffer and "%%QUOTE" not in buffer and not hide_future_output:
            self.token_received.emit(buffer)

        self.chat_completed.emit(full_response)

class LLMDockWidget(QDockWidget):
    def __init__(self, main_window=None, parent=None):
        super().__init__("LLM Chat", parent)
        self.main_window = main_window
        self.llm_manager = LocalLLMManager()
        self.current_existing_quotes = []
        self.theme = None
        self.llm_tools_locked = False

        self.setAllowedAreas(Qt.DockWidgetArea.RightDockWidgetArea)
        self.setFeatures(QDockWidget.DockWidgetFeature.DockWidgetClosable |
                         QDockWidget.DockWidgetFeature.DockWidgetMovable |
                         QDockWidget.DockWidgetFeature.DockWidgetFloatable)

        widget = QWidget()
        self.setWidget(widget)
        layout = QVBoxLayout(widget)

        top_layout = QHBoxLayout()
        self.status_lbl = QLabel("🔴 Status: Unindexed")
        top_layout.addWidget(self.status_lbl)
        top_layout.addStretch()

        self.agent_checkbox = QCheckBox("Use Advanced Agents (Slower, High Detail)")
        self.agent_checkbox.setChecked(True)
        top_layout.addWidget(self.agent_checkbox)

        self.model_combo = QComboBox()
        models = self.llm_manager.get_available_models()
        self.model_combo.addItems(models if models else ["llama3 (Local)"])

        self.btn_refresh_models = QPushButton("🔄")
        self.btn_refresh_models.setToolTip("Refresh Model List")
        self.btn_refresh_models.setFixedWidth(35)
        self.btn_refresh_models.clicked.connect(self.refresh_models)

        top_layout.addWidget(QLabel("Model:"))
        top_layout.addWidget(self.model_combo)
        top_layout.addWidget(self.btn_refresh_models)
        layout.addLayout(top_layout)

        layout.addWidget(QLabel("Select PDFs to Include in AI Search:"))
        self.pdf_list = QListWidget()
        self.pdf_list.setFixedHeight(100)
        layout.addWidget(self.pdf_list)

        self.btn_index = QPushButton("Build / Rebuild Search Index")
        self.btn_index.clicked.connect(self.start_indexing)
        layout.addWidget(self.btn_index)

        self.chat_history = QTextEdit()
        self.chat_history.setReadOnly(True)
        layout.addWidget(self.chat_history)

        input_layout = QHBoxLayout()
        self.chat_input = QLineEdit()
        self.chat_input.setPlaceholderText("Ask a question...")
        self.chat_input.returnPressed.connect(self.send_message)

        self.send_btn = QPushButton("Send")
        self.send_btn.clicked.connect(self.send_message)

        input_layout.addWidget(self.chat_input)
        input_layout.addWidget(self.send_btn)
        layout.addLayout(input_layout)

    def update_theme(self, theme):
        self.theme = theme
        # Remove inline styles

    def refresh_project_ui(self):
        self.pdf_list.clear()
        for pdf_path in self.main_window.pdf_controller.get_pdf_paths():
            doc_name = os.path.basename(pdf_path)
            item = QListWidgetItem(doc_name)
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            item.setCheckState(Qt.CheckState.Checked)
            self.pdf_list.addItem(item)

        if self.llm_tools_locked:
            return

        proj_path = self.main_window.pdf_controller.project_filepath
        if proj_path and hasattr(self, 'llm_manager'):
            self.llm_manager.set_project_database(proj_path)
            if self.llm_manager.collection and self.llm_manager.collection.count() > 0:
                self.status_lbl.setText("🟢 Status: Ready (Vector DB Loaded)")
            else:
                self.status_lbl.setText("🔴 Status: Needs Indexing")

    def refresh_models(self):
        models = self.llm_manager.get_available_models()
        if models:
            self.model_combo.clear()
            self.model_combo.addItems(models)

            if hasattr(self.main_window, '_trigger_background_preload'):
                self.main_window._trigger_background_preload()

    def lock_llm_tools(self):
        self.llm_tools_locked = True
        self.btn_index.setEnabled(False)
        self.btn_refresh_models.setEnabled(False)
        self.send_btn.setEnabled(False)
        self.chat_input.setEnabled(False)
        self.agent_checkbox.setEnabled(False)
        self.status_lbl.setText("⏳ AI indexing in progress — chat disabled until complete.")

    def unlock_llm_tools(self):
        self.llm_tools_locked = False
        self.btn_index.setEnabled(True)
        self.btn_refresh_models.setEnabled(True)
        self.send_btn.setEnabled(True)
        self.chat_input.setEnabled(True)
        self.agent_checkbox.setEnabled(True)
        self.status_lbl.setText("🟢 Status: Ready")

    def start_indexing(self):
        paths_to_index = self.main_window.pdf_controller.get_pdf_paths()
        if not paths_to_index:
            QMessageBox.warning(self, "Error", "No PDFs available in project to index.")
            return

        self.btn_index.setEnabled(False)
        self.btn_refresh_models.setEnabled(False)
        self.idx_worker = IndexWorker(self.llm_manager, paths_to_index, parent=self)
        self.idx_worker.progress.connect(self._update_index_progress, Qt.ConnectionType.QueuedConnection)
        self.idx_worker.finished_indexing.connect(self._on_index_complete, Qt.ConnectionType.QueuedConnection)

        # Register worker with thread manager for centralized lifecycle management
        self.main_window.thread_manager.register_worker("index_worker", self.idx_worker)

        self.idx_worker.start()

    def _update_index_progress(self, msg):
        self.status_lbl.setText(f"🟡 {msg}")

    def _on_index_complete(self, success, error_msg):
        self.btn_index.setEnabled(True)
        self.btn_refresh_models.setEnabled(True)
        if success:
            self.status_lbl.setText("🟢 Status: Ready (Indexed to Vector DB)")
        else:
            self.status_lbl.setText(f"❌ Indexing Failed: {error_msg}")

    def send_message(self):
        if self.llm_tools_locked:
            QMessageBox.warning(self, "AI Indexing", "AI chat is temporarily disabled while background indexing is running.")
            return
        user_text = self.chat_input.text().strip()
        if not user_text: return

        self.chat_history.append(f"<b>You:</b> {user_text}<br>")
        self.chat_input.clear()

        self.send_btn.setText("⏳ Processing...")
        self.send_btn.setEnabled(False)
        self.chat_input.setEnabled(False)

        use_agents = self.agent_checkbox.isChecked()
        if use_agents:
            self.status_lbl.setText("⚙️ AI is planning task...")
        else:
            self.status_lbl.setText("⚙️ AI is generating response...")

        allowed_docs = []
        allowed_paths = []
        for i in range(self.pdf_list.count()):
            item = self.pdf_list.item(i)
            if item.checkState() == Qt.CheckState.Checked:
                doc_name = item.text()
                allowed_docs.append(doc_name)
                for p in self.main_window.pdf_controller.get_pdf_paths():
                    if os.path.basename(p) == doc_name:
                        allowed_paths.append(p)
                        break

        existing_quotes = []
        for path in allowed_paths:
            doc = self.main_window.pdf_controller.get_doc(path)
            if doc:
                for i in range(len(doc)):
                    try:
                        for annot in doc.load_page(i).annots():
                            if annot.info and annot.info.get("subject"):
                                existing_quotes.append(annot.info.get("subject"))
                    except: pass

        self.current_existing_quotes = existing_quotes

        model = self.model_combo.currentText()

        active_pdf = None
        if allowed_paths:
            active_pdf = allowed_paths[0]
        elif self.main_window.current_file_path:
            active_pdf = self.main_window.current_file_path

        document_map = None
        missing_argument_map_note = False
        if active_pdf:
            document_map = self.main_window.pdf_controller.get_document_map(active_pdf)
            if not document_map:
                missing_argument_map_note = True

        if missing_argument_map_note and use_agents:
            self.status_lbl.setText(
                "⚠️ No argument map exists for the active PDF. Using standard agentic RAG; generate one to improve future reasoning."
            )

        self.chat_worker = ChatWorker(
            self.llm_manager,
            user_text,
            model,
            allowed_docs,
            use_agents,
            rag_enabled=True,
            document_map=document_map,
            custom_system_prompt=None,
            existing_highlights=existing_quotes,
            parent=self
        )
        self.chat_worker.token_received.connect(self._on_chat_token, Qt.ConnectionType.QueuedConnection)
        self.chat_worker.agent_update.connect(self._on_agent_update, Qt.ConnectionType.QueuedConnection)
        self.chat_worker.chat_completed.connect(self._on_chat_complete, Qt.ConnectionType.QueuedConnection)

        # Register worker with thread manager for centralized lifecycle management
        self.main_window.thread_manager.register_worker("chat_worker", self.chat_worker)

        self.chat_worker.start()

    def _on_agent_update(self, msg):
        self.chat_history.append(
            f"<div style='font-family: monospace; padding: 4px; border-left: 2px solid green; margin-bottom: 4px;'>"
            f"{msg}</div>"
        )

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

        self.chat_history.append("<br>")

        blocks = []
        seen_quotes = set()

        def normalize_text(text):
            return re.sub(r'[^a-z0-9]', '', str(text).lower())

        for eq in self.current_existing_quotes:
            seen_quotes.add(normalize_text(eq))

        for line in full_response.split('\n'):
            line = line.strip()
            if line.upper().startswith('%%QUOTE'):
                parts = line.split('|')
                if len(parts) >= 4:
                    doc_name = parts[1].strip()
                    raw_quote = parts[2].strip()
                    note = '|'.join(parts[3:]).strip()

                    raw_quote = re.sub(r'^["\']|["\']$', '', raw_quote).strip()
                    note = re.sub(r'%%$', '', note).strip()

                    if doc_name and "|" in doc_name:
                        doc_name = doc_name.split("|")[0].strip()

                    normalized_quote = normalize_text(raw_quote)

                    if normalized_quote not in seen_quotes and len(normalized_quote) > 5:
                        seen_quotes.add(normalized_quote)
                        blocks.append({
                            'doc': doc_name,
                            'quote': raw_quote,
                            'note': note
                        })

        allowed_paths = []
        for i in range(self.pdf_list.count()):
            item = self.pdf_list.item(i)
            if item.checkState() == Qt.CheckState.Checked:
                doc_name = item.text()
                for p in self.main_window.pdf_controller.get_pdf_paths():
                    if os.path.basename(p) == doc_name:
                        allowed_paths.append(p)
                        break

        success_count = 0
        failed_blocks = []

        for b in blocks:
            quote = b['quote']
            note = b['note']
            target_doc = b['doc']

            success = self.main_window.add_ai_annotation(quote, note, target_doc_name=target_doc, allowed_paths=allowed_paths)
            if success:
                success_count += 1
            else:
                failed_blocks.append(b)

        if success_count > 0:
            self.chat_history.append(
                f"<div style='font-weight: bold; padding: 5px 0px;'>"
                f"🖍️ Successfully applied {success_count} highlight(s) to the document(s)."
                f"</div>"
            )

        for b in failed_blocks:
            display_quote = b['quote'][:80] + "..." if len(b['quote']) > 80 else b['quote']
            target_doc = b['doc']
            doc_label = f" in {target_doc}" if target_doc else ""

            self.chat_history.append(
                f"<div style='padding: 10px; border-left: 4px solid red; margin-top: 5px; margin-bottom: 5px; border-radius: 0px 4px 4px 0px;'>"
                f"⚠️ <b>Failed to locate quote{doc_label}</b><br>"
                f"<i>\"{display_quote}\"</i>"
                f"</div>"
            )

        self.chat_history.append("<hr><br>")