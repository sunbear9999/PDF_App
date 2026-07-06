"""
core/engine/steps/rank_blocks_step.py

Deterministic, no-LLM step: runs named DeterministicPassRegistry passes over
a document's blocks (optionally scoped to a subset via block_ids) and joins
the highest-scoring, non-zero candidates into a compact text blob. General-
purpose primitive — any blueprint can compose this ahead of an LLM step to
shrink and pre-filter context before generation.

If no passes are configured, or too few candidate blocks survive ranking,
this step passes `fallback_text` through unchanged so behavior degrades to
"send the raw text" rather than silently losing information.
"""
from __future__ import annotations

from typing import Any, Dict, List, Tuple

from core.engine.steps.base_step import BaseStep
from core.plugins.plugin_step_protocol import StepContext

DEFAULT_BASE_NUM_PREDICT = 800
PER_BLOCK_TOKENS = 60
MAX_BONUS_TOKENS = 1200
MIN_CANDIDATES = 3


class RankBlocksStep(BaseStep):
    step_type = "RANK_BLOCKS"
    label = "Rank Blocks"
    category = "Extraction"
    description = (
        "Run registered deterministic ranking passes over a document's blocks "
        "and join the top-scoring candidates into compact text, with a dynamic "
        "token-budget suggestion for the LLM call that follows."
    )
    input_schema = {
        "doc_path": {"type": "string", "label": "Document Path"},
        "block_ids": {"type": "json", "label": "Block IDs to scope ranking to (optional; whole document if omitted)"},
        "passes": {"type": "json", "label": "List of {pass_id, params, top_n} resolved against DeterministicPassRegistry"},
        "fallback_text": {"type": "textarea", "label": "Text to use verbatim when no passes configured or too few candidates found"},
        "base_num_predict": {"type": "integer", "label": "Fallback token budget when passes are empty/sparse", "default": DEFAULT_BASE_NUM_PREDICT},
    }
    output_schema = {
        "compact_text": {"type": "string", "label": "Compact, pre-ranked text"},
        "suggested_evidence_num_predict": {"type": "integer", "label": "Suggested token budget for the following LLM call"},
        "candidate_count": {"type": "integer", "label": "Number of surviving ranked candidates"},
        "candidates": {"type": "json", "label": "Ranked candidates (score desc): {block_id, exact_text, score, page_num}"},
    }

    def execute(self, context: StepContext, inputs: dict):
        from core.models.analysis_models import DeterministicPassInput

        fallback_text = str(inputs.get("fallback_text") or "")
        base_num_predict = _safe_int(inputs.get("base_num_predict")) or DEFAULT_BASE_NUM_PREDICT
        passes_config = inputs.get("passes") or []
        if not isinstance(passes_config, list) or not passes_config:
            return self._passthrough(fallback_text, base_num_predict, 0)

        doc_path = inputs.get("doc_path") or context.state.get("target_doc")
        doc_ext = context.document_extraction_service
        registry = context.deterministic_pass_registry
        if not doc_path or doc_ext is None or registry is None:
            return self._passthrough(fallback_text, base_num_predict, 0)

        blocks = doc_ext.get_blocks(doc_path)
        block_ids = inputs.get("block_ids")
        if block_ids:
            allowed = set(block_ids)
            blocks = [b for b in blocks if b.block_id in allowed]
        if not blocks:
            return self._passthrough(fallback_text, base_num_predict, 0)

        citation_index = doc_ext.get_citation_index(doc_path)
        entity_list = doc_ext.get_entities(doc_path)
        date_list = doc_ext.get_dates(doc_path)
        # Table extraction is intentionally NOT fetched eagerly here: no built-in
        # pass uses table features, it triggers the heavier grid-extraction path,
        # and DocumentBlock.block_type already flags table blocks for free — the
        # only thing skipped is same-page table cross-referencing.
        table_list = None

        order_index = {b.block_id: i for i, b in enumerate(blocks)}
        best_by_block: Dict[str, Tuple[float, Any]] = {}

        for pass_cfg in passes_config:
            if not isinstance(pass_cfg, dict):
                continue
            spec = registry.get(pass_cfg.get("pass_id"))
            if spec is None:
                continue
            params = dict(pass_cfg.get("params") or {})
            if "top_n" not in params and pass_cfg.get("top_n") is not None:
                params["top_n"] = pass_cfg.get("top_n")
            pass_input = DeterministicPassInput(
                blocks=blocks,
                doc_id=doc_path,
                citation_index=citation_index,
                entity_list=entity_list,
                date_list=date_list,
                table_list=table_list,
                params=params,
            )
            try:
                output = spec.run_fn(pass_input)
            except Exception:
                continue
            for ranked in output.ranked_blocks:
                if ranked.score <= 0 or ranked.block is None:
                    continue
                existing = best_by_block.get(ranked.block_id)
                if existing is None or ranked.score > existing[0]:
                    best_by_block[ranked.block_id] = (ranked.score, ranked.block)

        scored: List[Tuple[float, Any]] = list(best_by_block.values())
        candidate_count = len(scored)

        if candidate_count < MIN_CANDIDATES:
            return self._passthrough(fallback_text, base_num_predict, candidate_count)

        score_desc = sorted(scored, key=lambda pair: pair[0], reverse=True)
        candidates = [
            {"block_id": block.block_id, "exact_text": block.exact_text, "score": score, "page_num": block.page_num}
            for score, block in score_desc
        ]

        doc_order = sorted(scored, key=lambda pair: order_index.get(pair[1].block_id, 0))
        compact_text = "\n\n".join(block.exact_text for _, block in doc_order)
        suggested_num_predict = min(
            base_num_predict + PER_BLOCK_TOKENS * candidate_count,
            base_num_predict + MAX_BONUS_TOKENS,
        )
        return self.build_result({
            "compact_text": compact_text,
            "suggested_evidence_num_predict": suggested_num_predict,
            "candidate_count": candidate_count,
            "candidates": candidates,
        })

    def _passthrough(self, fallback_text: str, base_num_predict: int, candidate_count: int):
        return self.build_result({
            "compact_text": fallback_text,
            "suggested_evidence_num_predict": base_num_predict,
            "candidate_count": candidate_count,
            "candidates": [],
        })


def _safe_int(val):
    try:
        return int(val)
    except (TypeError, ValueError):
        return None
