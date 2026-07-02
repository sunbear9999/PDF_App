"""Compatibility façade for the split workspace service modules.

New code should import each service from ``core.services.workspace`` or
``core.services.gui_bridge``.  Keeping this module as re-exports preserves the
public import surface used by older GUI code and third-party plugins without
maintaining a second implementation of every workspace service.
"""

from core.services.gui_bridge.workspace_ai_service import WorkspaceAIService
from core.services.gui_bridge.workspace_annotation_service import WorkspaceAnnotationService
from core.services.workspace.workspace_graph_service import WorkspaceGraphService
from core.services.workspace.workspace_layout_service import WorkspaceLayoutService, WorkspaceLayoutWorker
from core.services.workspace.workspace_service import WorkspaceService
from core.services.workspace.workspace_state_service import WorkspaceStateService

__all__ = [
    "WorkspaceAIService",
    "WorkspaceAnnotationService",
    "WorkspaceGraphService",
    "WorkspaceLayoutService",
    "WorkspaceLayoutWorker",
    "WorkspaceService",
    "WorkspaceStateService",
]
