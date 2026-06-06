# gui/docks/search_tab.py
import os
import urllib.parse
from PySide6.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QTextEdit,
                             QPushButton, QLabel, QCheckBox, QScrollArea, QFrame, QInputDialog, QComboBox)
from PySide6.QtCore import Qt, QThread, Signal, QUrl
from PySide6.QtGui import QCursor, QDesktopServices

from core.api.search_api import SearchAPI
from gui.docks.unified_research.tabs.base_tab import BaseTab
from gui.docks.unified_research.components.universal_search_components import UniversalSearchBar, ReusableCitationCard, ReusableTermCard

class AsyncCitationWorker(QThread):
    """Offloads the mathematical SearchAPI call so the GUI doesn't freeze."""
    citation_received = Signal(str, str, float)
    finished_extraction = Signal()
    
    def __init__(self, goal, allowed_docs, pm, llm, parent=None):
        super().__init__(parent)
        self.goal = goal
        self.allowed_docs = allowed_docs
        self.pm = pm
        self.llm = llm
        
    def run(self):
        citations = SearchAPI.extract_mathematical_citations(self.goal, self.allowed_docs, self.pm, self.llm)
        for c in citations:
            self.citation_received.emit(c['doc'], c['text'], c['score'])
        self.finished_extraction.emit()

class SearchTab(BaseTab):
    def __init__(self, main_window, parent=None):
        super().__init__(main_window, target_id="search_tab", parent=parent)
        self._build_ui()

    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)

        # 1. Inject the Reusable Universal Search Bar
        search_buttons = [
            ("🏛️ JSTOR", "jstor"), ("🎓 Scholar", "scholar"), ("👾 Reddit", "reddit"), 
            ("📰 News", "news"), ("🔗 Custom", "custom"), ("📄 RAG Search", "rag")
        ]
        self.manual_search = UniversalSearchBar(
            title="🔍 Quick Manual Search:", 
            placeholder="Type custom keywords or a boolean query here...", 
            buttons=search_buttons, 
            theme=self.theme
        )
        self.manual_search.search_requested.connect(self._handle_manual_search)
        layout.addWidget(self.manual_search)
        
        self.sep = QFrame()
        self.sep.setFrameShape(QFrame.Shape.HLine)
        self.sep.setFixedHeight(1)
        layout.addWidget(self.sep)

        # 2. AI Generator Block
        header_layout = QHBoxLayout()
        header_layout.addWidget(QLabel("<b>🤖 AI Query Generator:</b>"))
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

        # 3. Output Routing Space
        self.scroll_area = QScrollArea()
        self.scroll_area.setWidgetResizable(True)
        self.scroll_area.setFrameShape(QFrame.Shape.NoFrame)
        self.results_container = QWidget()
        self.results_layout = QVBoxLayout(self.results_container) # BaseTab routes widgets here!
        self.results_layout.setContentsMargins(0, 0, 0, 0)
        self.results_layout.addStretch() 
        self.scroll_area.setWidget(self.results_container)
        layout.addWidget(self.scroll_area)
        
        self.status_lbl = QLabel("")
        layout.addWidget(self.status_lbl)

    def _handle_manual_search(self, engine, text):
        if not text: return
        if engine == "rag":
            self.main_window.viewer.annot_manager.trigger_similar_context(text)
        else:
            self._execute_external_search(engine, text)

    def _execute_external_search(self, engine, text):
        encoded = urllib.parse.quote_plus(text)
        url = ""
        if engine == "jstor": url = f"https://www.jstor.org/action/doBasicSearch?Query={encoded}"
        elif engine == "scholar": url = f"https://scholar.google.com/scholar?q={encoded}"
        elif engine == "reddit": url = f"https://www.reddit.com/search/?q={encoded}"
        elif engine == "news": url = f"https://news.google.com/search?q={encoded}"
        elif engine == "custom":
            template = self.project_manager.get_metadata("custom_search_url", "https://en.wikipedia.org/w/index.php?search={term}")
            url = template.replace("{term}", encoded)
            
        if url: QDesktopServices.openUrl(QUrl(url))

    def _edit_custom_url(self):
        current = self.project_manager.get_metadata("custom_search_url", "https://en.wikipedia.org/w/index.php?search={term}")
        new_url, ok = QInputDialog.getText(self, "Edit Custom Database URL", "Use {term} where the search query should go:", text=current)
        if ok and new_url:
            self.project_manager.set_metadata("custom_search_url", new_url)

    def _clear_results(self):
        while self.results_layout.count() > 1:
            item = self.results_layout.takeAt(0)
            if item.widget(): item.widget().deleteLater()

    def render_search_terms(self, terms_list):
        """Called automatically by UI Router when the 'search_terms' format is received."""
        if isinstance(terms_list, dict):
            for v in terms_list.values():
                if isinstance(v, list): terms_list = v; break
            if isinstance(terms_list, dict): terms_list = [terms_list]
                
        buttons_config = [
            ("🏛️ JSTOR", "jstor"), ("🎓 Scholar", "scholar"),
            ("👾 Reddit", "reddit"), ("📰 News", "news"), ("🔗 Custom", "custom")
        ]
        
        for t in terms_list:
            if isinstance(t, dict):
                clean_term = t.get("term", t.get("query", "")).strip()
                reason = t.get("reason", t.get("description", ""))
            else:
                clean_term = str(t).strip()
                reason = ""
                
            if not clean_term: continue
                
            card = ReusableTermCard(clean_term, reason, buttons_config, self.theme)
            card.action_requested.connect(lambda action, data: self._execute_external_search(data["engine"], data["term"]) if action == "search_external" else None)
            self.results_layout.insertWidget(self.results_layout.count() - 1, card)
            
        self.status_lbl.setText("✅ Generation Complete")
        self.btn_generate.setEnabled(True)

    def receive_ai_payload(self, payload: dict):
        if payload.get("type") == "search_terms":
            items = payload.get("items", [])
            if not payload.get("success", True):
                self.status_lbl.setText("❌ Could not parse generated search terms.")
                self.btn_generate.setEnabled(True)
                return
            self.render_search_terms(items)
            return
        super().receive_ai_payload(payload)

    def _add_citation_card(self, doc_name, text, score):
        card = ReusableCitationCard(doc_name, text, score, theme=self.theme)
        card.action_requested.connect(lambda action, data: self._jump_to_source(data["doc_name"], data["text"]) if action == "jump" else None)
        self.results_layout.insertWidget(self.results_layout.count() - 1, card)

    def _trigger_ai_search(self):
        goal = self.input_goal.toPlainText().strip()
        model = self.model_combo.currentText()
        if not goal or not model: return
        
        self._clear_results()
        self.btn_generate.setEnabled(False)
        self.status_lbl.setText("⏳ Initializing AI Engine...")

        if self.chk_advanced.isChecked():
            allowed_docs = [os.path.basename(p) for p in self.project_manager.pdfs]
            self.math_worker = AsyncCitationWorker(goal, allowed_docs, self.project_manager, self.main_window.shared_llm_manager, parent=self)
            self.math_worker.citation_received.connect(self._add_citation_card)
            self.math_worker.start()

        from core.engine.default_blueprints import DefaultBlueprints
        blueprint = self.blueprint_manager.get_blueprint("Search Terms", lambda: DefaultBlueprints.get_search_terms_blueprint(self.prompt_manager, model=model))
        
        # Dispatch to engine via BaseTab
        self.send_to_pipeline(blueprint, {"goal": goal})

    def _jump_to_source(self, doc_name, text):
        pm = self.project_manager
        target_path = next((p for p in pm.pdfs if doc_name in os.path.basename(p)), None)
        if target_path:
            self.main_window.switch_to_pdf(target_path)
            viewer = self.main_window.viewer
            if not viewer.search_bar.isVisible():
                viewer.toggle_search_bar()
            viewer.search_bar.search_input.setText(text)
            viewer.execute_search(text, "Current Document", False)

    def update_theme(self, theme):
        super().update_theme(theme)
        input_style = f"background-color: {theme.get('bg_input', '#2b2b2b')}; color: {theme.get('text_main', '#fff')}; border: 1px solid {theme.get('border', '#444')}; padding: 4px; border-radius: 4px;"
        
        self.input_goal.setStyleSheet(input_style)
        self.model_combo.setStyleSheet(input_style)
        
        self.btn_generate.setStyleSheet(f"background-color: {theme.get('accent', '#b366ff')}; font-weight: bold; color: white; padding: 6px; border: none; border-radius: 4px;")
        self.btn_settings.setStyleSheet(f"background-color: transparent; color: {theme.get('text_muted', '#aaa')}; border: none;")
        self.sep.setStyleSheet(f"background-color: {theme.get('border', '#444')};")
        self.chk_advanced.setStyleSheet("background: transparent; font-weight: bold;")
        
        self.scroll_area.setStyleSheet("background: transparent; border: none;")
        self.results_container.setStyleSheet("background: transparent;")
        if hasattr(self, 'status_lbl'): self.status_lbl.setStyleSheet(f"font-weight: bold; color: {theme.get('accent', '#b366ff')};")
        
        if hasattr(self, 'manual_search'): self.manual_search.update_theme(theme)
