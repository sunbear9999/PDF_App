"""Pure helpers that place recorded artifact text inside a block's raw text.

Offsets are always into ``DocumentBlock.exact_text``. Matching is done on
normalized text (soft hyphens, line wraps and ligatures removed) and mapped
back to raw offsets through the sentence segmenter's offset map, so a quote
that wraps across PDF lines still lands on its real characters.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import List, Optional, Tuple

from core.models.source_anchor_models import NavigationPrecision
from core.services.extraction.sentence_segmentation_service import (
    normalize_with_offset_map,
)

_BRACKETS = "()[]{} "


@dataclass(frozen=True)
class LocatedRange:
    char_start: Optional[int]
    char_end: Optional[int]
    precision: NavigationPrecision


def _norm(text: str) -> str:
    clean, _positions = normalize_with_offset_map(text or "")
    return clean.casefold()


def text_key(text) -> str:
    """Normalized comparison key (layout breaks, ligatures and case removed)."""
    return _norm(str(text or "")).strip()


def text_matches(recorded: str, expected: Optional[str]) -> bool:
    """True when a recorded source slice plausibly is the expected text.

    Grouped citations store a synthesized ``(Author, Year)`` for one part of a
    ``(A, 2001; B, 2002)`` mark, so the recorded slice may *contain* the
    expected text once brackets are trimmed. A slice that is only a fragment
    of the expected text is a stale offset, not a match.
    """
    if not expected:
        return bool(recorded and recorded.strip())
    left, right = _norm(recorded), _norm(expected)
    if not left or not right:
        return False
    right_core = right.strip(_BRACKETS)
    return left == right or bool(right_core) and right_core in left


def find_occurrences(block_text: str, needle: str) -> List[Tuple[int, int]]:
    """Raw ``(start, end)`` ranges of every normalized occurrence of needle."""
    clean, positions = normalize_with_offset_map(block_text or "")
    target = _norm(needle).strip()
    if not target or not positions:
        return []
    haystack = clean.casefold()
    ranges: List[Tuple[int, int]] = []
    cursor = haystack.find(target)
    while cursor >= 0:
        end_index = cursor + len(target) - 1
        ranges.append((positions[cursor], positions[end_index] + 1))
        cursor = haystack.find(target, cursor + 1)
    return ranges


def locate_in_block(
    block_text: str,
    char_start: Optional[int],
    char_end: Optional[int],
    expected: Optional[str],
) -> LocatedRange:
    """Verify recorded offsets, else repair by unique text match, else coarse."""
    text = block_text or ""
    if _valid_range(text, char_start, char_end):
        if text_matches(text[char_start:char_end], expected):
            return LocatedRange(char_start, char_end, NavigationPrecision.EXACT)
    if expected:
        hits = find_occurrences(text, expected)
        chosen = _choose_occurrence(hits, char_start)
        if chosen:
            return LocatedRange(chosen[0], chosen[1], NavigationPrecision.REPAIRED)
    return LocatedRange(None, None, NavigationPrecision.COARSE)


def map_normalized_range(
    raw_text: str, norm_start: int, norm_end: int
) -> Optional[Tuple[int, int]]:
    """Map a range in ``normalize(raw_text)`` back to raw offsets."""
    _clean, positions = normalize_with_offset_map(raw_text or "")
    if not (0 <= norm_start < norm_end <= len(positions)):
        return None
    return positions[norm_start], positions[norm_end - 1] + 1


def _valid_range(text: str, start: Optional[int], end: Optional[int]) -> bool:
    return (
        isinstance(start, int) and isinstance(end, int)
        and 0 <= start < end <= len(text)
    )


def _choose_occurrence(
    hits: List[Tuple[int, int]], hint: Optional[int]
) -> Optional[Tuple[int, int]]:
    if len(hits) == 1:
        return hits[0]
    if hits and isinstance(hint, int):
        # Several identical phrases in one block: the stale offset still says
        # which one the producer meant.
        return min(hits, key=lambda item: abs(item[0] - hint))
    return None


_ORDER = {
    NavigationPrecision.EXACT: 0,
    NavigationPrecision.REPAIRED: 1,
    NavigationPrecision.COARSE: 2,
}


def weakest(precisions: List[NavigationPrecision]) -> NavigationPrecision:
    if not precisions:
        return NavigationPrecision.UNRESOLVED
    return max(precisions, key=lambda item: _ORDER.get(item, 3))
