"""Conservative document-order resolution of legal short references.

Unresolved short forms remain reviewable occurrences. They never create an
invented authority, and pin pages are never mistaken for an authority's page.
"""
from __future__ import annotations

import re
from dataclasses import replace

from core.services.extraction.legal_citation_patterns import (
    LEGAL_SHORT_REPORTER_RE, authority_key,
)

SHORT_REFERENCE_RE = re.compile(r"\b(?:[Ii]d\.|[Ii]bid\.),?(?:\s+at\s+\d+(?:[-–]\d+)?)?")   # "Id., at 548"
MAX_ID_DISTANCE = 800


def resolve_short_references(citations, entries, blocks):
    positions, offset = {}, 0
    block_types = {}
    for block in blocks:
        positions[block.block_id] = offset
        block_types[block.block_id] = block.block_type
        offset += len(block.exact_text) + 1
    ordered = sorted(citations, key=lambda cit: _position(cit, positions))
    keys = {authority_key(entry.raw_text) for entry in entries} - {""}
    result, previous, previous_key = [], None, ""
    for citation in ordered:
        key = authority_key(citation.raw_text) if citation.citation_type in {"legal", "statute"} else ""
        method = ""
        if citation.citation_type == "legal_short":
            key, method = _resolve(citation, keys, previous, previous_key, positions, block_types)
            inherited = None if key else _ibid_author_year(citation, previous, positions, block_types)
            if inherited is not None:
                citation = inherited
            else:
                citation = replace(citation, resolved_authority_key=key or None,
                                   resolution_method=method or "unresolved_short_reference")
        result.append(citation)
        previous, previous_key = citation, key
    return result


def _resolve(citation, keys, previous, previous_key, positions, block_types):
    reporter = LEGAL_SHORT_REPORTER_RE.search(citation.raw_text)
    if reporter:
        prefix = f"{reporter['volume']}|{re.sub(r'[^a-z0-9]', '', reporter['reporter'].lower())}|"
        candidates = [key for key in keys if key.startswith(prefix)]
        if len(candidates) == 1:
            return candidates[0], "unique_volume_reporter"
        # Multiple cases can occur in the same reporter volume. Don't guess
        # from a pin page, or reuse a nearby unrelated case.
        return "", "ambiguous_volume_reporter" if candidates else "unresolved_short_reference"
    if not previous or not previous_key or not SHORT_REFERENCE_RE.fullmatch(citation.raw_text.strip()):
        return "", ""
    distance = _position(citation, positions) - _position(previous, positions)
    if not 0 <= distance <= MAX_ID_DISTANCE:
        return "", ""
    current_type = block_types.get(citation.location.block_id)
    previous_type = block_types.get(previous.location.block_id)
    if (current_type == "footnote") != (previous_type == "footnote"):
        return "", ""
    return previous_key, "preceding_single_authority"


def _ibid_author_year(citation, previous, positions, block_types):
    """"(ibid.)" after "(Elliott 2024)" cites Elliott 2024: inherit the
    preceding author-year keys under the same distance/channel guards as Id."""
    if not previous or previous.citation_type != "author_year" or not previous.author_keys:
        return None
    if not SHORT_REFERENCE_RE.fullmatch(citation.raw_text.strip()):
        return None
    distance = _position(citation, positions) - _position(previous, positions)
    same_channel = (block_types.get(citation.location.block_id) == "footnote") == (
        block_types.get(previous.location.block_id) == "footnote")
    if not (0 <= distance <= MAX_ID_DISTANCE and same_channel):
        return None
    return replace(citation, citation_type="author_year", author_keys=list(previous.author_keys),
                   year=previous.year, resolution_method="ibid_preceding_author_year")


def _position(citation, positions):
    location = citation.location
    if not location:
        return -1
    return positions.get(location.block_id, 0) + (location.char_start or 0)
