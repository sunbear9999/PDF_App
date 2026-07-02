from __future__ import annotations

import re
from collections import Counter, defaultdict
from dataclasses import dataclass, replace
from typing import Any, Callable, Iterable, List, Optional, Sequence, Tuple

from PySide6.QtCore import QObject, QTimer, Signal

from core.utils.text_utils import sanitize_extracted_text


@dataclass(frozen=True)
class ReadingWord:
    text: str
    page_num: int
    word_index: int
    line_key: Tuple[int, int, int]
    bbox: Tuple[float, float, float, float]
    page_width: float
    page_height: float


@dataclass(frozen=True)
class ReadingLine:
    text: str
    page_num: int
    words: Tuple[ReadingWord, ...]
    bbox: Tuple[float, float, float, float]


@dataclass(frozen=True)
class ReadingPage:
    page_num: int
    width: float
    height: float
    lines: Tuple[ReadingLine, ...]


@dataclass(frozen=True)
class ReadingToken:
    text: str
    page_num: int
    token_index: int
    sentence_index: int
    source_word_index: int
    bbox: Tuple[float, float, float, float]


class ReadingTextExtractor:
    """Extract ordered PDF words and grouped lines for reading mode."""

    def extract(self, doc: Any) -> List[ReadingPage]:
        pages: List[ReadingPage] = []
        if doc is None:
            return pages

        for page_num in range(len(doc)):
            page = doc.load_page(page_num)
            rect = page.rect
            raw_words = page.get_text("words", sort=True) or []
            grouped: defaultdict[Tuple[int, int, int], List[ReadingWord]] = defaultdict(list)

            for word_index, raw in enumerate(raw_words):
                text = sanitize_extracted_text(str(raw[4]), collapse_whitespace=True)
                if not text:
                    continue
                line_key = (
                    int(raw[5]) if len(raw) > 5 else 0,
                    int(raw[6]) if len(raw) > 6 else 0,
                    page_num,
                )
                word = ReadingWord(
                    text=text,
                    page_num=page_num,
                    word_index=word_index,
                    line_key=line_key,
                    bbox=(float(raw[0]), float(raw[1]), float(raw[2]), float(raw[3])),
                    page_width=float(rect.width),
                    page_height=float(rect.height),
                )
                grouped[line_key].append(word)

            lines: List[ReadingLine] = []
            for words in grouped.values():
                words.sort(key=lambda w: (w.bbox[1], w.bbox[0], w.word_index))
                x0 = min(w.bbox[0] for w in words)
                y0 = min(w.bbox[1] for w in words)
                x1 = max(w.bbox[2] for w in words)
                y1 = max(w.bbox[3] for w in words)
                text = sanitize_extracted_text(" ".join(w.text for w in words), collapse_whitespace=True)
                if text:
                    lines.append(ReadingLine(text, page_num, tuple(words), (x0, y0, x1, y1)))

            lines.sort(key=lambda line: (line.bbox[1], line.bbox[0]))
            pages.append(ReadingPage(page_num, float(rect.width), float(rect.height), tuple(lines)))
        return pages


class ReadingTextCleaner:
    """Conservative cleanup for material people usually skip while reading."""

    _COPYRIGHT_RE = re.compile(
        r"\b(copyright|all rights reserved|creative commons|permission to reproduce|"
        r"printed in|published by|isbn)\b",
        re.IGNORECASE,
    )
    _URL_RE = re.compile(r"^(https?://|www\.|doi:|10\.\d{4,9}/)", re.IGNORECASE)
    _ANY_URL_RE = re.compile(r"\b(https?://|www\.|doi:|10\.\d{4,9}/)", re.IGNORECASE)
    _PAGE_NUM_RE = re.compile(r"^(?:page\s*)?\d{1,4}$", re.IGNORECASE)
    _ROMAN_RE = re.compile(r"^[ivxlcdm]{1,8}$", re.IGNORECASE)
    _LEADER_RE = re.compile(r"([.\-_\u2022])\1{3,}")
    _REAL_WORD_RE = re.compile(r"[A-Za-z]{3,}")
    _CASE_TITLE_RE = re.compile(r"\b(?:v\.|vs\.|versus)\b|\b(?:in re|ex parte)\b", re.IGNORECASE)
    _REFERENCES_HEADING_RE = re.compile(
        r"^(references|bibliography|works cited|literature cited|table of authorities)$",
        re.IGNORECASE,
    )

    def __init__(self, strictness: str = "balanced"):
        self.strictness = strictness

    def clean(
        self,
        pages: Sequence[ReadingPage],
        text_filters: Optional[Iterable[Callable[[List[ReadingPage]], Optional[List[ReadingPage]]]]] = None,
    ) -> List[ReadingPage]:
        cleaned = [self._clean_page(page, pages) for page in pages]
        cleaned = self._remove_repeated_running_lines(cleaned)
        cleaned = self._remove_reference_back_matter(cleaned)
        cleaned = [page for page in cleaned if page.lines]

        for callback in text_filters or []:
            try:
                result = callback(list(cleaned))
                if result is not None:
                    cleaned = list(result)
            except Exception as exc:
                print(f"[ReadingMode] Text filter failed: {exc}")
        return cleaned

    def _clean_page(self, page: ReadingPage, all_pages: Sequence[ReadingPage]) -> ReadingPage:
        kept = []
        for line in page.lines:
            if self._should_skip_line(line, page, len(all_pages)):
                continue
            kept.append(line)
        return replace(page, lines=tuple(kept))

    def _should_skip_line(self, line: ReadingLine, page: ReadingPage, total_pages: int) -> bool:
        text = sanitize_extracted_text(line.text, collapse_whitespace=True)
        if not text:
            return True

        y0, y1 = line.bbox[1], line.bbox[3]
        in_top_margin = y1 < page.height * 0.08
        in_bottom_margin = y0 > page.height * 0.92
        in_top_running_band = y1 < page.height * 0.20
        in_margin = in_top_margin or in_bottom_margin

        if in_margin:
            return True
        if page.page_num > 0 and in_top_running_band and self._CASE_TITLE_RE.search(text):
            return True
        if self._PAGE_NUM_RE.match(text) or self._ROMAN_RE.match(text):
            return True
        if self._LEADER_RE.search(text):
            return True
        if self._is_standalone_link_line(text):
            return True
        if self._COPYRIGHT_RE.search(text) and (page.page_num < 3 or page.page_num >= max(0, total_pages - 2)):
            return True

        alnum = sum(ch.isalnum() for ch in text)
        if len(text) > 0 and alnum / len(text) < 0.20:
            return True
        if len(text) <= 18 and not self._REAL_WORD_RE.search(text):
            return True
        return False

    def _remove_repeated_running_lines(self, pages: Sequence[ReadingPage]) -> List[ReadingPage]:
        normalized: Counter[str] = Counter()
        page_count = len(pages)
        if page_count < 3:
            return list(pages)

        for page in pages:
            edge_lines = list(page.lines[:4]) + list(page.lines[-3:])
            for line in edge_lines:
                y0, y1 = line.bbox[1], line.bbox[3]
                near_edge = y1 < page.height * 0.22 or y0 > page.height * 0.85
                if not near_edge:
                    continue
                key = self._normalize_running_line(line.text)
                if key:
                    normalized[key] += 1

        repeated = {
            key
            for key, count in normalized.items()
            if count >= max(3, int(page_count * 0.25))
        }
        if not repeated:
            return list(pages)

        result = []
        for page in pages:
            lines = []
            for line in page.lines:
                y0, y1 = line.bbox[1], line.bbox[3]
                near_edge = y1 < page.height * 0.22 or y0 > page.height * 0.85
                if near_edge and self._normalize_running_line(line.text) in repeated:
                    continue
                lines.append(line)
            result.append(replace(page, lines=tuple(lines)))
        return result

    def _remove_reference_back_matter(self, pages: Sequence[ReadingPage]) -> List[ReadingPage]:
        if not pages:
            return []
        first_reference_page = None
        min_reference_page = max(1, int(len(pages) * 0.55))
        for page in pages:
            for line in page.lines[:6]:
                heading = sanitize_extracted_text(line.text, collapse_whitespace=True).strip(" .:")
                if page.page_num >= min_reference_page and self._REFERENCES_HEADING_RE.match(heading):
                    first_reference_page = page.page_num
                    break
            if first_reference_page is not None:
                break

        if first_reference_page is None:
            return list(pages)

        result = []
        for page in pages:
            if page.page_num < first_reference_page:
                result.append(page)
                continue
            # Once the user reaches references/bibliography, the reading stream
            # should avoid full source entries. Inline citations in body pages are
            # already preserved because this only applies after the heading.
            result.append(replace(page, lines=tuple()))
        return result

    @staticmethod
    def _normalize_running_line(text: str) -> str:
        text = sanitize_extracted_text(text, collapse_whitespace=True).lower()
        text = re.sub(r"\d+", "#", text)
        text = re.sub(r"\b(page|p|slip opinion|opinion)\b", "", text)
        text = re.sub(r"[^a-z# ]+", "", text)
        text = re.sub(r"\s+", " ", text).strip()
        if len(text) < 4 or len(text) > 80:
            return ""
        return text

    def _is_standalone_link_line(self, text: str) -> bool:
        if self._URL_RE.match(text):
            return True
        if not self._ANY_URL_RE.search(text):
            return False
        real_words = self._REAL_WORD_RE.findall(text)
        return len(real_words) <= 5


class ReadingTokenStream:
    _TOKEN_RE = re.compile(r"\S+")

    def build(
        self,
        pages: Sequence[ReadingPage],
        token_transforms: Optional[Iterable[Callable[[List[ReadingToken]], Optional[List[ReadingToken]]]]] = None,
    ) -> List[ReadingToken]:
        tokens: List[ReadingToken] = []
        sentence_index = 0
        for page in pages:
            for line in page.lines:
                for word in line.words:
                    for match in self._TOKEN_RE.finditer(word.text):
                        text = sanitize_extracted_text(match.group(0), collapse_whitespace=True)
                        if not text:
                            continue
                        tokens.append(
                            ReadingToken(
                                text=text,
                                page_num=page.page_num,
                                token_index=len(tokens),
                                sentence_index=sentence_index,
                                source_word_index=word.word_index,
                                bbox=word.bbox,
                            )
                        )
                        if re.search(r'[.!?]["\')\]]*$', text):
                            sentence_index += 1

        for callback in token_transforms or []:
            try:
                result = callback(list(tokens))
                if result is not None:
                    tokens = [
                        replace(token, token_index=i)
                        for i, token in enumerate(result)
                    ]
            except Exception as exc:
                print(f"[ReadingMode] Token transform failed: {exc}")
        return tokens


class ReadingModeController(QObject):
    token_changed = Signal(object, int, int)
    stream_changed = Signal(int)
    playing_changed = Signal(bool)
    speed_changed = Signal(int)
    error = Signal(str)

    def __init__(self, app_context: Any = None, parent: Optional[QObject] = None):
        super().__init__(parent)
        self._app_context = app_context
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(self._advance_from_timer)
        self.extractor = ReadingTextExtractor()
        self.cleaner = ReadingTextCleaner()
        self.stream_builder = ReadingTokenStream()
        self.tokens: List[ReadingToken] = []
        self.pdf_path = ""
        self.current_index = 0
        self.wpm = 300
        self.is_playing = False
        self._positions: dict[str, int] = {}

    def load_pdf(self, path: str, doc: Any) -> bool:
        self.pause()
        self.pdf_path = path or ""
        self.tokens = []
        self.current_index = 0
        if doc is None:
            self.error.emit("No PDF is loaded.")
            self.stream_changed.emit(0)
            return False

        pages = self.extractor.extract(doc)
        text_filters, token_transforms = self._plugin_hooks()
        pages = self.cleaner.clean(pages, text_filters=text_filters)
        self.tokens = self.stream_builder.build(pages, token_transforms=token_transforms)
        self.current_index = min(self._positions.get(self.pdf_path, 0), max(0, len(self.tokens) - 1))
        self.stream_changed.emit(len(self.tokens))
        if not self.tokens:
            self.error.emit("No readable text found. OCR may be needed for this PDF.")
            return False
        self._emit_current()
        return True

    def play(self) -> None:
        if not self.tokens:
            return
        self.is_playing = True
        self.playing_changed.emit(True)
        self._schedule_next()

    def pause(self) -> None:
        self._timer.stop()
        if self.is_playing:
            self.is_playing = False
            self.playing_changed.emit(False)

    def toggle(self) -> None:
        if self.is_playing:
            self.pause()
        else:
            self.play()

    def seek_token(self, index: int) -> None:
        if not self.tokens:
            return
        self.current_index = max(0, min(index, len(self.tokens) - 1))
        self._remember_position()
        self._emit_current()
        if self.is_playing:
            self._schedule_next()

    def step_words(self, delta: int) -> None:
        self.seek_token(self.current_index + delta)

    def step_sentence(self, delta: int) -> None:
        if not self.tokens:
            return
        current_sentence = self.tokens[self.current_index].sentence_index
        target_sentence = current_sentence + (1 if delta > 0 else -1)
        candidates = [
            i for i, token in enumerate(self.tokens)
            if token.sentence_index == target_sentence
        ]
        if candidates:
            self.seek_token(candidates[0] if delta > 0 else candidates[-1])
        else:
            self.seek_token(len(self.tokens) - 1 if delta > 0 else 0)

    def set_wpm(self, value: int) -> None:
        self.wpm = max(80, min(900, int(value)))
        self.speed_changed.emit(self.wpm)
        if self.is_playing:
            self._schedule_next()

    def current_token(self) -> Optional[ReadingToken]:
        if not self.tokens:
            return None
        return self.tokens[self.current_index]

    def token_delay_ms(self, token: Optional[ReadingToken] = None) -> int:
        token = token or self.current_token()
        base = 60000 / max(1, self.wpm)
        if not token:
            return int(base)
        text = token.text
        factor = 1.0
        if len(text) >= 9:
            factor += 0.18
        if text.endswith((",", ";", ":")):
            factor += 0.35
        if text.endswith((".", "!", "?")):
            factor += 0.65
        return int(base * factor)

    def _advance_from_timer(self) -> None:
        if not self.is_playing:
            return
        if self.current_index >= len(self.tokens) - 1:
            self.pause()
            return
        self.current_index += 1
        self._remember_position()
        self._emit_current()
        self._schedule_next()

    def _schedule_next(self) -> None:
        self._timer.stop()
        if self.is_playing and self.tokens:
            self._timer.start(self.token_delay_ms())

    def _emit_current(self) -> None:
        token = self.current_token()
        if token:
            self.token_changed.emit(token, self.current_index, len(self.tokens))

    def _remember_position(self) -> None:
        if self.pdf_path:
            self._positions[self.pdf_path] = self.current_index

    def _plugin_hooks(self):
        registry = getattr(self._app_context, "plugin_extension_registry", None) if self._app_context else None
        text_filters = registry.get_reading_text_filters() if registry and hasattr(registry, "get_reading_text_filters") else []
        token_transforms = registry.get_reading_token_transforms() if registry and hasattr(registry, "get_reading_token_transforms") else []
        return (
            [spec.callback for spec in text_filters if getattr(spec, "callback", None)],
            [spec.callback for spec in token_transforms if getattr(spec, "callback", None)],
        )
