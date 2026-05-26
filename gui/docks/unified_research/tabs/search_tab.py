# gui/docks/search_tab.py
import os
import json
import math
import re
import webbrowser
from PySide6.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QLineEdit, QTextEdit,
                             QPushButton, QLabel, QCheckBox, QScrollArea, QFrame, QInputDialog, QComboBox)
from PySide6.QtCore import Qt, QThread, Signal, QUrl
from PySide6.QtGui import QCursor, QDesktopServices

from core.engine.action_model import AIActionBlueprint, ActionStep
from gui.docks.research_assistant.model import ResearchModel

class CitationWidget(QFrame):
    jump_requested = Signal(str, str)
    def __init__(self, doc_name, text, score, theme, parent=None):
        super().__init__(parent)
        self.doc_name = doc_name
        self.text = text
        bg_color = theme.get('bg_input', '#2b2b2b')
        border_color = theme.get('success', '#00cc66') 
        self.setStyleSheet(f"CitationWidget {{ background-color: {bg_color}; border: 1px solid {border_color}; border-radius: 6px; margin-bottom: 8px; }}")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.addWidget(QLabel(f"<b>📄 {doc_name}</b> <span style='color: {border_color};'>({int(score*100)}% Match)</span>"))
        lbl_text = QLabel(f"<i>\"{text}\"</i>")
        lbl_text.setWordWrap(True)
        lbl_text.setStyleSheet(f"color: {theme.get('text_muted', '#aaa')}; margin-top: 4px; margin-bottom: 4px;")
        layout.addWidget(lbl_text)
        self.btn_jump = QPushButton("🔗 Jump to Document")
        self.btn_jump.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        self.btn_jump.setStyleSheet(f"background-color: {theme.get('bg_panel', '#333')}; border: 1px solid {theme.get('border', '#444')}; padding: 4px; border-radius: 4px;")
        self.btn_jump.clicked.connect(lambda: self.jump_requested.emit(self.doc_name, self.text))
        layout.addWidget(self.btn_jump)

class TermWidget(QFrame):
    open_jstor = Signal(str)
    open_scholar = Signal(str)
    open_custom = Signal(str)
    def __init__(self, term, reason, theme, parent=None):
        super().__init__(parent)
        self.term = term
        bg_color = theme.get('bg_input', '#2b2b2b')
        border_color = theme.get('border', '#444')
        self.setStyleSheet(f"TermWidget {{ background-color: {bg_color}; border: 1px solid {border_color}; border-radius: 6px; margin-bottom: 8px; }}")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 12, 12, 12)
        lbl_term = QLabel(f"<b>{term}</b>")
        lbl_term.setStyleSheet("font-size: 14px;")
        lbl_term.setWordWrap(True)
        layout.addWidget(lbl_term)
        lbl_reason = QLabel(f"<i>{reason}</i>")
        lbl_reason.setStyleSheet(f"color: {theme.get('text_muted', '#aaa')};")
        lbl_reason.setWordWrap(True)
        layout.addWidget(lbl_reason)
        btn_layout = QHBoxLayout()
        self.btn_jstor = QPushButton("🏛️ JSTOR")
        self.btn_scholar = QPushButton("🎓 Scholar")
        self.btn_custom = QPushButton("🔗 Custom")
        for btn in [self.btn_jstor, self.btn_scholar, self.btn_custom]:
            btn.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
            btn.setStyleSheet(f"background-color: {theme.get('bg_panel', '#333')}; border: 1px solid {border_color}; padding: 4px; border-radius: 4px;")
            btn_layout.addWidget(btn)
        layout.addLayout(btn_layout)
        self.btn_jstor.clicked.connect(lambda: self.open_jstor.emit(self.term))
        self.btn_scholar.clicked.connect(lambda: self.open_scholar.emit(self.term))
        self.btn_custom.clicked.connect(lambda: self.open_custom.emit(self.term))

class MathCitationWorker(QThread):
    citation_received = Signal(str, str, float)
    finished_extraction = Signal()
    def __init__(self, main_window, goal, allowed_docs, parent=None):
        super().__init__(parent)
        self.main_window = main_window
        self.llm = main_window.shared_llm_manager
        self.goal = goal
        self.allowed_docs = allowed_docs
    def run(self):
        import fitz
        citation_pattern = re.compile(r'(\([A-Za-z][^\)]+?,\s*\d{4}[a-z]?\)|\[\d+(?:,\s*\d+)*\])')
        pm = self.main_window.project_manager
        all_citations = []
        for doc_name in self.allowed_docs:
            doc_path = next((p for p in pm.pdfs if doc_name in os.path.basename(p)), None)
            if not doc_path: continue
            try:
                doc = fitz.open(doc_path)
                for page_num in range(len(doc)):
                    text = doc.load_page(page_num).get_text("text").replace('\n', ' ')
                    sentences = re.split(r'(?<=[.!?]) +', text)
                    for i, sentence in enumerate(sentences):
                        if citation_pattern.search(sentence) and len(sentence) > 20:
                            context_text = ""
                            if i > 0: context_text += sentences[i-1].strip() + " "
                            context_text += sentence.strip()
                            all_citations.append({"doc": doc_name, "text": context_text})
                doc.close()
            except Exception: pass
            
        if not all_citations:
            self.finished_extraction.emit()
            return
            
        try:
            goal_query = f"search_query: {self.goal}"
            goal_emb = self.llm.get_embedding(goal_query)
            texts_to_embed = [f"search_document: {c['text']}" for c in all_citations]
            cit_embs = self.llm.get_batch_embeddings(texts_to_embed)
            def cosine_sim(v1, v2):
                dot = sum(a*b for a, b in zip(v1, v2))
                n1, n2 = math.sqrt(sum(a*a for a in v1)), math.sqrt(sum(b*b for b in v2))
                return dot / (n1 * n2) if n1 and n2 else 0
                
            valid_citations = []
            for i, c in enumerate(all_citations):
                score = cosine_sim(goal_emb, cit_embs[i])
                if score > 0.50: 
                    c["score"] = score
                    valid_citations.append(c)
                    
            valid_citations.sort(key=lambda x: x["score"], reverse=True)
            seen_texts = set()
            unique_citations = []
            for c in valid_citations:
                if c["text"] not in seen_texts:
                    seen_texts.add(c["text"])
                    unique_citations.append(c)
                    self.citation_received.emit(c['doc'], c['text'], c['score'])
                    if len(unique_citations) == 5: break
        except Exception: pass
        self.finished_extraction.emit()

class SearchTab(QWidget):
    def __init__(self, main_window, parent=None):
        super().__init__(parent)
        self.main_window = main_window
        self.theme = self.main_window.theme_manager.get_theme()
        self.model = ResearchModel()
        self._build_ui()

    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)

        manual_layout = QVBoxLayout()
        self.lbl_manual = QLabel("🔍 Quick Manual Search:")
        self.lbl_manual.setStyleSheet("font-weight: bold;")
        manual_layout.addWidget(self.lbl_manual)

        self.input_manual = QLineEdit()
        self.input_manual.setPlaceholderText("Type custom keywords or a boolean query here...")
        manual_layout.addWidget(self.input_manual)

        manual_btn_layout = QHBoxLayout()
        self.btn_man_jstor = QPushButton("🏛️ JSTOR")
        self.btn_man_scholar = QPushButton("🎓 Scholar")
        self.btn_man_custom = QPushButton("🔗 Custom")
        self.btn_man_google = QPushButton("🌐 Google")
        self.btn_man_rag = QPushButton("📄 RAG Search")
        
        for btn in [self.btn_man_jstor, self.btn_man_scholar, self.btn_man_custom, self.btn_man_google, self.btn_man_rag]:
            btn.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
            manual_btn_layout.addWidget(btn)

        manual_layout.addLayout(manual_btn_layout)
        layout.addLayout(manual_layout)
        
        self.btn_man_jstor.clicked.connect(lambda: QDesktopServices.openUrl(QUrl(self.model.get_jstor_url(self.input_manual.text().strip()))))
        self.btn_man_scholar.clicked.connect(lambda: QDesktopServices.openUrl(QUrl(self.model.get_scholar_url(self.input_manual.text().strip()))))
        self.btn_man_custom.clicked.connect(lambda: QDesktopServices.openUrl(QUrl(self.model.get_custom_url(self.input_manual.text().strip()))))
        self.btn_man_google.clicked.connect(lambda: QDesktopServices.openUrl(QUrl(self.model.get_google_url(self.input_manual.text().strip()))))
        self.btn_man_rag.clicked.connect(lambda: self.main_window.viewer.annot_manager.trigger_similar_context(self.input_manual.text().strip()))

        self.sep = QFrame()
        self.sep.setFrameShape(QFrame.Shape.HLine)
        self.sep.setFixedHeight(1)
        layout.addWidget(self.sep)

        header_layout = QHBoxLayout()
        self.lbl_title = QLabel("🤖 AI Query Generator:")
        self.lbl_title.setStyleSheet("font-weight: bold;")
        header_layout.addWidget(self.lbl_title)
        header_layout.addStretch()
        
        self.btn_settings = QPushButton("⚙️ Custom Link")
        self.btn_settings.clicked.connect(self._edit_custom_url)
        header_layout.addWidget(self.btn_settings)
        layout.addLayout(header_layout)

        self.input_goal = QTextEdit()
        self.input_goal.setPlaceholderText("Explain what you want to research or argue here. Be as specific as possible...")
        self.input_goal.setMaximumHeight(80)
        layout.addWidget(self.input_goal)

        options_layout = QHBoxLayout()
        options_layout.addWidget(QLabel("Model:"))
        self.model_combo = QComboBox()
        models = self.main_window.shared_llm_manager.get_available_models()
        if models: self.model_combo.addItems(models)
        options_layout.addWidget(self.model_combo, 1)

        self.chk_advanced = QCheckBox("Advanced (Scan Citations)")
        options_layout.addWidget(self.chk_advanced)
        options_layout.addStretch()
        
        self.btn_generate = QPushButton("Generate Search Terms")
        self.btn_generate.clicked.connect(self._trigger_ai_search)
        options_layout.addWidget(self.btn_generate)
        layout.addLayout(options_layout)

        self.scroll_area = QScrollArea()
        self.scroll_area.setWidgetResizable(True)
        self.scroll_area.setFrameShape(QFrame.Shape.NoFrame)
        self.results_container = QWidget()
        self.results_layout = QVBoxLayout(self.results_container)
        self.results_layout.setContentsMargins(0, 0, 0, 0)
        self.results_layout.addStretch() 
        self.scroll_area.setWidget(self.results_container)
        layout.addWidget(self.scroll_area)
        
        self.status_lbl = QLabel("")
        self.status_lbl.setStyleSheet(f"font-weight: bold; color: {self.theme.get('accent', '#b366ff')};")
        layout.addWidget(self.status_lbl)

    def _edit_custom_url(self):
        current = self.model.get_custom_url_template()
        new_url, ok = QInputDialog.getText(self, "Edit Custom Database URL", "Use {term} where the search query should go:", text=current)
        if ok and new_url:
            self.model.set_custom_url_template(new_url)

    def _clear_results(self):
        while self.results_layout.count() > 1:
            item = self.results_layout.takeAt(0)
            if item.widget(): item.widget().deleteLater()

    def render_search_terms(self, terms_list):
        """Called by ui_router.py to populate the cards safely on the main thread."""
        if isinstance(terms_list, dict):
            for v in terms_list.values():
                if isinstance(v, list): terms_list = v; break
            if isinstance(terms_list, dict): terms_list = [terms_list]
                
        for t in terms_list:
            if isinstance(t, dict):
                clean_term = t.get("term", t.get("query", "")).strip()
                reason = t.get("reason", t.get("description", ""))
            else:
                clean_term = str(t).strip()
                reason = ""
                
            if not clean_term: continue
                
            card = TermWidget(clean_term, reason, self.theme)
            card.open_jstor.connect(lambda text: QDesktopServices.openUrl(QUrl(self.model.get_jstor_url(text))))
            card.open_scholar.connect(lambda text: QDesktopServices.openUrl(QUrl(self.model.get_scholar_url(text))))
            card.open_custom.connect(lambda text: QDesktopServices.openUrl(QUrl(self.model.get_custom_url(text))))
            self.results_layout.insertWidget(self.results_layout.count() - 1, card)
            
        self.status_lbl.setText("✅ Generation Complete")
        self.btn_generate.setEnabled(True)

    def _add_citation_card(self, doc_name, text, score):
        card = CitationWidget(doc_name, text, score, self.theme)
        card.jump_requested.connect(self._jump_to_source)
        self.results_layout.insertWidget(self.results_layout.count() - 1, card)

    def _trigger_ai_search(self):
        goal = self.input_goal.toPlainText().strip()
        model = self.model_combo.currentText()
        if not goal or not model: return
        
        self._clear_results()
        self.btn_generate.setEnabled(False)
        self.status_lbl.setText("⏳ Initializing AI Engine...")

        if self.chk_advanced.isChecked():
            allowed_docs = [os.path.basename(p) for p in self.main_window.project_manager.pdfs]
            self.math_worker = MathCitationWorker(self.main_window, goal, allowed_docs, parent=self)
            self.math_worker.citation_received.connect(self._add_citation_card)
            self.math_worker.start()

        from core.engine.default_blueprints import DefaultBlueprints
        blueprint = self.main_window.blueprint_manager.get_blueprint(
            "Search Terms", 
            DefaultBlueprints.get_search_terms_blueprint, 
            model=model
        )
        
        # FIX 1: Provide all variables needed for custom tools
        state_dict = {
            "goal": goal,
            "selected_model": model 
        }
        
        self.main_window.execute_ai_blueprint(blueprint, state_dict)
        # FIX 2: Removed the rogue duplicate self.main_window.execute_ai_blueprint(blueprint, {}) call

    def _jump_to_source(self, doc_name, text):
        pm = self.main_window.project_manager
        target_path = next((p for p in pm.pdfs if doc_name in os.path.basename(p)), None)
        if target_path:
            self.main_window.switch_to_pdf(target_path)
            viewer = self.main_window.viewer
            if not viewer.search_bar.isVisible():
                viewer.toggle_search_bar()
            viewer.search_bar.search_input.setText(text)
            viewer.execute_search(text, "Current Document", False)

    def update_theme(self, theme):
        self.theme = theme
        self.setStyleSheet(f"background-color: {theme['bg_main']}; color: {theme['text_main']};")
        
        input_style = f"background-color: {theme['bg_input']}; color: {theme['text_main']}; border: 1px solid {theme['border']}; padding: 4px; border-radius: 4px;"
        self.input_goal.setStyleSheet(input_style)
        self.input_manual.setStyleSheet(input_style)
        self.model_combo.setStyleSheet(input_style)
        
        self.btn_generate.setStyleSheet(f"background-color: {theme['accent']}; font-weight: bold; color: white; padding: 6px; border: none; border-radius: 4px;")
        self.btn_settings.setStyleSheet(f"background-color: transparent; color: {theme.get('text_muted', '#aaa')}; border: none;")
        self.sep.setStyleSheet(f"background-color: {theme.get('border', '#444')};")
        self.chk_advanced.setStyleSheet("background: transparent; font-weight: bold;")
        
        btn_style = f"background-color: {theme['bg_panel']}; color: {theme['text_main']}; border: 1px solid {theme['border']}; padding: 4px; border-radius: 4px;"
        for btn in [self.btn_man_jstor, self.btn_man_scholar, self.btn_man_custom, self.btn_man_google, self.btn_man_rag]:
            btn.setStyleSheet(btn_style)
            
        self.scroll_area.setStyleSheet("background: transparent; border: none;")
        self.results_container.setStyleSheet("background: transparent;")
        if hasattr(self, 'status_lbl'): self.status_lbl.setStyleSheet(f"font-weight: bold; color: {theme.get('accent', '#b366ff')};")