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
from core.services.intelligence.retrieval_audit import (
    build_retrieval_audit, candidate_key,
)


DEFAULT_OUTPUT_TOKENS = 1024     # LLM_QUERY's default num_predict
REQUEST_OVERHEAD_TOKENS = 256    # fit_text_request's overhead
PROMPT_RESERVE_BYTES = 4096      # system prompt, chat history and the question
MIN_CONTEXT_CHARS = 1500

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

        if (not context.state.get("rag_enabled", True)
                or context.state.get("autopilot_disable_rag", False)):
            fallback = "[]" if ui_format == "chat_widgets" else ""
            return self.build_result(fallback)
        queries_input = self._parse_queries(inputs.get("queries", []))
        hybrid = getattr(context, "hybrid_retrieval_service", None)
        if not hybrid:
            from core.services.intelligence.legacy_retrieval_adapter import LegacyRetrievalAdapter
            hybrid = LegacyRetrievalAdapter(lm)
        if hybrid and queries_input:
            allowed_source_ids = self._normalize_filter_list(inputs.get("allowed_source_ids"))
            allowed_docs_for_hybrid = self._normalize_filter_list(inputs.get("allowed_docs"))
            n_res = int(inputs.get("n_results") or 5)
            candidate_limit = max(20, min(40, n_res * 2))
            hybrid_results = []
            unresolved = []
            for q in queries_input:
                if not q.strip() or context.is_aborted():
                    continue
                if hasattr(hybrid, "build_plan"):
                    plan = hybrid.build_plan(
                        q,
                        allowed_source_ids=allowed_source_ids,
                        allowed_docs=allowed_docs_for_hybrid,
                        max_results=candidate_limit,
                        tag_filters=self._normalize_filter_list(inputs.get("tag_filters")),
                        tag_logic=str(inputs.get("tag_logic") or "AND"),
                        has_citation=inputs.get("filter_has_citation"),
                        has_table=inputs.get("filter_has_table"),
                        block_types=self._normalize_filter_list(inputs.get("filter_block_types") or inputs.get("filter_section_types")),
                    )
                else:
                    plan = RetrievalPlan(
                        query=q,
                        allowed_source_ids=allowed_source_ids,
                        max_results=candidate_limit,
                    )
                unresolved.extend(r for r in _unresolved_refs(plan) if r not in unresolved)
                hybrid_results.extend(hybrid.retrieve(plan))
            if hybrid_results:
                hybrid_results = sorted(
                    {
                        candidate_key(item): item for item in hybrid_results
                    }.values(),
                    key=lambda item: item.score,
                    reverse=True,
                )[:candidate_limit]
                send_pool = hybrid_results[:n_res]
                preliminary_audit = build_retrieval_audit(
                    " | ".join(queries_input),
                    hybrid_results,
                    {candidate_key(item) for item in send_pool},
                    previous=context.state.get("retrieval_context_audit"),
                )
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
                        return self.build_result(
                            raw_value,
                            ui_payloads=[{
                                "type": "results_dialog",
                                "title": inputs.get("_ui_title", "Search Results"),
                                "items": matches,
                            }],
                            state_updates={
                                "retrieval_context_audit": preliminary_audit
                            },
                        )
                    payloads = build_ui_payloads(ui_format, raw_value, step=None, trace_id=context.trace_id)
                    return self.build_result(
                        raw_value,
                        ui_payloads=payloads,
                        state_updates={
                            "retrieval_context_audit": preliminary_audit
                        },
                    )
                from core.services.intelligence.context_packing_service import ContextPackingService
                packer = getattr(context, "context_packing_service", None) or ContextPackingService()
                if packer:
                    from core.services.intelligence import context_sources
                    known = dict(context.state.get(context_sources.STATE_KEY) or {})
                    bundle = packer.pack(
                        " | ".join(queries_input),
                        send_pool,
                        max_chars=self._context_budget_chars(context),
                        first_label=context_sources.next_number(known),
                    )
                    known.update(bundle.metadata.get("sources") or {})
                    sent_keys = {candidate_key(item) for item in bundle.items}
                    audit = build_retrieval_audit(
                        " | ".join(queries_input),
                        hybrid_results,
                        sent_keys,
                        previous=context.state.get("retrieval_context_audit"),
                    )
                    return self.build_result(
                        bundle.text,
                        state_updates={"retrieval_context_audit": audit,
                                       context_sources.STATE_KEY: known},
                    )
                sent_keys = {candidate_key(item) for item in send_pool}
                audit = build_retrieval_audit(
                    " | ".join(queries_input),
                    hybrid_results,
                    sent_keys,
                    previous=context.state.get("retrieval_context_audit"),
                )
                return self.build_result(
                    "\n\n".join(item.exact_text for item in send_pool),
                    state_updates={"retrieval_context_audit": audit},
                )
            # The canonical retrieval service owns both artifact and vector
            # fallback signals. An empty scoped result must not fall through
            # into a second, independently scoped legacy query.
            if ui_format in ["chat_widgets", "results_dialog"]:
                return self.build_result("[]")
            return self.build_result(_scope_message(unresolved) if unresolved else "No relevant documents found.")
        return self.build_result("[]" if ui_format in ["chat_widgets", "results_dialog"] else "No relevant documents found.")

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

    def _context_budget_chars(self, context: StepContext) -> int:
        role_svc = getattr(context, "model_role_service", None)
        if not role_svc:
            return self.MAX_CONTEXT_CHARS
        try:
            model = role_svc.resolve_role("main_answer_model")
            tokens = role_svc.max_context_tokens_for_model(model, role="main_answer_model")
            # Same conservative rule as the final request (one byte per token, see
            # core/utils/context_budget.py), leaving room for the answer, system prompt,
            # history and question, so whole passages fit and nothing is cut later.
            room = int(tokens) - DEFAULT_OUTPUT_TOKENS - REQUEST_OVERHEAD_TOKENS - PROMPT_RESERVE_BYTES
            return max(MIN_CONTEXT_CHARS, min(self.MAX_CONTEXT_CHARS, room))
        except Exception:
            return self.MAX_CONTEXT_CHARS


MAX_REFS_SHOWN = 5


def _unresolved_refs(plan) -> list:
    scope = getattr(plan, "source_scope", None)
    refs = scope.get("unresolved_refs") if isinstance(scope, dict) else getattr(scope, "unresolved_refs", None)
    return list(refs or [])


def _scope_message(refs) -> str:
    """Tell the reader (and the model) that the filter, not the documents, stopped the search."""
    shown = "; ".join(str(ref) for ref in refs[:MAX_REFS_SHOWN]) + ("; …" if len(refs) > MAX_REFS_SHOWN else "")
    return ("No documents were searched: the document filter could not be matched to project sources "
            f"({shown}). Tell the user to re-select documents or tags in Filter Context.")

