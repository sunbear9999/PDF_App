# gui/docks/research_assistant/controller.py
import re
import os
from PySide6.QtWidgets import QWidget, QVBoxLayout
from PySide6.QtCore import QThread, Signal
from PySide6.QtGui import QDesktopServices
from PySide6.QtCore import QUrl

from .model import ResearchModel
from .view import ResearchView

class ResearchWorker(QThread):
    term_received = Signal(str, str) 
    quote_received = Signal(str, str, str) 
    finished_generation = Signal()
    status_update = Signal(str)

    def __init__(self, llm_manager, goal, model, is_advanced, allowed_docs, parent=None):
        super().__init__(parent)
        self.llm = llm_manager
        self.goal = goal
        self.model = model
        self.is_advanced = is_advanced
        self.allowed_docs = allowed_docs

    def run(self):
        prompt = (
            f"You are an expert academic research assistant. The user's research goal is: '{self.goal}'\n\n"
            "INSTRUCTIONS:\n"
            "1. Generate 3 to 5 highly specific, advanced academic search queries (using boolean operators like AND/OR if helpful) to find literature for this topic.\n"
            "2. Format each search term strictly on a new single line exactly like this with no markdown formatting:\n"
            "%%TERM | exact boolean search phrase | A brief sentence explaining why this specific query is effective.\n\n"
        )
        
        if self.is_advanced:
            prompt += (
                "3. Scan the provided document context specifically for existing citations, footnotes, or references to other authors/works that relate to the user's goal.\n"
                "4. If you find relevant citations, you MUST output them at the very end under a '--- HIGHLIGHTS ---' section using this exact format:\n"
                "%%QUOTE | Document_Name.pdf | The exact citation text | Why this is a good source to track down.\n"
            )

        buffer = ""
        found_any = False
        
        def process_line(line):
            nonlocal found_any
            line = line.strip()
            
            # 1. Handle Quotes first since they have a strict 4-part format
            if "%%QUOTE" in line:
                parts = line.split('|')
                if len(parts) >= 4:
                    doc = parts[1].strip()
                    quote = parts[2].strip()
                    note = '|'.join(parts[3:]).strip()
                    self.quote_received.emit(doc, quote, note)
                    
            # 2. Handle Terms, being very forgiving if the AI drops the word "TERM"
            elif line.startswith("%%") or "%%TERM" in line:
                # Use regex to strip "%%", "%%TERM", and any rogue pipes/spaces at the start
                import re
                clean_line = re.sub(r'^%%(TERM)?\s*\|?\s*', '', line).strip()
                
                parts = clean_line.split('|')
                if len(parts) >= 2:
                    term = parts[0].strip()
                    reason = '|'.join(parts[1:]).strip()
                    self.term_received.emit(term, reason)
                    found_any = True

        def handle_chunk(chunk):
            nonlocal buffer
            
            # Print to console so we don't have silent failures anymore
            print(chunk, end="", flush=True) 

            if chunk.startswith("@@AGENT@@"):
                self.status_update.emit(chunk.replace("@@AGENT@@", "").strip())
                return
            
            # Catch LLM Manager Error Messages directly
            if "[Generation Error:" in chunk:
                self.status_update.emit(chunk.strip())
                return
                
            buffer += chunk
            while '\n' in buffer:
                line, buffer = buffer.split('\n', 1)
                process_line(line)

        try:
            self.status_update.emit(f"⚙️ Querying {self.model}...")
            print("\n--- RESEARCH ASSISTANT RAW OUTPUT ---")
            
            self.llm.query(
                question=prompt,
                selected_model=self.model,
                allowed_docs=self.allowed_docs if self.is_advanced else [],
                callback=handle_chunk,
                rag_enabled=self.is_advanced, 
                use_agents=False, 
                custom_system_prompt="You are a strict output generator. Follow format rules exactly. Do not include conversational filler."
            )
            print("\n--- END RESEARCH OUTPUT ---")
            
            # Flush the remaining buffer if the LLM didn't end on a newline
            if buffer.strip():
                process_line(buffer)
                
        except Exception as e:
            self.status_update.emit(f"Error: {str(e)}")
            
        if not found_any:
            self.status_update.emit("⚠️ Warning: AI responded, but didn't format any terms correctly. Check terminal.")
        else:
            self.status_update.emit("✅ Generation Complete")
            
        self.finished_generation.emit()

class ResearchDockWidget(QWidget):
    def __init__(self, main_window, parent=None):
        super().__init__(parent)
        self.main_window = main_window
        self.llm_manager = main_window.shared_llm_manager
        
        self.model = ResearchModel()
        self.view = ResearchView(self)
        
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self.view)

        # Populate available models
        models = self.llm_manager.get_available_models()
        if models:
            self.view.model_combo.addItems(models)

        self.view.generate_requested.connect(self._handle_generate)
        self.view.change_custom_url_requested.connect(self._handle_custom_url_change)

    def update_theme(self, theme):
        self.view.update_theme(theme)

    def _handle_custom_url_change(self):
        current = self.model.get_custom_url_template()
        new_url = self.view.prompt_for_custom_url(current)
        if new_url:
            self.model.set_custom_url_template(new_url)

    def _handle_generate(self, goal, model, is_advanced):
        self.view.set_loading_state(True)
        
        allowed_docs = [os.path.basename(p) for p in self.main_window.project_manager.pdfs] if is_advanced else []

        self.worker = ResearchWorker(self.llm_manager, goal, model, is_advanced, allowed_docs, parent=self)
        self.worker.term_received.connect(self._on_term_received)
        self.worker.quote_received.connect(self._on_quote_received)
        self.worker.status_update.connect(lambda msg: self.view.set_status(msg))
        self.worker.finished_generation.connect(self._on_generation_finished)
        self.worker.start()

    def _on_term_received(self, term, reason):
        # Removed the .strip('"\'') so we don't break boolean quotes!
        clean_term = term.strip() 
        card = self.view.add_term_card(clean_term, reason)
        card.open_jstor.connect(lambda t: QDesktopServices.openUrl(QUrl(self.model.get_jstor_url(t))))
        card.open_scholar.connect(lambda t: QDesktopServices.openUrl(QUrl(self.model.get_scholar_url(t))))
        card.open_custom.connect(lambda t: QDesktopServices.openUrl(QUrl(self.model.get_custom_url(t))))

    def _on_quote_received(self, doc_name, quote, note):
        if hasattr(self.main_window, 'add_ai_annotation'):
            success = self.main_window.add_ai_annotation(quote, f"[AI Citation Scanner] {note}", target_doc_name=doc_name)
            if success:
                self.view.set_status(f"🖍️ Found and highlighted citation in {doc_name}")

    def _on_generation_finished(self):
        self.view.set_loading_state(False)