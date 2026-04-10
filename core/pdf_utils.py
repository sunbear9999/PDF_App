import fitz  # PyMuPDF

from core.text_utils import sanitize_extracted_text

def extract_filtered_blocks(pdf_path: str, ignore_margins: bool = True, start_page: int = 1, end_page: int = None) -> str:
    """
    Extracts text, filters out images/metadata, and optionally ignores headers/footers.
    Supports specific page ranges.
    """
    collected_pages = []
    try:
        with fitz.open(pdf_path) as doc:
            # Adjust for 0-based indexing
            start_idx = max(0, start_page - 1)
            end_idx = min(len(doc) - 1, end_page - 1) if end_page else len(doc) - 1

            for page_num in range(start_idx, end_idx + 1):
                page = doc.load_page(page_num)
                page_height = page.rect.height

                # Using words instead of blocks avoids hidden annotation anchor fragments from highlights.
                words = page.get_text("words", sort=True)
                page_words = []
                for w in words:
                    y0 = w[1]
                    y1 = w[3]
                    if ignore_margins and (y1 < (page_height * 0.10) or y0 > (page_height * 0.90)):
                        continue

                    word = sanitize_extracted_text(w[4], collapse_whitespace=True)
                    if word:
                        page_words.append(word)

                page_text = sanitize_extracted_text(" ".join(page_words), collapse_whitespace=True)
                if page_text:
                    collected_pages.append(page_text)

        return "\n\n".join(collected_pages).strip()
    except Exception as e:
        return f"Error processing PDF: {str(e)}"