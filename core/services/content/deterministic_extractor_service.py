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
from core.models.ontology_model import EntityModel, EntityType, RelationModel, RelationType
from core.services.content.deterministic_extractor import (
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


class _ExtractionWorker(QThread):
    """Runs extraction off the main thread."""
    finished = Signal(list, str, str)   # groups, entity_type, source_path
    error = Signal(str)

    def __init__(self, pdf_path: str, entity_type: str):
        super().__init__()
        self._pdf_path = pdf_path
        self._entity_type = entity_type

    def run(self):
        try:
            spec = DeterministicExtractorRegistry.get(self._entity_type)
            if not spec:
                self.error.emit(f"No extractor registered for '{self._entity_type}'")
                return
            full_text, page_texts = _extract_pages_fitz(self._pdf_path)
            groups = spec.extractor(full_text, page_texts)
            self.finished.emit(groups, self._entity_type, self._pdf_path)
        except Exception as exc:
            self.error.emit(str(exc))


class DeterministicExtractorService(QObject):
    def __init__(self, project_manager):
        super().__init__()
        self._pm = project_manager
        self._bus = EventBus.get_instance()
        self._last_results: List[ExtractedEntityGroup] = []
        self._last_source_path: str = ""
        self._worker: Optional[_ExtractionWorker] = None

        self._bus.discovery_action_requested.connect(self._handle_intent)
        self._bus.document_added.connect(self._on_document_added)

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

        # Run in background thread
        if self._worker and self._worker.isRunning():
            self._worker.terminate()
            self._worker.wait(500)

        self._worker = _ExtractionWorker(payload.source_path, payload.entity_type)
        self._worker.finished.connect(self._on_extraction_done)
        self._worker.error.connect(self._on_extraction_error)
        self._worker.start()

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
            return self._save_citation(group, source_path, db)
        elif group.entity_type == EntityType.PERSON_ORG.value:
            return self._save_person_org(group, source_path, db)
        elif group.entity_type == EntityType.TIMELINE_EVENT.value:
            return self._save_timeline_event(group, source_path, db)
        else:
            return self._save_generic(group, source_path, db)

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
            # Merge new context sentences into existing properties
            existing_contexts = existing.properties.get("contexts", [])
            new_contexts = [m.context for m in group.mentions]
            merged = list(dict.fromkeys(existing_contexts + new_contexts))[:20]
            entity.properties["contexts"] = merged
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
