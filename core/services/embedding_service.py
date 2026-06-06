# core/services/embedding_service.py
from PySide6.QtCore import QObject, QRunnable, QThreadPool, Slot
from core.events.event_bus import EventBus
from core.events.domains.workspace_events import WorkspaceIntent, WorkspacePayload

class BackgroundEmbeddingTask(QRunnable):
    def __init__(self, node_id, text, llm_manager, project_manager):
        super().__init__()
        self.node_id = node_id
        self.text = text
        self.llm_manager = llm_manager
        self.project_manager = project_manager

    @Slot()
    def run(self):
        if not self.text.strip() or not self.llm_manager or not getattr(self.llm_manager, 'ai_enabled', False):
            return
        try:
            vector = self.llm_manager.get_embedding(self.text)
            if vector and self.project_manager:
                self.project_manager.save_node_embedding_threadsafe(self.node_id, vector)
        except Exception as e:
            print(f"[Embedding Service] Background embedding failed: {e}")

class EmbeddingService(QObject):
    """Listens for text changes and silently generates vector embeddings in the background."""
    def __init__(self, llm_manager, project_manager, parent=None):
        super().__init__(parent)
        self.llm_manager = llm_manager
        self.project_manager = project_manager
        self.bus = EventBus.get_instance()
        
        self.bus.workspace_action_requested.connect(self._handle_intent)

    def _handle_intent(self, intent_name: WorkspaceIntent, payload: WorkspacePayload):
        if intent_name == WorkspaceIntent.EMBED_NODE_TEXT:
            node_id = payload.get("node_id")
            text = payload.get("text")
            if node_id and text:
                task = BackgroundEmbeddingTask(node_id, text, self.llm_manager, self.project_manager)
                QThreadPool.globalInstance().start(task)
