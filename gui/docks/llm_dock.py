# gui/tabs/llm_dock.py
import re
import os
from PyQt6.QtWidgets import (QWidget, QVBoxLayout, QTextEdit, QPushButton, QLabel, 
                             QComboBox, QHBoxLayout, QLineEdit, QMessageBox, QListWidget, QListWidgetItem, QCheckBox,
                             QScrollArea, QFrame, QSizePolicy)
from PyQt6.QtCore import pyqtSignal, QThread, Qt
from PyQt6.QtGui import QTextCursor, QColor
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
    
    def __init__(self, llm, question, model, allowed_docs, tag_filters, use_agents, rag_enabled=True, custom_system_prompt=None, existing_highlights=None, parent=None):
        super().__init__(parent)
        self.llm = llm
        self.question = question
        self.model = model
        self.allowed_docs = allowed_docs
        self.tag_filters = tag_filters or []
        self.use_agents = use_agents
        self.rag_enabled = rag_enabled
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
            self.llm.query(
                self.question, 
                self.model, 
                allowed_docs=self.allowed_docs, 
                tag_filters=self.tag_filters,
                callback=handle_chunk, 
                rag_enabled=self.rag_enabled,
                use_agents=self.use_agents,
                custom_system_prompt=self.custom_system_prompt,
                existing_highlights=self.existing_highlights
            )
        except Exception as e:
            handle_chunk(f"\n[System Error: {str(e)}]")
        
        if buffer and "%%QUOTE" not in buffer and not hide_future_output:
            self.token_received.emit(buffer)
            
        self.chat_completed.emit(full_response)

class LLMTab(QWidget):
    def __init__(self, shared_llm_manager, parent=None, main_window=None): # <-- Added parameter
        super().__init__(parent)
        self.main_window = main_window
        self.llm_manager = shared_llm_manager # <-- Use the shared instance
        self.current_existing_quotes = []
        self.theme = None
        
        outer_layout = QVBoxLayout(self)
        outer_layout.setContentsMargins(0, 0, 0, 0)

        self.tab_scroll_area = QScrollArea(self)
        self.tab_scroll_area.setWidgetResizable(True)
        self.tab_scroll_area.setFrameShape(QFrame.Shape.NoFrame)

        self.content_widget = QWidget()
        layout = QVBoxLayout(self.content_widget)
        
        status_layout = QHBoxLayout()
        self.status_lbl = QLabel("🔴 Status: Unindexed")
        status_layout.addWidget(self.status_lbl)
        status_layout.addStretch()

        self.agent_checkbox = QCheckBox("Use Advanced Agents")
        self.agent_checkbox.setChecked(True)
        status_layout.addWidget(self.agent_checkbox)
        layout.addLayout(status_layout)

        model_layout = QHBoxLayout()
        model_layout.addWidget(QLabel("Model:"))
        
        self.model_combo = QComboBox()
        models = self.llm_manager.get_available_models()
        self.model_combo.addItems(models if models else ["llama3 (Local)"])
        self.model_combo.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        
        self.btn_refresh_models = QPushButton("🔄")
        self.btn_refresh_models.setToolTip("Refresh Model List")
        self.btn_refresh_models.setFixedWidth(35)
        self.btn_refresh_models.clicked.connect(self.refresh_models)

        model_layout.addWidget(self.model_combo, 1)
        model_layout.addWidget(self.btn_refresh_models)
        layout.addLayout(model_layout)

        layout.addWidget(QLabel("Select PDFs to Include in AI Search:"))
        self.pdf_list = QListWidget()
        self.pdf_list.setFixedHeight(100)
        layout.addWidget(self.pdf_list)

        layout.addWidget(QLabel("Filter by Tags (optional, all selected tags required):"))
        self.tag_filter_list = QListWidget()
        self.tag_filter_list.setFixedHeight(90)
        layout.addWidget(self.tag_filter_list)
        
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
        self.chat_input.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        
        self.send_btn = QPushButton("Send")
        self.send_btn.clicked.connect(self.send_message)
        
        input_layout.addWidget(self.chat_input)
        input_layout.addWidget(self.send_btn)
        layout.addLayout(input_layout)

        self.tab_scroll_area.setWidget(self.content_widget)
        outer_layout.addWidget(self.tab_scroll_area)

    def update_theme(self, theme):
        self.theme = theme
        self.setStyleSheet(f"background-color: {theme['bg_main']};")
        self.tab_scroll_area.setStyleSheet("background: transparent; border: none;")
        self.tab_scroll_area.viewport().setStyleSheet(f"background-color: {theme['bg_main']};")
        self.content_widget.setStyleSheet(f"background-color: {theme['bg_main']};")
        self.status_lbl.setStyleSheet(f"font-weight: bold; font-size: 14px; color: {theme['text_main']};")
        self.agent_checkbox.setStyleSheet(f"color: {theme['text_muted']}; margin-right: 15px;")
        self.model_combo.setStyleSheet(
            f"background-color: {theme['bg_input']}; color: {theme['text_main']}; border: 1px solid {theme['border']};"
        )
        self.pdf_list.setStyleSheet(
            f"background-color: {theme['bg_input']}; color: {theme['text_main']}; border: 1px solid {theme['border']};"
        )
        self.tag_filter_list.setStyleSheet(
            f"background-color: {theme['bg_input']}; color: {theme['text_main']}; border: 1px solid {theme['border']};"
        )
        self.chat_history.setStyleSheet(
            f"background-color: {theme['bg_input']}; color: {theme['text_main']}; border: 1px solid {theme['border']};"
        )
        self.chat_input.setStyleSheet(
            f"background-color: {theme['bg_input']}; color: {theme['text_main']}; border: 1px solid {theme['border']};"
        )
        self.btn_refresh_models.setStyleSheet(f"background-color: {theme['bg_input']}; font-weight: bold;")
        self.btn_index.setStyleSheet(f"background-color: {theme['bg_panel']}; padding: 6px; font-weight: bold; margin-bottom: 10px; border: 1px solid {theme['border']};")
        self.send_btn.setStyleSheet(f"background-color: {theme['accent']}; padding: 8px 20px; font-weight: bold; color: #ffffff; border-radius: 4px;")
        
    def refresh_project_ui(self):
        self.pdf_list.clear()
        if self.main_window.project_manager.pdfs:
            for pdf_path in self.main_window.project_manager.pdfs:
                doc_name = os.path.basename(pdf_path)
                item = QListWidgetItem(doc_name)
                item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
                item.setCheckState(Qt.CheckState.Checked) 
                self.pdf_list.addItem(item)

        self.refresh_tag_filters()
                
        proj_path = self.main_window.project_manager.project_filepath
        if proj_path and hasattr(self, 'llm_manager'):
            self.llm_manager.set_project_database(proj_path)
            if self.llm_manager.collection and self.llm_manager.collection.count() > 0:
                self.status_lbl.setText("🟢 Status: Ready (Vector DB Loaded)")
                color = self.theme['success'] if self.theme else "#00cc66"
                self.status_lbl.setStyleSheet(f"font-weight: bold; color: {color}; font-size: 14px;")
            else:
                self.status_lbl.setText("🔴 Status: Needs Indexing")
                color = self.theme['warning'] if self.theme else "#ffaa00"
                self.status_lbl.setStyleSheet(f"font-weight: bold; color: {color}; font-size: 14px;")

    def refresh_tag_filters(self):
        self.tag_filter_list.clear()
        pm = self.main_window.project_manager if self.main_window and hasattr(self.main_window, "project_manager") else None
        tags = pm.get_all_tags() if pm else []

        for tag in tags:
            tag_name = tag.get("name", "")
            tag_color = tag.get("color") or "#808080"
            item = QListWidgetItem(tag_name)
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            item.setCheckState(Qt.CheckState.Unchecked)
            item.setData(Qt.ItemDataRole.UserRole, tag_name)
            item.setForeground(QColor(tag_color))
            self.tag_filter_list.addItem(item)

    def refresh_models(self):
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
        self.idx_worker = IndexWorker(self.llm_manager, paths_to_index, parent=self)
        self.idx_worker.progress.connect(self._update_index_progress)
        self.idx_worker.finished_indexing.connect(self._on_index_complete)
        self.idx_worker.start()

    def _update_index_progress(self, msg):
        self.status_lbl.setText(f"🟡 {msg}")

    def _on_index_complete(self, success, error_msg):
        self.btn_index.setEnabled(True)
        if success:
            # ---> FIX: Re-sync tags to the newly created index chunks so they are searchable
            if hasattr(self, 'main_window') and self.main_window:
                pm = self.main_window.project_manager
                for pdf_path in pm.pdfs:
                    pm._sync_doc_tags_for_llm(pdf_path)
            # <---
            
            self.status_lbl.setText("🟢 Status: Ready (Indexed to Vector DB)")
            color = self.theme['success'] if self.theme else "#00cc66"
            self.status_lbl.setStyleSheet(f"font-weight: bold; color: {color}; font-size: 14px;")
        else:
            self.status_lbl.setText(f"❌ Indexing Failed: {error_msg}")
            color = self.theme['error'] if self.theme else "#ff4444"
            self.status_lbl.setStyleSheet(f"font-weight: bold; color: {color}; font-size: 14px;")

    def send_message(self):
        user_text = self.chat_input.text().strip()
        if not user_text: return
        
        accent_color = self.theme['accent'] if self.theme else "#55aaff"
        self.chat_history.append(f"<b style='color:{accent_color}'>You:</b> {user_text}<br>")
        self.chat_input.clear()
        
        self.send_btn.setText("⏳ Processing...")
        self.send_btn.setEnabled(False)
        self.chat_input.setEnabled(False)
        
        use_agents = self.agent_checkbox.isChecked()
        if use_agents:
            self.status_lbl.setText("⚙️ AI is planning task...")
        else:
            self.status_lbl.setText("⚙️ AI is generating response...")
            
        warn_color = self.theme['warning'] if self.theme else "#ffaa00"
        self.status_lbl.setStyleSheet(f"font-weight: bold; color: {warn_color}; font-size: 14px;")
        
        allowed_docs = []
        allowed_paths = []
        for i in range(self.pdf_list.count()):
            item = self.pdf_list.item(i)
            if item.checkState() == Qt.CheckState.Checked:
                doc_name = item.text()
                allowed_docs.append(doc_name)
                for p in self.main_window.project_manager.pdfs:
                    if os.path.basename(p) == doc_name:
                        allowed_paths.append(p)
                        break

        selected_tag_filters = []
        for i in range(self.tag_filter_list.count()):
            item = self.tag_filter_list.item(i)
            if item.checkState() == Qt.CheckState.Checked:
                selected_tag_filters.append(item.data(Qt.ItemDataRole.UserRole))
        
        existing_quotes = []
        for path in allowed_paths:
            doc = self.main_window.project_manager.get_doc(path)
            if doc:
                for i in range(len(doc)):
                    try:
                        for annot in doc.load_page(i).annots():
                            if annot.info and annot.info.get("subject"):
                                existing_quotes.append(annot.info.get("subject"))
                    except: pass
                    
        self.current_existing_quotes = existing_quotes
        
        model = self.model_combo.currentText()
        
        self.chat_worker = ChatWorker(
            self.llm_manager, 
            user_text,
            model, 
            allowed_docs, 
            selected_tag_filters,
            use_agents, 
            rag_enabled=True,
            custom_system_prompt=None,
            existing_highlights=existing_quotes,
            parent=self
        )
        self.chat_worker.token_received.connect(self._on_chat_token)
        self.chat_worker.agent_update.connect(self._on_agent_update)
        self.chat_worker.chat_completed.connect(self._on_chat_complete)
        self.chat_worker.start()

    def _on_agent_update(self, msg):
        suc_color = self.theme['success'] if self.theme else "#4CAF50"
        self.chat_history.append(
            f"<div style='color: {suc_color}; font-family: monospace; padding: 4px; border-left: 2px solid {suc_color}; margin-bottom: 4px;'>"
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
        suc_color = self.theme['success'] if self.theme else "#00cc66"
        self.status_lbl.setStyleSheet(f"font-weight: bold; color: {suc_color}; font-size: 14px;")
        
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
                for p in self.main_window.project_manager.pdfs:
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
            ai_color = self.theme['ai_bubble_border'] if self.theme else "#d194ff"
            self.chat_history.append(
                f"<div style='color: {ai_color}; font-weight: bold; padding: 5px 0px;'>"
                f"🖍️ Successfully applied {success_count} highlight(s) to the document(s)."
                f"</div>"
            )
            
        for b in failed_blocks:
            display_quote = b['quote'][:80] + "..." if len(b['quote']) > 80 else b['quote']
            target_doc = b['doc']
            doc_label = f" in {target_doc}" if target_doc else ""
            err_color = self.theme['error'] if self.theme else "#ff4444"
            panel_bg = self.theme['bg_panel'] if self.theme else "#2b2b2b"
            text_muted = self.theme['text_muted'] if self.theme else "#ddd"
            
            self.chat_history.append(
                f"<div style='background-color: {panel_bg}; padding: 10px; border-left: 4px solid {err_color}; margin-top: 5px; margin-bottom: 5px; border-radius: 0px 4px 4px 0px;'>"
                f"⚠️ <b style='color:{err_color};'>Failed to locate quote{doc_label}</b><br>"
                f"<i style='color:{text_muted};'>\"{display_quote}\"</i>"
                f"</div>"
            )
        
        self.chat_history.append("<hr><br>")