"""Headless projection of canonical citation artifacts into a typed graph."""
from __future__ import annotations

import os
from typing import Any, Dict

from core.models.intelligence_models import StageStatus


class CitationGraphService:
    def __init__(self, project_manager=None, artifact_service=None):
        self.project_manager = project_manager
        self.artifact_service = artifact_service

    def build(self, source: Dict[str, Any]) -> Dict[str, Any]:
        db = getattr(self.project_manager, "db_intelligence", None)
        if not source or not db or not self.artifact_service:
            return {"entities": [], "relations": [], "diagnostics": {"error": "Source unavailable"}}
        source_id = str(source.get("id") or source.get("source_id") or "")
        source_artifact_id = self.artifact_service.ensure_source_artifact(source)
        path = source.get("path") or ""
        entities = [{
            "id": source_artifact_id, "type": "entity.source",
            "text": source.get("title") or os.path.basename(path) or source_id,
            "source_id": source_id, "pdf_path": path,
            "artifact_id": source_artifact_id, "verified": True,
        }]
        relations = []
        entries = db.list_artifacts(
            source_id=source_id, artifact_type="citation_entry",
            status=StageStatus.COMPLETE.value, limit=100000,
        )
        matches_by_entry = {}
        for citation_id, entry_id in db.citation_assignments(source_id).items():
            if entry_id:
                matches_by_entry.setdefault(entry_id, []).append(citation_id)
        for entry in entries:
            entry_id = entry.get("artifact_id")
            meta = entry.get("metadata") or {}
            title = meta.get("title") or entry.get("exact_text") or "Bibliography entry"
            entities.append({
                "id": entry_id, "type": "entity.source", "text": str(title)[:240],
                "title": title, "authors": meta.get("authors") or [],
                "year": meta.get("year"), "doi": meta.get("doi"), "url": meta.get("url"),
                "pdf_path": path, "page_num": (entry.get("locator") or {}).get("page"),
                "artifact_id": entry_id, "review_state": entry.get("review_state"),
            })
            relations.append({
                "source": source_artifact_id, "target": entry_id,
                "type": "relation.references", "label": "references",
                "evidence_ids": [entry_id],
            })
            self._append_occurrences(
                db, entry_id, matches_by_entry.get(entry_id, []),
                source_id, path, entities, relations,
            )
        entities = list({item["id"]: item for item in entities if item.get("id")}.values())
        unique = {}
        for relation in relations:
            key = (relation["source"], relation["target"], relation["type"])
            unique.setdefault(key, relation)
        return {
            "entities": entities, "relations": list(unique.values()),
            "diagnostics": {
                "source_id": source_id, "bibliography_entries": len(entries),
                "in_text_citations": sum(
                    entity.get("type") == "entity.quote" for entity in entities
                ),
            },
        }

    @staticmethod
    def _append_occurrences(db, entry_id, citation_ids, source_id, path, entities, relations):
        for cite_id in citation_ids:
            cite = db.get_artifact(cite_id)
            if not cite or cite.get("source_id") != source_id:
                continue
            meta = cite.get("metadata") or {}
            entities.append({
                "id": cite_id, "type": "entity.quote",
                "text": cite.get("exact_text") or meta.get("raw_text") or "In-text citation",
                "quote": meta.get("raw_text") or cite.get("exact_text"),
                "pdf_path": path, "page_num": (cite.get("locator") or {}).get("page"),
                "artifact_id": cite_id, "citation_type": meta.get("citation_type"),
                "review_state": cite.get("review_state"),
            })
            relations.append({
                "source": entry_id, "target": cite_id,
                "type": "relation.cited_at", "label": "cited at",
                "evidence_ids": [cite_id],
            })
