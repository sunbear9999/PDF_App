import os
import json
import re
import sqlite3

import fitz
from PyQt6.QtCore import pyqtSignal, QThread

class AIIndexingWorker(QThread):
    progress = pyqtSignal(str)
    finished_all = pyqtSignal(bool, str)
    pdf_mapped = pyqtSignal(str)

    def __init__(self, llm_manager, model_name, project_filepath, pdf_paths=None, parent=None):
        super().__init__(parent)
        self.llm_manager = llm_manager
        self.model_name = model_name
        self.project_filepath = project_filepath
        self.pdf_paths = pdf_paths or []
        self.db_conn = None

    def _chunk_text(self, text, chunk_size=1600):
        words = re.split(r"\s+", text.strip())
        chunks = []
        for idx in range(0, len(words), chunk_size):
            chunk = " ".join(words[idx: idx + chunk_size]).strip()
            if chunk:
                chunks.append(chunk)
        return chunks

    def _extract_json_object(self, text):
        start = None
        stack = []
        for idx, char in enumerate(text):
            if char == '{':
                if start is None:
                    start = idx
                stack.append(char)
            elif char == '}' and stack:
                stack.pop()
                if not stack and start is not None:
                    return text[start:idx + 1]
        return None

    def _safe_json_parse(self, raw_text):
        if not raw_text:
            return None

        try:
            return json.loads(raw_text)
        except Exception:
            try:
                normalized = re.sub(r'[^\x00-\x7F\n\r{}\[\]",:]+', "", raw_text)
                return json.loads(normalized)
            except Exception:
                pass

        candidate = self._extract_json_object(raw_text)
        if candidate:
            try:
                return json.loads(candidate)
            except Exception:
                try:
                    normalized = re.sub(r'[^\x00-\x7F\n\r{}\[\]",:]+', "", candidate)
                    return json.loads(normalized)
                except Exception:
                    return None
        return None

    def run(self):
        try:
            print("[AIIndexingWorker] run() started")
            if not self.project_filepath:
                print("[AIIndexingWorker] No project filepath provided")
                self.finished_all.emit(False, "No project filepath")
                return

            self.db_conn = sqlite3.connect(self.project_filepath)
            print(f"[AIIndexingWorker] Opened database connection: {self.project_filepath}")
            cursor = self.db_conn.cursor()
            cursor.execute('''CREATE TABLE IF NOT EXISTS document_maps (pdf_path TEXT PRIMARY KEY, json_map TEXT)''')
            self.db_conn.commit()

            if self.pdf_paths:
                queue = self._filter_unmapped_pdfs(self.pdf_paths)
            else:
                queue = self._get_unmapped_pdfs()
            print(f"[AIIndexingWorker] Found {len(queue)} unmapped PDFs: {queue}")
            if not queue:
                print("[AIIndexingWorker] No unmapped PDFs, emitting finished")
                self.progress.emit("No new PDFs found for GraphRAG indexing.")
                self.finished_all.emit(True, "No unmapped PDFs found.")
                if self.db_conn:
                    self.db_conn.close()
                return

            for pdf_path in queue:
                doc_name = pdf_path and pdf_path.split(os.sep)[-1] if pdf_path else "Unknown PDF"
                print(f"[AIIndexingWorker] Processing {doc_name}")
                try:
                    msg = f"[{doc_name}] Opening document for GraphRAG extraction..."
                    print(f"[AIIndexingWorker] Emitting: {msg}")
                    self.progress.emit(msg)
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
                        self.progress.emit(f"[{doc_name}] No readable text could be extracted.")
                        continue

                    chunks = self._chunk_text(full_text)
                    print(f"[AIIndexingWorker] Split into {len(chunks)} chunks")
                    chunk_maps = []
                    for idx, chunk in enumerate(chunks, start=1):
                        msg = f"[{doc_name}] Generating argument map chunk {idx}/{len(chunks)}..."
                        print(f"[AIIndexingWorker] {msg}")
                        self.progress.emit(msg)
                        prompt = (
                            "Read the excerpt and produce ONLY a single valid JSON object with exactly these keys: "
                            "\"Main Claim\", \"Supporting Points\", and \"Counterarguments\". "
                            "Do not include any extra text, explanation, or markdown. "
                            "Use as few tokens as possible while still fully populating every field. "
                            "If the passage does not include a clear opposing claim, summarize a relevant caveat, limitation, or alternative perspective under Counterarguments. "
                            "If the excerpt is not argumentative, still populate each field with a concise summary of the author’s train of thought.\n\n"
                            f"TEXT:\n{chunk}\n"
                        )
                        print(f"[AIIndexingWorker] Calling generate_response for chunk {idx}")
                        response = self.llm_manager.generate_response(
                            prompt,
                            self.model_name,
                            system_prompt="You are a concise argument map extractor. Output only valid JSON with no extra text.",
                            stream=False,
                            options={"temperature": 0.0, "max_tokens": 260, "top_p": 1.0, "num_predict": 200}
                        )
                        print(f"[AIIndexingWorker] Got response length: {len(response) if response else 0}")
                        parsed = self._safe_json_parse(response or "")
                        if isinstance(parsed, dict):
                            chunk_maps.append(parsed)
                            print(f"[AIIndexingWorker] Successfully parsed chunk {idx}")
                        else:
                            self.progress.emit(f"[{doc_name}] Chunk {idx} did not return a valid JSON object.")
                            print(f"[AIIndexingWorker] Failed to parse chunk {idx}. Response: {response}")

                    if not chunk_maps:
                        self.progress.emit(f"[{doc_name}] Unable to parse chunk-level maps; trying a single concise document-level map.")
                        print(f"[AIIndexingWorker] No chunk maps created, trying full-document fallback")
                        fallback_text = full_text[:18000]
                        fallback_prompt = (
                            "Read the text below and output ONLY a single valid JSON object with exactly these keys: "
                            "\"Main Claim\", \"Supporting Points\", and \"Counterarguments\". "
                            "Do not include any extra text or markdown. Use as few tokens as possible while still fully populating every field. "
                            "If the text lacks a clear counterargument, summarize a related nuance or alternative perspective under Counterarguments.\n\n"
                            f"TEXT:\n{fallback_text}\n"
                        )
                        fallback_response = self.llm_manager.generate_response(
                            fallback_prompt,
                            self.model_name,
                            system_prompt="You are a concise argument map extractor. Output only valid JSON with no extra text.",
                            stream=False,
                            options={"temperature": 0.15, "max_tokens": 260, "top_p": 1.0, "num_predict": 200}
                        )
                        print(f"[AIIndexingWorker] Fallback response length: {len(fallback_response) if fallback_response else 0}")
                        parsed = self._safe_json_parse(fallback_response or "")
                        if isinstance(parsed, dict):
                            final_map = parsed
                            print(f"[AIIndexingWorker] Fallback map parsed successfully")
                        else:
                            print(f"[AIIndexingWorker] Fallback parse failed. Response: {fallback_response}")
                            final_map = {
                                "Main Claim": "",
                                "Supporting Points": "",
                                "Counterarguments": ""
                            }
                    else:
                        msg = f"[{doc_name}] Consolidating chunk maps into a global JSON map..."
                        print(f"[AIIndexingWorker] {msg}")
                        self.progress.emit(msg)
                        reduce_payload = json.dumps(chunk_maps, ensure_ascii=False, indent=2)
                        reduce_prompt = (
                            "You are consolidating a set of JSON argument maps from different chunks of the same document. "
                            "Produce a single JSON object with exactly these keys: \"Main Claim\", "
                            "\"Supporting Points\", and \"Counterarguments\". "
                            "Combine and reduce the content, removing duplicates and keeping the structure strict. "
                            "Output only valid JSON.\n\n"
                            f"CHUNK_MAPS:\n{reduce_payload}\n"
                        )
                        print(f"[AIIndexingWorker] Calling generate_response for reduce pass")
                        reduced = self.llm_manager.generate_response(
                            reduce_prompt,
                            self.model_name,
                            system_prompt="You are a concise argument map consolidator. Output only a single valid JSON object with the required keys and no extra text.",
                            stream=False,
                            options={"temperature": 0.15, "max_tokens": 1000, "top_p": 1.0, "num_predict": 400}
                        )
                        print(f"[AIIndexingWorker] Got reduced response length: {len(reduced) if reduced else 0}")
                        print(f"[AIIndexingWorker] Raw reduce response: {reduced}")
                        consolidated = self._safe_json_parse(reduced or "")
                        if isinstance(consolidated, dict):
                            final_map = consolidated
                            print(f"[AIIndexingWorker] Successfully consolidated map")
                        else:
                            self.progress.emit(f"[{doc_name}] Final reduce pass returned invalid JSON; using fallback structure.")
                            print(f"[AIIndexingWorker] Failed to consolidate, using fallback. Raw reduce response: {reduced}")
                            final_map = {
                                "Main Claim": "",
                                "Supporting Points": "",
                                "Counterarguments": ""
                            }

                    final_json = json.dumps(final_map, ensure_ascii=False)
                    print(f"[AIIndexingWorker] Saving document map for {pdf_path}")
                    saved = self._save_document_map(pdf_path, final_json)
                    print(f"[AIIndexingWorker] Save result: {saved}")
                    self.pdf_mapped.emit(pdf_path)
                    self.progress.emit(f"[{doc_name}] Cached logic map successfully.")

                except Exception as e:
                    print(f"[AIIndexingWorker] Exception processing {doc_name}: {e}")
                    import traceback
                    traceback.print_exc()
                    self.progress.emit(f"[{doc_name}] GraphRAG indexing failed: {e}")
                    continue

            print("[AIIndexingWorker] Finished all PDFs")
            if self.db_conn:
                self.db_conn.close()
            self.finished_all.emit(True, "GraphRAG indexing complete.")
        except Exception as e:
            print(f"[AIIndexingWorker] Fatal exception in run(): {e}")
            import traceback
            traceback.print_exc()
            if self.db_conn:
                self.db_conn.close()
            self.finished_all.emit(False, str(e))

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
