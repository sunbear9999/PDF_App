import pytesseract
from PIL import Image

from core.services.document.ocr_engine_setup import ocr_engine_status, tesseract_path
from core.services.document.pdf_ocr import run_pdf_ocr


def _configure_tesseract_cmd():
    path = tesseract_path()
    if path:
        pytesseract.pytesseract.tesseract_cmd = path


def run_ocr_on_image(image_path):
    """
    OCR a standalone image file (png/jpg/tiff/bmp/webp/...), returning
    word-level data (text + bbox + confidence) via pytesseract.image_to_data
    — not just image_to_string — so images get the same highlight-quality
    granularity as PDF pages (word rects). Used by ImageOcrParser to build
    DocumentBlocks with populated `bbox`.
    """
    status = ocr_engine_status()
    if not status["ready"]:
        return {"error": status["error"]}
    _configure_tesseract_cmd()
    try:
        with Image.open(image_path) as img:
            img = img.convert("RGB")
            return pytesseract.image_to_data(img, output_type=pytesseract.Output.DICT)
    except Exception as e:
        return {"error": str(e)}


def run_ocr_on_pdf(pdf_path, mode="text", save_path=None, progress_callback=None, cancel_check=None, report=None,
                   reserve_workers=None):
    """
    Runs OCR on the pages that lack usable text.
    mode="text" returns the recognised text.
    mode="pdf" writes a searchable copy to save_path (original page images
    kept, invisible text added) and returns the text for preview.
    Errors are returned as a string starting with "OCR Engine Error".
    ``report`` receives ``failed_pages`` (1-based) that could not be read.
    """
    return run_pdf_ocr(pdf_path, mode=mode, save_path=save_path,
                       progress_callback=progress_callback, cancel_check=cancel_check,
                       report=report, reserve_workers=reserve_workers)
