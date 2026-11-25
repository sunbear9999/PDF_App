"""
SentenceSegmentationService — deterministic sentence-level segmentation.

Produces stable, provenance-carrying sentence records from DocumentBlocks so
every unit of text (down to the sentence) has an ID, page, and char offsets
that later stages (classification, entity mentions, retrieval, citation) key
against. Uses one abbreviation-aware deterministic splitter across backends so model
availability cannot change source sentence IDs — no LLM, no new dependencies.

Sentence records are plain dicts (not a heavyweight dataclass) so they persist
straight through ExtractionCache's JSON storage.
"""
from __future__ import annotations

import re
from typing import Any, Dict, List, Optional, Tuple
from core.services.extraction.block_roles import content_text, is_ocr_text
from core.services.extraction.sentence_boundaries import split_sentences

# Block types that carry prose worth splitting into sentences. Tables/figures
# are handled by their own extractors and are skipped here.
# Block geometry stays on the block. Copied into every sentence it grew with
# block length x sentences per block (4.7 GB of JSON for one 106-page opinion).
BLOCK_ONLY_STRUCTURE = frozenset({"line_rects"})
_PROSE_BLOCK_TYPES = {
    "paragraph", "heading", "list", "quote", "caption", "footnote", "endnote", "unknown", "",
}

_LIGATURES = str.maketrans({
    "ﬀ": "ff", "ﬁ": "fi", "ﬂ": "fl", "ﬃ": "ffi", "ﬄ": "ffl",
    # Some PDF fonts emit the "fi" ligature as the control glyph U+0019
    # ("\x19rst", "quali\x19ed"); a control character is never content.
    "\x19": "fi",
})
_TERMINAL_RE = re.compile(
    r"[.!?](?:[\"'’”)\]]|[0-9¹²³⁰⁴-⁹,;:–-])*$"
)


# Line-break hyphenation: "administra-|tive", "sum-|mary" lose the hyphen;
# compounds broken at the line ("gut-|brain") keep it.
_SUFFIXES = frozenset("""
tion tions sion sions ment ments ness ity ities ing ings ed er ers est ly ally ary ory ive ives
ous able ible al als ance ances ence ences ant ants ent ents ism isms ist ists ize ized izes ise
ised ation ations ative ature ure ures ship hood ful less ian ians ial ical ically ics ogy ology
ologies ular ularly ative atively ency encies ancy ory ories ward ments ence tive tives sive
""".split())


def soft_line_break(right_word: str) -> bool:
    """True when a hyphen at a line/block end splits one word."""
    word = re.match(r"[a-z]+", right_word or "")
    return bool(word) and (len(word.group(0)) <= 4 or word.group(0) in _SUFFIXES)


def _inside_identifier(text: str, index: int) -> bool:
    """A hyphen ending a line inside a URL/DOI ("…/annurev-\nmed-0423…") is
    part of the identifier, never a word break."""
    token = re.split(r"\s", text[max(0, index - 80):index])[-1]
    return "/" in token or "doi" in token.casefold() or re.search(r"\w\.\w", token) is not None


def normalize_extracted_text(text: str) -> str:
    """Reconstruct readable prose from layout-preserving PDF text.

    PDF text commonly contains discretionary soft hyphens and newlines in the
    middle of words.  This removes only layout characters; semantic spaces and
    real hyphens remain intact.
    """
    clean, _positions = _normalize_with_positions(text or "")
    return clean


def normalize_with_offset_map(text: str) -> Tuple[str, List[int]]:
    """Normalize like ``normalize_extracted_text`` and return, for every
    output character, its index in the raw input — the map that lets
    sentence-relative offsets resolve back to raw source spans."""
    return _normalize_with_positions(text or "")


def _normalize_with_positions(text: str) -> Tuple[str, List[int]]:
    output: List[str] = []
    positions: List[int] = []
    index = 0
    while index < len(text):
        char = text[index]
        if char in {"\u200b", "\u200c", "\u200d", "\ufeff"}:
            index += 1
            continue
        if char == "\u00ad":
            index += 1
            while index < len(text) and text[index].isspace():
                index += 1
            continue
        if char == "-" and index + 1 < len(text) and text[index + 1].isspace():
            end = index + 1
            while end < len(text) and text[end].isspace():
                end += 1
            if "\n" in text[index + 1:end] or "\r" in text[index + 1:end]:
                if not soft_line_break(text[end:end + 20]) or _inside_identifier(text, index):
                    output.append(char)
                    positions.append(index)
                index = end
                continue
        if char.isspace():
            start = index
            while index < len(text) and text[index].isspace():
                index += 1
            if output and output[-1] != " ":
                output.append(" ")
                positions.append(start)
            continue
        replacement = char.translate(_LIGATURES)
        for value in replacement:
            output.append(value)
            positions.append(index)
        index += 1
    while output and output[-1] == " ":
        output.pop()
        positions.pop()
    return "".join(output), positions


def _is_fragment(text: str, block_type: str) -> bool:
    words = re.findall(r"[A-Za-zÀ-ɏ]{2,}", text or "")
    if block_type in {"heading", "caption"} or len(words) < 4:
        return True
    if text[:1].islower():
        return True
    return not bool(_TERMINAL_RE.search(text.rstrip()))


class SentenceSegmentationService:
    """Split DocumentBlocks into ordered sentence records with char offsets."""

    def __init__(self, language_feature_service=None) -> None:
        self._lang = language_feature_service

    def segment(self, blocks: List[Any], doc_id: str) -> List[Dict[str, Any]]:
        sentences: List[Dict[str, Any]] = []
        for block in blocks or []:
            block_type = getattr(block, "block_type", "") or ""
            if block_type not in _PROSE_BLOCK_TYPES:
                continue
            raw = getattr(block, "exact_text", "") or ""
            # Furniture (running heads glued into paragraphs, page numbers)
            # is blanked at equal length, so offsets stay raw offsets.
            text = content_text(block)
            if not text.strip():
                continue
            block_id = getattr(block, "block_id", "") or ""
            page_num = getattr(block, "page_num", None)
            section_path = list(getattr(block, "section_path", []) or [])
            text_source = "ocr" if is_ocr_text(block) else ""
            structure = {key: value for key, value in (getattr(block, "structure", {}) or {}).items()
                         if key not in BLOCK_ONLY_STRUCTURE}
            for idx, (sent_text, start, end) in enumerate(self._split_with_offsets(text)):
                clean = sent_text.strip()
                if len(clean) < 2:
                    continue
                sentences.append({
                    "sentence_id": f"{block_id}:s{idx}",
                    "doc_id": doc_id,
                    "block_id": block_id,
                    "block_type": block_type,
                    "text_source": text_source,
                    "structure": structure,
                    "page_num": page_num,
                    "section_path": section_path,
                    "char_start": start,
                    "char_end": end,
                    "text": clean,
                    "raw_text": raw[start:end],
                    "is_fragment": _is_fragment(clean, block_type),
                })
        return self._merge_continuations(sentences)

    @staticmethod
    def _merge_continuations(sentences: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        merged: List[Dict[str, Any]] = []
        for sentence in sentences:
            if merged and _can_merge(merged[-1], sentence):
                previous = merged[-1]
                previous["text"] = _join_fragments(previous, sentence)
                previous["raw_text"] = (
                    str(previous.get("raw_text") or "")
                    + str(sentence.get("raw_text") or "")
                )
                previous.setdefault("block_ids", [previous.get("block_id")])
                previous["block_ids"].append(sentence.get("block_id"))
                previous.setdefault("source_spans", [_source_span(previous)])
                previous["source_spans"].append(_source_span(sentence))
                previous["char_end"] = None
                previous["is_fragment"] = _is_fragment(
                    previous["text"], previous.get("block_type") or ""
                )
                continue
            item = dict(sentence)
            item["block_ids"] = [item.get("block_id")]
            item["source_spans"] = [_source_span(item)]
            merged.append(item)
        return merged

    def _split_with_offsets(self, text: str):
        """Yield (sentence_text, char_start, char_end) within the block text."""
        normalized, positions = _normalize_with_positions(text)
        parts = self._raw_split(normalized)
        cursor = 0
        for part in parts:
            stripped = part.strip()
            if not stripped:
                continue
            found = normalized.find(stripped, cursor)
            if found < 0:
                # Fall back to an approximate position rather than dropping it.
                found = cursor
            normalized_end = found + len(stripped)
            cursor = normalized_end
            start = positions[found] if found < len(positions) else 0
            end = (
                positions[normalized_end - 1] + 1
                if normalized_end and normalized_end <= len(positions)
                else len(text)
            )
            if normalized_end == len(normalized):
                end = len(text)
            yield stripped, start, end

    def _raw_split(self, text: str) -> List[str]:
        # One offset-preserving boundary policy, regardless of optional models.
        # The old punctuation fallback split U.S.C., Cir., v. and initials into
        # separate 'sentences', starving argument classification of context.
        return split_sentences(text)


def _naive_sentence_split(text: str) -> List[str]:
    return split_sentences(text)


def _can_merge(previous: Dict[str, Any], current: Dict[str, Any]) -> bool:
    if not previous.get("is_fragment"):
        return False
    if not _same_or_next_page(previous, current):
        return False
    if previous.get("block_type") != "paragraph" or current.get("block_type") != "paragraph":
        return False
    if list(previous.get("section_path") or []) != list(current.get("section_path") or []):
        return False
    current_text = str(current.get("text") or "").lstrip()
    previous_text = str(previous.get("text") or "").rstrip()
    if previous_text.isupper() or current_text.isupper():
        return False
    return bool(
        current_text[:1].islower() or current_text.startswith(("(", ",", ";"))
        or (len(previous_text.split()) >= 6
            and not _TERMINAL_RE.search(previous_text))
    )


def _same_or_next_page(previous: Dict[str, Any], current: Dict[str, Any]) -> bool:
    """Continuation may cross a page break: furniture between pages produces
    no sentences, so the next sentence is the next page's first content."""
    left, right = previous.get("page_num"), current.get("page_num")
    if left == right:
        return True
    return isinstance(left, int) and isinstance(right, int) and right == left + 1


def _join_fragments(previous: Dict[str, Any], current: Dict[str, Any]) -> str:
    left = str(previous.get("text") or "").rstrip()
    right = str(current.get("text") or "").lstrip()
    raw_left = str(previous.get("raw_text") or "").rstrip()
    separator = "" if raw_left.endswith("\u00ad") else " "
    if left.endswith("-") and right[:1].islower():
        # A word hyphenated across blocks/pages ("administra-" | "tive").
        left, separator = (left[:-1], "") if soft_line_break(right) else (left, "")
    return normalize_extracted_text(left + separator + right)


def _source_span(sentence: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "page": sentence.get("page_num"),
        "block_id": sentence.get("block_id"),
        "char_start": sentence.get("char_start"),
        "char_end": sentence.get("char_end"),
    }
