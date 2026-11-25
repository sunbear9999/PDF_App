"""
CitationExtractionService — in-text citation extraction + citation index assembly.

Stages:
  1. InTextCitationExtractor.extract(body_blocks) → List[InTextCitation]
     Detects numeric [1], [2,3], [4-6], superscript digits, author-year (Smith, 2020),
     legal reporter citations, and statutory citations.
  2. CitationExtractionService.build_citation_index(doc_id, blocks) → CitationIndex
     Orchestrates bibliography extraction, in-text extraction, and CitationMatcher.

No LLM calls.  All extraction is deterministic.
"""
from __future__ import annotations

import uuid
from typing import Dict, List, Set

from core.services.extraction.in_text_citation_extractor import (
    InTextCitationExtractor, _EXTRACTOR_VERSION, _provenance,
)
from core.services.extraction.legal_citation_resolution import resolve_short_references
from core.services.extraction.citation_matcher import (
    AUTHORITY_CITATION_TYPES,
    authority_key,
)
from core.services.extraction.footnote_notes import extract_notes
from core.services.extraction.structure.reference_style import decide, note_targets, marker_role
from core.services.extraction.markers import ocr_markers
from core.services.extraction.raster_marker_citations import recover_markers
from core.services.extraction.note_bibliography_links import link_note_targets
from core.services.extraction.native_reference_links import numeric_native_markers, target_index, native_targets
from core.models.extraction_models import (
    BibliographyEntry,
    CitationIndex,
    CitationMatch,
    DocumentBlock,
    InTextCitation,
)

def _via_notes(matches: List[CitationMatch], notes, entries) -> List[CitationMatch]:
    """A marker that resolves to a note also cites the bibliography entry that
    note names, so the entry lists every place its source is cited."""
    links = link_note_targets(notes, entries)
    for note in notes:
        if note.entry_id in links:
            note.cited_entry_ids = links[note.entry_id]
            if len(note.cited_entry_ids) == 1:
                note.normalized_keys["cites_entry"] = note.cited_entry_ids[0]
    derived = []
    for match in matches:
        for target in links.get(match.bibliography_entry_id, []):
            if not match.is_ambiguous:
                derived.append(CitationMatch(
                    match_id=str(uuid.uuid5(uuid.NAMESPACE_URL, f"{match.match_id}|via_note|{target}")),
                    in_text_citation_id=match.in_text_citation_id, bibliography_entry_id=target,
                    confidence=round(match.confidence * 0.9, 3),
                    score_breakdown={"via_note": 1.0}, reason=f"via note {match.bibliography_entry_id}",
                ))
    return derived


def _authority_entries(
    doc_id: str, in_text: List[InTextCitation], existing=(),
) -> List[BibliographyEntry]:
    """Derive one entry per distinct legal authority cited inline.

    Judicial opinions and statutes carry no reference list, so a document full
    of "572 U.S. 782" and "25 U.S.C. § 2710" produced an entirely empty
    citation index. Grouping the occurrences by authority key gives those
    sources the same grouped-by-reference view a bibliography provides.
    """
    entries: List[BibliographyEntry] = []
    seen: Set[str] = {authority_key(entry.raw_text) for entry in existing}
    for citation in in_text:
        if citation.citation_type not in AUTHORITY_CITATION_TYPES:
            continue
        key = authority_key(citation.raw_text)
        if not key or key in seen:
            continue
        seen.add(key)
        entries.append(BibliographyEntry(
            entry_id=str(uuid.uuid5(
                uuid.NAMESPACE_URL, f"{doc_id}|inline_authority|{key}"
            )),
            doc_id=doc_id,
            raw_text=citation.raw_text,
            year=citation.year,
            reference_number=None,
            citation_style=citation.citation_type,
            location=citation.location,
            normalized_keys={"authority": key},
            provenance=_provenance("inline_authority", 0.85),
        ))
    return entries


# ── Service ─────────────────────────────────────────────────────────────────

class CitationExtractionService:
    """
    Orchestrates bibliography extraction, in-text citation detection, and
    citation matching to produce a complete CitationIndex per document.
    """

    def __init__(
        self,
        bibliography_service,
        citation_matcher,
        pattern_registry=None,
    ) -> None:
        self._bib_svc = bibliography_service
        self._matcher = citation_matcher
        self._in_text = InTextCitationExtractor(pattern_registry)

    # ------------------------------------------------------------------

    def build_citation_index(
        self, doc_id: str, blocks: List[DocumentBlock], marker_images=None, tesseract: str = "",
    ) -> CitationIndex:
        """
        Build a complete CitationIndex for a document.

        Steps:
          1. Detect + segment bibliography entries from bibliography blocks
             (or, for sources that cite authorities inline, derive entries
              from the in-text citations themselves)
          2. Extract in-text citations from body blocks
          3. Match in-text to bibliography entries
          4. Build entry_page_index and classify unmatched / ambiguous
        """
        bib_blocks = self._bib_svc.detect_bibliography_blocks(blocks)
        body_blocks = [b for b in blocks if b.block_type != "bibliography"]

        entries: List[BibliographyEntry] = self._bib_svc.segment_entries(bib_blocks, doc_id)
        in_text: List[InTextCitation] = self._in_text.extract(body_blocks, doc_id)
        in_text.extend(ocr_markers(body_blocks, doc_id))
        in_text.extend(numeric_native_markers(body_blocks, in_text, doc_id))
        # A source can contain both a bibliography and inline authorities.
        # Reuse a parsed entry when it identifies that authority; otherwise
        # derive an entry at its first full occurrence, not an arbitrary match.
        entries.extend(_authority_entries(doc_id, in_text, entries))
        in_text = resolve_short_references(in_text, entries, body_blocks)

        # Footnote/endnote markers point at notes, not reference numbers.
        notes = extract_notes(body_blocks, doc_id)
        unreadable = []
        if marker_images is not None and tesseract:
            # Scans: markers the OCR layer misread are read again from the page image.
            in_text, unreadable = recover_markers(body_blocks, doc_id, in_text, notes, marker_images, tesseract)
        # match_all, not match(): a grouped citation such as "[2,5,7]" references
        # three entries, and matching it once credited only one of them.
        matches: List[CitationMatch] = []
        by_block = {b.block_id: b for b in blocks}
        native_index = target_index(entries + notes, by_block)
        for citation in in_text:
            native = native_targets(citation, by_block.get(citation.location.block_id), native_index)
            if native is not None:
                roles = {"notes" if entry.citation_style == "footnote" else
                         "numeric_citations" if citation.numeric_keys else "author_year" for entry in native}
                citation.reference_role = next(iter(roles)) if len(roles) == 1 else "unresolved"
                for target in native if len(roles) == 1 else []:
                    matches.append(CitationMatch(
                        match_id=str(uuid.uuid5(uuid.NAMESPACE_URL, f"{citation.citation_id}|native|{target.entry_id}")),
                        in_text_citation_id=citation.citation_id, bibliography_entry_id=target.entry_id,
                        confidence=0.99, score_breakdown={"native_destination": 1.0},
                        reason="Native PDF link identifies the target text line",
                    ))
                continue
            citation.reference_role = marker_role(citation, notes, entries, by_block)
            candidates = note_targets(citation, notes, by_block) if citation.citation_type == "superscript" else []
            # Superscripts with note evidence but no unique scoped target stay
            # unresolved; they must not accidentally match bibliography #N.
            targets = candidates if citation.reference_role == "notes" else entries
            matches.extend(self._matcher.match_all([citation], targets))
        matches.extend(_via_notes(matches, notes, entries))
        index = self._assemble_index(doc_id, entries + notes, in_text, matches)
        index.reference_style = decide(in_text, entries, notes)
        if unreadable:
            index.reference_style["unreadable_markers"] = unreadable
        return index

    def _assemble_index(self, doc_id, entries, in_text, matches):
        entries_by_id = {entry.entry_id: entry for entry in entries}
        resolved = {}
        for match in matches:
            if not match.is_ambiguous:
                resolved.setdefault(match.in_text_citation_id, set()).add(
                    entries_by_id[match.bibliography_entry_id].reference_number)
        for citation in in_text:
            citation.unmatched_numeric_keys = [key for key in citation.numeric_keys
                if key not in resolved.get(citation.citation_id, set())]
        matched_bib_ids: Set[str] = {m.bibliography_entry_id for m in matches if not m.is_ambiguous}
        matched_in_text_ids: Set[str] = {m.in_text_citation_id for m in matches if not m.is_ambiguous}
        ambiguous_match_ids: List[str] = [m.match_id for m in matches if m.is_ambiguous]

        unmatched_bib_entry_ids = [e.entry_id for e in entries if e.entry_id not in matched_bib_ids]
        unmatched_in_text_ids = [c.citation_id for c in in_text if c.citation_id not in matched_in_text_ids or c.unmatched_numeric_keys]

        entry_page_index = self._build_page_index(matches, in_text)
        citation_contexts = {c.citation_id: c.context_text for c in in_text}

        return CitationIndex(
            doc_id=doc_id,
            bibliography_entries=entries,
            in_text_citations=in_text,
            citation_matches=matches,
            unmatched_bib_entry_ids=unmatched_bib_entry_ids,
            unmatched_in_text_ids=unmatched_in_text_ids,
            ambiguous_match_ids=ambiguous_match_ids,
            entry_page_index=entry_page_index,
            citation_contexts=citation_contexts,
        )

    # ------------------------------------------------------------------

    def _build_page_index(
        self,
        matches: List[CitationMatch],
        in_text: List[InTextCitation],
    ) -> Dict[str, List[int]]:
        """Map bibliography entry_id → sorted list of pages where it's cited."""
        cit_page: Dict[str, int] = {
            c.citation_id: (c.location.page_num if c.location else 0)
            for c in in_text
        }
        index: Dict[str, List[int]] = {}
        for m in matches:
            if m.is_ambiguous:
                continue
            page = cit_page.get(m.in_text_citation_id, 0)
            index.setdefault(m.bibliography_entry_id, [])
            if page not in index[m.bibliography_entry_id]:
                index[m.bibliography_entry_id].append(page)
        for pages in index.values():
            pages.sort()
        return index
