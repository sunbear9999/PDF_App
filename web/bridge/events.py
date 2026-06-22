from __future__ import annotations

from typing import Any

from PySide6.QtCore import QObject, Slot

from .serialization import json_safe
from .state import WebState


class EventGateway(QObject):
    """Copies selected Qt domain events into the transport-neutral journal."""
    SIGNALS = {
        "project_loaded": ("project", "loaded", True),
        "project_saved": ("project", "saved", False),
        "source_added": ("sources", "added", True),
        "source_renamed": ("sources", "renamed", True),
        "source_removed": ("sources", "removed", True),
        "highlight_created": ("annotations", "created", True),
        "highlight_updated": ("annotations", "updated", True),
        "highlight_deleted": ("annotations", "deleted", True),
        "workspace_changed": ("workspaces", "changed", True),
        "workspace_saved": ("workspaces", "saved", True),
        "entity_changed": ("ontology", "entity_changed", True),
        "relation_changed": ("ontology", "relation_changed", True),
        "tag_data_updated": ("tags", "changed", True),
        "notes_data_ready": ("notes", "changed", False),
        "data_dock_state_changed": ("data", "changed", True),
        "workflow_state_changed": ("workflow", "state", False),
        "ui_render_requested": ("workflow", "render", False),
        "video_status_updated": ("video", "status", True),
        "ocr_status_updated": ("ocr", "status", True),
        "tts_status_updated": ("tts", "status", False),
        "tts_text_extracted": ("tts", "text", False),
        "citation_table_data_ready": ("citations", "table", False),
        "citation_status_updated": ("citations", "status", False),
        "source_eval_state_changed": ("evaluation", "state", True),
        "research_agent_session_changed": ("research", "session", True),
        "status_message_requested": ("system", "status", False),
    }

    def __init__(self, event_bus, state: WebState, parent=None, sanitizer=None):
        super().__init__(parent)
        self.bus = event_bus
        self.state = state
        self.sanitizer = sanitizer
        for signal_name, spec in self.SIGNALS.items():
            signal = getattr(event_bus, signal_name, None)
            if signal is not None:
                signal.connect(lambda *args, _spec=spec: self._relay(_spec, args))

    def _relay(self, spec: tuple[str, str, bool], args: tuple[Any, ...]) -> None:
        domain, operation, mutates = spec
        if domain == "project" and operation == "loaded":
            self.state.reset_project()
        payload = json_safe(list(args))
        if self.sanitizer:
            payload = self.sanitizer(payload)
        resource_id = self._resource_id(payload)
        self.state.publish(domain, operation, resource_id, payload, mutate=mutates)

    def relay_external(self, domain: str, operation: str, *args, mutates: bool = False) -> None:
        self._relay((domain, operation, mutates), args)

    def _resource_id(self, payload: Any) -> str | None:
        values = payload if isinstance(payload, list) else [payload]
        for value in reversed(values):
            if not isinstance(value, dict):
                continue
            for key in ("source_id", "annot_id", "id", "workspace_id", "job_id", "trace_id"):
                if value.get(key) is not None:
                    return str(value[key])
        return None
