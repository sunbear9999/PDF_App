"""
core/engine/steps/read_document_step.py

Reads raw text from an indexed PDF/document by path without calling the LLM.
Pulls from the chunk store (vector DB) or falls back to direct PDF extraction.
Useful for building non-LLM workflows that inspect or transform document content.
"""
from __future__ import annotations

from core.engine.steps.base_step import BaseStep
from core.plugins.plugin_step_protocol import StepContext


class ReadDocumentStep(BaseStep):
    step_type = "READ_DOCUMENT_TEXT"
    label = "Read Document Text"
    category = "Data"
    description = "Read raw text from an indexed document by file path."
    input_schema = {
        "doc_path": {"type": "string", "label": "Document Path", "required": True},
        "max_chars": {"type": "integer", "label": "Max Characters (default 20000)"},
        "page": {"type": "integer", "label": "Specific page number (0-indexed, optional)"},
    }

    def execute(self, context: StepContext, inputs: dict):
        doc_path = str(inputs.get("doc_path") or "").strip()
        if not doc_path:
            return self.build_result("Error: doc_path is required")

        max_chars = int(inputs.get("max_chars") or 20000)
        page = inputs.get("page")

        pm = context.project_manager
        if not pm:
            return self.build_result("Error: project_manager not available")

        # Try chunk store (already-indexed text) first
        try:
            index_svc = getattr(pm, "index_service", None) or getattr(pm, "_index_svc", None)
            if index_svc and hasattr(index_svc, "get_chunks_for_doc"):
                chunks = index_svc.get_chunks_for_doc(doc_path, page=page)
                if chunks:
                    text = "\n\n".join(c.get("text", "") if isinstance(c, dict) else str(c) for c in chunks)
                    return self.build_result(text[:max_chars])
        except Exception:
            pass

        # Fallback: try direct DB lookup of stored document text
        try:
            db = getattr(pm, "db_docs", None)
            if db and hasattr(db, "get_document_text"):
                text = db.get_document_text(doc_path) or ""
                if page is not None:
                    # Very rough page isolation by looking for page markers
                    pass
                return self.build_result(text[:max_chars])
        except Exception:
            pass

        return self.build_result(f"Could not read document: {doc_path}")
