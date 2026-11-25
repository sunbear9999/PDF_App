from __future__ import annotations

import hashlib
import os
import re
import uuid
from dataclasses import dataclass, field
from typing import Dict, Iterable, List, Optional, Tuple

import datefinder
import fitz
from PySide6.QtCore import QObject, QRunnable, QThreadPool, QTimer, Signal, Slot

from core.events.domains.document_events import DocumentEvent, DocumentEventPayload
from core.events.event_bus import EventBus
from core.models.extraction_models import DocumentBlock, BibliographyEntry as CanonicalBibliographyEntry
from core.services.extraction.bibliography_extraction_service import BibliographyExtractionService
from core.services.extraction.citation_extraction_service import InTextCitationExtractor
from core.services.extraction.citation_matcher import CitationMatcher
from core.services.extraction.citation_text_adapter import bibliography_entries_from_text
from core.services.extraction.language_feature_service import shared_language_service
from core.models.ontology_model import (
    EntityIntent,
    EntityPayload,
    EntityType,
    RelationIntent,
    RelationPayload,
    RelationType,
)


MONTHS = (
    "January|February|March|April|May|June|July|August|September|October|November|December|"
    "Jan\\.?|Feb\\.?|Mar\\.?|Apr\\.?|Jun\\.?|Jul\\.?|Aug\\.?|Sep\\.?|Sept\\.?|Oct\\.?|Nov\\.?|Dec\\.?"
)
AUTHOR_TOKEN = r"[A-Z][A-Za-z'`-]+"
PERSON_STOPWORDS = {
    "Abstract", "Introduction", "Discussion", "Conclusion", "References", "Figure", "Table",
    "University", "Department", "Journal", "Press", "Retrieved", "Available", "Copyright",
    "United States", "New York", "Los Angeles", "San Francisco", "Washington Post",
}


@dataclass
class PageText:
    page_num: int
    text: str


@dataclass
class BibliographyEntry:
    raw: str
    source_id: str
    title: str
    authors: List[str] = field(default_factory=list)
    year: Optional[str] = None
    key: str = ""
    ref_number: Optional[str] = None
    doi: Optional[str] = None


@dataclass
class IngestionResult:
    path: str
    source_id: Optional[str]
    entity_events: List[Tuple[EntityIntent, EntityPayload]] = field(default_factory=list)
    relation_events: List[Tuple[RelationIntent, RelationPayload]] = field(default_factory=list)


class IngestionWorkerSignals(QObject):
    finished = Signal(object)


def _get_spacy_nlp():
    """The shared spaCy pipeline (a private copy here loaded spaCy a third time)."""
    return shared_language_service().get_nlp()


class DocumentIngestionTask(QRunnable):
    def __init__(
        self,
        path,
        source_id,
        llm_manager,
        bus,
        signals: Optional[IngestionWorkerSignals] = None,
        document_extraction_service=None,
    ):
        super().__init__()
        self.path = path
        self.source_id = source_id
        self.llm_manager = llm_manager
        self.bus = bus
        self.signals = signals
        self.doc_name = os.path.basename(self.path)
        self._result = IngestionResult(path=path, source_id=source_id)
        self._batching = signals is not None
        self._page_offsets: List[int] = []
        self._metadata: Dict[str, str] = {}
        self._des = document_extraction_service

    def _status(self, msg: str, ms: int = 4000) -> None:
        """Emit a status message, silently ignoring a deleted or unavailable bus signal."""
        try:
            self.bus.status_message_requested.emit(msg, ms)
        except (RuntimeError, AttributeError):
            pass

    @Slot()
    def run(self):
        try:
            self._status(f"Indexing '{self.doc_name}'...", 4000)
            if self.llm_manager and getattr(self.llm_manager, "ai_enabled", False):
                self.llm_manager.index_documents([self.path])

            pages = self._load_pages()
            full_text = "\f".join(page.text for page in pages)
            self._page_offsets = self._compute_page_offsets(pages)
            self._emit_parent_source_metadata(full_text)
            self._status("Discovering citations, dates, and people...", 4000)

            bibliography_spans = self._bibliography_spans(full_text)
            bibliography_text = self._slice_spans(full_text, bibliography_spans)
            bibliography = self._extract_bibliography_entries(bibliography_text)
            self._emit_bibliography_graph(bibliography)

            citation_spans = self._extract_in_text_citations(full_text, bibliography)
            masked_text = self._mask_spans(full_text, bibliography_spans + [span for span, _entry in citation_spans])

            self._extract_dates(masked_text, full_text)
            self._extract_people(masked_text, full_text)

            if self.signals:
                self.signals.finished.emit(self._result)
            self._status(f"Finished processing '{self.doc_name}'.", 4000)
        except Exception as e:
            print(f"[Ingestion Service] Failed to process {self.path}: {e}")
            self._status(f"Failed to process '{self.doc_name}'.", 5000)

    def _load_pages(self) -> List[PageText]:
        # Phase 1 adapter: use shared blocks when DocumentExtractionService is available
        if self._des is not None:
            try:
                blocks = self._des.get_blocks(self.path)
                if blocks:
                    page_map: Dict[int, List[str]] = {}
                    for block in blocks:
                        page_map.setdefault(block.page_num, []).append(block.exact_text)
                    # Still open the doc briefly to read metadata only
                    try:
                        _doc = fitz.open(self.path)
                        self._metadata = dict(_doc.metadata or {})
                        _doc.close()
                    except Exception:
                        pass
                    return [
                        PageText(page_num=p, text="\n".join(page_map[p]))
                        for p in sorted(page_map)
                    ]
            except Exception:
                pass
        # Fallback: direct fitz extraction
        doc = fitz.open(self.path)
        try:
            self._metadata = dict(doc.metadata or {})
            return [PageText(page_num=i, text=doc.load_page(i).get_text("text") or "") for i in range(len(doc))]
        finally:
            doc.close()

    def _compute_page_offsets(self, pages: List[PageText]) -> List[int]:
        offsets = []
        cursor = 0
        for page in pages:
            offsets.append(cursor)
            cursor += len(page.text) + 1
        return offsets

    def _emit_parent_source_metadata(self, text: str):
        if not self.source_id:
            return
        title = (self._metadata.get("title") or "").strip() or self._guess_document_title(text)
        authors = self._parse_metadata_authors(self._metadata.get("author") or "")
        doi = self._first_doi(text)
        year = self._metadata_year() or self._first_year(text[:4000])
        properties = {
            "path": self.path,
            "title": title,
            "authors": ", ".join(authors),
            "year": year or "",
            "doi": doi or "",
            "extraction_kind": "source_metadata",
        }
        self._emit_entity_update(self.source_id, properties)
        for author in authors:
            author_id = self._emit_author_entity(author, context=title, source_id=self.source_id)
            self._emit_relation(
                source_id=self.source_id,
                target_id=author_id,
                relation_type=RelationType.AUTHORED_BY.value,
                properties={"role": "author", "extraction_kind": "source_author_metadata"},
            )

    def _bibliography_spans(self, text: str) -> List[Tuple[int, int]]:
        heading = re.search(
            r"(?im)^\s*(references|bibliography|works cited|literature cited)\s*$",
            text,
        )
        if not heading:
            heading = re.search(
                r"(?i)(?:^|\n|\.\s+)(references|bibliography|works cited|literature cited)\s*\n",
                text,
            )
        if not heading:
            return []
        return [(heading.start(), len(text))]

    def _slice_spans(self, text: str, spans: Iterable[Tuple[int, int]]) -> str:
        return "\n".join(text[start:end] for start, end in spans)

    def _mask_spans(self, text: str, spans: Iterable[Tuple[int, int]]) -> str:
        masked = list(text)
        for start, end in spans:
            start = max(0, start)
            end = min(len(masked), end)
            for idx in range(start, end):
                masked[idx] = "\n" if masked[idx] == "\n" else " "
        return "".join(masked)

    def _extract_bibliography_entries(self, bibliography_text: str) -> Dict[str, BibliographyEntry]:
        entries: Dict[str, BibliographyEntry] = {}
        for raw in self._split_bibliography_entries(bibliography_text):
            entry = self._parse_bibliography_entry(raw)
            if not entry:
                continue
            entries[entry.source_id] = entry
        return entries

    def _split_bibliography_entries(self, bibliography_text: str) -> List[str]:
        return [entry.raw_text for entry in bibliography_entries_from_text(bibliography_text)]

    def _parse_bibliography_entry(self, raw: str) -> Optional[BibliographyEntry]:
        entry = BibliographyExtractionService().normalize_entry(raw, self.source_id or "legacy")
        doi = entry.doi.lower().rstrip(".,;") if entry.doi else None
        key = doi or self._citation_key(entry.authors, entry.year, entry.title or "")
        if not key:
            return None
        return BibliographyEntry(
            raw=raw, source_id=self._stable_id("source", key),
            title=entry.title or f"Cited Source {entry.reference_number or entry.year or key[:8]}",
            authors=entry.authors, year=entry.year, key=key,
            ref_number=entry.reference_number, doi=doi,
        )

    def _parse_metadata_authors(self, value: str) -> List[str]:
        if not value:
            return []
        raw_authors = re.split(r"\s*(?:;|,?\s+and\s+|,?\s*&\s+)\s*", value)
        return [
            author.strip()
            for author in raw_authors
            if author.strip() and not self._is_bad_person_name(author.strip())
        ][:12]

    def _guess_document_title(self, text: str) -> str:
        for line in text.splitlines()[:30]:
            cleaned = re.sub(r"\s+", " ", line).strip()
            if 8 <= len(cleaned) <= 180 and not re.match(r"(?i)^(abstract|introduction|keywords|references)$", cleaned):
                return cleaned
        return os.path.basename(self.path)

    def _first_doi(self, text: str) -> Optional[str]:
        match = re.search(r"\b10\.\d{4,9}/[-._;()/:A-Z0-9]+\b", text, re.I)
        return match.group(0).lower() if match else None

    def _first_year(self, text: str) -> Optional[str]:
        match = re.search(r"\b(?:18|19|20)\d{2}\b", text)
        return match.group(0) if match else None

    def _metadata_year(self) -> Optional[str]:
        for value in (self._metadata.get("creationDate", ""), self._metadata.get("modDate", "")):
            match = re.search(r"\b(?:18|19|20)\d{2}\b", value or "")
            if match:
                return match.group(0)
        return None

    def _citation_key(self, authors: List[str], year: Optional[str], title: str) -> str:
        lead = authors[0].lower() if authors else ""
        year_key = (year or "").lower()
        title_key = re.sub(r"[^a-z0-9]+", " ", (title or "").lower()).strip()[:80]
        return " ".join(part for part in [lead, year_key, title_key] if part)

    def _emit_bibliography_graph(self, bibliography: Dict[str, BibliographyEntry]):
        current_author_ids = self._current_source_author_ids()
        for entry in bibliography.values():
            self._emit_entity(
                entity_id=entry.source_id,
                entity_type=EntityType.SOURCE.value,
                page_num=None,
                properties={
                    "title": entry.title,
                    "authors": ", ".join(entry.authors),
                    "year": entry.year or "",
                    "doi": entry.doi or "",
                    "bibliography_entry": entry.raw,
                    "citation_key": entry.key,
                    "is_extracted": True,
                    "extraction_kind": "bibliography_source",
                    "origin_id": entry.doi or entry.key,
                    "pdf_path": "",
                    "cited_by_source_id": self.source_id,
                },
            )
            cited_author_ids = [
                self._emit_author_entity(author, context=entry.raw, source_id=entry.source_id, year=entry.year)
                for author in entry.authors
            ]
            for author_id in cited_author_ids:
                self._emit_relation(
                    source_id=entry.source_id,
                    target_id=author_id,
                    relation_type=RelationType.AUTHORED_BY.value,
                    properties={
                        "role": "author",
                        "source_year": entry.year,
                        "source_title": entry.title,
                        "extraction_kind": "bibliography_author",
                    },
                )
            for current_author_id in current_author_ids:
                for cited_author_id in cited_author_ids:
                    self._emit_relation(
                        source_id=current_author_id,
                        target_id=cited_author_id,
                        relation_type=RelationType.REFERENCES.value,
                        properties={
                            "label": "cites",
                            "source_id": self.source_id,
                            "cited_source_id": entry.source_id,
                            "cited_year": entry.year,
                            "extraction_kind": "author_cites_author",
                        },
                    )
            if self.source_id:
                self._emit_relation(
                    source_id=self.source_id,
                    target_id=entry.source_id,
                    relation_type=RelationType.REFERENCES.value,
                    properties={
                        "citation_context": entry.raw,
                        "extraction_kind": "bibliography_reference",
                        "ref_number": entry.ref_number,
                        "cited_year": entry.year,
                        "cited_authors": entry.authors,
                        "cited_title": entry.title,
                    },
                )

    def _current_source_author_ids(self) -> List[str]:
        authors = self._parse_metadata_authors(self._metadata.get("author") or "")
        return [self._stable_id("personorg", author.lower()) for author in authors]

    def _emit_author_entity(self, author: str, context: str = "", source_id: Optional[str] = None, year: Optional[str] = None) -> str:
        author_id = self._stable_id("personorg", author.lower())
        self._emit_entity(
            entity_id=author_id,
            entity_type=EntityType.PERSON_ORG.value,
            page_num=None,
            properties={
                "title": author,
                "text": author,
                "role": "author",
                "context": context,
                "source_id": source_id,
                "year": year or "",
                "extraction_kind": "citation_author",
            },
        )
        return author_id

    def _extract_in_text_citations(self, text: str, bibliography: Dict[str, BibliographyEntry]) -> List[Tuple[Tuple[int, int], BibliographyEntry]]:
        doc_id = self.source_id or "legacy"
        body = self._mask_spans(text, self._bibliography_spans(text))
        block = DocumentBlock("legacy_body", doc_id, 0, "paragraph", body, body.casefold())
        citations = InTextCitationExtractor().extract([block], doc_id)
        entries = [CanonicalBibliographyEntry(
            entry_id=entry.source_id, doc_id=doc_id, raw_text=entry.raw,
            authors=entry.authors, first_author_surname=entry.authors[0] if entry.authors else "",
            year=entry.year, title=entry.title, doi=entry.doi, reference_number=entry.ref_number,
        ) for entry in bibliography.values()]
        by_id = {citation.citation_id: citation for citation in citations}
        spans = []
        for match in CitationMatcher().match_all(citations, entries):
            if match.is_ambiguous:
                continue
            citation = by_id[match.in_text_citation_id]
            entry = bibliography[match.bibliography_entry_id]
            start, end = citation.location.char_start, citation.location.char_end
            spans.append(((start, end), entry))
            span_match = re.compile(re.escape(citation.raw_text)).match(text, start)
            if span_match:
                self._emit_in_text_citation(citation.raw_text, span_match, text, entry)
        return spans

    def _emit_in_text_citation(self, citation_text: str, match, full_text: str, entry: BibliographyEntry):
        context = self._context(match, full_text, radius=260)
        citation_entity_id = self._stable_id("quote", f"{self.source_id}:{entry.source_id}:{match.start()}:{citation_text}")
        self._emit_entity(
            entity_id=citation_entity_id,
            entity_type=EntityType.QUOTE.value,
            page_num=self._page_for_offset(full_text, match.start()),
            properties={
                "quote": citation_text,
                "exact_text": citation_text,
                "text": citation_text,
                "context": context,
                "source_id": self.source_id,
                "cited_source_id": entry.source_id,
                "cited_title": entry.title,
                "cited_authors": entry.authors,
                "cited_year": entry.year,
                "cited_doi": entry.doi or "",
                "extraction_kind": "in_text_citation",
                "suggested_entity_types": [EntityType.EVIDENCE.value],
            },
        )
        if self.source_id:
            self._emit_relation(
                source_id=self.source_id,
                target_id=entry.source_id,
                relation_type="relation.attributed_to",
                properties={
                    "context": context,
                    "citation_text": citation_text,
                    "citation_entity_id": citation_entity_id,
                    "extraction_kind": "in_text_citation",
                    "cited_year": entry.year,
                    "cited_authors": entry.authors,
                    "cited_title": entry.title,
                },
            )
        self._emit_relation(
            source_id=citation_entity_id,
            target_id=entry.source_id,
            relation_type=RelationType.REFERENCES.value,
            properties={
                "citation_context": context,
                "citation_text": citation_text,
                "extraction_kind": "in_text_citation_evidence",
                "cited_year": entry.year,
                "cited_authors": entry.authors,
                "cited_title": entry.title,
            },
        )

    def _extract_dates(self, masked_text: str, original_text: str):
        seen = set()
        try:
            for found in datefinder.find_dates(
                masked_text,
                source=True,
                index=True,
                strict=False,
                allow_month_only=False,
                allow_compact_numeric=False,
            ):
                parsed_date, date_text, indexes = found
                start, end = indexes
                if not self._is_useful_date_text(date_text):
                    continue
                key = (date_text.lower(), self._page_for_offset(original_text, start), self._sentence_context(original_text, start, end))
                if key in seen:
                    continue
                seen.add(key)
                self._emit_timeline_event(date_text, start, end, original_text, normalized_date=parsed_date.date().isoformat())
        except Exception:
            pass

        date_patterns = [
            r"\b(?:18|19|20)\d{2}\s*(?:-|–|—|to)\s*(?:18|19|20)?\d{2}\b",
            r"\b(?:early|mid|late)\s+(?:18|19|20)\d0s\b",
            r"\b(?:18|19|20)\d0s\b",
            r"\b(?:18|19|20)\d{2}\b",
        ]
        for pattern in date_patterns:
            for match in re.finditer(pattern, masked_text, re.I):
                date_text = match.group(0).strip()
                key = (date_text.lower(), self._page_for_offset(original_text, match.start()), self._sentence_context(original_text, match.start(), match.end()))
                if key in seen:
                    continue
                seen.add(key)
                self._emit_timeline_event(date_text, match.start(), match.end(), original_text)

    def _is_useful_date_text(self, date_text: str) -> bool:
        return bool(re.search(r"(?:18|19|20)\d{2}|today|yesterday|tomorrow|century|decade", date_text, re.I))

    def _emit_timeline_event(self, date_text: str, start: int, end: int, original_text: str, normalized_date: str = ""):
        context = self._context_span(start, end, original_text)
        self._emit_entity(
            entity_id=self._stable_id("event", f"{self.source_id}:{date_text}:{context[:180]}"),
            entity_type=EntityType.TIMELINE_EVENT.value,
            page_num=self._page_for_offset(original_text, start),
            properties={
                "date": date_text,
                "normalized_date": normalized_date,
                "title": f"Timeline Event: {date_text}",
                "text": self._sentence_context(original_text, start, end) or date_text,
                "context": context,
                "source_id": self.source_id,
                "extraction_kind": "date_context",
                "suggested_entity_types": [EntityType.TIMELINE_EVENT.value],
            },
        )

    def _extract_people(self, masked_text: str, original_text: str):
        nlp = _get_spacy_nlp()
        if nlp:
            seen = set()
            for chunk_start, chunk in self._nlp_chunks(masked_text):
                doc = nlp(chunk)
                for ent in doc.ents:
                    if ent.label_ not in {"PERSON", "ORG"}:
                        continue
                    name = re.sub(r"\s+", " ", ent.text).strip()
                    if self._is_bad_person_name(name):
                        continue
                    start = chunk_start + ent.start_char
                    end = chunk_start + ent.end_char
                    key = (ent.label_, name.lower())
                    if key in seen:
                        continue
                    seen.add(key)
                    self._emit_person_entity(name, ent.label_.lower(), start, end, original_text, "spacy_ner")
            return

        pattern = re.compile(
            rf"\b(?:Dr\.|Prof\.|Professor|President|Minister|Senator|Judge|Justice|Sir|Dame)?\s*"
            rf"({AUTHOR_TOKEN}(?:\s+(?:[A-Z]\.|(?:de|del|van|von|da|di|la|le)\s+{AUTHOR_TOKEN}|{AUTHOR_TOKEN})){{1,3}})"
        )
        seen = set()
        for match in pattern.finditer(masked_text):
            name = re.sub(r"\s+", " ", match.group(1)).strip()
            if self._is_bad_person_name(name):
                continue
            context = self._context(match, original_text)
            key = name.lower()
            if key in seen:
                continue
            seen.add(key)
            self._emit_person_entity(name, "person", match.start(), match.end(), original_text, "regex_name")

    def _nlp_chunks(self, text: str, max_chars: int = 90000):
        cursor = 0
        length = len(text)
        while cursor < length:
            end = min(length, cursor + max_chars)
            if end < length:
                split_at = max(text.rfind("\n", cursor, end), text.rfind(". ", cursor, end))
                if split_at > cursor + 1000:
                    end = split_at + 1
            yield cursor, text[cursor:end]
            cursor = end

    def _emit_person_entity(self, name: str, label: str, start: int, end: int, original_text: str, extractor: str):
        context = self._context_span(start, end, original_text)
        entity_id = self._stable_id("personorg", f"{label}:{name.lower()}")
        self._emit_entity(
            entity_id=entity_id,
            entity_type=EntityType.PERSON_ORG.value,
            page_num=self._page_for_offset(original_text, start),
            properties={
                "title": name,
                "text": name,
                "role": label,
                "context": context,
                "source_id": self.source_id,
                "extractor": extractor,
                "extraction_kind": "person_org_mention",
                "suggested_entity_types": [EntityType.PERSON_ORG.value],
            },
        )
        if self.source_id:
            self._emit_relation(
                source_id=self.source_id,
                target_id=entity_id,
                relation_type=RelationType.BASIC.value,
                properties={"label": "mentions", "context": context, "extraction_kind": "person_org_mention"},
            )

    def _context(self, match, text: str, radius: int = 180) -> str:
        return self._context_span(match.start(), match.end(), text, radius=radius)

    def _context_span(self, start_offset: int, end_offset: int, text: str, radius: int = 180) -> str:
        start = max(0, start_offset - radius)
        end = min(len(text), end_offset + radius)
        return re.sub(r"\s+", " ", text[start:end]).strip()

    def _sentence_context(self, text: str, start: int, end: int) -> str:
        left = max(text.rfind(".", 0, start), text.rfind("\n", 0, start))
        right_dot = text.find(".", end)
        right_nl = text.find("\n", end)
        right_candidates = [idx for idx in [right_dot, right_nl] if idx != -1]
        right = min(right_candidates) if right_candidates else min(len(text), end + 180)
        return re.sub(r"\s+", " ", text[left + 1:right + 1]).strip()

    def _page_for_offset(self, full_text: str, offset: int) -> int:
        if self._page_offsets:
            page_num = 0
            for idx, page_offset in enumerate(self._page_offsets):
                if page_offset <= offset:
                    page_num = idx
                else:
                    break
            return page_num
        return full_text[:offset].count("\f")

    def _looks_like_citation_context(self, context: str) -> bool:
        return bool(re.search(r"\bdoi\b|https?://|\bet al\.|\(\s*(?:18|19|20)\d{2}[a-z]?\s*\)", context, re.I))

    def _is_bad_person_name(self, name: str) -> bool:
        compact = re.sub(r"\s+", " ", name).strip()
        if compact in PERSON_STOPWORDS:
            return True
        tokens = compact.split()
        if len(tokens) < 2 or len(tokens) > 4:
            return True
        if any(token.strip(".") in {"The", "This", "These", "Those", "Figure", "Table"} for token in tokens):
            return True
        if any(re.match(rf"^(?:{MONTHS})$", token, re.I) for token in tokens):
            return True
        return False

    def _stable_id(self, prefix: str, text: str) -> str:
        clean_text = re.sub(r"\s+", " ", text.strip()).lower()
        digest = hashlib.sha1(clean_text.encode("utf-8")).hexdigest()[:16]
        return f"{prefix}_{digest}"

    def _dedupe(self, values: Iterable[str]) -> List[str]:
        seen = set()
        result = []
        for value in values:
            key = value.lower()
            if key in seen:
                continue
            seen.add(key)
            result.append(value)
        return result

    def _emit_entity(self, entity_id, entity_type, page_num, properties):
        properties = dict(properties)
        origin_id = properties.get("origin_id") or self.path
        properties.setdefault("origin_id", origin_id)
        properties.setdefault("pdf_path", self.path)
        if page_num is not None:
            properties["page_num"] = page_num

        payload = EntityPayload(
            entity_id=entity_id,
            entity_type=entity_type,
            origin_id=origin_id,
            data={
                "properties": properties,
                "state": {"is_verified": False, "ai_generated": False, "origin": "deterministic_extractor"},
            },
        )
        if self._batching:
            self._result.entity_events.append((EntityIntent.ADD, payload))
        else:
            self.bus.entity_action_requested.emit(EntityIntent.ADD, payload)

    def _emit_entity_update(self, entity_id, properties):
        if not entity_id:
            return
        payload = EntityPayload(entity_id=entity_id, data=properties)
        if self._batching:
            self._result.entity_events.append((EntityIntent.UPDATE_PROPERTIES, payload))
        else:
            self.bus.entity_action_requested.emit(EntityIntent.UPDATE_PROPERTIES, payload)

    def _emit_relation(self, source_id, target_id, relation_type, properties):
        if not source_id or not target_id:
            return
        relation_id = self._stable_id("rel", f"{source_id}:{relation_type}:{target_id}:{properties.get('citation_text') or properties.get('label') or properties.get('ref_number') or ''}")
        payload = RelationPayload(
            relation_id=relation_id,
            source_id=source_id,
            target_id=target_id,
            relation_type=relation_type,
            data={
                "properties": properties,
                "state": {"is_verified": False, "origin": "deterministic_extractor"},
            },
        )
        if self._batching:
            self._result.relation_events.append((RelationIntent.ADD, payload))
        else:
            self.bus.relation_action_requested.emit(RelationIntent.ADD, payload)


class DocumentIngestionService(QObject):
    def __init__(self, llm_manager, source_format_registry=None, parent=None):
        super().__init__(parent)
        self.llm_manager = llm_manager
        self.bus = EventBus.get_instance()
        self._flush_queue: List[Tuple[str, object, object]] = []
        self._flush_timer = QTimer(self)
        self._flush_timer.setInterval(0)
        self._flush_timer.timeout.connect(self._flush_ingestion_events)
        self._document_extraction_service = None
        self._source_format_registry = source_format_registry
        self._intelligence_scheduler = None
        self.bus.document_added.connect(self._on_document_added)

    def set_document_extraction_service(self, svc) -> None:
        """Inject the shared DocumentExtractionService (Phase 1 adapter)."""
        self._document_extraction_service = svc

    def set_source_format_registry(self, registry) -> None:
        self._source_format_registry = registry

    def set_intelligence_scheduler(self, scheduler) -> None:
        self._intelligence_scheduler = scheduler

    def _accepts_ingestion(self, source_type: Optional[str]) -> bool:
        """
        True for source_types whose ingestion path is this generic
        text/entity extraction pipeline (backed by DocumentExtractionService's
        per-format DocumentBlock parsers). video/audio have their own
        transcription-based ingestion; data formats (csv/xlsx) have their own
        loader-based path — neither goes through here.
        """
        if self._source_format_registry is None:
            # No registry wired (e.g. a bare test construction) — preserve
            # the historical PDF-only behavior.
            return (source_type or "pdf") == "pdf"
        spec = self._source_format_registry.get(source_type or "")
        return spec is not None and spec.category in ("document", "image")

    def _on_document_added(self, event: DocumentEvent, payload: DocumentEventPayload):
        # SourceIntelligenceService owns canonical ingestion when present. Keep
        # this legacy worker only for standalone/back-compat constructions.
        if self._intelligence_scheduler is not None:
            return
        if event == DocumentEvent.DOCUMENT_ADDED and self._accepts_ingestion(payload.source_type):
            signals = IngestionWorkerSignals()
            signals.finished.connect(self._queue_ingestion_result)
            task = DocumentIngestionTask(
                path=payload.path,
                source_id=payload.source_id,
                llm_manager=self.llm_manager,
                bus=self.bus,
                signals=signals,
                document_extraction_service=self._document_extraction_service,
            )
            task._signals_ref = signals
            QThreadPool.globalInstance().start(task)

    def _queue_ingestion_result(self, result: IngestionResult):
        for intent, payload in result.entity_events:
            self._flush_queue.append(("entity", intent, payload))
        for intent, payload in result.relation_events:
            self._flush_queue.append(("relation", intent, payload))
        self.bus.status_message_requested.emit(
            f"Saving {len(self._flush_queue)} extracted graph items...",
            4000,
        )
        if not self._flush_timer.isActive():
            self._flush_timer.start()

    def _flush_ingestion_events(self):
        chunk_size = 40
        for _ in range(min(chunk_size, len(self._flush_queue))):
            kind, intent, payload = self._flush_queue.pop(0)
            if kind == "entity":
                self.bus.entity_action_requested.emit(intent, payload)
            else:
                self.bus.relation_action_requested.emit(intent, payload)
        if not self._flush_queue:
            self._flush_timer.stop()
            self.bus.status_message_requested.emit("Finished saving extracted graph items.", 3000)
