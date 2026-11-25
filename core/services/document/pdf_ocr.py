"""OCR a PDF in place: add an invisible text layer only where text is missing.

Pages that already carry usable text are left untouched. A scanned page keeps
its original image and geometry and gains Tesseract's text-only layer on top,
so highlights, anchors and page sizes do not move. A page whose hidden OCR
layer is garbled has that layer removed first; a page whose *visible* text is
garbled (broken font encoding) is replaced by a raster of itself plus a new
text layer.

Rendering and PDF edits stay on the calling thread (PyMuPDF documents are not
thread-safe); only the Tesseract subprocesses run in parallel.
"""
from __future__ import annotations

import io
import os
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FutureTimeout
from dataclasses import dataclass
from typing import Callable, Dict, List, Optional

import fitz  # PyMuPDF
import pytesseract
from PIL import Image

from core.services.document.ocr_engine_setup import ocr_engine_status
from core.services.extraction.pdf_layout_parser import ocr_text_layer
from core.services.extraction.text_layer_quality import text_is_garbled

OCR_DPI = 300                 # Tesseract's recommended input resolution.
MIN_PAGE_CHARS = 25           # Matches the text-layer check's empty threshold.
MAX_PARALLEL_PAGES = 4        # Bounded by memory: one Tesseract process each.
# A page image above this is downscaled. 16 MP is A3 at 300 DPI and keeps each
# Tesseract process well under 1 GB; a 120 MP render froze a 7 GB machine.
MAX_OCR_PIXELS = 16_000_000
MIN_LONG_SIDE_PX = 2200       # Upscale low-resolution scans to at least this.
# Scans are often placed at 72 DPI, making a book page "45 inches" tall. Such a
# page is assumed to be a normal page when telling Tesseract its resolution.
MAX_PLAUSIBLE_PAGE_INCHES = 17.0
TYPICAL_PAGE_INCHES = 11.0
MIN_TESSERACT_DPI, MAX_TESSERACT_DPI = 70, 1200
PAGE_TIMEOUT_SECONDS = 180    # One unreadable page must not stall the run.
CANCEL_POLL_SECONDS = 0.5
CANCELLED_MESSAGE = "OCR Engine Error: OCR was cancelled."
ERROR_PREFIX = "OCR Engine Error: "

SKIP, OVERLAY, CLEAN_OVERLAY, RASTER = "skip", "overlay", "clean_overlay", "raster"


@dataclass
class _PageJob:
    index: int
    action: str
    rotation: int
    png: bytes
    dpi: int


def plan_page(page) -> str:
    """What OCR must do to this page so its text becomes usable."""
    text = page.get_text("text") or ""
    if len(text.strip()) < MIN_PAGE_CHARS:
        return OVERLAY if page.get_images(full=False) else SKIP
    if not text_is_garbled(text):
        return SKIP
    return CLEAN_OVERLAY if ocr_text_layer(page) else RASTER


def parallel_pages() -> int:
    return max(1, min(MAX_PARALLEL_PAGES, (os.cpu_count() or 2) - 1))


def render_scale(page) -> float:
    """Pixels per PDF point: the scan's own resolution, never upscaled past
    300 DPI or above the pixel cap, and not so small that text is unreadable."""
    rect = page.rect
    scale = OCR_DPI / 72
    native = _native_scale(page)
    if native:
        scale = min(scale, native)
    long_side = max(rect.width, rect.height) or 1.0
    scale = max(scale, min(OCR_DPI / 72, MIN_LONG_SIDE_PX / long_side))
    pixels = rect.width * rect.height * scale * scale
    if pixels > MAX_OCR_PIXELS:
        scale *= (MAX_OCR_PIXELS / pixels) ** 0.5
    return scale


def _native_scale(page) -> float:
    best = 0.0
    for info in page.get_image_info():
        width = info["bbox"][2] - info["bbox"][0]
        if width > 0 and info.get("width"):
            best = max(best, info["width"] / width)
    return best


def tesseract_dpi(page, scale: float) -> int:
    long_points = max(page.rect.width, page.rect.height) or 1.0
    inches = long_points / 72
    if inches > MAX_PLAUSIBLE_PAGE_INCHES:
        inches = TYPICAL_PAGE_INCHES
    dpi = long_points * scale / inches
    return int(min(MAX_TESSERACT_DPI, max(MIN_TESSERACT_DPI, dpi)))


def run_pdf_ocr(pdf_path: str, mode: str = "text", save_path: Optional[str] = None,
                progress_callback: Optional[Callable[[int, int], None]] = None,
                cancel_check: Optional[Callable[[], bool]] = None,
                report: Optional[dict] = None,
                reserve_workers: Optional[Callable[[int], int]] = None) -> str:
    """Return recognised text ("text" mode) or write a searchable PDF ("pdf").

    Errors come back as a string starting with ``OCR Engine Error:`` — the
    contract OCRAppService already relies on. ``report`` (if given) receives
    ``failed_pages``: 1-based pages Tesseract could not read.
    ``reserve_workers(maximum)`` (if given) frees memory first and returns how
    many Tesseract processes fit (the central memory manager).
    """
    status = ocr_engine_status()
    if not status["ready"]:
        return ERROR_PREFIX + str(status["error"])
    cancel_check = cancel_check or (lambda: False)
    progress = progress_callback or (lambda _current, _total: None)
    report = report if report is not None else {}
    report["failed_pages"] = []
    try:
        doc = fitz.open(pdf_path)
    except Exception as exc:
        return ERROR_PREFIX + str(exc)
    try:
        texts = _process(doc, mode, progress, cancel_check, str(status["path"]), report, reserve_workers)
        if texts is None:
            return CANCELLED_MESSAGE
        if report.get("all_failed"):
            return ERROR_PREFIX + "Tesseract could not read any page: " + report["last_error"]
        if mode == "pdf" and save_path:
            doc.save(save_path, garbage=3, deflate=True)
        return "".join(f"\n\n--- Page {index + 1} ---\n\n{texts[index]}" for index in sorted(texts))
    except Exception as exc:
        return ERROR_PREFIX + str(exc)
    finally:
        doc.close()


def _process(doc, mode, progress, cancel_check, tesseract_cmd, report,
             reserve_workers=None) -> Optional[Dict[int, str]]:
    plan = [(index, plan_page(doc.load_page(index))) for index in range(doc.page_count)]
    texts = {index: doc.load_page(index).get_text("text") for index, action in plan if action == SKIP}
    pending = [(index, action) for index, action in plan if action != SKIP]
    total, done = doc.page_count, len(texts)
    progress(done, total)
    # The memory manager frees idle models first and says how many fit.
    workers = parallel_pages()
    if reserve_workers is not None and pending:
        workers = max(1, min(workers, int(reserve_workers(workers) or 1)))
    with ThreadPoolExecutor(max_workers=workers) as pool:
        # One page per worker in flight: bounded memory, cancel within a page.
        for start in range(0, len(pending), workers):
            if cancel_check():
                return None
            jobs = [_render(doc, index, action) for index, action in pending[start:start + workers]]
            futures = [pool.submit(_tesseract, job, mode, tesseract_cmd) for job in jobs]
            for job, future in zip(jobs, futures):
                outcome = _await(future, cancel_check)
                if outcome is _CANCELLED:
                    pool.shutdown(wait=True, cancel_futures=True)
                    return None
                texts[job.index] = _finish(doc, job, outcome, mode, report)
                done += 1
                progress(done, total)
    if pending and len(report["failed_pages"]) == len(pending):
        report["all_failed"] = True
    return texts


_CANCELLED = object()


def _await(future, cancel_check):
    while True:
        if cancel_check():
            return _CANCELLED
        try:
            return future.result(timeout=CANCEL_POLL_SECONDS)
        except FutureTimeout:
            continue
        except Exception as exc:  # Tesseract failed or timed out on this page.
            return exc


def _finish(doc, job: _PageJob, outcome, mode: str, report) -> str:
    if isinstance(outcome, Exception):
        report["failed_pages"].append(job.index + 1)
        report["last_error"] = str(outcome) or type(outcome).__name__
        _restore_rotation(doc.load_page(job.index), job.rotation)
        return ""
    return _apply(doc, job, outcome, mode)


def _render(doc, index: int, action: str) -> _PageJob:
    page = doc.load_page(index)
    rotation = page.rotation
    if rotation:
        # Overlay coordinates are unrotated; render the same frame.
        page.set_rotation(0)
    scale = render_scale(page)
    colorspace = fitz.csRGB if action == RASTER else fitz.csGRAY
    pix = page.get_pixmap(matrix=fitz.Matrix(scale, scale), colorspace=colorspace, annots=False)
    return _PageJob(index, action, rotation, pix.tobytes("png"), tesseract_dpi(page, scale))


def _tesseract(job: _PageJob, mode: str, tesseract_cmd: str):
    pytesseract.pytesseract.tesseract_cmd = tesseract_cmd
    # One thread per Tesseract process; parallelism comes from the pool.
    os.environ.setdefault("OMP_THREAD_LIMIT", "1")
    config = f"--dpi {job.dpi}"
    with Image.open(io.BytesIO(job.png)) as image:
        if mode == "pdf":
            return pytesseract.image_to_pdf_or_hocr(
                image, extension="pdf", config=config + " -c textonly_pdf=1",
                timeout=PAGE_TIMEOUT_SECONDS)
        return pytesseract.image_to_string(image, config=config, timeout=PAGE_TIMEOUT_SECONDS)


def _apply(doc, job: _PageJob, result, mode: str) -> str:
    page = doc.load_page(job.index)
    if mode != "pdf":
        _restore_rotation(page, job.rotation)
        return str(result or "")
    if job.action == CLEAN_OVERLAY:
        _remove_text(page, fitz.PDF_REDACT_TEXT_REMOVE_INVISIBLE)
    elif job.action == RASTER:
        _remove_text(page, fitz.PDF_REDACT_TEXT_REMOVE, everything=True)
        page.insert_image(page.rect, stream=job.png)
    with fitz.open("pdf", result) as layer:
        page.show_pdf_page(page.rect, layer, 0, keep_proportion=False, overlay=True)
        text = layer.load_page(0).get_text("text")
    _restore_rotation(page, job.rotation)
    return text


def _remove_text(page, text_mode: int, everything: bool = False) -> None:
    page.add_redact_annot(page.rect)
    page.apply_redactions(
        images=fitz.PDF_REDACT_IMAGE_REMOVE if everything else fitz.PDF_REDACT_IMAGE_NONE,
        graphics=fitz.PDF_REDACT_LINE_ART_REMOVE_IF_TOUCHED if everything else fitz.PDF_REDACT_LINE_ART_NONE,
        text=text_mode,
    )


def _restore_rotation(page, rotation: int) -> None:
    if rotation:
        page.set_rotation(rotation)


def pages_needing_ocr(pdf_path: str) -> List[int]:
    """Zero-based pages that OCR would change (used by tests and diagnostics)."""
    with fitz.open(pdf_path) as doc:
        return [index for index in range(doc.page_count) if plan_page(doc.load_page(index)) != SKIP]
