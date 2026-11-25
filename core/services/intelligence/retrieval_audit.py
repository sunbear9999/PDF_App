"""Serializable provenance for the context considered by an LLM workflow.

Shared retrieval steps write this contract into workflow state. LLM_QUERY
turns it into a response-scoped view after checking which raw candidate text
actually survived prompt packing/truncation.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, is_dataclass
from typing import Any, Iterable


def candidate_key(item: Any) -> str:
    data = _as_dict(item)
    artifact_id = str(data.get("artifact_id") or "")
    if artifact_id:
        return artifact_id
    raw = "|".join([
        str(data.get("source_id") or ""),
        json.dumps(data.get("locator") or data.get("source_locator") or {}, sort_keys=True),
        str(data.get("exact_text") or data.get("text") or ""),
    ])
    return "context:" + hashlib.sha1(raw.encode("utf-8")).hexdigest()


def build_retrieval_audit(
    query: str,
    ranked_candidates: Iterable[Any],
    sent_keys: set[str],
    *,
    previous: Any = None,
    max_candidates: int = 20,
) -> dict:
    """Merge a ranked retrieval pass into the workflow-wide context audit."""
    merged: dict[str, dict] = {}
    previous_queries = []
    if isinstance(previous, dict):
        previous_queries = list(previous.get("queries") or [])
        for candidate in previous.get("candidates") or []:
            if isinstance(candidate, dict):
                merged[str(candidate.get("candidate_id") or candidate_key(candidate))] = dict(candidate)

    for item in ranked_candidates:
        data = _as_dict(item)
        key = candidate_key(data)
        text = str(data.get("exact_text") or data.get("text") or "")
        locator = data.get("locator") or data.get("source_locator") or {
            locator_key: data.get(locator_key)
            for locator_key in (
                "page", "start", "end", "char_start", "char_end",
            )
            if data.get(locator_key) is not None
        }
        existing = merged.get(key, {})
        score = _float(data.get("score"), existing.get("score", 0.0))
        candidate = {
            "candidate_id": key,
            "artifact_id": str(data.get("artifact_id") or ""),
            "artifact_type": str(data.get("artifact_type") or "chunk"),
            "revision": data.get("revision") or (data.get("metadata") or {}).get("revision"),
            "source_id": str(data.get("source_id") or ""),
            "source_type": str(
                data.get("source_type")
                or (data.get("metadata") or {}).get("source_type")
                or "pdf"
            ),
            "doc_name": str(
                data.get("source_title") or data.get("doc_name") or ""
            ),
            "source_locator": _json_safe(locator),
            "section_path": list(data.get("section_path") or []),
            "text": text,
            "score": max(score, _float(existing.get("score"), 0.0)),
            "score_breakdown": _json_safe(data.get("score_breakdown") or {}),
            "reason": str(
                data.get("retrieval_reason")
                or data.get("reason")
                or data.get("query_used")
                or ""
            ),
            "received_by_model": bool(
                key in sent_keys or existing.get("received_by_model")
            ),
            "selected_quotes": list(existing.get("selected_quotes") or []),
        }
        merged[key] = candidate

    candidates = sorted(
        merged.values(),
        key=lambda value: (
            not bool(value.get("received_by_model")),
            -_float(value.get("score"), 0.0),
            str(value.get("doc_name") or "").casefold(),
        ),
    )
    sent_count = sum(bool(value.get("received_by_model")) for value in candidates)
    limit = max(max_candidates, sent_count)
    candidates = candidates[:limit]
    for rank, candidate in enumerate(candidates, 1):
        candidate["rank"] = rank
        candidate["exclusion_reason"] = (
            "" if candidate.get("received_by_model")
            else "Ranked below the context budget or packing limit"
        )
    queries = previous_queries + ([query] if query and query not in previous_queries else [])
    return {
        "version": 1,
        "queries": queries,
        "candidates": candidates,
        "sent_count": sum(bool(value.get("received_by_model")) for value in candidates),
        "candidate_count": len(candidates),
    }


def audit_for_prompt(audit: Any, prompt_text: str) -> dict:
    """Return a response-scoped copy marking text actually present in the prompt."""
    if not isinstance(audit, dict):
        return {}
    prompt_norm = _normalize(prompt_text)
    result = _json_safe(audit)
    candidates = result.get("candidates") or []
    for candidate in candidates:
        text_norm = _normalize(candidate.get("text") or "")
        # Matching the beginning of a raw chunk also detects a chunk that was
        # partially retained by final prompt truncation.
        needle = text_norm[:160]
        candidate["received_by_model"] = bool(
            needle and len(needle) >= 20 and needle in prompt_norm
        )
        candidate["exclusion_reason"] = (
            "" if candidate["received_by_model"]
            else "Not present in the final prompt sent to the model"
        )
    candidates.sort(key=lambda value: (
        not bool(value.get("received_by_model")),
        int(value.get("rank") or 10**9),
    ))
    for rank, candidate in enumerate(candidates, 1):
        candidate["rank"] = rank
    result["sent_count"] = sum(
        bool(value.get("received_by_model")) for value in candidates
    )
    result["candidate_count"] = len(candidates)
    return result


def apply_selected_quotes(audit: Any, citations: Iterable[dict]) -> dict:
    result = _json_safe(audit) if isinstance(audit, dict) else {}
    candidates = result.get("candidates") or []
    for citation in citations or []:
        if not isinstance(citation, dict):
            continue
        quote = str(citation.get("quote") or "").strip()
        if not quote:
            continue
        artifact_id = str(citation.get("artifact_id") or "")
        source_id = str(citation.get("source_id") or "")
        doc_name = str(citation.get("doc_name") or "").casefold()
        quote_norm = _normalize(quote)
        best = None
        for candidate in candidates:
            candidate_text = _normalize(candidate.get("text") or "")
            if artifact_id and artifact_id == candidate.get("artifact_id"):
                best = candidate
                break
            if quote_norm and quote_norm in candidate_text:
                best = candidate
                if (
                    (source_id and source_id == candidate.get("source_id"))
                    or (doc_name and doc_name == str(candidate.get("doc_name") or "").casefold())
                ):
                    break
        if best is not None and quote not in best.setdefault("selected_quotes", []):
            best["selected_quotes"].append(quote)
    return result


def _as_dict(value: Any) -> dict:
    if is_dataclass(value):
        return asdict(value)
    return dict(value) if isinstance(value, dict) else {"text": str(value or "")}


def _json_safe(value: Any):
    try:
        return json.loads(json.dumps(value, ensure_ascii=False, default=str))
    except Exception:
        return {}


def _float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return float(default)


def _normalize(value: Any) -> str:
    return " ".join(str(value or "").casefold().split())
