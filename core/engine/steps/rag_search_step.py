"""
core/engine/steps/rag_search_step.py

Migrated from MasterActionRunner._run_rag(), _normalize_filter_list(), _build_rag_where_clause().
"""
from __future__ import annotations

import json
import re

from core.engine.steps.base_step import BaseStep
from core.engine.ui_payloads import build_ui_payloads
from core.models.intelligence_models import RetrievalPlan
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
        # Extraction-derived filters (Phase 7) — only apply when chunks have enriched metadata
        "filter_has_citation": {"type": "boolean", "label": "Only blocks with citations"},
        "filter_has_table": {"type": "boolean", "label": "Only blocks co-located with tables"},
        "filter_block_types": {"type": "array", "label": "Allowed block types (paragraph, heading, etc.)"},
        "filter_section_types": {"type": "array", "label": "Alias for filter_block_types"},
    }

    # Max total context characters sent to the LLM — keeps prompts lean but informative
    MAX_CONTEXT_CHARS = 12_000

    def execute(self, context: StepContext, inputs: dict):
        lm = context.llm_manager
        ui_format = inputs.get("_ui_format", "silent")

        if context.state.get("autopilot_disable_rag", False):
            fallback = "[]" if ui_format == "chat_widgets" else "Context skipped by Auto-Pilot."
            return self.build_result(fallback)
        queries_input = self._parse_queries(inputs.get("queries", []))
        hybrid = getattr(context, "hybrid_retrieval_service", None)
        if hybrid and queries_input:
            allowed_source_ids = self._normalize_filter_list(inputs.get("allowed_source_ids"))
            allowed_docs_for_hybrid = self._normalize_filter_list(inputs.get("allowed_docs"))
            if allowed_docs_for_hybrid and not allowed_source_ids:
                allowed_source_ids = allowed_docs_for_hybrid
            n_res = int(inputs.get("n_results") or 5)
            hybrid_results = []
            for q in queries_input:
                if not q.strip() or context.is_aborted():
                    continue
                if hasattr(hybrid, "build_plan"):
                    plan = hybrid.build_plan(
                        q,
                        allowed_source_ids=allowed_source_ids,
                        max_results=n_res,
                    )
                else:
                    plan = RetrievalPlan(
                        query=q,
                        allowed_source_ids=allowed_source_ids,
                        max_results=n_res,
                    )
                hybrid_results.extend(hybrid.retrieve(plan))
            if hybrid_results:
                hybrid_results = sorted(hybrid_results, key=lambda item: item.score, reverse=True)[:20]
                if ui_format in ["chat_widgets", "results_dialog"]:
                    matches = [
                        {
                            "artifact_id": item.artifact_id,
                            "artifact_type": item.artifact_type,
                            "doc_name": item.source_title,
                            "source_id": item.source_id or "",
                            "source_locator": item.locator,
                            "section_path": item.section_path,
                            "text": item.exact_text[:450] + "..." if len(item.exact_text) > 450 else item.exact_text,
                            "score": item.score,
                            "reason": item.retrieval_reason,
                        }
                        for item in hybrid_results
                    ]
                    raw_value = json.dumps(matches)
                    if ui_format == "results_dialog":
                        return self.build_result(raw_value, ui_payloads=[{
                            "type": "results_dialog",
                            "title": inputs.get("_ui_title", "Search Results"),
                            "items": matches,
                        }])
                    payloads = build_ui_payloads(ui_format, raw_value, step=None, trace_id=context.trace_id)
                    return self.build_result(raw_value, ui_payloads=payloads)
                packer = getattr(context, "context_packing_service", None)
                if packer:
                    return self.build_result(
                        packer.pack(
                            " | ".join(queries_input),
                            hybrid_results,
                            max_chars=self._context_budget_chars(context),
                        ).text
                    )
        has_sources = bool(
            lm and (
                lm.has_rag_sources() if hasattr(lm, "has_rag_sources")
                else getattr(lm, "collection", None)
            )
        )
        if not lm or not getattr(lm, "ai_enabled", False) or not has_sources:
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

        # Extraction-derived filters (Phase 7)
        filter_has_citation = inputs.get("filter_has_citation")
        filter_has_table = inputs.get("filter_has_table")
        filter_block_types = self._normalize_filter_list(
            inputs.get("filter_block_types") or inputs.get("filter_section_types")
        )

        aggregated_docs = {}

        for q in queries_input:
            if not q.strip() or context.is_aborted():
                continue
            try:
                emb = lm.get_embedding(q)
                if hasattr(lm, "query_by_raw_embedding"):
                    results = lm.query_by_raw_embedding(
                        emb,
                        n_results=n_res,
                        allowed_docs=allowed_docs,
                        tag_filters=tag_filters,
                        tag_logic=tag_logic,
                    )
                else:
                    # Compatibility for lightweight third-party manager adapters.
                    where_clause = self._build_where_clause(
                        allowed_docs, tag_filters, tag_logic,
                        filter_has_citation=filter_has_citation,
                        filter_has_table=filter_has_table,
                        filter_block_types=filter_block_types,
                    )
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
                                "source_locator": meta.get("source_locator", {}),
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
                    "source_locator": d.get("source_locator", {}),
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
                    "source_locator": d.get("source_locator", {}),
                    "quote": d["text"][:400] + "..." if len(d["text"]) > 400 else d["text"],
                    "note": f"Confidence: {95 - int(45 * (d['distance'] - min_d) / (max_d - min_d))}% | "
                            f"Found via: '{d['query_used'][:30]}...'",
                }
                for d in reading_order
            ]
            raw_value = json.dumps(bubbles)
            payloads = build_ui_payloads(ui_format, raw_value, step=None, trace_id=context.trace_id)
            return self.build_result(raw_value, ui_payloads=payloads)

        context_pieces = []
        for d in reading_order:
            locator = d.get("source_locator", {})
            locator_lines = "\n".join(
                f"CITATION_LOCATOR_{key}: {str(value).replace(chr(10), ' ')}"
                for key, value in locator.items()
            ) if isinstance(locator, dict) else f"CITATION_LOCATOR_value: {locator}"
            context_pieces.append(
                f"--- DOCUMENT: {d['doc_name']} | "
                f"{'TIME ' + str(round(float(d.get('start', 0)), 1)) + 's' if d.get('source_type') == 'video' else 'PAGE ' + str(d['page'] + 1)} ---\n"
                f"CITATION_DOC_NAME: {d['doc_name']}\n"
                f"CITATION_SOURCE_TYPE: {d.get('source_type', 'pdf')}\n"
                f"CITATION_SOURCE_ID: {d.get('source_id', '')}\n"
                f"CITATION_PAGE: {d.get('page', 0)}\n"
                f"CITATION_START: {d.get('start', 0)}\n"
                f"CITATION_END: {d.get('end', 0)}\n"
                f"{locator_lines}\n"
                f"{d['text']}"
            )
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

    def _parse_queries(self, queries_input) -> list:
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
        if not isinstance(queries_input, list):
            queries_input = [queries_input]
        return [str(item) for item in queries_input if str(item).strip()]

    def _build_where_clause(
        self,
        allowed_docs: list,
        tag_filters: list,
        tag_logic: str,
        filter_has_citation=None,
        filter_has_table=None,
        filter_block_types: list = None,
    ):
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
        # Extraction-derived filters — only applied when chunks carry enriched metadata
        if filter_has_citation is True:
            conditions.append({"has_citation": True})
        if filter_has_table is True:
            conditions.append({"has_table": True})
        if filter_block_types:
            if len(filter_block_types) == 1:
                conditions.append({"block_type": filter_block_types[0]})
            else:
                conditions.append({"block_type": {"$in": filter_block_types}})
        if not conditions:
            return None
        if len(conditions) == 1:
            return conditions[0]
        return {"$and": conditions}

    def _context_budget_chars(self, context: StepContext) -> int:
        role_svc = getattr(context, "model_role_service", None)
        if not role_svc:
            return self.MAX_CONTEXT_CHARS
        try:
            model = role_svc.resolve_role("main_answer_model")
            tokens = role_svc.max_context_tokens_for_model(model, role="main_answer_model")
            return max(2500, min(self.MAX_CONTEXT_CHARS, int(tokens * 1.4)))
        except Exception:
            return self.MAX_CONTEXT_CHARS
