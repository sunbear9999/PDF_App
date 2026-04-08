import uuid
from typing import Any, Dict
from core.project_manager import ProjectManager


class WorkspaceService:
    def __init__(self, project_manager: ProjectManager):
        self.project_manager = project_manager

    def load_workspace_data(self) -> Dict[str, Any]:
        return self.project_manager.get_workspace_data()

    def save_workspace_data(self, workspace_data: Dict[str, Any]) -> None:
        self.project_manager.save_workspace_data(workspace_data)
        self.project_manager.mark_dirty("workspace")

    def mark_dirty(self, filepath: str = "workspace") -> None:
        self.project_manager.mark_dirty(filepath)

    def create_edge_data(self, source: str, target: str, label: str = "", color: str = "#888888", weight: int = 2) -> Dict[str, Any]:
        from models.workspace_models import EdgeData

        edge = EdgeData(
            edge_id=str(uuid.uuid4()),
            source=source,
            target=target,
            label=label,
            color=color,
            weight=weight,
        )
        return edge.to_dict()
