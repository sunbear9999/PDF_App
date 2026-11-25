"""
core/services/content/deterministic_extractor_service.py

Event-driven wrapper around the deterministic extraction engine.

Responsibilities:
  - Listen for DiscoveryIntent events on the bus
  - Run the correct extractor and emit results
  - Save entities + edges to GraphDB with cross-document deduplication
"""

from __future__ import annotations

import os
import re
import uuid
from difflib import SequenceMatcher
from typing import Any, Dict, List, Optional, Tuple

from PySide6.QtCore import QObject, QThread, Signal

from core.events.event_bus import EventBus
from core.events.domains.discovery_events import (
    DiscoveryEvent,
    DiscoveryEventPayload,
    DiscoveryIntent,
    DiscoveryPayload,
)
from core.events.domains.workspace_events import WorkspaceIntent, WorkspacePayload
from core.models.ontology_model import EntityModel, EntityType, RelationModel, RelationType
from core.extraction.deterministic import (
    DeterministicExtractorRegistry,
    ExtractedEntityGroup,
    ExtractedMention,
)


_TO_SUPERSCRIPT = str.maketrans("0123456789", "⁰¹²³⁴⁵⁶⁷⁸⁹")


def _extract_page_text(page) -> str:
    """Extract text from a page, replacing actual PDF superscript digits with Unicode superscript chars.

    Uses 'dict' mode to get font-size metadata per span. Any span whose font size
    is notably smaller than the page median AND contains only digits is treated as
    a superscript citation marker (e.g. raised reference numbers in academic PDFs).
    Falls back to plain 'text' mode if dict extraction fails.
    """
    try:
        page_dict = page.get_text("dict")
        blocks = page_dict.get("blocks", [])

        all_sizes = [
            span["size"]
            for block in blocks
            for line in block.get("lines", [])
            for span in line.get("spans", [])
            if span.get("size", 0) > 0
        ]
        if not all_sizes:
            return page.get_text("text") or ""

        all_sizes.sort()
        body_size = all_sizes[len(all_sizes) // 2]

        parts: List[str] = []
        for block in blocks:
            for line in block.get("lines", []):
                for span in line.get("spans", []):
                    text = span.get("text", "")
                    size = span.get("size", body_size)
                    # Superscript: font < 70% of page median, contains only digits
                    if size < body_size * 0.85 and text.strip().isdigit():
                        parts.append(text.strip().translate(_TO_SUPERSCRIPT))
                    else:
                        parts.append(text)
                parts.append("\n")
        return "".join(parts)
    except Exception:
        return page.get_text("text") or ""


def _extract_pages_fitz(pdf_path: str) -> Tuple[str, List[Tuple[int, str]]]:
    """Return (full_text, [(page_num, page_text), ...]) using PyMuPDF.

    Superscript citation digits are normalised to Unicode superscript chars
    (¹²³…) so the extractor can detect them without confusing figure/section numbers.
    """
    import fitz  # noqa: PLC0415
    page_texts: List[Tuple[int, str]] = []
    try:
        with fitz.open(pdf_path) as doc:
            for i, page in enumerate(doc):
                page_texts.append((i, _extract_page_text(page)))
    except Exception:
        pass
    full_text = "\n".join(t for _, t in page_texts)
    return full_text, page_texts


def _blocks_to_page_texts(
    blocks,
) -> Tuple[str, List[Tuple[int, str]]]:
    """Convert a List[DocumentBlock] into the (full_text, page_texts) tuple
    that the deterministic extractors expect.  Block text already contains
    Unicode superscript chars from PDFLayoutParser."""
    page_map: Dict[int, List[str]] = {}
    for block in blocks:
        page_map.setdefault(block.page_num, []).append(block.exact_text)
    page_texts: List[Tuple[int, str]] = [
        (page_num, "\n".join(page_map[page_num]))
        for page_num in sorted(page_map)
    ]
    full_text = "\n".join(t for _, t in page_texts)
    return full_text, page_texts


class _ExtractionWorker(QThread):
    """Runs extraction off the main thread."""
    finished = Signal(list, str, str)   # groups, entity_type, source_path
    error = Signal(str)

    def __init__(self, pdf_path: str, entity_type: str, document_extraction_service=None):
        super().__init__()
        self._pdf_path = pdf_path
        self._entity_type = entity_type
        self._des = document_extraction_service

    def run(self):
        try:
            spec = DeterministicExtractorRegistry.get(self._entity_type)
            if not spec:
                self.error.emit(f"No extractor registered for '{self._entity_type}'")
                return

            # Prefer shared blocks (Phase 1 adapter); fall back to direct fitz parse
            if self._des is not None:
                try:
                    blocks = self._des.get_blocks(self._pdf_path)
                    if blocks:
                        full_text, page_texts = _blocks_to_page_texts(blocks)
                    else:
                        full_text, page_texts = _extract_pages_fitz(self._pdf_path)
                except Exception:
                    full_text, page_texts = _extract_pages_fitz(self._pdf_path)
            else:
                full_text, page_texts = _extract_pages_fitz(self._pdf_path)

            groups = spec.extractor(full_text, page_texts)
            self.finished.emit(groups, self._entity_type, self._pdf_path)
        except Exception as exc:
            self.error.emit(str(exc))


def _is_separator_hit(page, rect) -> bool:
    """Return True if the hit rect sits inside a decorative graphic rule or text separator.

    Two checks only (no "citation fragment" check — the worker must not miss
    bare years or short values that happen to have no letter-words on their line):
    1. Hairline height (<2 PDF points) — graphic rule.
    2. Full-width band dominated by non-alphanumeric chars (dashes, box-drawing, etc.).
    """
    import fitz
    if rect.height < 2.0:
        return True
    page_w = page.rect.width
    band = fitz.Rect(0, rect.y0 - 1, page_w, rect.y1 + 1)
    line_text = page.get_text("text", clip=band).strip()
    if not line_text:
        return False
    alnum = sum(c.isalnum() for c in line_text)
    return alnum / len(line_text) < 0.20


def _extract_sentence(text: str, term: str) -> str:
    """Return the sentence within *text* that contains *term* (case-insensitive).

    Falls back to the first 300 chars of *text* if no sentence boundary match is found.
    """
    if not text or not term:
        return text[:300] if text else ""
    term_lower = term.lower()
    # Split on common sentence-ending punctuation followed by whitespace or end-of-string.
    sentences = re.split(r'(?<=[.!?])\s+', text)
    for s in sentences:
        if term_lower in s.lower():
            return s.strip()[:300]
    return text[:300]


def _normalize_search_term(raw: str) -> str:
    """Normalise an entity's canonical text into a search-friendly string.

    Removes trailing parentheticals, normalises dashes and whitespace so the
    string more closely matches verbatim PDF text.
    """
    term = re.sub(r'\s+', ' ', raw).strip()
    # Drop trailing parentheticals: "Smith, J. (b. 1942)" → "Smith, J."
    term = re.sub(r'\s*\([^)]{0,60}\)\s*$', '', term).strip()
    # Normalise en/em dashes to ASCII hyphen (PDFs vary in dash encoding)
    term = term.replace('–', '-').replace('—', '-')
    return term


class _RefsWorker(QThread):
    """Scans all project PDFs for mentions of an entity name off the main thread.

    Opens each PDF independently with fitz.open() instead of using the shared
    pm.get_doc() cache — that cache is not thread-safe and the Document objects
    it holds are being used by the main thread for rendering.
    """
    finished = Signal(str, str, list)   # entity_name, entity_type, references

    def __init__(self, entity_name: str, entity_type: str, pm):
        super().__init__()
        self._entity_name = entity_name
        self._entity_type = entity_type
        self._pm = pm

    def run(self):
        import fitz
        refs_by_doc: Dict[str, Dict] = {}

        # Resolve PDF paths — prefer list_sources() (database-backed, handles removed docs)
        # over pm.pdfs (flat list that may be stale).
        pdf_paths: List[str] = []
        try:
            sources = self._pm.list_sources(source_type="pdf")
            pdf_paths = [s["path"] for s in sources if s.get("path")]
        except Exception:
            pdf_paths = list(getattr(self._pm, "pdfs", []) or [])

        search_term = _normalize_search_term(self._entity_name)
        if not search_term:
            self.finished.emit(self._entity_name, self._entity_type, [])
            return

        for pdf_path in pdf_paths:
            if not os.path.isfile(pdf_path):
                continue
            doc_name = os.path.basename(pdf_path)
            mention_count = 0
            contexts: List[str] = []
            occurrences: List[Dict] = []
            try:
                # Open a private copy — never touch the shared main-thread cache
                with fitz.open(pdf_path) as doc:
                    for page_num in range(len(doc)):
                        try:
                            page = doc.load_page(page_num)
                            hits = page.search_for(search_term, quads=False)
                            for rect in hits:
                                if _is_separator_hit(page, rect):
                                    continue
                                mention_count += 1
                                # Sentence-level context: full-width band, 60 pts above/below
                                clip = fitz.Rect(0, rect.y0 - 60, page.rect.width, rect.y1 + 60)
                                raw = page.get_text("text", clip=clip).strip()
                                ctx = _extract_sentence(raw, search_term)
                                occurrences.append({"page_num": page_num, "context": ctx})
                                if len(contexts) < 3:
                                    contexts.append(ctx[:200])
                        except Exception:
                            continue
            except Exception:
                continue

            if mention_count > 0:
                refs_by_doc[pdf_path] = {
                    "doc_path": pdf_path,
                    "doc_name": doc_name,
                    "mention_count": mention_count,
                    "contexts": contexts,
                    "occurrences": occurrences,
                }

        # Enrich with context snippets from previously saved entities (no DB writes here)
        db = getattr(self._pm, "db_graph", None)
        if db and self._entity_type:
            norm = self._entity_name.lower().strip()
            try:
                for entity in db.get_entities_by_type(self._entity_type):
                    stored = (
                        entity.properties.get("name")
                        or entity.properties.get("text")
                        or entity.properties.get("title")
                        or ""
                    ).lower()
                    if not stored or SequenceMatcher(None, norm, stored).ratio() < 0.75:
                        continue
                    for ref in entity.properties.get("source_refs", []):
                        path = ref.get("pdf_path") or ref.get("doc_path", "")
                        if not path or path not in refs_by_doc:
                            continue
                        entry = refs_by_doc[path]
                        ctx = ref.get("context", "")
                        if ctx and ctx not in entry["contexts"] and len(entry["contexts"]) < 5:
                            entry["contexts"].append(ctx)
            except Exception:
                pass

        references = sorted(refs_by_doc.values(), key=lambda r: -r["mention_count"])
        self.finished.emit(self._entity_name, self._entity_type, references)


class DeterministicExtractorService(QObject):
    def __init__(self, project_manager):
        super().__init__()
        self._pm = project_manager
        self._bus = EventBus.get_instance()
        self._last_results: List[ExtractedEntityGroup] = []
        self._last_source_path: str = ""
        self._worker: Optional[_ExtractionWorker] = None
        self._refs_worker: Optional[_RefsWorker] = None
        self._document_extraction_service = None

        self._bus.discovery_action_requested.connect(self._handle_intent)
        self._bus.document_added.connect(self._on_document_added)

    def set_document_extraction_service(self, svc) -> None:
        """Inject the shared DocumentExtractionService (Phase 1 adapter)."""
        self._document_extraction_service = svc

    # ------------------------------------------------------------------
    # Auto-merge detection

    def _on_document_added(self, event, payload) -> None:
        """When a new PDF is added, check if any saved citation entity matches it."""
        path = getattr(payload, "path", None)
        if not path:
            return
        db = getattr(self._pm, "db_graph", None)
        if not db:
            return
        try:
            filename_base = os.path.splitext(os.path.basename(path))[0]
            name_words = [w.lower() for w in re.split(r"[\s_\-]+", filename_base) if len(w) > 3]
            if not name_words:
                return
            existing = db.get_entities_by_type("entity.source")
            for entity in existing:
                if not entity.properties.get("citation_key"):
                    continue  # skip plain PDF source entities
                if entity.properties.get("in_project"):
                    continue  # already linked
                title = (entity.properties.get("title") or "").lower()
                matches = sum(1 for w in name_words[:6] if w in title)
                if matches >= min(3, len(name_words)):
                    authors_str = ", ".join(entity.properties.get("authors") or [])[:60]
                    title_short = (entity.properties.get("title") or "")[:60]
                    self._bus.status_message_requested.emit(
                        f"New PDF may match saved citation \"{title_short}\" ({authors_str}). "
                        f"Open Discovery dock → save that citation to link them.",
                        10000,
                    )
                    break
        except Exception:
            pass

    # ------------------------------------------------------------------
    # Intent routing
    # ------------------------------------------------------------------

    def _handle_intent(self, intent: DiscoveryIntent, payload: DiscoveryPayload):
        if intent == DiscoveryIntent.RUN_EXTRACTION:
            self._run_extraction(payload)
        elif intent == DiscoveryIntent.SAVE_ENTITY:
            if payload.entity_groups:
                self._save_group(payload.entity_groups[0], payload.source_path)
        elif intent == DiscoveryIntent.SAVE_ALL:
            self._save_all(payload.source_path or self._last_source_path)
        elif intent == DiscoveryIntent.FIND_REFERENCES:
            self._find_references(payload)

    # ------------------------------------------------------------------
    # Extraction
    # ------------------------------------------------------------------

    def _run_extraction(self, payload: DiscoveryPayload):
        if not payload.source_path or not payload.entity_type:
            return

        self._bus.discovery_state_changed.emit(
            DiscoveryEvent.EXTRACTION_STARTED,
            DiscoveryEventPayload(entity_type=payload.entity_type, source_path=payload.source_path),
        )

        # Prefer entities already produced by the ingestion pipeline (the
        # intelligence ENTITIES stage). Opening Discovery should just display
        # them — not re-run extraction.
        intel_groups = self._groups_from_intelligence(payload.source_path, payload.entity_type)
        if intel_groups:
            self._on_extraction_done(intel_groups, payload.entity_type, payload.source_path)
            return

        cached_groups = self._groups_from_shared_extraction(payload.source_path, payload.entity_type)
        if cached_groups:
            self._on_extraction_done(cached_groups, payload.entity_type, payload.source_path)
            return

        # Run in background thread
        if self._worker and self._worker.isRunning():
            self._worker.terminate()
            self._worker.wait(500)

        self._worker = _ExtractionWorker(
            payload.source_path,
            payload.entity_type,
            self._document_extraction_service,
        )
        self._worker.finished.connect(self._on_extraction_done)
        self._worker.error.connect(self._on_extraction_error)
        self._worker.start()

    def _groups_from_intelligence(self, source_path: str, entity_type: str) -> List[ExtractedEntityGroup]:
        """Build Discovery groups from the ingestion pipeline's canonical entities.

        The intelligence ENTITIES stage (layered NER + resolution) writes typed,
        resolved person/org entities + mentions to intelligence_artifacts at
        document-add time. This reads them back so Discovery displays existing
        results instantly instead of re-running extraction.
        """
        if entity_type != EntityType.PERSON_ORG.value:
            return []
        db = getattr(self._pm, "db_intelligence", None)
        if not db:
            return []
        try:
            rec = self._pm.get_source_record_by_path(source_path)
        except Exception:
            rec = None
        source_id = rec.get("id") if rec else None
        if not source_id:
            return []
        try:
            raw_mentions = db.list_artifacts(source_id=source_id, artifact_type="entity_mention", limit=100000)
        except Exception:
            return []
        by_canonical: Dict[str, List[dict]] = {}
        for m in raw_mentions:
            if (m.get("metadata") or {}).get("entity_type") not in ("PERSON", "ORG"):
                continue
            parent = m.get("parent_artifact_id")
            if parent:
                by_canonical.setdefault(parent, []).append(m)
        if not by_canonical:
            return []

        # Batch-fetch sentence texts for mention context.
        from core.models.intelligence_models import ArtifactRecord
        sent_ids = {
            (m.get("locator") or {}).get("sentence_id")
            for ms in by_canonical.values() for m in ms
            if (m.get("locator") or {}).get("sentence_id")
        }
        sent_artifact_ids = [
            ArtifactRecord.stable_id(source_id, "sentence", "extraction_sentences", sid)
            for sid in sent_ids
        ]
        sent_text: Dict[str, str] = {}
        try:
            for art in db.list_artifacts_by_ids(sent_artifact_ids):
                sent_text[art.get("storage_ref_id")] = art.get("exact_text") or ""
        except Exception:
            pass

        groups: List[ExtractedEntityGroup] = []
        for canonical_id, ms in by_canonical.items():
            canonical = db.get_artifact(canonical_id)
            if not canonical:
                continue
            name = canonical.get("exact_text") or ""
            role = (canonical.get("metadata") or {}).get("entity_type", "")
            mentions, seen_ctx = [], set()
            for m in ms:
                loc = m.get("locator") or {}
                surface = (m.get("metadata") or {}).get("surface_text") or m.get("exact_text") or name
                ctx = sent_text.get(loc.get("sentence_id"), "") or surface
                if ctx in seen_ctx:
                    continue
                seen_ctx.add(ctx)
                mentions.append(ExtractedMention(text=surface, context=ctx[:300], page_num=loc.get("page")))
            if mentions:
                auto_desc = " ".join(dict.fromkeys(x.context[:200] for x in mentions[:2]))[:400]
                groups.append(ExtractedEntityGroup(
                    canonical_text=name,
                    entity_type=EntityType.PERSON_ORG.value,
                    mentions=mentions,
                    extra_properties={"role": role, "description": auto_desc},
                ))
        return sorted(groups, key=lambda g: -len(g.mentions))

    def _groups_from_shared_extraction(self, source_path: str, entity_type: str) -> List[ExtractedEntityGroup]:
        """Reuse DocumentExtractionService outputs before launching discovery extraction."""
        des = self._document_extraction_service
        if not des or entity_type != EntityType.PERSON_ORG.value:
            return []
        getter = getattr(des, "get_cached_entities", None)
        entities = getter(source_path) if callable(getter) else []
        if not entities:
            return []
        try:
            blocks = des.get_blocks(source_path) or []
        except Exception:
            blocks = []
        block_text = {getattr(block, "block_id", ""): getattr(block, "exact_text", "") for block in blocks}
        groups: List[ExtractedEntityGroup] = []
        for entity in entities:
            if getattr(entity, "entity_type", "") != entity_type:
                continue
            mentions = []
            for mention in getattr(entity, "mentions", []) or []:
                context = block_text.get(getattr(mention, "block_id", "") or "")
                if not context:
                    context = getattr(entity, "canonical_text", "")
                page_num = getattr(mention, "page_num", None)
                mentions.append(ExtractedMention(
                    text=getattr(entity, "canonical_text", ""),
                    context=context[:500],
                    page_num=int(page_num or 0),
                ))
            if mentions:
                groups.append(ExtractedEntityGroup(
                    canonical_text=getattr(entity, "canonical_text", ""),
                    entity_type=entity_type,
                    mentions=mentions,
                    extra_properties={
                        "aliases": list(getattr(entity, "aliases", []) or []),
                        "shared_extraction_cache": True,
                    },
                ))
        return groups

    def _on_extraction_done(self, groups: List[ExtractedEntityGroup], entity_type: str, source_path: str):
        self._last_results = groups
        self._last_source_path = source_path
        self._bus.discovery_state_changed.emit(
            DiscoveryEvent.EXTRACTION_COMPLETE,
            DiscoveryEventPayload(
                entity_groups=groups,
                entity_type=entity_type,
                source_path=source_path,
            ),
        )

    def _on_extraction_error(self, error_msg: str):
        self._bus.discovery_state_changed.emit(
            DiscoveryEvent.EXTRACTION_COMPLETE,
            DiscoveryEventPayload(error=error_msg),
        )

    # ------------------------------------------------------------------
    # Saving
    # ------------------------------------------------------------------

    def _find_references(self, payload: DiscoveryPayload) -> None:
        """Launch background scan of all project PDFs for mentions of the entity."""
        entity_name = payload.entity_name
        entity_type = payload.entity_type or ""
        if not entity_name:
            return
        if self._refs_worker and self._refs_worker.isRunning():
            self._refs_worker.quit()
            self._refs_worker.wait(500)
        self._refs_worker = _RefsWorker(entity_name, entity_type, self._pm)
        self._refs_worker.finished.connect(self._on_refs_done)
        self._refs_worker.start()

    def _on_refs_done(self, entity_name: str, entity_type: str, references: List[Dict]) -> None:
        self._bus.discovery_state_changed.emit(
            DiscoveryEvent.REFERENCES_FOUND,
            DiscoveryEventPayload(
                entity_name=entity_name,
                entity_type=entity_type,
                references=references,
            ),
        )

    def _save_all(self, source_path: str):
        saved_ids: List[str] = []
        for group in self._last_results:
            entity_id = self._save_group(group, source_path)
            if entity_id:
                saved_ids.append(entity_id)
        self._bus.discovery_state_changed.emit(
            DiscoveryEvent.SAVE_COMPLETE,
            DiscoveryEventPayload(saved_ids=saved_ids, source_path=source_path),
        )

    def _save_group(self, group: ExtractedEntityGroup, source_path: str) -> Optional[str]:
        db = getattr(self._pm, "db_graph", None)
        if not db:
            return None

        if group.entity_type == EntityType.SOURCE.value:
            entity_id = self._save_citation(group, source_path, db)
        elif group.entity_type == EntityType.PERSON_ORG.value:
            entity_id = self._save_person_org(group, source_path, db)
        elif group.entity_type == EntityType.TIMELINE_EVENT.value:
            entity_id = self._save_timeline_event(group, source_path, db)
        else:
            entity_id = self._save_generic(group, source_path, db)

        if entity_id:
            self._dispatch_to_workspace(entity_id, group)
        return entity_id

    def _dispatch_to_workspace(self, entity_id: str, group: ExtractedEntityGroup) -> None:
        """After saving an entity to the graph DB, add a corresponding node to the workspace canvas."""
        text = (
            group.extra_properties.get("name")
            or group.extra_properties.get("title")
            or group.canonical_text
        )
        entity_dict = {
            "id": entity_id,
            "type": group.entity_type,
            "text": text,
            **group.extra_properties,
        }
        self._bus.workspace_action_requested.emit(
            WorkspaceIntent.IMPORT_GRAPH,
            WorkspacePayload(extra={"graph": {"entities": [entity_dict], "relations": []}}),
        )

    # ---------- Citation saving ----------

    def _citation_dedup_key(self, group: ExtractedEntityGroup) -> str:
        props = group.extra_properties
        year = props.get("year", "")
        title = props.get("title", "")
        authors = props.get("authors") or []
        first_author = (authors[0].split()[-1] if authors else "").lower()
        title_words = "_".join(re.sub(r"[^\w\s]", "", title).lower().split()[:5])
        return f"{year}_{first_author}_{title_words}"

    def _find_matching_citation_entity(self, group: ExtractedEntityGroup, db) -> Optional[EntityModel]:
        target_key = self._citation_dedup_key(group)
        if not target_key.strip("_"):
            return None
        existing = db.get_entities_by_type(EntityType.SOURCE.value)
        for entity in existing:
            stored_props = entity.properties
            # Skip plain PDF source entities (path-based)
            if stored_props.get("path") and not stored_props.get("citation_key"):
                continue
            stored_key = self._citation_dedup_key_from_props(stored_props)
            if stored_key and SequenceMatcher(None, target_key, stored_key).ratio() > 0.80:
                return entity
        return None

    def _citation_dedup_key_from_props(self, props: Dict) -> str:
        year = props.get("year", "")
        title = props.get("title", "")
        authors = props.get("authors") or []
        first_author = (authors[0].split()[-1] if authors else "").lower()
        title_words = "_".join(re.sub(r"[^\w\s]", "", title).lower().split()[:5])
        return f"{year}_{first_author}_{title_words}"

    def _is_source_in_project(self, title: str) -> bool:
        """Return True if a PDF with a similar title is already in the project."""
        pm = self._pm
        if not pm or not title:
            return False
        title_lower = title.lower().strip()
        title_words = [w for w in title_lower.split() if len(w) > 3]
        if not title_words:
            return False
        pdf_paths = getattr(pm, "pdfs", []) or []
        for path in pdf_paths:
            basename = os.path.basename(path).lower().replace("_", " ").replace("-", " ")
            if sum(1 for w in title_words[:5] if w in basename) >= min(2, len(title_words)):
                return True
        return False

    def _save_citation(self, group: ExtractedEntityGroup, source_path: str, db) -> Optional[str]:
        props = group.extra_properties
        title = props.get("title", group.canonical_text)
        in_project = self._is_source_in_project(title)

        # Find or create the cited Source entity
        existing = self._find_matching_citation_entity(group, db)
        if existing:
            cited_entity = existing
            # Update in_project if we now know it's in the project
            if in_project and not existing.properties.get("in_project"):
                existing.properties["in_project"] = True
                db.upsert_entity(existing, commit=False)
        else:
            cited_entity = EntityModel(
                id=str(uuid.uuid4()),
                entity_type=EntityType.SOURCE.value,
                origin_id=None,
                properties={
                    "title": title,
                    "authors": props.get("authors", []),
                    "year": props.get("year", ""),
                    "journal": props.get("journal", ""),
                    "citation_key": props.get("citation_key", ""),
                    "bibliography_raw": props.get("bibliography_raw", ""),
                    "source_type": "citation",
                    "in_project": in_project,
                },
                state={"is_verified": True, "ai_generated": False, "origin": "deterministic"},
            )
            db.upsert_entity(cited_entity, commit=False)

        # Get or create the current document's Source entity
        current_source = db.ensure_source_entity(source_path, commit=False) if source_path else None

        # Create/ensure a cites edge from current doc → cited source
        if current_source and current_source.id != cited_entity.id:
            self._ensure_relation(
                db,
                source_id=current_source.id,
                target_id=cited_entity.id,
                relation_type=RelationType.REFERENCES.value,
                properties={
                    "label": "cites",
                    "citation_key": props.get("citation_key", ""),
                    "citation_count": len(group.mentions),
                },
            )

        # Save in-text mentions as Quote entities linked to cited source
        for mention in group.mentions:
            self._save_quote(mention, cited_entity.id, source_path, db)

        db._conn.commit()
        return cited_entity.id

    # ---------- Shared helpers ----------

    def _make_source_ref(self, source_path: str, group: ExtractedEntityGroup) -> Dict:
        """Build a single source_ref dict from the current extraction context."""
        first_mention = group.mentions[0] if group.mentions else None
        return {
            "pdf_path": source_path,
            "page_num": first_mention.page_num if first_mention else 0,
            "doc_name": os.path.basename(source_path) if source_path else "",
            "context": first_mention.context if first_mention else "",
        }

    def _merge_source_refs(self, entity: EntityModel, source_path: str, group: ExtractedEntityGroup) -> None:
        """Add a new source_ref to an existing entity's source_refs list if not already present."""
        refs = entity.properties.setdefault("source_refs", [])
        existing_paths = {r.get("pdf_path", "") for r in refs}
        if source_path and source_path not in existing_paths:
            refs.append(self._make_source_ref(source_path, group))

    # ---------- Person/Org saving ----------

    def _find_matching_person(self, canonical: str, db) -> Optional[EntityModel]:
        existing = db.get_entities_by_type(EntityType.PERSON_ORG.value)
        norm = canonical.lower().strip()
        for entity in existing:
            stored_name = (entity.properties.get("name") or entity.properties.get("text") or "").lower().strip()
            if SequenceMatcher(None, norm, stored_name).ratio() > 0.88:
                return entity
        return None

    def _save_person_org(self, group: ExtractedEntityGroup, source_path: str, db) -> Optional[str]:
        existing = self._find_matching_person(group.canonical_text, db)
        if existing:
            entity = existing
            existing_contexts = existing.properties.get("contexts", [])
            new_contexts = [m.context for m in group.mentions]
            merged = list(dict.fromkeys(existing_contexts + new_contexts))[:20]
            entity.properties["contexts"] = merged
            self._merge_source_refs(entity, source_path, group)
        else:
            entity = EntityModel(
                id=str(uuid.uuid4()),
                entity_type=EntityType.PERSON_ORG.value,
                origin_id=source_path or None,
                properties={
                    "name": group.canonical_text,
                    "text": group.canonical_text,
                    "role": group.extra_properties.get("role", ""),
                    "description": group.extra_properties.get("description", ""),
                    "contexts": [m.context for m in group.mentions][:10],
                    "mention_count": len(group.mentions),
                    "source_refs": [self._make_source_ref(source_path, group)],
                },
                state={"is_verified": True, "ai_generated": False, "origin": "deterministic"},
            )
        db.upsert_entity(entity, commit=True)
        return entity.id

    # ---------- Timeline event saving ----------

    def _save_timeline_event(self, group: ExtractedEntityGroup, source_path: str, db) -> Optional[str]:
        norm_date = group.extra_properties.get("date", group.canonical_text)
        existing = db.get_entities_by_type(EntityType.TIMELINE_EVENT.value)
        match = next(
            (e for e in existing if e.properties.get("date", "") == norm_date),
            None,
        )
        if match:
            entity = match
            existing_contexts = entity.properties.get("contexts", [])
            new_contexts = [m.context for m in group.mentions]
            entity.properties["contexts"] = list(dict.fromkeys(existing_contexts + new_contexts))[:20]
            self._merge_source_refs(entity, source_path, group)
        else:
            entity = EntityModel(
                id=str(uuid.uuid4()),
                entity_type=EntityType.TIMELINE_EVENT.value,
                origin_id=source_path or None,
                properties={
                    "text": group.canonical_text,
                    "date": norm_date,
                    "certainty": group.extra_properties.get("certainty", "confirmed"),
                    "description": group.extra_properties.get("description", ""),
                    "contexts": [m.context for m in group.mentions][:10],
                    "source_refs": [self._make_source_ref(source_path, group)],
                },
                state={"is_verified": True, "ai_generated": False, "origin": "deterministic"},
            )
        db.upsert_entity(entity, commit=True)
        return entity.id

    # ---------- Generic entity saving ----------

    def _save_generic(self, group: ExtractedEntityGroup, source_path: str, db) -> Optional[str]:
        entity = EntityModel(
            id=str(uuid.uuid4()),
            entity_type=group.entity_type,
            origin_id=source_path or None,
            properties={
                "text": group.canonical_text,
                "contexts": [m.context for m in group.mentions][:10],
                "source_refs": [self._make_source_ref(source_path, group)],
                **group.extra_properties,
            },
            state={"is_verified": True, "ai_generated": False, "origin": "deterministic"},
        )
        db.upsert_entity(entity, commit=True)
        return entity.id

    # ---------- Shared helpers ----------

    def _save_quote(self, mention: ExtractedMention, cited_entity_id: str,
                    source_path: str, db):
        entity = EntityModel(
            id=str(uuid.uuid4()),
            entity_type=EntityType.QUOTE.value,
            origin_id=source_path or None,
            properties={
                "text": mention.text,
                "quote": mention.context,
                "context": mention.context,
                "page_num": mention.page_num,
                "pdf_path": source_path,
                "source_id": cited_entity_id,
            },
            state={"is_verified": True, "ai_generated": False, "origin": "deterministic"},
        )
        db.upsert_entity(entity, commit=False)
        self._ensure_relation(
            db,
            source_id=entity.id,
            target_id=cited_entity_id,
            relation_type=RelationType.REFERENCES.value,
            properties={"label": "in-text citation", "context": mention.context},
        )

    def _ensure_relation(self, db, source_id: str, target_id: str,
                         relation_type: str, properties: Dict):
        if not db._conn:
            return
        cursor = db._conn.cursor()
        cursor.execute(
            "SELECT id FROM relations WHERE source_id=? AND target_id=? AND relation_type=?",
            (source_id, target_id, relation_type),
        )
        row = cursor.fetchone()
        if row:
            return  # already exists
        relation = RelationModel(
            id=str(uuid.uuid4()),
            relation_type=relation_type,
            source_id=source_id,
            target_id=target_id,
            evidence_ids=[],
            properties=properties,
            state={"is_verified": True, "origin": "deterministic"},
        )
        db.upsert_relation(relation, commit=False)

    # ------------------------------------------------------------------
    # LLM context export
    # ------------------------------------------------------------------

    def get_last_results_as_context(self) -> str:
        """Format last extraction results as compact text for LLM prompts.

        Includes description (user-edited or auto-generated) and up to 5 mention
        context snippets per entity so the LLM has real evidence to work from.
        """
        if not self._last_results:
            return "No entities extracted yet."
        lines = []
        for group in self._last_results[:80]:
            desc = group.extra_properties.get("description", "")
            header = f"[{group.entity_type}] {group.canonical_text}"
            if desc:
                header += f"  — {desc[:150]}"
            lines.append(header)
            for mention in group.mentions[:5]:
                lines.append(f"  (p.{mention.page_num + 1}) {mention.context[:300]}")
        return "\n".join(lines)
