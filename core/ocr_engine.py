import fitz  # PyMuPDF
import pytesseract
from PIL import Image
import io
import os
<<<<<<< HEAD
import platform

=======
>>>>>>> e7346c3e8bd3f0dbfd96400e56ce8826fed31f2b

def run_ocr_on_pdf(pdf_path, mode="text", save_path=None, progress_callback=None):
    """
    Runs OCR. 
    mode="text" returns just the string.
    mode="pdf" creates a new searchable PDF at save_path, and returns the string for preview.
    """
    try:
        doc = fitz.open(pdf_path)
        total_pages = len(doc)
        full_text = ""
        
        out_pdf = fitz.open() if mode == "pdf" else None

        for page_num in range(total_pages):
            if progress_callback:
                progress_callback(page_num + 1, total_pages)

            page = doc.load_page(page_num)
            zoom = 2.0 
            mat = fitz.Matrix(zoom, zoom)
            pix = page.get_pixmap(matrix=mat)
            
            img_data = pix.tobytes("png")
            img = Image.open(io.BytesIO(img_data))
<<<<<<< HEAD
            if platform.system() == "Windows":
                # Explicitly tell pytesseract where the installer put the binary
                tess_path = r"C:\Program Files\Tesseract-OCR\tesseract.exe"
                if os.path.exists(tess_path):
                    pytesseract.pytesseract.tesseract_cmd = tess_path
=======
            
>>>>>>> e7346c3e8bd3f0dbfd96400e56ce8826fed31f2b
            if mode == "text":
                text = pytesseract.image_to_string(img)
                full_text += f"\n\n--- Page {page_num + 1} ---\n\n{text}"
            elif mode == "pdf":
                # Create a searchable PDF page and append it to our output document
                pdf_bytes = pytesseract.image_to_pdf_or_hocr(img, extension='pdf')
                page_doc = fitz.open("pdf", pdf_bytes)
                out_pdf.insert_pdf(page_doc)
                page_doc.close()
                
                # Still grab the text for the UI preview
                full_text += pytesseract.image_to_string(img) + "\n"

        doc.close()

        if mode == "pdf" and save_path:
            out_pdf.save(save_path)
            out_pdf.close()

        return full_text

    except Exception as e:
        return f"OCR Engine Error: {str(e)}"