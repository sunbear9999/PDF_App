"""
core/engine/steps/section_extract_step.py

Deterministic, no-LLM step: chunks a document along real heading-derived
section boundaries (via DocumentExtractionService's cached blocks + section
tree) instead of arbitrary fixed page/char slices. Falls back to the legacy
page-based chunker (AnalysisRuntime.chunk_document / DocumentParser) when the
extraction service is unavailable, the document has no blocks, or no real
heading structure was detected (e.g. a scanned PDF).

Drop-in replacement for DOCUMENT_CHUNK in the analysis blueprint — produces
the same chunk dict shape plus section_id/section_path/section_number/
total_sections/block_ids, so downstream steps (EVIDENCE_STORE, progress UI)
can report accurate section-aware progress instead of "Section N/0".
"""
from __future__ import annotations

from typing import Any, Dict, List

from core.engine.steps.base_step import BaseStep
from core.plugins.plugin_step_protocol import StepContext


class SectionExtractStep(BaseStep):
    step_type = "SECTION_EXTRACT"
    label = "Section Extract"
    category = "Analysis"
    description = (
        "Chunk a document along real heading-derived section boundaries using "
        "DocumentExtractionService, falling back to page-based chunking when "
        "structure extraction is unavailable or no real sections are detected."
    )
    input_schema = {
        "doc_path": {"type": "string", "label": "Document Path"},
        "template": {"type": "object", "label": "Analysis Template"},
        "contract": {"type": "object", "label": "Analysis Contract"},
        "template_id": {"type": "string", "label": "Template ID"},
    }
    output_schema = {
        "chunks": {"type": "json", "label": "Section-scoped analysis chunks"},
    }

    def execute(self, context: StepContext, inputs: dict):
        from core.engine.analysis_runtime import AnalysisRuntime

        runtime = AnalysisRuntime(
            context.project_manager, context.prompt_manager, context.ontology_registry,
        )
        template = inputs.get("template") or {}
        contract = inputs.get("contract") or context.state.get("analysis_contract") or {}
        doc_path = (
            inputs.get("doc_path")
            or context.state.get("analysis_doc_path")
            or context.state.get("target_doc")
        )
        template_id = (
            inputs.get("template_id")
            or context.state.get("analysis_template_id")
            or template.get("id")
            or "analysis"
        )

        doc_ext = context.document_extraction_service
        blocks = doc_ext.get_blocks(doc_path) if doc_ext else []
        sections = doc_ext.get_sections(doc_path) if (doc_ext and blocks) else []

        if not blocks or len(sections) <= 1:
            return self._fallback(runtime, doc_path, template_id, template, contract)

        limits = contract.get("limits") or runtime.analysis_limits(template)
        prompt_contract = contract.get("prompt_contract") or runtime.build_prompt_contract(contract)
        chunks = self._build_section_chunks(
            blocks, doc_path, template_id, template.get("instructions", ""),
            prompt_contract, int(limits.get("max_chunk_chars") or 14000),
        )
        if not chunks:
            return self._fallback(runtime, doc_path, template_id, template, contract)

        total_sections = max((c["total_sections"] for c in chunks), default=0)
        return self.build_result(
            chunks,
            state_updates={
                "analysis_total_chunks": len(chunks),
                "analysis_total_sections": total_sections,
            },
        )

    def _fallback(self, runtime, doc_path, template_id, template, contract):
        chunks = runtime.chunk_document(doc_path, template_id, template, contract)
        if not chunks:
            raise ValueError("Could not parse document into analysis chunks.")
        for i, c in enumerate(chunks):
            c.setdefault("section_id", None)
            c.setdefault("section_path", [])
            c["section_number"] = i + 1
            c["total_sections"] = len(chunks)
            c.setdefault("block_ids", [])
        return self.build_result(
            chunks,
            state_updates={
                "analysis_total_chunks": len(chunks),
                "analysis_total_sections": len(chunks),
            },
        )

    @staticmethod
    def _build_section_chunks(
        blocks, doc_path: str, template_id: str, template_instructions: str,
        template_schema: str, max_chars: int,
    ) -> List[Dict[str, Any]]:
        # Group by TOP-LEVEL heading only — a deeply-nested document (many
        # subheadings) would otherwise produce one chunk per subsection, wildly
        # over-fragmenting the analysis into far more LLM calls than the section
        # count actually warrants.
        groups: List[List[Any]] = []
        index_by_key: Dict[tuple, int] = {}
        for b in blocks:
            key = tuple((b.section_path or [])[:1])
            if key not in index_by_key:
                index_by_key[key] = len(groups)
                groups.append([key, []])
            groups[index_by_key[key]][1].append(b)

        total_sections = len(groups)
        chunks: List[Dict[str, Any]] = []
        pending_blocks: List[Any] = []
        pending_key = None
        pending_section_number = None

        def flush() -> None:
            nonlocal pending_blocks, pending_key, pending_section_number
            if not pending_blocks:
                return
            section_id = "::".join(pending_key) if pending_key else f"section_{pending_section_number}"
            pages = [b.page_num for b in pending_blocks if b.page_num is not None]
            chunks.append({
                "doc_path": doc_path,
                "template_id": template_id,
                "template_instructions": template_instructions,
                "template_schema": template_schema,
                "chunk_index": len(chunks),
                "section_id": section_id,
                "section_path": list(pending_key or []),
                "section_number": pending_section_number,
                "total_sections": total_sections,
                "page_range": f"{min(pages)}-{max(pages)}" if pages else "",
                "text": "\n\n".join(b.exact_text for b in pending_blocks),
                "block_ids": [b.block_id for b in pending_blocks],
            })
            pending_blocks = []
            pending_key = None
            pending_section_number = None

        for section_number, (key, group_blocks) in enumerate(groups, start=1):
            group_len = sum(len(b.exact_text or "") for b in group_blocks)

            if group_len > max_chars:
                # This single section alone exceeds the budget — flush whatever
                # was pending, then sub-split just this section on its own.
                flush()
                for sub in SectionExtractStep._split_by_chars(group_blocks, max_chars):
                    pending_blocks, pending_key, pending_section_number = sub, key, section_number
                    flush()
                continue

            pending_len = sum(len(b.exact_text or "") for b in pending_blocks)
            if pending_blocks and pending_len + group_len > max_chars:
                flush()

            if not pending_blocks:
                pending_key = key
                pending_section_number = section_number
            pending_blocks.extend(group_blocks)

        flush()
        return chunks

    @staticmethod
    def _split_by_chars(blocks, max_chars: int) -> List[List[Any]]:
        groups: List[List[Any]] = []
        current: List[Any] = []
        current_len = 0
        for b in blocks:
            text_len = len(b.exact_text or "")
            if current and current_len + text_len > max_chars:
                groups.append(current)
                current = []
                current_len = 0
            current.append(b)
            current_len += text_len
        if current:
            groups.append(current)
        return groups
