"""
gui/workspace — workspace canvas domain components.

Import workspace components from this package:

    from gui.workspace import WorkspaceView, Node, Edge, ...
"""
from gui.workspace.workspace_view import WorkspaceView
from gui.workspace.workspace_items import Node, Edge, EditableTextItem, ResizeHandle
from gui.workspace.workspace_widgets import (
    GhostLineItem,
    CollapsingButton,
    CollapsingSection,
    CheckableComboBox,
)

__all__ = [
    "WorkspaceView",
    "Node",
    "Edge",
    "EditableTextItem",
    "ResizeHandle",
    "GhostLineItem",
    "CollapsingButton",
    "CollapsingSection",
    "CheckableComboBox",
]
