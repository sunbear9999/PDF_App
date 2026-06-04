# core/utils/doc_parser.py
import fitz

class DocumentParser:
    @staticmethod
    def chunk_document_for_analysis(doc_path: str, template_id: str, template_instructions: str, template_schema: str, chunk_size=4) -> list:
        """Headless utility to chunk PDFs into manageable arrays for the AI pipeline."""
        try:
            doc = fitz.open(doc_path)
            chunks = []
            for i in range(0, doc.page_count, chunk_size):
                chunk_text = ""
                for j in range(i, min(i+chunk_size, doc.page_count)):
                    chunk_text += f"\n--- Page {j+1} ---\n" + doc.load_page(j).get_text()
                
                chunks.append({
                    "doc_path": doc_path,
                    "template_id": template_id,
                    "template_instructions": template_instructions,
                    "template_schema": template_schema,
                    "chunk_index": i // chunk_size,
                    "page_range": f"{i+1}-{min(i+chunk_size, doc.page_count)}",
                    "text": chunk_text
                })
            doc.close()
            return chunks
        except Exception as e:
            print(f"[DocParser] Error chunking document: {e}")
            return []