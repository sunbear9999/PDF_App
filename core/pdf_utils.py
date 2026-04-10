import fitz  # PyMuPDF

def extract_filtered_blocks(pdf_path: str, ignore_margins: bool = True, start_page: int = 1, end_page: int = None) -> str:
    """
    Extracts text, filters out images/metadata, and optionally ignores headers/footers.
    Supports specific page ranges.
    """
    clean_text = ""
    try:
        doc = fitz.open(pdf_path)
        
        # Adjust for 0-based indexing
        start_idx = max(0, start_page - 1)
        end_idx = min(len(doc) - 1, end_page - 1) if end_page else len(doc) - 1
        
        for page_num in range(start_idx, end_idx + 1):
            page = doc.load_page(page_num)
            page_height = page.rect.height
            
            # get_text("blocks") returns: (x0, y0, x1, y1, "text", block_no, block_type)
            blocks = page.get_text("blocks")
            
            for block in blocks:
                block_type = block[6]
                # Type 0 is text. Type 1 is image/vector graphic (which causes the HTML junk).
                if block_type != 0:
                    continue
                    
                y0 = block[1] 
                y1 = block[3] 
                text = block[4]
                
                # Filter margins
                if ignore_margins:
                    if y1 < (page_height * 0.10) or y0 > (page_height * 0.90):
                        continue 
                        
                clean_text += text + "\n"
                
        doc.close()
        return clean_text.strip()
    except Exception as e:
        return f"Error processing PDF: {str(e)}"