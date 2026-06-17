from __future__ import annotations

from PySide6.QtCore import QObject, QThread, Signal

from core.events.domains.workspace_events import WorkspaceEvent, WorkspaceEventPayload, WorkspaceIntent, WorkspacePayload


class WorkspaceLayoutWorker(QThread):
    layout_ready = Signal(dict)
    layout_failed = Signal(str)

    def __init__(self, payload: dict, llm_manager=None, project_manager=None, parent=None):
        super().__init__(parent)
        self.payload = payload
        self.llm_manager = llm_manager
        self.project_manager = project_manager

    def run(self):
        try:
            from core.layout_engine import calculate_force_directed_layout
            from core.utils.text_utils import get_semantic_similarity_matrix

            node_ids = self.payload.get("node_ids", [])
            texts = self.payload.get("texts", [])
            similarity_matrix = {}
            if self.payload.get("use_ai") and self.llm_manager and getattr(self.llm_manager, "ai_enabled", False):
                similarity_matrix = get_semantic_similarity_matrix(node_ids, texts, self.llm_manager, self.project_manager)

            positions = calculate_force_directed_layout(
                self.payload.get("nodes_info", {}),
                self.payload.get("edges_info", []),
                self.payload.get("center_x", 0),
                self.payload.get("center_y", 0),
                similarity_matrix=similarity_matrix,
                semantic_strength=self.payload.get("semantic_strength", 1.0),
            )
            self.layout_ready.emit(positions or {})
        except Exception as exc:
            self.layout_failed.emit(str(exc))


class WorkspaceLayoutService(QObject):
    def __init__(self, project_manager=None, llm_manager=None, event_bus=None, parent=None):
        super().__init__(parent)
        self.pm = project_manager
        self.llm = llm_manager
        self.bus = event_bus
        self.workers = []
        if self.bus:
            self.bus.workspace_action_requested.connect(self._handle_intent)

    def _handle_intent(self, intent: WorkspaceIntent, payload: WorkspacePayload):
        if intent != WorkspaceIntent.CALCULATE_LAYOUT:
            return
        self.calculate_layout(payload.get("extra", {}))

    def calculate_layout(self, layout_payload: dict):
        worker = WorkspaceLayoutWorker(layout_payload, self.llm, self.pm, self)
        self.workers.append(worker)
        worker.layout_ready.connect(self._emit_layout_ready)
        worker.layout_failed.connect(self._emit_layout_failed)
        worker.finished.connect(lambda w=worker: self._release_worker(w))
        worker.start()

    def _emit_layout_ready(self, positions: dict):
        if self.bus:
            self.bus.workspace_changed.emit(
                WorkspaceEvent.LAYOUT_READY,
                WorkspaceEventPayload(changes={"positions": positions}),
            )

    def _emit_layout_failed(self, message: str):
        if self.bus:
            self.bus.status_message_requested.emit(f"Layout failed: {message}", 8000)

    def _release_worker(self, worker: WorkspaceLayoutWorker):
        if worker in self.workers:
            self.workers.remove(worker)
        worker.deleteLater()
