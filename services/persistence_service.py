from typing import Any, Dict, Optional
from core.project_manager import ProjectManager


class PersistenceService:
    """Centralized persistence service for marking dirty state and saving workspace data."""

    def __init__(self, project_manager: ProjectManager):
        self.project_manager = project_manager

    def mark_dirty(self, filepath: str = "workspace") -> None:
        """Mark a file as having unsaved changes."""
        self.project_manager.mark_dirty(filepath)

    def save_workspace_data(self, workspace_data: Dict[str, Any]) -> None:
        """Save workspace graph data to the project database."""
        self.project_manager.save_workspace_data(workspace_data)

    def get_workspace_data(self) -> Dict[str, Any]:
        """Retrieve workspace graph data from the project database."""
        return self.project_manager.get_workspace_data()
