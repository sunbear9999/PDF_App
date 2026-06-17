# Backward-compat shim — canonical location is gui.workspace.workspace_context_menus
from gui.workspace.workspace_context_menus import *  # noqa: F401, F403
from gui.workspace.workspace_context_menus import (
    build_ai_menu,
    build_canvas_context_menu,
    build_edge_context_menu,
    build_node_context_menu,
    build_selected_nodes_context_menu,
    populate_pdf_filter_combo,
    populate_tag_filter_combo,
    workspace_toolbar_stylesheet,
)
