"""Vector signal adapter with model/ledger identity validation."""
from typing import List
from core.models.intelligence_models import RetrievalPlan, RetrievalResult
from core.services.intelligence.retrieval_ranking import _section_path
from core.services.intelligence.embedding_identity import embedding_input_hash

def _embedding_model_name(llm):
    value = getattr(llm, "_embedding_model_name", None)
    return value() if callable(value) else value or getattr(llm, "embedding_model", "")

def _vector_search(llm, db, source_ids, vector_filters, plan: RetrievalPlan, remaining: int) -> List[RetrievalResult]:
    lm = llm
    if not lm or not getattr(lm, "ai_enabled", False) or not hasattr(lm, "query_by_raw_embedding"):
        return []
    if source_ids and not vector_filters:
        # A constrained query must never silently become project-wide just
        # because the legacy vector adapter could not resolve its doc_name.
        return []
    try:
        embedding = lm.get_embedding(plan.query)
        raw = lm.query_by_raw_embedding(
            embedding,
            n_results=max(1, remaining),
            allowed_docs=vector_filters,
            tag_filters=plan.tag_filters, tag_logic=plan.tag_logic,
            allowed_source_ids=source_ids,
            has_citation=plan.has_citation, has_table=plan.has_table, block_types=plan.block_types,
        )
    except Exception:
        return []
    if not raw:
        return []
    return _vector_results(llm, db, plan, raw)

def _vector_results(llm, db, plan, raw):
    ids = (raw.get("ids") or [[]])[0]
    docs = (raw.get("documents") or [[]])[0]
    metas = (raw.get("metadatas") or [[]])[0]
    distances = (raw.get("distances") or [[]])[0]
    results = []
    for index, doc_text in enumerate(docs):
        meta = metas[index] if index < len(metas) and isinstance(metas[index], dict) else {}
        active_model = _embedding_model_name(llm)
        if active_model and meta.get("model_name") and meta["model_name"] != active_model:
            continue
        distance = float(distances[index]) if index < len(distances) else 1.0
        score = 1.0 / (1.0 + max(distance, 0.0))
        artifact_id = meta.get("artifact_id") or (ids[index] if index < len(ids) else "")
        if db:
            ledger = db.get_artifact_embedding(str(artifact_id))
            if ledger and (ledger.get("status") != "complete" or (active_model and ledger.get("model_name") != active_model)):
                continue
            if ledger:
                from core.services.intelligence.embedding_identity import embedding_input_hash
                row = db.get_artifact(str(artifact_id))
                if not row or ledger.get("input_hash") != embedding_input_hash(ledger.get("model_name"), row):
                    continue
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
            metadata={**meta, "stale": False, "review_state": meta.get("review_state", "unreviewed")},
        ))
    return results
