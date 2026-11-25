"""
ImageOcrParser — OCRs a standalone image file (png/jpg/tiff/bmp/webp/...)
into DocumentBlocks using word-level Tesseract output (core.ocr_engine.run_ocr_on_image),
grouped by Tesseract's own block/paragraph/line numbering into paragraph
blocks with a populated `bbox` (pixel bounding rectangle) — the same
highlight-quality granularity PDF blocks have via bbox, letting images
support click-to-highlight the same way PDF word-rects do.
"""
from __future__ import annotations

import importlib.util
import shutil
from typing import Any, Dict, List, Tuple

from core.models.extraction_models import DocumentBlock
from core.services.extraction.formats._shared import make_provenance, normalize_text

_EXTRACTOR_NAME = "image_ocr_parser"
_EXTRACTOR_VERSION = "1.0"
_PARSER_PROVIDER = "image_ocr_parser"
_MIN_WORD_CONFIDENCE = 0  # Tesseract returns -1 confidence for non-text regions


class ImageOcrParser:
    provider_id = _PARSER_PROVIDER

    @staticmethod
    def is_available() -> bool:
        return (
            importlib.util.find_spec("pytesseract") is not None
            and shutil.which("tesseract") is not None
        )

    def parse(self, path: str, doc_id: str) -> List[DocumentBlock]:
        try:
            from core.ocr_engine import run_ocr_on_image
        except ImportError:
            return []
        data = run_ocr_on_image(path)
        if not data or "error" in data or not data.get("text"):
            return []
        return self._blocks_from_ocr_data(data, doc_id)

    @staticmethod
    def _blocks_from_ocr_data(data: Dict[str, list], doc_id: str) -> List[DocumentBlock]:
        groups: Dict[Tuple[int, int, int], Dict[str, Any]] = {}
        order: List[Tuple[int, int, int]] = []
        word_count = len(data.get("text", []))

        for i in range(word_count):
            text = (data["text"][i] or "").strip()
            if not text:
                continue
            try:
                confidence = float(data["conf"][i])
            except (ValueError, TypeError):
                confidence = -1
            if confidence < _MIN_WORD_CONFIDENCE:
                continue

            key = (data["block_num"][i], data["par_num"][i], data["line_num"][i])
            if key not in groups:
                groups[key] = {"words": [], "x0": None, "y0": None, "x1": None, "y1": None}
                order.append(key)

            group = groups[key]
            group["words"].append(text)
            x, y, w, h = data["left"][i], data["top"][i], data["width"][i], data["height"][i]
            group["x0"] = x if group["x0"] is None else min(group["x0"], x)
            group["y0"] = y if group["y0"] is None else min(group["y0"], y)
            group["x1"] = (x + w) if group["x1"] is None else max(group["x1"], x + w)
            group["y1"] = (y + h) if group["y1"] is None else max(group["y1"], y + h)

        provenance = make_provenance(_EXTRACTOR_NAME, _EXTRACTOR_VERSION, _PARSER_PROVIDER)
        blocks: List[DocumentBlock] = []
        for idx, key in enumerate(order):
            group = groups[key]
            text = " ".join(group["words"]).strip()
            if not text:
                continue
            blocks.append(DocumentBlock(
                block_id=f"{doc_id}_b{idx}",
                doc_id=doc_id,
                page_num=None,
                block_type="paragraph",
                exact_text=text,
                normalized_text=normalize_text(text),
                bbox=[float(group["x0"]), float(group["y0"]), float(group["x1"]), float(group["y1"])],
                provenance=provenance,
            ))
        return blocks
