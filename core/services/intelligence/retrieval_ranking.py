"""Pure retrieval ranking, fusion and coverage helpers."""
from typing import Any, Dict, List
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
    "sentence",
    "argument_unit",
    "summary",
    "block",
]

INTENT_TYPE_HINTS = {
    "annotation": ["annotation", "block", "transcript_segment"],
    "citation": ["citation_entry", "in_text_citation", "block"],
    "entity": ["entity", "entity_mention", "block"],
    "date": ["date_mention", "block"],
    "table": ["table", "block"],
    "transcript": ["transcript_segment"],
    "evidence": ["argument_unit", "sentence", "transcript_segment", "table"],
    "claim": ["argument_unit", "sentence", "summary"],
    "argument": ["argument_unit", "sentence", "summary"],
}


_RRF_K = 60  # reciprocal-rank-fusion constant
_SIGNAL_WEIGHTS = {
    "structured": 1.0,
    "keyword": 1.0,
    "entity": 1.15,
    "embedding": 1.0,
    "graph": 0.7,
}
# Query-intent → sentence class_labels to prefer (free filter; classification
# already ran at ingestion).
_INTENT_CLASS_LABELS = {
    "evidence": {"evidence"},
    "claim": {"claim"},
    "argument": {"claim", "reasoning"},
}




def _with_score(item: RetrievalResult, score: float, breakdown: Dict[str, float]) -> RetrievalResult:
    """Return a copy of a (frozen) RetrievalResult with a new score/breakdown."""
    return RetrievalResult(
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
    )


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
        key = (
            (item.metadata or {}).get("canonical_evidence_id")
            or item.artifact_id or item.exact_text[:80]
        )
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
    if any(token in q for token in ("evidence", "support for", "proof", "data for", "back up")):
        return "evidence"
    if any(token in q for token in ("argument", "reasoning", "why does", "rationale")):
        return "argument"
    if any(token in q for token in ("claim", "argue", "assert", "thesis", "position")):
        return "claim"
    return "general"


def _type_boost(artifact_type: str, plan: RetrievalPlan) -> float:
    if artifact_type in (plan.preferred_artifact_types or []):
        return 1.25
    if artifact_type == "argument_unit":
        return 1.15
    if artifact_type in {"annotation", "citation_entry", "entity"}:
        return 1.0
    if artifact_type in {"sentence", "transcript_segment"}:
        return 0.9  # fine tier — precise, provenance-rich
    if artifact_type in {"date_mention", "table"}:
        return 0.85
    if artifact_type == "summary":
        return 0.8  # breadth tier
    if artifact_type in {"block", "chunk"}:
        return 0.65  # coarse breadth tier
    return 0.55


def _rrf_fuse(signal_lists: List[List[RetrievalResult]]) -> List[RetrievalResult]:
    """Reciprocal-rank fusion across heterogeneous signals."""
    agg: Dict[str, Dict[str, Any]] = {}
    for signal in signal_lists:
        for rank, item in enumerate(signal):
            key = item.artifact_id or (item.exact_text or "")[:80]
            if not key:
                continue
            entry = agg.get(key)
            signal_name = next(iter((item.score_breakdown or {"structured": 0}).keys()))
            rrf = _SIGNAL_WEIGHTS.get(signal_name, 1.0) / (_RRF_K + rank + 1)
            if entry is None:
                agg[key] = {"item": item, "score": rrf, "breakdown": dict(item.score_breakdown)}
            else:
                entry["score"] += rrf
            for sig_name, sig_score in (item.score_breakdown or {}).items():
                agg[key]["breakdown"][sig_name] = sig_score
    fused = []
    for entry in agg.values():
        item = entry["item"]
        fused.append(_with_score(item, entry["score"], {**entry["breakdown"], "rrf": entry["score"]}))
    return fused


def _apply_sentence_type_preference(plan: RetrievalPlan, results: List[RetrievalResult]) -> List[RetrievalResult]:
    desired = _INTENT_CLASS_LABELS.get(plan.query_intent)
    if not desired:
        return results
    boosted = []
    for item in results:
        label = (
            (item.metadata or {}).get("argument_role")
            or (item.metadata or {}).get("assigned_role")
            or (item.metadata or {}).get("suggested_role")
            or (item.metadata or {}).get("class_label")
        )
        if label in {"methodology", "limitation", "counterclaim"}:
            label = "claim" if label == "methodology" else "reasoning"
        if label in desired:
            boosted.append(_with_score(item, item.score * 1.3,
                                       {**item.score_breakdown, "sentence_type": 1.3}))
        else:
            boosted.append(item)
    return boosted


def _rerank(plan: RetrievalPlan, results: List[RetrievalResult], cross_encoder=None) -> List[RetrievalResult]:
    if not plan.use_reranker or plan.reranker_policy == "off":
        return results
    head = results[: max(plan.rerank_top_k, plan.max_results)]
    tail = results[max(plan.rerank_top_k, plan.max_results):]

    # Cross-encoder rerank (ONNX) when available — a classifier pass, cheap
    # on CPU; falls back to deterministic lexical overlap otherwise.
    if cross_encoder is not None and getattr(cross_encoder, "is_available", lambda: False)():
        pairs = [((item.exact_text or ""), item) for item in head]
        reranked = cross_encoder.rerank(plan.query, pairs, top_k=len(pairs))
        if reranked:
            out = []
            for item, ce_score in reranked:
                out.append(_with_score(item, item.score + float(ce_score),
                                       {**item.score_breakdown, "cross_encoder": float(ce_score)}))
            return out + tail

    query_terms = {term.lower() for term in plan.query.split() if len(term) > 2}
    reranked = []
    for item in head:
        text = (item.exact_text or "").lower()
        lexical = sum(1 for term in query_terms if term in text) / max(1, len(query_terms))
        reranked.append(_with_score(item, item.score + 0.25 * lexical,
                                    {**item.score_breakdown, "lexical_rerank": lexical}))
    return reranked + tail


def _apply_coverage(
    plan: RetrievalPlan, results: List[RetrievalResult]
) -> List[RetrievalResult]:
    if not results or not (
        plan.coverage_strategy == "section_diverse"
        or plan.per_section_limit or plan.min_sections
    ):
        return results
    limit = max(1, int(plan.per_section_limit or 3))
    groups = {}
    for item in results:
        key = (item.source_id, tuple(item.section_path or []))
        groups.setdefault(key, []).append(item)
    target = min(plan.max_results, plan.min_sections or (
        plan.max_results if plan.coverage_strategy == "section_diverse" else 0))
    selected = [items[0] for items in list(groups.values())[:target]]
    selected_ids = {item.artifact_id for item in selected}
    counts = {(item.source_id, tuple(item.section_path or [])): 1 for item in selected}
    deferred = []
    for item in results:
        if item.artifact_id in selected_ids:
            continue
        key = (item.source_id, tuple(item.section_path or []))
        if counts.get(key, 0) < limit:
            selected.append(item)
            counts[key] = counts.get(key, 0) + 1
        else:
            deferred.append(item)
    return [*selected, *deferred]


def _apply_review_preference(results: List[RetrievalResult]) -> List[RetrievalResult]:
    adjusted = []
    for item in results:
        state = (item.metadata or {}).get("review_state")
        factor = 1.15 if state in {"verified", "corrected"} else 1.0
        adjusted.append(_with_score(
            item,
            item.score * factor,
            {**item.score_breakdown, "review_boost": factor},
        ))
    return adjusted
