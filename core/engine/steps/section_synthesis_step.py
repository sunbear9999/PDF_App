"""
core/engine/steps/section_synthesis_step.py

Pure data-transformation step (no LLM call).  Groups per-chunk compact
evidence refs into sections of N chunks, formatting each section's refs
as a compact text block suitable for injection into the analysis_section_
synthesis_query_prompt template.

The actual LLM synthesis call is made by the blueprint's FOREACH loop
over the output section_groups list so every LLM call is routed through
MasterActionRunner and all prompts remain editable in PromptManager.

Output (raw_value): section_groups list, each item:
    {
        "section_id":         "section_0",
        "section_index":      0,           # 0-based
        "compact_refs_text":  "...",        # for {item.compact_refs_text} injection
        "chunk_range":        "0-4",
    }

State updates:
    top_quote_ids_compact  — importance-ranked quote_id+snippet for graph planner
"""
from __future__ import annotations

from typing import Any, Dict, List

from core.engine.steps.base_step import BaseStep
from core.plugins.plugin_step_protocol import StepContext


class SectionGroupStep(BaseStep):
    step_type = "SECTION_GROUP"
    label = "Section Group"
    category = "Analysis"
    description = (
        "Group per-chunk compact evidence refs into fixed-size sections "
        "for subsequent per-section LLM synthesis calls."
    )
    input_schema = {
        "all_chunk_evidence": {"type": "array", "label": "List of per-chunk {chunk_id, refs} dicts"},
        "chunks_per_section": {"type": "integer", "label": "Chunks per section (default 5)"},
    }

    def execute(self, context: StepContext, inputs: dict):
        from core.utils.json_utils import extract_and_heal_json

        all_chunk_evidence = inputs.get("all_chunk_evidence") or []
        if isinstance(all_chunk_evidence, str):
            _, all_chunk_evidence = extract_and_heal_json(all_chunk_evidence)
        if not isinstance(all_chunk_evidence, list):
            all_chunk_evidence = []

        chunks_per_section = int(inputs.get("chunks_per_section") or 5)

        raw_sections = _group_into_sections(all_chunk_evidence, chunks_per_section)

        section_groups: List[Dict[str, Any]] = []
        for idx, section_chunks in enumerate(raw_sections):
            refs = _collect_refs(section_chunks)
            section_groups.append({
                "section_id": f"section_{idx}",
                "section_index": idx,
                "section_number": idx + 1,
                "total_sections": len(raw_sections),
                "compact_refs_text": _format_compact_refs(refs),
                "chunk_range": _chunk_range(section_chunks),
            })

        top_quote_ids = _top_quote_ids_text(all_chunk_evidence)

        return self.build_result(
            section_groups,
            state_updates={"top_quote_ids_compact": top_quote_ids},
        )


# ── helpers ────────────────────────────────────────────────────────────────


def _group_into_sections(
    all_chunk_evidence: List[Any], chunks_per_section: int
) -> List[List[Any]]:
    if not all_chunk_evidence:
        return []
    sections = []
    for i in range(0, len(all_chunk_evidence), chunks_per_section):
        sections.append(all_chunk_evidence[i : i + chunks_per_section])
    return sections


def _collect_refs(section_chunks: List[Any]) -> List[Dict[str, Any]]:
    refs = []
    for chunk in section_chunks:
        if isinstance(chunk, dict):
            refs.extend(chunk.get("refs") or [])
    return refs


def _format_compact_refs(refs: List[Dict[str, Any]]) -> str:
    """Compact text: '[qid] snippet | note [role imp=X.X]' — no full text."""
    lines = []
    for r in refs:
        qid = str(r.get("quote_id") or "")
        snippet = str(r.get("snippet") or "")[:100]
        note = str(r.get("note") or "")[:80]
        role = str(r.get("role") or "")
        imp = float(r.get("importance") or 0.5)
        lines.append(f"[{qid}] {snippet} | {note} [{role} imp={imp:.1f}]")
    return "\n".join(lines) if lines else "(no evidence refs)"


def _chunk_range(section_chunks: List[Any]) -> str:
    ids = [
        c.get("chunk_id")
        for c in section_chunks
        if isinstance(c, dict) and c.get("chunk_id") is not None
    ]
    if not ids:
        return ""
    return f"{min(ids)}-{max(ids)}"


def _top_quote_ids_text(all_chunk_evidence: List[Any], top_n: int = 30) -> str:
    scored = []
    for chunk in all_chunk_evidence:
        if not isinstance(chunk, dict):
            continue
        for ref in (chunk.get("refs") or []):
            if isinstance(ref, dict) and ref.get("quote_id"):
                scored.append((
                    float(ref.get("importance") or 0.5),
                    str(ref["quote_id"]),
                    str(ref.get("snippet") or "")[:80],
                    str(ref.get("note") or "")[:60],
                ))
    scored.sort(key=lambda x: x[0], reverse=True)
    lines = [
        f"[{qid}] {snippet} — {note}"
        for _, qid, snippet, note in scored[:top_n]
    ]
    return "\n".join(lines) if lines else "(no quotes)"
