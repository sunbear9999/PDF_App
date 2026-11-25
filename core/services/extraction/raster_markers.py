"""Note markers an OCR layer misread, recovered from the page image (digits only).

Scanned books set note numbers as small raised digits; OCR layers often turn
them into ’ ” ° * ! ? (book.pdf: 43 markers found for 489 notes). A candidate is
a run of such glyphs glued to the end of a word or sentence that is not the
closing quote of an open quotation. Its crop, taken above and after the run,
loses the tail of the preceding letter and is read twice by Tesseract with a
digit whitelist (single word and single line). A number is accepted only when
both readings agree and that number is an existing, unclaimed note in the
marker's scope; nothing is ever inferred from the note sequence. Every other
candidate is reported as unreadable for review.
"""
from __future__ import annotations

import re
import subprocess
from dataclasses import dataclass, field
from typing import Callable, List, Optional, Tuple

from core.services.extraction.block_roles import content_text, is_ocr_text

MARK_GLYPHS = "’”°*!?'\"¹²³⁴⁵⁶⁷⁸⁹⁰"
_CANDIDATE = re.compile(r"(?<=[^\W\d_]|[.,;:)\]?!])([" + re.escape(MARK_GLYPHS) + r"0-9]{1,3})(?=\s|$)")
_CLOSING = {"”", '"', "’"}
SCALE = 4                       # render zoom for the crop
CROP_ABOVE = 0.3                # of line height
CROP_BELOW = 0.75
CROP_WIDTH = 1.4                # of line height, beyond the run
MIN_DIGIT_SHARE = 0.55          # a digit is at least this share of the tallest region's height
MAX_DIGIT_GAP = 0.35            # horizontal gap between digits of one number, as a share of digit height
INK = 140                       # grey level counted as ink
PAD = 16
TESSERACT_TIMEOUT_S = 10
PSM_MODES = ("8", "7", "13")    # single word, single line, raw line
MIN_AGREEING = 2                # readings that must give the same number


@dataclass
class MarkerCandidate:
    block: object
    start: int
    end: int
    glyphs: str
    readings: List[str] = field(default_factory=list)
    number: Optional[int] = None
    reason: str = ""


def candidates(block) -> List[MarkerCandidate]:
    """Glyph runs that may be misread note markers in an OCR body block."""
    if not is_ocr_text(block) or block.structure.get("region", "body") != "body":
        return []
    text = content_text(block)
    found, open_quotes = [], 0
    cursor = 0
    for match in _CANDIDATE.finditer(text):
        open_quotes += text.count("“", cursor, match.start()) - text.count("”", cursor, match.start())
        cursor = match.end()
        glyphs = match.group(1)
        if glyphs[:1].isdigit() and match.start(1) >= 2 and text[match.start(1) - 2].isdigit():
            continue                  # "Table 8.1", "3.5": a decimal, not a marker
        if glyphs in _CLOSING and open_quotes > 0:
            open_quotes -= glyphs == "”" or glyphs == '"'
            continue                  # the closing quote of a quotation
        found.append(MarkerCandidate(block, match.start(1), match.end(1), glyphs))
    return found


def read_digits(images, tesseract: str) -> List[Tuple[Optional[int], List[str]]]:
    """Per image: a number when at least ``MIN_AGREEING`` Tesseract modes read the same
    digits. One Tesseract run per mode for all images (a list file), not one per crop."""
    import os
    import tempfile
    if not images:
        return []
    readings: List[List[str]] = [[] for _ in images]
    with tempfile.TemporaryDirectory(prefix="papyrus-markers-") as folder:
        paths = []
        for index, image in enumerate(images):
            paths.append(os.path.join(folder, f"{index}.png"))
            image.save(paths[-1])
        listing = os.path.join(folder, "images.txt")
        with open(listing, "w", encoding="utf-8") as stream:
            stream.write("\n".join(paths) + "\n")
        for psm in PSM_MODES:
            pages = _run(tesseract, listing, psm, len(images))
            for index, text in enumerate(pages):
                readings[index].append(text)
    return [(_vote(r), r) for r in readings]


def _run(tesseract: str, listing: str, psm: str, count: int) -> List[str]:
    try:
        result = subprocess.run([tesseract, listing, "stdout", "--psm", psm, "-c",
                                 "tessedit_char_whitelist=0123456789"], capture_output=True,
                                timeout=TESSERACT_TIMEOUT_S + count)
        pages = result.stdout.decode("utf-8", "replace").split("\f")
    except (OSError, subprocess.SubprocessError):
        pages = []
    pages = [page.strip() for page in pages[:count]]
    return pages + [""] * (count - len(pages))


def _vote(readings: List[str]) -> Optional[int]:
    votes = [r for r in readings if r.isdigit() and len(r) <= 3]
    best = max(sorted(set(votes)), key=votes.count) if votes else ""
    return int(best) if best and votes.count(best) >= MIN_AGREEING else None


def isolate(image):
    """The marker's own ink: connected regions not touching the top edge (the
    line above) or the left edge (the word's tail), clustered left to right from
    the first digit-sized region until a word-sized gap."""
    import numpy as np
    from PIL import Image
    from scipy import ndimage
    pixels = np.asarray(image.convert("L"))
    labels, count = ndimage.label(pixels < INK)
    boxes = [box for box in ndimage.find_objects(labels) if box is not None]
    regions = [(rows, cols) for rows, cols in boxes if rows.start > 0 and cols.start > 0]
    if not regions:
        return None
    tallest = max(rows.stop - rows.start for rows, _ in regions)
    digits = sorted((r for r in regions if r[0].stop - r[0].start >= MIN_DIGIT_SHARE * tallest), key=lambda r: r[1].start)
    if not digits:
        return None
    chosen = [digits[0]]
    for region in digits[1:]:
        if region[1].start - max(c[1].stop for c in chosen) > MAX_DIGIT_GAP * tallest:
            break
        chosen.append(region)
    top, bottom = min(r[0].start for r in chosen), max(r[0].stop for r in chosen)
    left, right = min(r[1].start for r in chosen), max(r[1].stop for r in chosen)
    keep = np.isin(labels[top:bottom, left:right], [labels[r[0].start:r[0].stop, r[1].start:r[1].stop].max() for r in chosen])
    crop = np.where(keep, pixels[top:bottom, left:right], 255).astype("uint8")
    canvas = Image.new("L", (crop.shape[1] + 2 * PAD, crop.shape[0] + 2 * PAD), 255)
    canvas.paste(Image.fromarray(crop), (PAD, PAD))
    return canvas


def crop_box(rect, line_height: float) -> Tuple[float, float, float, float]:
    x0, top = rect[0], rect[1]
    return (x0 - 0.05 * line_height, top - CROP_ABOVE * line_height,
            max(rect[2], x0) + CROP_WIDTH * line_height * 0.5, top + CROP_BELOW * line_height)


class PdfMarkerImages:
    """Page geometry and crops for one PDF (the parser's own block geometry)."""

    def __init__(self, path: str) -> None:
        import fitz
        self._doc = fitz.open(path)

    def close(self) -> None:
        self._doc.close()

    def crop(self, block, start: int, end: int):
        import io
        import fitz
        from PIL import Image
        from core.services.extraction.pdf_layout_parser import block_char_rects, parse_block_id
        parsed = parse_block_id(block.block_id)
        if not parsed or block.page_num is None:
            return None
        page = self._doc[block.page_num]
        found = block_char_rects(page, parsed[1], start, end)
        if not found or found[0] != block.exact_text or not found[1]:
            return None               # text no longer matches the page: never guess a position
        rect = found[1][0]
        height = rect[3] - rect[1]
        if height <= 0:
            return None
        clip = fitz.Rect(*crop_box(rect, height)) & page.rect
        pixmap = page.get_pixmap(clip=clip, matrix=fitz.Matrix(SCALE, SCALE), colorspace=fitz.csGRAY)
        return Image.open(io.BytesIO(pixmap.tobytes("png")))


def recover(blocks, images: PdfMarkerImages, tesseract: str,
            accept: Callable[[MarkerCandidate], bool]) -> List[MarkerCandidate]:
    """Read every candidate; ``accept`` validates a number against the notes in scope."""
    out, crops = [], []
    for block in blocks:
        for candidate in candidates(block):
            image = images.crop(block, candidate.start, candidate.end)
            marker = isolate(image) if image is not None else None
            candidate.reason = "no_image" if marker is None else ""
            out.append(candidate)
            if marker is not None:
                crops.append((candidate, marker))
    for (candidate, _marker), (number, readings) in zip(crops, read_digits([m for _c, m in crops], tesseract)):
        candidate.number, candidate.readings = number, readings
        candidate.reason = "" if number is not None else "readings_disagree"
        if number is not None and not accept(candidate):
            candidate.reason, candidate.number = "not_a_note_in_scope", None
    return out
