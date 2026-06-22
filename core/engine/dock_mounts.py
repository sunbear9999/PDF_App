"""
core/engine/dock_mounts.py

String constants for all known DockActionSpec mount points.

Plugins use these when calling ``api.register_dock_action(DockActionSpec(mounts=[...]))``
so that dock widgets can filter by exact mount string.

Convention: ``"<dock_id>:<zone_type>:<zone_name>"``
"""

# ── Source Viewer / PDF Viewer ─────────────────────────────────────────
SOURCE_VIEWER_TEXT_SELECTION  = "source_viewer:context_menu:text_selection"
SOURCE_VIEWER_ANNOTATION      = "source_viewer:context_menu:annotation"
SOURCE_VIEWER_TOOLBAR         = "source_viewer:toolbar"

# ── Document List ─────────────────────────────────────────────────────
DOCUMENT_LIST_ITEM            = "document_list:context_menu:item"
DOCUMENT_LIST_TOOLBAR         = "document_list:toolbar"

# ── Essay Dock ────────────────────────────────────────────────────────
ESSAY_TOOLBAR                 = "essay_dock:toolbar"
ESSAY_CONTEXT_MENU_SELECTION  = "essay_dock:context_menu:text_selection"

# ── Notes Dock ────────────────────────────────────────────────────────
NOTES_TOOLBAR                 = "notes_dock:toolbar"
NOTES_CONTEXT_MENU_ANNOTATION = "notes_dock:context_menu:annotation"

# ── Data Dock ─────────────────────────────────────────────────────────
DATA_DOCK_TOOLBAR             = "data_dock:toolbar"
DATA_DOCK_CONTEXT_MENU_CELL   = "data_dock:context_menu:cell"
DATA_DOCK_CONTEXT_MENU_HEADER = "data_dock:context_menu:header"

# ── Entity Discovery Panel ────────────────────────────────────────────
ENTITY_DISCOVERY_ENTITY       = "entity_discovery:context_menu:entity"
ENTITY_DISCOVERY_TOOLBAR      = "entity_discovery:toolbar"

# ── OCR Dock ─────────────────────────────────────────────────────────
OCR_TOOLBAR                   = "ocr_dock:toolbar"
OCR_CONTEXT_MENU_RESULT       = "ocr_dock:context_menu:result"

# ── Slideshow Dock ────────────────────────────────────────────────────
SLIDESHOW_TOOLBAR             = "slideshow_dock:toolbar"

# ── Citation Dock ─────────────────────────────────────────────────────
CITATION_CONTEXT_MENU_ENTRY   = "citation_dock:context_menu:entry"
CITATION_TOOLBAR              = "citation_dock:toolbar"

# ── Workspace Canvas ─────────────────────────────────────────────────
# (in addition to WorkspaceContextMenuSpec already in extension_registry)
WORKSPACE_NODE_CONTEXT_MENU   = "workspace:context_menu:node"
WORKSPACE_SELECTION_CONTEXT_MENU = "workspace:context_menu:selection"
WORKSPACE_CANVAS_CONTEXT_MENU = "workspace:context_menu:canvas"
