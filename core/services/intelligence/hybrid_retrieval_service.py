"""Artifact-first retrieval with vector RAG fallback."""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from core.models.intelligence_models import RetrievalPlan, RetrievalResult


STRUCTURED_TYPES = [
    "annotation",
    "entity",
    "entity_mention",
    "citation_entry",
    "in_text_citation",
    "date_mention",
    "table",
    "transcript_segment",
    "block",
]

INTENT_TYPE_HINTS = {
    "annotation": ["annotation", "block", "transcript_segment"],
    "citation": ["citation_entry", "in_text_citation", "block"],
    "entity": ["entity", "entity_mention", "block"],
    "date": ["date_mention", "block"],
    "table": ["table", "block"],
    "transcript": ["transcript_segment"],
}


class HybridRetrievalService:
    def __init__(self, project_manager=None, llm_manager=None, artifact_service=None):
        self.project_manager = project_manager
        self.llm_manager = llm_manager
        self.artifact_service = artifact_service
        self._providers: Dict[str, Any] = {}

    def register_provider(self, provider_id: str, callback) -> None:
        if not provider_id or not callable(callback):
            raise ValueError("Retrieval provider requires a stable id and callable.")
        self._providers[provider_id] = callback

    def unregister_provider(self, provider_id: str) -> None:
        self._providers.pop(provider_id, None)

    def build_plan(
        self,
        query: str,
        *,
        allowed_source_ids: Optional[List[str]] = None,
        max_results: int = 10,
        artifact_types: Optional[List[str]] = None,
        use_embeddings: bool = True,
    ) -> RetrievalPlan:
        intent = _infer_intent(query)
        preferred = list(artifact_types or INTENT_TYPE_HINTS.get(intent, []))
        return RetrievalPlan(
            query=query,
            allowed_source_ids=list(allowed_source_ids or []),
            artifact_types=list(artifact_types or preferred or []),
            preferred_artifact_types=preferred,
            query_intent=intent,
            use_embeddings=use_embeddings,
            max_results=max_results,
        )

    def retrieve(self, plan: RetrievalPlan) -> List[RetrievalResult]:
        results: List[RetrievalResult] = []
        if self.artifact_service:
            try:
                self.artifact_service.sync_all_annotation_artifacts()
            except Exception:
                pass
        if plan.use_structured:
            results.extend(self._structured_search(plan))
        for provider_id, provider in list(self._providers.items()):
            try:
                for item in provider(plan):
                    if isinstance(item, RetrievalResult):
                        results.append(item)
            except Exception as exc:
                print(f"[HybridRetrievalService] provider '{provider_id}' failed: {exc}")
        if plan.use_embeddings and len(results) < plan.max_results:
            results.extend(self._vector_search(plan, remaining=plan.max_results - len(results)))
        if plan.use_reranker:
            results = self._rerank(plan, results)
        return _dedupe_results(sorted(results, key=lambda item: item.score, reverse=True))[: plan.max_results]

    def _structured_search(self, plan: RetrievalPlan) -> List[RetrievalResult]:
        db = getattr(self.project_manager, "db_intelligence", None)
        if not db:
            return []
        artifact_types = plan.artifact_types or plan.preferred_artifact_types or STRUCTURED_TYPES
        rows = db.search_artifacts(
            plan.query,
            artifact_types=artifact_types,
            source_ids=plan.allowed_source_ids,
            limit=max(plan.max_results * 2, 10),
        )
        results = []
        for row in rows:
            text = row.get("exact_text") or row.get("normalized_text") or ""
            artifact_type = row.get("artifact_type") or ""
            boost = _type_boost(artifact_type, plan)
            if not plan.include_stale and row.get("status") == "stale":
                boost *= 0.5
            results.append(RetrievalResult(
                artifact_id=row["artifact_id"],
                artifact_type=artifact_type,
                source_id=row.get("source_id"),
                source_title=self._source_title(row.get("source_id"), row.get("source_path")),
                locator=row.get("locator") or {},
                section_path=row.get("section_path") or [],
                exact_text=text,
                score=boost,
                score_breakdown={"structured": boost, "type_priority": boost},
                retrieval_reason=f"Matched {artifact_type} artifact for {plan.query_intent} retrieval",
                confidence=row.get("confidence"),
                metadata=row.get("metadata") or {},
            ))
        return results

    def _rerank(self, plan: RetrievalPlan, results: List[RetrievalResult]) -> List[RetrievalResult]:
        # Keep reranking deterministic for now. LLM reranking must be added as a
        # blueprint step so it remains prompt-managed and traceable.
        query_terms = {term.lower() for term in plan.query.split() if len(term) > 2}
        reranked = []
        for item in results[: max(plan.rerank_top_k, plan.max_results)]:
            text = (item.exact_text or "").lower()
            lexical = sum(1 for term in query_terms if term in text) / max(1, len(query_terms))
            score = item.score + (0.25 * lexical)
            breakdown = dict(item.score_breakdown)
            breakdown["deterministic_rerank"] = lexical
            reranked.append(RetrievalResult(
                artifact_id=item.artifact_id,
                artifact_type=item.artifact_type,
                source_id=item.source_id,
                source_title=item.source_title,
                locator=item.locator,
                section_path=item.section_path,
                exact_text=item.exact_text,
                score=score,
                score_breakdown=breakdown,
                retrieval_reason=item.retrieval_reason,
                confidence=item.confidence,
                metadata=item.metadata,
            ))
        return reranked + results[max(plan.rerank_top_k, plan.max_results):]

    def _vector_search(self, plan: RetrievalPlan, remaining: int) -> List[RetrievalResult]:
        lm = self.llm_manager
        if not lm or not getattr(lm, "ai_enabled", False) or not hasattr(lm, "query_by_raw_embedding"):
            return []
        try:
            embedding = lm.get_embedding(plan.query)
            raw = lm.query_by_raw_embedding(
                embedding,
                n_results=max(1, remaining),
                allowed_docs=plan.allowed_source_ids,
            )
        except Exception:
            return []
        if not raw:
            return []
        ids = (raw.get("ids") or [[]])[0]
        docs = (raw.get("documents") or [[]])[0]
        metas = (raw.get("metadatas") or [[]])[0]
        distances = (raw.get("distances") or [[]])[0]
        results = []
        for index, doc_text in enumerate(docs):
            meta = metas[index] if index < len(metas) and isinstance(metas[index], dict) else {}
            distance = float(distances[index]) if index < len(distances) else 1.0
            score = 1.0 / (1.0 + max(distance, 0.0))
            artifact_id = meta.get("artifact_id") or (ids[index] if index < len(ids) else "")
            results.append(RetrievalResult(
                artifact_id=str(artifact_id),
                artifact_type=meta.get("artifact_type", "chunk"),
                source_id=meta.get("source_id"),
                source_title=meta.get("doc_name", ""),
                locator=meta.get("source_locator") or {"page": meta.get("page"), "start": meta.get("start"), "end": meta.get("end")},
                section_path=_section_path(meta.get("section_path")),
                exact_text=doc_text,
                score=score,
                score_breakdown={"embedding": score},
                retrieval_reason="Vector fallback",
                metadata=meta,
            ))
        return results

    def _source_title(self, source_id: Optional[str], source_path: Optional[str]) -> str:
        pm = self.project_manager
        if pm and source_id:
            for source in pm.list_sources(include_removed=True):
                if source.get("id") == source_id:
                    return source.get("title") or source.get("path") or ""
        return source_path or ""


def _section_path(value: Any) -> List[str]:
    if isinstance(value, list):
        return [str(item) for item in value]
    if isinstance(value, str) and value:
        return [part.strip() for part in value.split(">") if part.strip()]
    return []


def _dedupe_results(items: List[RetrievalResult]) -> List[RetrievalResult]:
    seen = set()
    result = []
    for item in items:
        key = item.artifact_id or item.exact_text[:80]
        if key in seen:
            continue
        seen.add(key)
        result.append(item)
    return result


def _infer_intent(query: str) -> str:
    q = str(query or "").lower()
    if any(token in q for token in ("highlight", "note", "annotation", "annotated")):
        return "annotation"
    if any(token in q for token in ("citation", "cites", "bibliography", "reference")):
        return "citation"
    if any(token in q for token in ("who", "person", "organization", "entity", "mentions")):
        return "entity"
    if any(token in q for token in ("when", "date", "timeline", "year")):
        return "date"
    if any(token in q for token in ("table", "spreadsheet", "row", "column")):
        return "table"
    if any(token in q for token in ("transcript", "timestamp", "video", "audio")):
        return "transcript"
    return "general"


def _type_boost(artifact_type: str, plan: RetrievalPlan) -> float:
    if artifact_type in (plan.preferred_artifact_types or []):
        return 1.25
    if artifact_type in {"annotation", "citation_entry", "entity"}:
        return 1.0
    if artifact_type in {"date_mention", "table", "transcript_segment"}:
        return 0.85
    if artifact_type in {"block", "chunk"}:
        return 0.65
    return 0.55
