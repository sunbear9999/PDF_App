"""Compatibility adapter for external managers without canonical intelligence.

Uses RetrievalPlan/Result and the shared vector policy. Uncatalogued records are
explicitly marked; their vector IDs are never advertised as canonical artifact IDs.
"""
from core.models.intelligence_models import RetrievalPlan, RetrievalResult, SourceScope
from core.services.intelligence.vector_filters import metadata_matches, vector_where


class LegacyRetrievalAdapter:
    def __init__(self, llm_manager):
        self.llm_manager = llm_manager

    def build_plan(self, query, *, allowed_docs=None, allowed_source_ids=None, **kwargs):
        refs = allowed_docs or []
        paths = [ref for ref in refs if "/" in ref or "\\" in ref]
        names = [ref for ref in refs if ref not in paths]
        return RetrievalPlan(query=query, allowed_source_ids=allowed_source_ids or [],
                             source_scope=SourceScope(source_ids=allowed_source_ids or [], source_paths=paths, display_names=names), **kwargs)

    def retrieve(self, plan):
        manager = self.llm_manager
        if not manager or not getattr(manager, "ai_enabled", False):
            return []
        try:
            embedding = manager.get_embedding(plan.query)
            if hasattr(manager, "query_by_raw_embedding"):
                raw = manager.query_by_raw_embedding(embedding, n_results=plan.max_results,
                    allowed_docs=[*plan.source_scope.source_paths, *plan.source_scope.display_names],
                    tag_filters=plan.tag_filters, tag_logic=plan.tag_logic)
            else:
                raw = manager.collection.query(query_embeddings=[embedding], n_results=plan.max_results,
                    where=vector_where(plan), include=["documents", "metadatas", "distances"])
        except Exception:
            return []
        return self._results(plan, raw or {})

    @staticmethod
    def _results(plan, raw):
        results = []
        ids, distances = (raw.get("ids") or [[]])[0], (raw.get("distances") or [[]])[0]
        metadata = (raw.get("metadatas") or [[]])[0]
        for index, text in enumerate((raw.get("documents") or [[]])[0]):
            meta = metadata[index] if index < len(metadata) else {}
            meta = meta or {}
            if not metadata_matches(plan, meta):
                continue
            locator = dict(meta.get("source_locator") or {})
            for name in ("page", "start", "end", "char_start", "char_end", "block_id", "segment_index"):
                if name in meta:
                    locator.setdefault(name, meta[name])
            distance = float(distances[index]) if index < len(distances) else 1.0
            results.append(RetrievalResult(
                artifact_id="", artifact_type=meta.get("artifact_type") or "chunk",
                source_id=meta.get("source_id"), source_title=meta.get("doc_name") or "",
                locator=locator, exact_text=text, score=1 / (1 + max(0, distance)),
                retrieval_reason="Legacy vector record; ingest into canonical intelligence for exact artifact navigation",
                metadata={**meta, "legacy_unhydrated": True, "legacy_record_id": ids[index] if index < len(ids) else ""},
            ))
        return sorted(results, key=lambda item: item.score, reverse=True)[:plan.max_results]
