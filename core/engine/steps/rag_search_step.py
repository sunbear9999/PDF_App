"""
core/engine/steps/rag_search_step.py

Migrated from MasterActionRunner._run_rag(), _normalize_filter_list(), _build_rag_where_clause().
"""
from __future__ import annotations

import json
import re

from core.engine.steps.base_step import BaseStep
from core.engine.ui_payloads import build_ui_payloads
from core.plugins.plugin_step_protocol import StepContext


class RagSearchStep(BaseStep):
    step_type = "RAG_SEARCH"
    label = "RAG Search"
    category = "Search"
    description = "Search indexed documents and return context."
    input_schema = {
        "queries": {"type": "array", "label": "Search Queries", "required": True},
        "allowed_docs": {"type": "array", "label": "Allowed Documents"},
        "tag_filters": {"type": "array", "label": "Tag Filters"},
        "tag_logic": {"type": "string", "label": "Tag Logic (AND/OR)"},
        "n_results": {"type": "integer", "label": "Results Per Query"},
    }

    # Max total context characters sent to the LLM — keeps prompts lean but informative
    MAX_CONTEXT_CHARS = 12_000

    def execute(self, context: StepContext, inputs: dict):
        lm = context.llm_manager
        ui_format = inputs.get("_ui_format", "silent")

        if context.state.get("autopilot_disable_rag", False):
            fallback = "[]" if ui_format == "chat_widgets" else "Context skipped by Auto-Pilot."
            return self.build_result(fallback)
        if not lm or not getattr(lm, "ai_enabled", False) or not getattr(lm, "collection", None):
            fallback = "[]" if ui_format == "chat_widgets" else "RAG is offline or collection is empty."
            return self.build_result(fallback)

        queries_input = inputs.get("queries", [])
        if isinstance(queries_input, str):
            try:
                match = re.search(r"\{.*\}|\[.*\]", queries_input, re.DOTALL)
                parsed = json.loads(match.group(0)) if match else json.loads(queries_input)
                if isinstance(parsed, dict):
                    for v in parsed.values():
                        if isinstance(v, list):
                            parsed = v
                            break
                queries_input = parsed if isinstance(parsed, list) else [str(parsed)]
            except Exception:
                lines = [re.sub(r"^\d+\.\s*|^-\s*", "", l).strip() for l in queries_input.split("\n")]
                queries_input = [l for l in lines if len(l) > 5]

        allowed_docs = self._normalize_filter_list(inputs.get("allowed_docs"))
        tag_filters = self._normalize_filter_list(inputs.get("tag_filters"))
        tag_logic = inputs.get("tag_logic", "AND")
        # n_results: how many chunks per query to retrieve — default 5 for good coverage
        n_res = int(inputs.get("n_results") or 5)
        aggregated_docs = {}

        for q in queries_input:
            if not q.strip() or context.is_aborted():
                continue
            try:
                emb = lm.get_embedding(q)
                where_clause = self._build_where_clause(allowed_docs, tag_filters, tag_logic)
                results = lm.collection.query(
                    query_embeddings=[emb],
                    n_results=n_res,
                    where=where_clause,
                    include=["documents", "metadatas", "distances"],
                )
                if results.get("documents") and results["documents"][0]:
                    for idx, doc_text in enumerate(results["documents"][0]):
                        doc_id_val = results["ids"][0][idx]
                        if doc_id_val not in aggregated_docs:
                            meta = results["metadatas"][0][idx] or {}
                            aggregated_docs[doc_id_val] = {
                                "text": doc_text,
                                "doc_name": meta.get("doc_name", ""),
                                "page": meta.get("page", 0),
                                "source_type": meta.get("source_type", "pdf"),
                                "source_id": meta.get("source_id", ""),
                                "start": meta.get("start", 0),
                                "end": meta.get("end", 0),
                                "distance": results["distances"][0][idx],
                                "query_used": q,
                            }
            except Exception:
                continue

        if not aggregated_docs:
            fallback = "[]" if ui_format in ["chat_widgets", "results_dialog"] else "No relevant documents found."
            return self.build_result(fallback)

        best_chunks = sorted(aggregated_docs.values(), key=lambda x: x["distance"])
        # Allow up to 6 chunks per query, capped at 20 total
        max_chunks = min(20, max(len(queries_input) * 6, n_res))
        top_chunks = best_chunks[:max_chunks]
        reading_order = sorted(top_chunks, key=lambda x: (x["doc_name"], x["page"]))

        if ui_format == "results_dialog":
            matches = [
                {
                    "doc_name": d["doc_name"],
                    "page": d["page"],
                    "source_type": d.get("source_type", "pdf"),
                    "source_id": d.get("source_id", ""),
                    "start": d.get("start", 0),
                    "end": d.get("end", 0),
                    "text": d["text"][:450] + "..." if len(d["text"]) > 450 else d["text"],
                }
                for d in reading_order
            ]
            raw_value = json.dumps(matches)
            ui_title = inputs.get("_ui_title", "Search Results")
            ui_payloads = [{"type": "results_dialog", "title": ui_title, "items": matches}]
            return self.build_result(raw_value, ui_payloads=ui_payloads)

        if ui_format == "chat_widgets":
            distances = [d["distance"] for d in reading_order]
            min_d = min(distances) if distances else 0
            max_d = max(distances) if distances else 1
            if max_d - min_d < 0.0001:
                max_d = min_d + 1
            bubbles = [
                {
                    "doc_name": d["doc_name"],
                    "source_type": d.get("source_type", "pdf"),
                    "source_id": d.get("source_id", ""),
                    "start": d.get("start", 0),
                    "end": d.get("end", 0),
                    "quote": d["text"][:400] + "..." if len(d["text"]) > 400 else d["text"],
                    "note": f"Confidence: {95 - int(45 * (d['distance'] - min_d) / (max_d - min_d))}% | "
                            f"Found via: '{d['query_used'][:30]}...'",
                }
                for d in reading_order
            ]
            raw_value = json.dumps(bubbles)
            payloads = build_ui_payloads(ui_format, raw_value, step=None, trace_id=context.trace_id)
            return self.build_result(raw_value, ui_payloads=payloads)

        context_pieces = [
            f"--- DOCUMENT: {d['doc_name']} | "
            f"{'TIME ' + str(round(float(d.get('start', 0)), 1)) + 's' if d.get('source_type') == 'video' else 'PAGE ' + str(d['page'] + 1)} ---\n{d['text']}"
            for d in reading_order
        ]
        return self.build_result("\n\n".join(context_pieces))

    # ------------------------------------------------------------------
    # Helpers (migrated from runner)
    # ------------------------------------------------------------------

    def _normalize_filter_list(self, value) -> list:
        if not value:
            return []
        if isinstance(value, str):
            try:
                parsed = json.loads(value)
                value = parsed
            except Exception:
                value = [value]
        if not isinstance(value, list):
            value = [value]
        return [str(item).strip() for item in value if str(item).strip()]

    def _build_where_clause(self, allowed_docs: list, tag_filters: list, tag_logic: str):
        conditions = []
        if allowed_docs:
            if len(allowed_docs) == 1:
                conditions.append({"doc_name": allowed_docs[0]})
            else:
                conditions.append({"doc_name": {"$in": allowed_docs}})
        if tag_filters:
            tag_conditions = [{f"tag_{tag}": True} for tag in tag_filters]
            if str(tag_logic).upper() == "OR" and len(tag_conditions) > 1:
                conditions.append({"$or": tag_conditions})
            else:
                conditions.extend(tag_conditions)
        if not conditions:
            return None
        if len(conditions) == 1:
            return conditions[0]
        return {"$and": conditions}
