"""
core/engine/steps/detect_patterns_step.py

Deterministic, no-LLM step: runs the regex pattern packs registered in
PatternDetectorRegistry directly over supplied text or a document's blocks.
Exposes the registry's built-in extractors (causal/argument markers, dates,
citations, legal citations, statistical markers, etc.) as a standalone,
general-purpose primitive any blueprint or plugin tool can pick between.
"""
from __future__ import annotations

from typing import Any, Dict, List

from core.engine.steps.base_step import BaseStep
from core.plugins.plugin_step_protocol import StepContext


class DetectPatternsStep(BaseStep):
    step_type = "DETECT_PATTERNS"
    label = "Detect Patterns"
    category = "Extraction"
    description = (
        "Run registered regex pattern packs (causal/argument markers, dates, "
        "citations, statistical markers, etc.) over raw text or a document's blocks."
    )
    input_schema = {
        "entity_type": {"type": "string", "label": "Pattern entity type (e.g. 'causal_marker', 'date', 'citation')", "required": True},
        "text": {"type": "textarea", "label": "Raw text to scan (optional — used instead of doc_path)"},
        "doc_path": {"type": "string", "label": "Document Path (optional — scans all blocks if text not given)"},
    }
    output_schema = {
        "matches": {"type": "json", "label": "Pattern matches"},
    }

    def execute(self, context: StepContext, inputs: dict):
        entity_type = inputs.get("entity_type")
        registry = context.pattern_detector_registry
        if not entity_type or registry is None:
            return self.build_result([])

        packs = registry.get_packs_for_type(entity_type)
        if not packs:
            return self.build_result([])

        text = inputs.get("text")
        if text:
            return self.build_result(_scan_text(packs, text))

        doc_path = inputs.get("doc_path") or context.state.get("target_doc")
        svc = context.document_extraction_service
        if not doc_path or svc is None:
            return self.build_result([])

        matches: List[Dict[str, Any]] = []
        for block in svc.get_blocks(doc_path):
            for match in _scan_text(packs, block.exact_text):
                match["block_id"] = block.block_id
                match["page_num"] = block.page_num
                matches.append(match)
        return self.build_result(matches)


def _scan_text(packs, text: str) -> List[Dict[str, Any]]:
    matches: List[Dict[str, Any]] = []
    for pack in packs:
        for pattern in pack.compiled:
            for m in pattern.finditer(text):
                matches.append({
                    "pack_id": pack.pack_id,
                    "match_text": m.group(0),
                    "start": m.start(),
                    "end": m.end(),
                })
    return matches
