"""
core/engine/steps/notes_read_step.py

Reads notes from the project notes database and returns them as a JSON array.
Useful for building workflows that synthesize, tag, or export the user's notes.
"""
from __future__ import annotations

import json

from core.engine.steps.base_step import BaseStep
from core.plugins.plugin_step_protocol import StepContext


class NotesReadStep(BaseStep):
    step_type = "NOTES_READ"
    label = "Read Notes"
    category = "Data"
    description = "Read notes from the project database and return as a JSON list."
    input_schema = {
        "limit": {"type": "integer", "label": "Max notes to return (default 50)"},
        "filter_tag": {"type": "string", "label": "Filter by tag name (optional)"},
        "doc_path": {"type": "string", "label": "Filter by source document path (optional)"},
    }

    def execute(self, context: StepContext, inputs: dict):
        pm = context.project_manager
        if not pm:
            return self.build_result("[]")

        limit = int(inputs.get("limit") or 50)
        filter_tag = str(inputs.get("filter_tag") or "").strip()
        doc_path = str(inputs.get("doc_path") or "").strip()

        try:
            db = getattr(pm, "db_docs", None)
            if not db:
                return self.build_result("[]")

            # Try service-level get_notes first
            notes_svc = getattr(pm, "notes_service", None)
            if notes_svc and hasattr(notes_svc, "get_all_notes"):
                notes = notes_svc.get_all_notes(limit=limit, tag=filter_tag or None, doc_path=doc_path or None)
                return self.build_result(json.dumps(notes))

            # Fallback: direct DB query
            if hasattr(db, "get_notes"):
                notes = db.get_notes(limit=limit) or []
                if filter_tag:
                    notes = [n for n in notes if filter_tag in (n.get("tags") or [])]
                if doc_path:
                    notes = [n for n in notes if (n.get("doc_path") or "") == doc_path]
                return self.build_result(json.dumps(notes[:limit]))
        except Exception as e:
            return self.build_result(f"Error reading notes: {e}")

        return self.build_result("[]")
