from __future__ import annotations

from typing import Optional

from PySide6.QtCore import QObject, Signal

from core.models.workspace_models import WorkspaceModel
from core.events.domains.workspace_events import WorkspaceEvent, WorkspaceEventPayload


class WorkspaceService(QObject):
    workspace_changed = Signal(int, dict)
    workspace_saved = Signal(int)
    workspace_loaded = Signal(int)

    def __init__(self, project_manager, event_bus=None, parent=None):
        super().__init__(parent)
        self.pm = project_manager
        self.bus = event_bus

    def load_workspace(self, workspace_id: int = 1) -> WorkspaceModel:
        model = self.pm.get_workspace_data(workspace_id) if self.pm else WorkspaceModel(workspace_id)
        self.workspace_loaded.emit(workspace_id)
        if self.bus and hasattr(self.bus, "workspace_loaded"):
            self.bus.workspace_loaded.emit(WorkspaceEvent.LOADED, WorkspaceEventPayload(workspace_id=workspace_id))
        return model

    def sync_workspace(self, model: WorkspaceModel):
        if not self.pm:
            return
        self.pm.sync_workspace(model)
        self.workspace_saved.emit(model.workspace_id)
        if self.bus and hasattr(self.bus, "workspace_saved"):
            self.bus.workspace_saved.emit(WorkspaceEvent.SAVED, WorkspaceEventPayload(workspace_id=model.workspace_id))

    def sync_delta(self, delta: WorkspaceModel):
        if not self.pm:
            return
        self.pm.sync_workspace_delta(delta)
        summary = {
            "nodes": len(delta.nodes),
            "edges": len(delta.edges),
            "deleted_nodes": len(delta.deleted_node_ids),
            "deleted_edges": len(delta.deleted_edge_ids),
        }
        self.workspace_changed.emit(delta.workspace_id, summary)
        if self.bus and hasattr(self.bus, "workspace_changed"):
            self.bus.workspace_changed.emit(
                WorkspaceEvent.CHANGED,
                WorkspaceEventPayload(workspace_id=delta.workspace_id, summary=summary),
            )

    def mark_dirty(self, workspace_id: int, autosave: bool = False, model: Optional[WorkspaceModel] = None):
        if not self.pm:
            return
        self.pm.mark_dirty("workspace")
        if self.bus and hasattr(self.bus, "workspace_changed"):
            self.bus.workspace_changed.emit(
                WorkspaceEvent.CHANGED,
                WorkspaceEventPayload(workspace_id=workspace_id, summary={"autosave": autosave}),
            )
        if autosave and model is not None:
            self.sync_workspace(model)

    def create_workspace(self, name: str) -> Optional[int]:
        if not self.pm:
            return None
        new_id = self.pm.create_workspace(name)
        if new_id and self.bus and hasattr(self.bus, "workspace_changed"):
            self.bus.workspace_changed.emit(
                WorkspaceEvent.CHANGED,
                WorkspaceEventPayload(workspace_id=int(new_id), summary={"created": True}),
            )
        return new_id
