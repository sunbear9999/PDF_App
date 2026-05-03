# gui/docks/brainstorm_dock.py
import re
import urllib.parse
from PySide6.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QTextEdit, 
                             QPushButton, QComboBox, QLabel, QScrollArea, QFrame, QApplication, QTextBrowser, QMenu)
from PySide6.QtCore import QThread, Signal, Qt, QUrl
from PySide6.QtGui import QCursor, QAction
from core.brainstorm_manager import BrainstormManager

class ExpandableCategory(QFrame):
    jump_requested = Signal(str, str) # doc_name, exact_quote
    send_to_chat_requested = Signal(str)

    def __init__(self, title, content, theme, parent=None):
        super().__init__(parent)
        self.raw_content = content
        
        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 4, 8, 4)
        layout.setSpacing(4)
        
        self.btn_toggle = QPushButton(f"▶ {title}")
        self.btn_toggle.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        self.btn_toggle.clicked.connect(self.toggle)
        layout.addWidget(self.btn_toggle)
        
        self.content_view = QTextBrowser()
        self.content_view.setOpenExternalLinks(False)
        self.content_view.setOpenLinks(False) # We will handle clicks manually
        self.content_view.anchorClicked.connect(self._on_link_clicked)
        self.content_view.setVisible(False)
        
        # Setup context menu for right-clicking
        self.content_view.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.content_view.customContextMenuRequested.connect(self._show_context_menu)
        
        layout.addWidget(self.content_view)
        
        self.update_theme(theme)

    def update_theme(self, theme):
        self.current_theme = theme
        bg_color = theme.get('bg_input', '#2b2b2b')
        border_color = theme.get('border', '#444')
        text_color = theme.get('text_main', '#fff')
        accent = theme.get('accent', '#b366ff')
        
        self.setStyleSheet(f"""
            ExpandableCategory {{
                background-color: {bg_color};
                border: 1px solid {border_color};
                border-radius: 6px;
                margin-top: 4px;
                margin-bottom: 4px;
            }}
        """)
        
        self.btn_toggle.setStyleSheet(f"""
            QPushButton {{
                text-align: left;
                background: transparent;
                color: {accent};
                font-weight: bold;
                border: none;
                font-size: 13px;
                padding: 4px;
            }}
            QPushButton:hover {{
                color: {text_color};
            }}
        """)
        
        self.content_view.setStyleSheet(f"color: {text_color}; background: transparent; border: none;")
        self._render_content(accent)

    def _render_content(self, accent_color):
        # Regex to find our strict citation format [[DocName|Quote]]
        pattern = r'\[\[(.*?)\|(.*?)\]\]'
        
        def replace_citation(match):
            doc_name = match.group(1).strip()
            quote = match.group(2).strip()
            
            doc_safe = urllib.parse.quote(doc_name)
            quote_safe = urllib.parse.quote(quote)
            
            # --- THE FIX ---
            # Mask the custom protocol as standard HTTP so Qt's markdown sanitizer doesn't destroy it
            url = f"http://papyrus.cite/{doc_safe}?q={quote_safe}"
            return f'&nbsp;<a href="{url}" style="text-decoration:none; color:{accent_color}; font-weight:bold; font-size:11px;">[📄 {doc_name}]</a>'

        parsed_text = re.sub(pattern, replace_citation, self.raw_content)
        self.content_view.setMarkdown(parsed_text)

    def _on_link_clicked(self, url: QUrl):
        url_str = url.toString()
        if "papyrus.cite" in url_str:
            try:
                # Strip the dummy domain out
                parts = url_str.split("papyrus.cite/")[1].split("?q=")
                doc_name = urllib.parse.unquote(parts[0])
                quote = urllib.parse.unquote(parts[1]) if len(parts) > 1 else ""
                self.jump_requested.emit(doc_name, quote)
            except Exception as e:
                print(f"Failed to parse citation URL: {e}")

    def _show_context_menu(self, pos):
        menu = self.content_view.createStandardContextMenu()
        menu.addSeparator()
        
        action_send = QAction("➤ Send to Chat", self)
        
        # Check if the user has specifically highlighted text
        selected_text = self.content_view.textCursor().selectedText()
        
        if selected_text:
            action_send.setText("➤ Send Highlighted to Chat")
            action_send.triggered.connect(lambda: self.send_to_chat_requested.emit(selected_text))
        else:
            # If nothing is highlighted, grab the specific bullet point/paragraph under the cursor
            cursor = self.content_view.cursorForPosition(pos)
            cursor.select(cursor.SelectionType.BlockUnderCursor)
            block_text = cursor.selectedText()
            
            if block_text.strip():
                action_send.setText("➤ Send Topic to Chat")
                action_send.triggered.connect(lambda: self.send_to_chat_requested.emit(block_text))
            else:
                action_send.setEnabled(False)

        menu.addAction(action_send)
        menu.exec(self.content_view.mapToGlobal(pos))

    def toggle(self):
        visible = not self.content_view.isVisible()
        self.content_view.setVisible(visible)
        title = self.btn_toggle.text()[2:]
        self.btn_toggle.setText(f"▼ {title}" if visible else f"▶ {title}")
        
        # Dynamically resize the browser to fit the text so we don't get inner scrollbars
        if visible:
            self.content_view.document().adjustSize()
            self.content_view.setMinimumHeight(int(self.content_view.document().size().height()) + 15)

class BrainstormWorker(QThread):
    chunk_received = Signal(str)
    finished_with_data = Signal(str, str)
    jump_requested = Signal(str, str)
    def __init__(self, manager, query, mode, model, current_goal):
        super().__init__()
        self.manager = manager
        self.query = query
        self.mode = mode
        self.model = model
        self.current_goal = current_goal

    def run(self):
        cleaned_resp, new_goal = self.manager.generate_response(
            self.query, 
            self.mode, 
            self.model, 
            self.current_goal,
            callback=lambda chunk: self.chunk_received.emit(chunk)
        )
        self.finished_with_data.emit(cleaned_resp, new_goal or "")

class BrainstormDock(QWidget):
    def __init__(self, llm_manager, project_manager, parent=None):
        super().__init__(parent)
        self.llm_manager = llm_manager
        self.project_manager = project_manager
        self.manager = BrainstormManager(llm_manager, llm_manager.prompt_manager)
        
        # Fallback theme for early instantiations
        self.current_theme = {'bg_main': '#1e1e1e', 'bg_input': '#2d2d2d', 'bg_panel': '#252525', 'text_main': '#ffffff', 'text_muted': '#aaaaaa', 'border': '#444444', 'accent': '#b366ff', 'accent_hover': '#9933ff'}
        
        self.stream_lbl = None
        self.stream_text = ""
        
        self._build_ui()
        self._load_initial_goal()

    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(8)

        # --- Top Controls ---
        ctrl_layout = QHBoxLayout()
        self.model_combo = QComboBox()
        self.model_combo.addItems(self.llm_manager.get_available_models())
        ctrl_layout.addWidget(QLabel("Model:"))
        ctrl_layout.addWidget(self.model_combo)

        self.mode_combo = QComboBox()
        self.mode_combo.addItems(["Logical/Neutral (Default)", "RAG Enabled", "RAG Only"])
        ctrl_layout.addWidget(QLabel("Mode:"))
        ctrl_layout.addWidget(self.mode_combo)

        self.btn_clear = QPushButton("🗑️ Clear Context")
        self.btn_clear.clicked.connect(self._clear_chat)
        ctrl_layout.addWidget(self.btn_clear)
        layout.addLayout(ctrl_layout)

        # --- Project Goal Memory Area ---
        goal_label = QLabel("🧠 <b>Current Project Goal</b> (AI will update this automatically):")
        layout.addWidget(goal_label)
        
        self.goal_edit = QTextEdit()
        self.goal_edit.setMaximumHeight(60) # Slimmer goal box
        self.goal_edit.setPlaceholderText("No goal set. Chat with the AI to develop one, or type it here manually.")
        self.goal_edit.textChanged.connect(self._save_manual_goal_edit)
        layout.addWidget(self.goal_edit)

        # --- Structured Chat Display Area ---
        self.chat_scroll = QScrollArea()
        self.chat_scroll.setWidgetResizable(True)
        self.chat_scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.chat_scroll.setStyleSheet("background: transparent;")
        
        self.chat_container = QWidget()
        self.chat_layout = QVBoxLayout(self.chat_container)
        self.chat_layout.setContentsMargins(0, 0, 0, 0)
        self.chat_layout.setSpacing(6)
        self.chat_layout.addStretch() # Push all messages to the top
        self.chat_scroll.setWidget(self.chat_container)
        
        layout.addWidget(self.chat_scroll, 1) # Give it the expanding space

        # Initial message
        self.welcome_lbl = QLabel("<i>Welcome to the Brainstorming Assistant. <br><br>Describe a topic you want to research, a dead end you've hit, or an argument you are trying to structure.</i>")
        self.welcome_lbl.setWordWrap(True)
        self.welcome_lbl.setStyleSheet("color: #888;")
        self.chat_layout.insertWidget(self.chat_layout.count() - 1, self.welcome_lbl)

        # --- Sleek Input Area ---
        input_layout = QHBoxLayout()
        input_layout.setContentsMargins(0, 0, 0, 0)
        self.input_field = QTextEdit()
        self.input_field.setMaximumHeight(50) # Very sleek height
        self.input_field.setPlaceholderText("Type your thoughts here...")
        input_layout.addWidget(self.input_field)

        self.btn_send = QPushButton("➤ Send")
        self.btn_send.setFixedSize(80, 50) # Proportional, neat square button
        self.btn_send.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        self.btn_send.clicked.connect(self._send_message)
        input_layout.addWidget(self.btn_send)

        layout.addLayout(input_layout)

    def _scroll_to_bottom(self):
        QApplication.processEvents()
        vbar = self.chat_scroll.verticalScrollBar()
        vbar.setValue(vbar.maximum())

    def _load_initial_goal(self):
        saved_goal = self.project_manager.get_metadata("project_description", "")
        self.goal_edit.blockSignals(True)
        self.goal_edit.setPlainText(saved_goal)
        self.goal_edit.blockSignals(False)

    def _save_manual_goal_edit(self):
        self.project_manager.set_metadata("project_description", self.goal_edit.toPlainText().strip())

    def _clear_chat(self):
        self.manager.clear_history()
        # Remove all elements except the stretch at the end
        while self.chat_layout.count() > 1:
            item = self.chat_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

        lbl = QLabel("<i>Context cleared. The assistant has forgotten previous messages.</i>")
        lbl.setStyleSheet(f"color: {self.current_theme.get('text_muted', '#aaa')}; margin-top: 10px;")
        self.chat_layout.insertWidget(self.chat_layout.count() - 1, lbl)

    def _send_message(self):
        query = self.input_field.toPlainText().strip()
        if not query: return

        self.input_field.clear()
        self.btn_send.setEnabled(False)

        # Build User Bubble
        lbl_user = QLabel(f"<b>You:</b> {query}")
        lbl_user.setWordWrap(True)
        lbl_user.setStyleSheet(f"color: {self.current_theme.get('text_main', '#fff')}; margin-top: 15px; margin-bottom: 5px;")
        self.chat_layout.insertWidget(self.chat_layout.count() - 1, lbl_user)

        # Build Live Stream Bubble
        self.stream_lbl = QLabel("<i>Thinking...</i>")
        self.stream_lbl.setWordWrap(True)
        self.stream_lbl.setStyleSheet(f"color: {self.current_theme.get('text_muted', '#aaa')};")
        self.chat_layout.insertWidget(self.chat_layout.count() - 1, self.stream_lbl)
        self.stream_text = ""
        self._scroll_to_bottom()

        mode_text = self.mode_combo.currentText()
        if "Default" in mode_text: mode = "Default"
        elif "Enabled" in mode_text: mode = "RAG Enabled"
        else: mode = "RAG Only"

        current_goal = self.project_manager.get_metadata("project_description", "No project goal defined yet.")

        self.worker = BrainstormWorker(
            self.manager, 
            query, 
            mode, 
            self.model_combo.currentText(),
            current_goal
        )
        self.worker.chunk_received.connect(self._on_chunk)
        self.worker.finished_with_data.connect(self._on_finished)
        self.worker.start()

    def _on_chunk(self, chunk):
        if not self.stream_lbl: return
        self.stream_text += chunk
        self.stream_lbl.setTextFormat(Qt.TextFormat.PlainText) # Keep plain during stream to prevent jumping
        self.stream_lbl.setText(self.stream_text)
        self._scroll_to_bottom()

    def _on_finished(self, cleaned_resp, new_goal):
        # 1. Update Project Goal
        if new_goal:
            self.project_manager.set_metadata("project_description", new_goal)
            self.goal_edit.blockSignals(True)
            self.goal_edit.setPlainText(new_goal)
            self.goal_edit.blockSignals(False)
            
            original_style = self.goal_edit.styleSheet()
            self.goal_edit.setStyleSheet(original_style + "border: 2px solid #a8ff9d;")
            from PySide6.QtCore import QTimer
            QTimer.singleShot(1500, lambda: self.goal_edit.setStyleSheet(original_style))

        # 2. Remove the raw text stream blob
        if self.stream_lbl:
            self.stream_lbl.deleteLater()
            self.stream_lbl = None

        # 3. Parse Structural Markdown into beautiful Widgets
        # Split precisely by newlines followed by ### 
        parts = re.split(r'\n###\s+', "\n" + cleaned_resp) 
        
        intro = parts[0].strip()
        if intro:
            intro_lbl = QLabel()
            intro_lbl.setWordWrap(True)
            intro_lbl.setTextFormat(Qt.TextFormat.MarkdownText)
            intro_lbl.setText(intro)
            intro_lbl.setStyleSheet(f"color: {self.current_theme.get('text_main', '#fff')}; margin-bottom: 4px;")
            self.chat_layout.insertWidget(self.chat_layout.count() - 1, intro_lbl)

        # Parse the categories the AI invented
        # Parse the categories the AI invented
        for part in parts[1:]:
            lines = part.split('\n', 1)
            title = lines[0].strip()
            content = lines[1].strip() if len(lines) > 1 else ""
            
            card = ExpandableCategory(title, content, self.current_theme)
            card.send_to_chat_requested.connect(self._handle_send_to_chat)
            card.jump_requested.connect(self._jump_to_pdf_citation)
            self.chat_layout.insertWidget(self.chat_layout.count() - 1, card)

        self._scroll_to_bottom()
        self.btn_send.setEnabled(True)
        self.input_field.setFocus()
    def _handle_send_to_chat(self, text):
        current_text = self.input_field.toPlainText().strip()
        new_text = f"{current_text}\n\n{text}".strip() if current_text else text
        
        self.input_field.setPlainText(new_text)
        self.input_field.setFocus()
        
        cursor = self.input_field.textCursor()
        cursor.movePosition(cursor.MoveOperation.End)
        self.input_field.setTextCursor(cursor)
    def _jump_to_pdf_citation(self, doc_name, text):
        import os
        main_window = self.window()
        
        # --- THE FIX ---
        # Fuzzy match: lowercase and strip extensions so we find the PDF even if the AI drops the .pdf
        search_name = doc_name.lower().replace(".pdf", "").strip()
        target_path = None
        
        for p in self.project_manager.pdfs:
            base = os.path.basename(p).lower()
            if search_name in base:
                target_path = p
                break
                
        if target_path:
            main_window.switch_to_pdf(target_path)
            viewer = main_window.viewer
            if not viewer.search_bar.isVisible():
                viewer.toggle_search_bar()
                
            viewer.search_bar.search_input.setText(text)
            viewer.execute_search(text, "Current Document", False)
        else:
            # Fallback so we don't silently fail anymore
            if hasattr(main_window, 'statusBar'):
                main_window.statusBar().showMessage(f"⚠️ Could not find a document matching '{doc_name}'", 5000)
            print(f"Citation Jump Failed: Could not locate '{doc_name}' in project PDFs.")
    def update_theme(self, theme):
        self.current_theme = theme
        self.setStyleSheet(f"""
            QWidget {{
                background-color: {theme['bg_main']};
                color: {theme['text_main']};
            }}
            QTextEdit {{
                background-color: {theme['bg_input']};
                color: {theme['text_main']};
                border: 1px solid {theme['border']};
                border-radius: 4px;
            }}
            QPushButton {{
                background-color: {theme['bg_panel']};
                color: {theme['text_main']};
                border: 1px solid {theme['border']};
                border-radius: 4px;
                padding: 4px 8px;
            }}
            QPushButton:hover {{
                background-color: {theme['accent_hover']};
            }}
            QComboBox {{
                background-color: {theme['bg_input']};
                color: {theme['text_main']};
                border: 1px solid {theme['border']};
                border-radius: 4px;
                padding: 4px;
            }}
            QLabel {{
                background: transparent;
            }}
        """)
        for i in range(self.chat_layout.count()):
            widget = self.chat_layout.itemAt(i).widget()
            if isinstance(widget, ExpandableCategory):
                widget.update_theme(theme)
        # Force the send button to pop
        self.btn_send.setStyleSheet(f"background-color: {theme['accent']}; color: #ffffff; font-weight: bold; border: none; border-radius: 4px;")