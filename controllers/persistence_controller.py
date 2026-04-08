from typing import Any, Dict
from services.persistence_service import PersistenceService


class PersistenceController:
    """Routes persistence operations from UI components through the service layer."""

    def __init__(self, persistence_service: PersistenceService):
        self.persistence_service = persistence_service

    def mark_dirty(self, filepath: str = "workspace") -> None:
        """Mark a resource as having unsaved changes.
        
        Args:
            filepath: The file/resource to mark dirty. Defaults to "workspace" for graph data.
        """
        self.persistence_service.mark_dirty(filepath)

    def save_workspace_data(self, workspace_data: Dict[str, Any]) -> None:
        """Save workspace graph data.
        
        Args:
            workspace_data: Dictionary with 'nodes', 'edges', and metadata.
        """
        self.persistence_service.save_workspace_data(workspace_data)

    def get_workspace_data(self) -> Dict[str, Any]:
        """Retrieve current workspace graph data."""
        return self.persistence_service.get_workspace_data()
