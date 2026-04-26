# gui/docks/research_assistant/controller.py
import re
import os
from PySide6.QtWidgets import QWidget, QVBoxLayout
from PySide6.QtCore import QThread, Signal
from PySide6.QtGui import QDesktopServices
from PySide6.QtCore import QUrl
import fitz
import math
from .model import ResearchModel
from .view import ResearchView

class ResearchWorker(QThread):
    term_received = Signal(str, str) 
    citation_received = Signal(str, str, float) # <--- NEW SIGNAL
    finished_generation = Signal()
    status_update = Signal(str)

    def __init__(self, llm_manager, goal, model, is_advanced, allowed_docs, parent=None):
        super().__init__(parent)
        self.llm = llm_manager
        self.goal = goal
        self.model = model
        self.is_advanced = is_advanced
        self.allowed_docs = allowed_docs

    def _extract_and_rank_citations(self):
        """Pure math extraction. No LLM hallucination possible."""
        import fitz
        import re
        import math
        import os
        
        citation_pattern = re.compile(r'(\([A-Za-z][^\)]+?,\s*\d{4}[a-z]?\)|\[\d+(?:,\s*\d+)*\])')
        pm = self.parent().main_window.project_manager
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
                            # 🔥 FIX 1: Context Starvation.
                            # Grab the sentence BEFORE the citation as well so the vector 
                            # engine actually knows what claim the citation is proving.
                            context_text = ""
                            if i > 0:
                                context_text += sentences[i-1].strip() + " "
                            context_text += sentence.strip()
                            
                            all_citations.append({
                                "doc": doc_name,
                                "text": context_text
                            })
                doc.close()
            except Exception as e:
                print(f"Error extracting citations from {doc_name}: {e}")

        if not all_citations:
            return []

        self.status_update.emit("🧮 Ranking citations by relevance to your goal...")
        
        try:
            # 🔥 FIX 2: Nomic-embed-text requires task prefixes for accurate RAG.
            goal_query = f"search_query: {self.goal}"
            goal_emb = self.llm.get_embedding(goal_query)
            
            texts_to_embed = [f"search_document: {c['text']}" for c in all_citations]
            cit_embs = self.llm.get_batch_embeddings(texts_to_embed)

            def cosine_sim(v1, v2):
                dot = sum(a*b for a, b in zip(v1, v2))
                norm1 = math.sqrt(sum(a*a for a in v1))
                norm2 = math.sqrt(sum(b*b for b in v2))
                return dot / (norm1 * norm2) if norm1 and norm2 else 0

            valid_citations = []
            for i, c in enumerate(all_citations):
                score = cosine_sim(goal_emb, cit_embs[i])
                c["score"] = score
                
                # 🔥 FIX 3: The Garbage Threshold. 
                # A score below 0.50 means the AI is just guessing. Skip it.
                if score > 0.50: 
                    valid_citations.append(c)

            # Sort by highest score first
            valid_citations.sort(key=lambda x: x["score"], reverse=True)
            
            # 🔥 Bonus Fix: Deduplicate overlapping citations
            seen_texts = set()
            unique_citations = []
            for c in valid_citations:
                if c["text"] not in seen_texts:
                    seen_texts.add(c["text"])
                    unique_citations.append(c)
                    if len(unique_citations) == 5: # Only keep the Top 5 best unique matches
                        break

            return unique_citations

        except Exception as e:
            print(f"Embedding error during citation rank: {e}")
            return []

    def run(self):
        # 1. Execute purely mathematical citation extraction FIRST
        if self.is_advanced:
            self.status_update.emit("🔍 Sweeping documents for academic citations...")
            top_citations = self._extract_and_rank_citations()
            for c in top_citations:
                self.citation_received.emit(c['doc'], c['text'], c['score'])

        # 2. Standard AI Keyword Generation (stripped of citation instructions)
        prompt = (
            f"You are an expert academic research assistant. The user's research goal is: '{self.goal}'\n\n"
            "INSTRUCTIONS:\n"
            "1. Generate 3 to 5 highly specific, advanced academic search queries (using boolean operators like AND/OR if helpful) to find literature for this topic.\n"
            "2. Format each search term strictly on a new single line exactly like this with no markdown formatting:\n"
            "%%TERM | exact boolean search phrase | A brief sentence explaining why this specific query is effective.\n\n"
        )
        
        buffer = ""
        found_any = False
        
        def process_line(line):
            nonlocal found_any
            line = line.strip()
            
            if line.startswith("%%") or "%%TERM" in line:
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
            if chunk.startswith("@@AGENT@@"):
                self.status_update.emit(chunk.replace("@@AGENT@@", "").strip())
                return
            if "[Generation Error:" in chunk:
                self.status_update.emit(chunk.strip())
                return
                
            buffer += chunk
            while '\n' in buffer:
                line, buffer = buffer.split('\n', 1)
                process_line(line)

        try:
            self.status_update.emit(f"⚙️ Querying {self.model} for search terms...")
            self.llm.query(
                question=prompt,
                selected_model=self.model,
                allowed_docs=[], # No context needed for term generation
                callback=handle_chunk,
                rag_enabled=False, 
                use_agents=False, 
                custom_system_prompt="You are a strict output generator. Follow format rules exactly. Do not include conversational filler."
            )
            
            if buffer.strip():
                process_line(buffer)
                
        except Exception as e:
            self.status_update.emit(f"Error: {str(e)}")
            
        if not found_any:
            self.status_update.emit("⚠️ Generation Complete. (AI provided no external search terms)")
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

        models = self.llm_manager.get_available_models()
        if models:
            self.view.model_combo.addItems(models)

        self.view.generate_requested.connect(self._handle_generate)
        self.view.change_custom_url_requested.connect(self._handle_custom_url_change)
        
        # Connect manual searches (if you added those in the prior prompt)
        if hasattr(self.view, 'manual_search_jstor'):
            self.view.manual_search_jstor.connect(lambda t: QDesktopServices.openUrl(QUrl(self.model.get_jstor_url(t))))
            self.view.manual_search_scholar.connect(lambda t: QDesktopServices.openUrl(QUrl(self.model.get_scholar_url(t))))
            self.view.manual_search_custom.connect(lambda t: QDesktopServices.openUrl(QUrl(self.model.get_custom_url(t))))
            self.view.manual_search_google.connect(lambda t: QDesktopServices.openUrl(QUrl(self.model.get_google_url(t))))

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
        self.worker.citation_received.connect(self._on_citation_received) # <--- Connect New Signal
        self.worker.status_update.connect(lambda msg: self.view.set_status(msg))
        self.worker.finished_generation.connect(self._on_generation_finished)
        self.worker.start()

    def _on_term_received(self, term, reason):
        clean_term = term.strip() 
        card = self.view.add_term_card(clean_term, reason)
        card.open_jstor.connect(lambda t: QDesktopServices.openUrl(QUrl(self.model.get_jstor_url(t))))
        card.open_scholar.connect(lambda t: QDesktopServices.openUrl(QUrl(self.model.get_scholar_url(t))))
        card.open_custom.connect(lambda t: QDesktopServices.openUrl(QUrl(self.model.get_custom_url(t))))

    def _on_citation_received(self, doc_name, text, score):
        """Creates the UI card and hooks up the jump logic."""
        card = self.view.add_citation_card(doc_name, text, score)
        card.jump_requested.connect(self._jump_to_pdf_citation)

    def _jump_to_pdf_citation(self, doc_name, text):
        """Silently searches the PDF for the exact citation text and pans the camera to it."""
        pm = self.main_window.project_manager
        target_path = next((p for p in pm.pdfs if doc_name in os.path.basename(p)), None)
        if target_path:
            self.main_window.switch_to_pdf(target_path)
            
            viewer = self.main_window.viewer
            if not viewer.search_bar.isVisible():
                viewer.toggle_search_bar()
                
            viewer.search_bar.search_input.setText(text)
            
            # Execute the search natively in the PDF Viewer to highlight and pan to the sentence
            viewer.execute_search(text, "Current Document", False)

    def _on_generation_finished(self):
        self.view.set_loading_state(False)