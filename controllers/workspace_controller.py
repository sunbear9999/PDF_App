from typing import Any, Dict, Optional
from services.workspace_service import WorkspaceService


class WorkspaceController:
    def __init__(self, workspace_service: WorkspaceService, view: Optional[object] = None):
        self.workspace_service = workspace_service
        self.view = view

    def set_view(self, view: object) -> None:
        self.view = view

    def load_workspace(self) -> Dict[str, Any]:
        data = self.workspace_service.load_workspace_data()
        if self.view:
            self.view.load_workspace_state(data)
        return data

    def get_workspace_data(self) -> Dict[str, Any]:
        return self.workspace_service.load_workspace_data()

    def save_workspace(self) -> Dict[str, Any]:
        if not self.view:
            return {}
        workspace_data = self.view.serialize_workspace()
        self.workspace_service.save_workspace_data(workspace_data)
        return workspace_data

    def save_workspace_data(self, workspace_data: Dict[str, Any]) -> None:
        self.workspace_service.save_workspace_data(workspace_data)

    def mark_dirty(self, filepath: str = "workspace") -> None:
        self.workspace_service.mark_dirty(filepath)

    def create_edge_data(self, source: str, target: str, label: str = "", color: str = "#888888", weight: int = 2) -> Dict[str, Any]:
        return self.workspace_service.create_edge_data(source, target, label, color, weight)
