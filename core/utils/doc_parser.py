# core/utils/doc_parser.py
import fitz

class DocumentParser:
    @staticmethod
    def chunk_document_for_analysis(doc_path: str, template_id: str, template_instructions: str, template_schema: str, chunk_size=4, max_chars_per_chunk=None) -> list:
        """Headless utility to chunk PDFs into manageable arrays for the AI pipeline."""
        try:
            doc = fitz.open(doc_path)
            chunks = []
            chunk_pages = []
            chunk_text = ""
            chunk_start = 1

            def flush_chunk():
                nonlocal chunk_text, chunk_pages, chunk_start
                if not chunk_text.strip():
                    return
                chunks.append({
                    "doc_path": doc_path,
                    "template_id": template_id,
                    "template_instructions": template_instructions,
                    "template_schema": template_schema,
                    "chunk_index": len(chunks),
                    "page_range": f"{chunk_start}-{chunk_pages[-1] if chunk_pages else chunk_start}",
                    "text": chunk_text
                })
                chunk_pages = []
                chunk_text = ""

            for page_idx in range(doc.page_count):
                page_num = page_idx + 1
                page_text = f"\n--- Page {page_num} ---\n" + doc.load_page(page_idx).get_text()
                if not chunk_pages:
                    chunk_start = page_num

                would_exceed_pages = len(chunk_pages) >= max(1, int(chunk_size or 1))
                would_exceed_chars = bool(max_chars_per_chunk and chunk_text and len(chunk_text) + len(page_text) > max_chars_per_chunk)
                if would_exceed_pages or would_exceed_chars:
                    flush_chunk()
                    chunk_start = page_num

                if max_chars_per_chunk and len(page_text) > max_chars_per_chunk:
                    marker = f"\n--- Page {page_num}"
                    body = page_text
                    part = 1
                    while body:
                        chunk_pages = [page_num]
                        chunk_text = f"{marker} part {part} ---\n{body[:max_chars_per_chunk]}"
                        flush_chunk()
                        body = body[max_chars_per_chunk:]
                        part += 1
                    continue

                chunk_pages.append(page_num)
                chunk_text += page_text
            flush_chunk()
            doc.close()
            return chunks
        except Exception as e:
            print(f"[DocParser] Error chunking document: {e}")
            return []
