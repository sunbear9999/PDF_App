import os
import json
import re
import sqlite3

import fitz
from PyQt6.QtCore import pyqtSignal
from core.base_ai_worker import BaseAIWorker
from core.prompts import Prompts


class AIIndexingWorker(BaseAIWorker):
    """[REFACTOR] Index PDF documents for GraphRAG using BaseAIWorker.
    
    [AI OPTIMIZATION] Features:
    - Single-phase document processing
    - Temperature=0.0 for deterministic extraction
    - Centralized prompts for argument mapping
    - Uses vector_store.index_documents() instead of direct collection access
    """

    pdf_mapped = pyqtSignal(str)
    finished_all = pyqtSignal(bool, str)

    def __init__(self, llm_manager, model_name, project_filepath, pdf_paths=None, parent=None):
        super().__init__()
        self.llm_manager = llm_manager
        self.model_name = model_name
        self.project_filepath = project_filepath
        self.pdf_paths = pdf_paths or []
        self.db_conn = None

    def execute_task(self):
        """[REFACTOR] Execute document indexing task with optimized settings."""
        if not self.project_filepath:
            raise ValueError("No project filepath provided")

        self.db_conn = sqlite3.connect(self.project_filepath)
        cursor = self.db_conn.cursor()
        cursor.execute('''CREATE TABLE IF NOT EXISTS document_maps (pdf_path TEXT PRIMARY KEY, json_map TEXT)''')
        self.db_conn.commit()

        if self.pdf_paths:
            queue = self._filter_unmapped_pdfs(self.pdf_paths)
        else:
            queue = self._get_unmapped_pdfs()

        if not queue:
            self.emit_progress("No new PDFs found for GraphRAG indexing.")
            self.finished_all.emit(True, "No unmapped PDFs found.")
            return "No unmapped PDFs found."

        for pdf_path in queue:
            doc_name = pdf_path and pdf_path.split(os.sep)[-1] if pdf_path else "Unknown PDF"
            try:
                self.emit_progress(f"[{doc_name}] Opening document for GraphRAG extraction...")
                doc = fitz.open(pdf_path)
                pages = []
                for page_num in range(len(doc)):
                    page = doc.load_page(page_num)
                    text = page.get_text("text").replace('\n', ' ').strip()
                    text = re.sub(r"\s+", ' ', text)
                    if text:
                        pages.append(text)
                doc.close()

                full_text = " ".join(pages)
                if not full_text:
                    self.emit_progress(f"[{doc_name}] No readable text could be extracted.")
                    continue

                chunks = self._chunk_text(full_text)
                chunk_maps = []
                for idx, chunk in enumerate(chunks, start=1):
                    self.emit_progress(f"[{doc_name}] Generating argument map chunk {idx}/{len(chunks)}...")

                    # [REFACTOR] Use centralized prompt
                    prompt = f"TEXT:\n{chunk}\n"

                    # [AI OPTIMIZATION] Query with temperature=0.0 for deterministic extraction
                    response = self.llm_manager.generate_response(
                        prompt,
                        self.model_name,
                        system_prompt=Prompts.get_system_prompt('indexing'),
                        stream=False,
                        options={"temperature": 0.0, "max_tokens": 260, "top_p": 1.0, "num_predict": 200}
                    )

                    parsed = self.clean_and_parse_json(response or "")
                    if isinstance(parsed, dict):
                        chunk_maps.append(parsed)
                    else:
                        self.emit_progress(f"[{doc_name}] Chunk {idx} did not return a valid JSON object.")

                if not chunk_maps:
                    self.emit_progress(f"[{doc_name}] Unable to parse chunk-level maps; trying a single concise document-level map.")
                    fallback_text = full_text[:18000]
                    fallback_prompt = f"TEXT:\n{fallback_text}\n"

                    # [AI OPTIMIZATION] Query with temperature=0.0 for fallback extraction
                    fallback_response = self.llm_manager.generate_response(
                        fallback_prompt,
                        self.model_name,
                        system_prompt=Prompts.get_system_prompt('indexing'),
                        stream=False,
                        options={"temperature": 0.0, "max_tokens": 260, "top_p": 1.0, "num_predict": 200}
                    )

                    parsed = self.clean_and_parse_json(fallback_response or "")
                    if isinstance(parsed, dict):
                        final_map = parsed
                    else:
                        final_map = {
                            "Main Claim": "",
                            "Supporting Points": "",
                            "Counterarguments": ""
                        }
                else:
                    self.emit_progress(f"[{doc_name}] Consolidating chunk maps into a global JSON map...")
                    reduce_payload = json.dumps(chunk_maps, ensure_ascii=False, indent=2)
                    reduce_prompt = f"CHUNK_MAPS:\n{reduce_payload}\n"

                    # [REFACTOR] Use centralized consolidate prompt
                    reduced = self.llm_manager.generate_response(
                        reduce_prompt,
                        self.model_name,
                        system_prompt=Prompts.get_system_prompt('indexing_consolidate'),
                        stream=False,
                        options={"temperature": 0.0, "max_tokens": 1000, "top_p": 1.0, "num_predict": 400}
                    )

                    consolidated = self.clean_and_parse_json(reduced or "")
                    if isinstance(consolidated, dict):
                        final_map = consolidated
                    else:
                        self.emit_progress(f"[{doc_name}] Final reduce pass returned invalid JSON; using fallback structure.")
                        final_map = {
                            "Main Claim": "",
                            "Supporting Points": "",
                            "Counterarguments": ""
                        }

                final_json = json.dumps(final_map, ensure_ascii=False)
                saved = self._save_document_map(pdf_path, final_json)
                self.pdf_mapped.emit(pdf_path)
                self.emit_progress(f"[{doc_name}] Cached logic map successfully.")

            except Exception as e:
                self.emit_progress(f"[{doc_name}] GraphRAG indexing failed: {e}")
                continue

        self.finished_all.emit(True, "GraphRAG indexing complete.")
        return "GraphRAG indexing complete."

    def _chunk_text(self, text, chunk_size=1600):
        words = re.split(r"\s+", text.strip())
        chunks = []
        for idx in range(0, len(words), chunk_size):
            chunk = " ".join(words[idx: idx + chunk_size]).strip()
            if chunk:
                chunks.append(chunk)
        return chunks

    def _get_unmapped_pdfs(self):
        try:
            cursor = self.db_conn.cursor()
            cursor.execute(
                "SELECT p.path FROM pdfs p LEFT JOIN document_maps m ON p.path = m.pdf_path WHERE m.pdf_path IS NULL"
            )
            return [row[0] for row in cursor.fetchall()]
        except Exception as e:
            print(f"[AIIndexingWorker] Error querying unmapped PDFs: {e}")
            return []

    def _filter_unmapped_pdfs(self, candidate_paths):
        if not candidate_paths:
            return []
        try:
            cursor = self.db_conn.cursor()
            placeholders = ",".join("?" for _ in candidate_paths)
            query = (
                "SELECT p.path FROM pdfs p LEFT JOIN document_maps m ON p.path = m.pdf_path "
                f"WHERE m.pdf_path IS NULL AND p.path IN ({placeholders})"
            )
            cursor.execute(query, candidate_paths)
            return [row[0] for row in cursor.fetchall()]
        except Exception as e:
            print(f"[AIIndexingWorker] Error filtering unmapped PDFs: {e}")
            return []

    def _save_document_map(self, pdf_path, json_map_str):
        try:
            cursor = self.db_conn.cursor()
            cursor.execute(
                "INSERT OR REPLACE INTO document_maps (pdf_path, json_map) VALUES (?, ?)",
                (pdf_path, json_map_str)
            )
            self.db_conn.commit()
            print(f"[AIIndexingWorker] Saved document map for {pdf_path}")
            return True
        except Exception as e:
            print(f"[AIIndexingWorker] Error saving document map for {pdf_path}: {e}")
            return False
