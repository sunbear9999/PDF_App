from __future__ import annotations

import copy
import dataclasses
import json
import uuid

from PySide6.QtCore import Qt, QRectF
from PySide6.QtGui import QColor, QPen, QBrush, QPainter, QPainterPath, QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFormLayout,
    QFrame,
    QGraphicsItem,
    QGraphicsPathItem,
    QGraphicsRectItem,
    QGraphicsScene,
    QGraphicsTextItem,
    QGraphicsView,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QPushButton,
    QScrollArea,
    QSplitter,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from core.engine.action_model import ActionStep, AIActionBlueprint
from core.engine.workflow_graph_service import GUI_COMPONENTS, NODE_TYPE_TO_STEP_TYPE, WorkflowGraphService
from core.engine.workflow_model import WorkflowEdge, WorkflowGraph, WorkflowNode

# Step types that use the LLM and need the dedicated LLM properties section
_LLM_STEP_TYPES = {"LLM_QUERY", "LLM_SCHEMA_QUERY"}

# Sidebar grouping
_STEP_CATEGORIES = [
    ("LLM", ["LLM_QUERY", "LLM_SCHEMA_QUERY"]),
    ("Search", ["RAG_SEARCH"]),
    ("Context", ["SOURCE_STATISTICS", "ONTOLOGY_CATALOG"]),
    ("Flow", ["BRANCH", "FOREACH", "USER_INPUT"]),
    ("Code / Data", ["PYTHON_SCRIPT", "DATABASE_WRITE"]),
    ("Analysis", ["ANALYSIS_CONTRACT", "DOCUMENT_CHUNK", "ANALYSIS_COMPACT",
                   "ANALYSIS_FINALIZE", "ANALYSIS_SEND_TO_WORKSPACE", "GRAPH_VALIDATOR"]),
    ("Ontology", ["ONTOLOGY_UPSERT"]),
    ("Events", ["DISPATCH_EVENT", "AWAIT_EVENT"]),
    ("Data", ["READ_DOCUMENT_TEXT", "QUERY_DATABASE", "NOTES_READ"]),
    ("Workspace", ["WORKSPACE_WRITE"]),
    ("Interaction", ["SHOW_ITEM_SELECTOR"]),
]

_STEP_HINTS = {
    "LLM_QUERY": "Calls the LLM with an optional system prompt and context. Set Prompt Key to use a saved prompt template, or write the system prompt directly. Query/Input passes context from state. ui_format on the connected GUI node controls how output is rendered.",
    "LLM_SCHEMA_QUERY": "Same as LLM Query but forces JSON mode. Output must match the output_schema you define.",
    "RAG_SEARCH": "Searches indexed documents. 'queries' input = list of search strings (or key holding list). n_results = chunks per query (default 5). tag_filters = CSV of tags. tag_logic = AND or OR.",
    "SOURCE_STATISTICS": "Collects reusable source facts such as page count, indexed chunk count, and file size. Choose metrics in the inputs and reference the output from any later blueprint step.",
    "ONTOLOGY_CATALOG": "Loads registered entity and relation categories on demand. Feed its output into graph builders or any tool that must use exact ontology types.",
    "BRANCH": "Conditional router. Query = condition like `{confidence} > 0.8` or `{tag} == 'yes'`. Routes to true or false child branches.",
    "FOREACH": "Loops over a list in state. Query = state key holding the list. Connect child steps via the 'each' port.",
    "PYTHON_SCRIPT": "Sandboxed Python. Access state via `state` dict. Set `result = your_value`. Use `workflow_api.emit_event(type, payload)` to fire events.",
    "DATABASE_WRITE": "Writes to project SQLite. Inputs: table (str) + payload (dict of column:value). Returns 'Success' or 'Failed'.",
    "USER_INPUT": "Pauses workflow and shows a dialog. Query = message shown to user. Result = user's typed text.",
    "ANALYSIS_CONTRACT": "Starts a multi-chunk analysis. Define expected outputs here, then chain Document Chunk steps.",
    "DOCUMENT_CHUNK": "Splits a document into analysis chunks. Input: doc_path (or read from state['analysis_doc_path']).",
    "ANALYSIS_COMPACT": "Merges partial analysis results into running summaries.",
    "ANALYSIS_FINALIZE": "Emits completed analysis to the workspace event bus.",
    "ANALYSIS_SEND_TO_WORKSPACE": "Sends structured graph JSON to the workspace graph importer.",
    "GRAPH_VALIDATOR": "Validates a workspace graph JSON structure against the ontology schema.",
    "ONTOLOGY_UPSERT": "Inserts or updates entities and relations in the project ontology. Input: JSON with 'entities' and 'relations' lists.",
    "DISPATCH_EVENT": "Fires a named event on the bus. Inputs: signal_name, intent, payload (dict).",
    "AWAIT_EVENT": "Blocks and waits for a named event. Inputs: signal_name, timeout_ms (default 30000).",
    "LIBRARY_REF": "Runs a saved reusable step by reference. Set Step Ref to the saved step name.",
    "READ_DOCUMENT_TEXT": "Reads raw text from an indexed document by file path. Inputs: doc_path (required), max_chars (int), page (optional 0-indexed). No LLM required.",
    "QUERY_DATABASE": "Runs a SELECT query against the project SQLite database. Inputs: sql (SELECT only), output_format ('list'|'first'|'count'), max_rows. Returns JSON.",
    "NOTES_READ": "Reads notes from the project database. Inputs: limit (int), filter_tag (string), doc_path (string). Returns JSON array.",
    "WORKSPACE_WRITE": "Writes nodes and/or edges directly to the active workspace graph. Inputs: data (JSON list or {entities, relations}), node_type (default 'claim'), source_path.",
    "SHOW_ITEM_SELECTOR": "Pauses the workflow and shows a checkable list dialog. User selects items and clicks action button; selected items returned as JSON array. Inputs: items, title, action_label, item_display_key, item_subtitle_key.",
}

# Node color accent by category
_STEP_TYPE_COLORS = {
    "LLM_QUERY": "#7c4dff",
    "LLM_SCHEMA_QUERY": "#5e35b1",
    "RAG_SEARCH": "#0288d1",
    "SOURCE_STATISTICS": "#00838f",
    "ONTOLOGY_CATALOG": "#00695c",
    "BRANCH": "#e65100",
    "FOREACH": "#f57c00",
    "USER_INPUT": "#00897b",
    "PYTHON_SCRIPT": "#558b2f",
    "DATABASE_WRITE": "#37474f",
    "ANALYSIS_CONTRACT": "#6a1b9a",
    "DOCUMENT_CHUNK": "#4527a0",
    "ANALYSIS_COMPACT": "#283593",
    "ANALYSIS_FINALIZE": "#1565c0",
    "ANALYSIS_SEND_TO_WORKSPACE": "#0277bd",
    "GRAPH_VALIDATOR": "#00695c",
    "ONTOLOGY_UPSERT": "#ad1457",
    "DISPATCH_EVENT": "#c62828",
    "AWAIT_EVENT": "#b71c1c",
    "LIBRARY_REF": "#546e7a",
}


class WorkflowNodeItem(QGraphicsRectItem):
    def __init__(self, node: WorkflowNode, is_ui_node: bool, theme: dict):
        super().__init__(0, 0, 200, 96)
        self.node = node
        self.is_ui_node = is_ui_node
        self.theme = theme
        self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsMovable, True)
        self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsSelectable, True)
        self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemSendsGeometryChanges, True)
        self.setAcceptHoverEvents(True)
        self.setBrush(QBrush(QColor(theme.get("bg_panel", "#2a2a3a"))))
        self._update_border(False)
        self.title = QGraphicsTextItem(self)
        self.subtitle = QGraphicsTextItem(self)
        self.title.setDefaultTextColor(QColor(theme.get("text_main", "#fff")))
        self.subtitle.setDefaultTextColor(QColor(theme.get("text_muted", "#aaa")))
        self.title.setTextWidth(180)
        self.subtitle.setTextWidth(180)
        self.title.setPos(10, 8)
        self.subtitle.setPos(10, 46)
        self.refresh()
        self.setPos(node.x, node.y)
        self._build_tooltip()

    def _build_tooltip(self):
        if self.is_ui_node:
            fmt = self.node.inputs.get("ui_format", "live_stream")
            target = self.node.inputs.get("ui_target", "floating")
            tip = f"<b>GUI Output: {fmt}</b><br>Target: {target}<br>Connect a step to this via a 'render' edge to display its output."
        else:
            step_type = NODE_TYPE_TO_STEP_TYPE.get(self.node.type_id, self.node.type_id)
            hint = _STEP_HINTS.get(step_type, "")
            tip = f"<b>{step_type}</b><br>{hint}<br><br><i>Click to inspect and edit fields in the Inspector panel →</i>"
        self.setToolTip(tip)

    def _update_border(self, selected: bool):
        if self.is_ui_node:
            color = self.theme.get("success", "#00cc66")
        else:
            step_type = NODE_TYPE_TO_STEP_TYPE.get(self.node.type_id, "")
            color = _STEP_TYPE_COLORS.get(step_type, self.theme.get("accent", "#b366ff"))
        width = 3 if selected else 2
        self.setPen(QPen(QColor(color), width))

    def refresh(self):
        self.title.setPlainText(self.node.label or self.node.id)
        if self.is_ui_node:
            step_type = "GUI: " + self.node.inputs.get("ui_format", "output")
        else:
            step_type = NODE_TYPE_TO_STEP_TYPE.get(self.node.type_id, self.node.type_id)
        self.subtitle.setPlainText(step_type)

    def itemChange(self, change, value):
        if change == QGraphicsItem.GraphicsItemChange.ItemPositionHasChanged:
            self.node.x = self.pos().x()
            self.node.y = self.pos().y()
            scene = self.scene()
            if scene and hasattr(scene, "refresh_edges"):
                scene.refresh_edges()
        if change == QGraphicsItem.GraphicsItemChange.ItemSelectedHasChanged:
            self._update_border(bool(value))
        return super().itemChange(change, value)


class WorkflowEdgeItem(QGraphicsPathItem):
    def __init__(self, source_item: WorkflowNodeItem, target_item: WorkflowNodeItem, edge: WorkflowEdge, theme: dict):
        super().__init__()
        self.source_item = source_item
        self.target_item = target_item
        self.edge = edge
        self.theme = theme
        self.setPen(QPen(QColor(self._edge_color()), 2))
        self.setZValue(-1)
        self.label = QGraphicsTextItem()
        self.label.setDefaultTextColor(QColor(theme.get("text_muted", "#aaa")))
        self.label.setZValue(2)
        self.label.setPlainText(self._edge_label())
        self.refresh()

    def refresh(self):
        start = self.source_item.mapToScene(self.source_item.rect().right(), self.source_item.rect().center().y())
        end = self.target_item.mapToScene(self.target_item.rect().left(), self.target_item.rect().center().y())
        path = QPainterPath(start)
        dx = max(60, abs(end.x() - start.x()) / 2)
        path.cubicTo(start.x() + dx, start.y(), end.x() - dx, end.y(), end.x(), end.y())
        self.setPath(path)
        self.label.setPos((start.x() + end.x()) / 2 - 24, (start.y() + end.y()) / 2 - 22)

    def _edge_label(self):
        if self.edge.target_port == "render":
            return "render"
        if self.edge.source_port == "true":
            return "true"
        if self.edge.source_port == "false":
            return "false"
        if self.edge.source_port == "each":
            return "each"
        return "next"

    def _edge_color(self):
        if self.edge.target_port == "render":
            return self.theme.get("success", "#00cc66")
        if self.edge.source_port == "true":
            return "#3bb273"
        if self.edge.source_port == "false":
            return "#d95d5d"
        if self.edge.source_port == "each":
            return "#4da3ff"
        return self.theme.get("accent", "#b366ff")


class WorkflowScene(QGraphicsScene):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.edge_items = []

    def refresh_edges(self):
        for edge_item in self.edge_items:
            edge_item.refresh()


class VisualWorkflowEditor(QWidget):
    def __init__(self, theme: dict, node_type_registry=None, step_manager=None, parent=None):
        super().__init__(parent)
        self.theme = theme or {}
        self.node_type_registry = node_type_registry
        self.step_manager = step_manager
        self.graph_service = WorkflowGraphService()
        self.graph: WorkflowGraph | None = None
        self.node_items = {}
        self.edge_items = {}
        self._build_ui()

    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        # Canvas toolbar (zoom controls + layout)
        toolbar = QFrame()
        toolbar.setFixedHeight(36)
        tb_layout = QHBoxLayout(toolbar)
        tb_layout.setContentsMargins(6, 4, 6, 4)
        tb_layout.setSpacing(4)
        for label, tip, fn in [
            ("⊕", "Zoom In (Ctrl++)", self._zoom_in),
            ("⊖", "Zoom Out (Ctrl+-)", self._zoom_out),
            ("⊡", "Fit to View (Ctrl+0)", self._zoom_fit),
        ]:
            btn = QPushButton(label)
            btn.setToolTip(tip)
            btn.setFixedSize(28, 26)
            btn.clicked.connect(fn)
            tb_layout.addWidget(btn)
        tb_layout.addSpacing(8)
        auto_btn = QPushButton("Auto Layout")
        auto_btn.setToolTip("Arrange nodes automatically left-to-right")
        auto_btn.clicked.connect(self._auto_layout)
        tb_layout.addWidget(auto_btn)
        tb_layout.addStretch()
        tb_layout.addWidget(QLabel("Tip: Select 2 nodes → Connect | Del = delete selected"))
        layout.addWidget(toolbar)

        # Main splitter
        self.splitter = QSplitter(Qt.Orientation.Horizontal)
        layout.addWidget(self.splitter)

        # --- Sidebar ---
        sidebar_scroll = QScrollArea()
        sidebar_scroll.setWidgetResizable(True)
        sidebar_scroll.setFixedWidth(175)
        sidebar_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        sidebar_inner = QWidget()
        sidebar_layout = QVBoxLayout(sidebar_inner)
        sidebar_layout.setContentsMargins(6, 6, 6, 6)
        sidebar_layout.setSpacing(3)

        all_node_types = self._iter_step_node_types()
        type_by_step = {nt.step_type: nt for nt in all_node_types if nt.step_type != "LIBRARY_REF"}

        for category, step_types in _STEP_CATEGORIES:
            hdr = QLabel(f"<b>{category}</b>")
            hdr.setStyleSheet("margin-top:6px;")
            sidebar_layout.addWidget(hdr)
            for st in step_types:
                nt = type_by_step.get(st)
                if nt:
                    btn = QPushButton(nt.label)
                    btn.setToolTip(_STEP_HINTS.get(st, nt.description or st))
                    btn.clicked.connect(lambda checked=False, tid=nt.id: self.add_step_type(tid))
                else:
                    from core.engine.workflow_graph_service import STEP_TYPE_TO_NODE_TYPE
                    type_id = STEP_TYPE_TO_NODE_TYPE.get(st, "workflow.llm_query")
                    label = st.replace("_", " ").title()
                    btn = QPushButton(label)
                    btn.setToolTip(_STEP_HINTS.get(st, st))
                    btn.clicked.connect(lambda checked=False, s=st: self.add_step(s))
                accent = _STEP_TYPE_COLORS.get(st, "#555")
                btn.setStyleSheet(f"border-left: 3px solid {accent};")
                sidebar_layout.addWidget(btn)

        # --- Plugin-contributed step types (dynamic) ---
        plugin_types = [nt for nt in all_node_types if getattr(nt, "plugin_id", None)]
        if plugin_types:
            sidebar_layout.addSpacing(8)
            hdr = QLabel("<b>Plugin Steps</b>")
            hdr.setStyleSheet("margin-top:6px; color: #c0a0ff;")
            sidebar_layout.addWidget(hdr)
            for nt in plugin_types:
                btn = QPushButton(f"{nt.label} [{nt.plugin_id}]")
                btn.setToolTip(f"[Plugin: {nt.plugin_id}]\n{nt.description or nt.label}")
                btn.setStyleSheet("border-left: 3px solid #c0a0ff;")
                btn.clicked.connect(lambda checked=False, tid=nt.id: self.add_step_type(tid))
                sidebar_layout.addWidget(btn)

        sidebar_layout.addSpacing(8)
        sidebar_layout.addWidget(QLabel("<b>Reusable Steps</b>"))
        self.reusable_steps_layout = QVBoxLayout()
        sidebar_layout.addLayout(self.reusable_steps_layout)
        self._refresh_reusable_steps()

        sidebar_layout.addSpacing(8)
        sidebar_layout.addWidget(QLabel("<b>GUI Output Nodes</b>"))
        for component_id, component in GUI_COMPONENTS.items():
            btn = QPushButton(component["label"])
            btn.setToolTip(component.get("description", component_id))
            btn.setStyleSheet("border-left: 3px solid #00cc66;")
            btn.clicked.connect(lambda checked=False, cid=component_id: self.add_ui_component(cid))
            sidebar_layout.addWidget(btn)

        sidebar_layout.addSpacing(8)
        sidebar_layout.addWidget(QLabel("<b>Wiring</b>"))
        self.combo_connection_mode = QComboBox()
        self.combo_connection_mode.addItem("Sequence: result → next", ("result", "next"))
        self.combo_connection_mode.addItem("Render: result → GUI", ("result", "render"))
        self.combo_connection_mode.addItem("Branch: true path", ("true", "branch_true"))
        self.combo_connection_mode.addItem("Branch: false path", ("false", "branch_false"))
        self.combo_connection_mode.addItem("Loop: foreach body", ("each", "foreach_body"))
        sidebar_layout.addWidget(self.combo_connection_mode)
        connect_btn = QPushButton("Connect Selected")
        connect_btn.clicked.connect(self.connect_selected)
        sidebar_layout.addWidget(connect_btn)
        delete_btn = QPushButton("Remove Selected")
        delete_btn.setStyleSheet("color: #ff6b6b;")
        delete_btn.clicked.connect(self.delete_selected)
        sidebar_layout.addWidget(delete_btn)
        dup_btn = QPushButton("Duplicate Selected")
        dup_btn.clicked.connect(self.duplicate_selected)
        sidebar_layout.addWidget(dup_btn)
        sidebar_layout.addStretch()

        sidebar_scroll.setWidget(sidebar_inner)
        self.splitter.addWidget(sidebar_scroll)

        # --- Canvas ---
        canvas_frame = QFrame()
        canvas_layout = QVBoxLayout(canvas_frame)
        canvas_layout.setContentsMargins(0, 0, 0, 0)
        self.scene = WorkflowScene(self)
        self.scene.setSceneRect(-2000, -1500, 5000, 4000)
        self.scene.selectionChanged.connect(self._on_selection_changed)
        self.view = QGraphicsView(self.scene)
        self.view.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        self.view.setDragMode(QGraphicsView.DragMode.ScrollHandDrag)
        self.view.setTransformationAnchor(QGraphicsView.ViewportAnchor.AnchorUnderMouse)
        self.view.setResizeAnchor(QGraphicsView.ViewportAnchor.AnchorUnderMouse)
        self.view.wheelEvent = self._wheel_zoom
        canvas_layout.addWidget(self.view)
        self.splitter.addWidget(canvas_frame)

        # --- Inspector (scrollable) ---
        inspector_scroll = QScrollArea()
        inspector_scroll.setWidgetResizable(True)
        inspector_scroll.setMinimumWidth(300)
        inspector_scroll.setMaximumWidth(420)
        inspector_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        inspector_inner = QWidget()
        inspector_layout = QVBoxLayout(inspector_inner)
        inspector_layout.setContentsMargins(8, 8, 8, 8)
        inspector_layout.setSpacing(6)

        inspector_layout.addWidget(QLabel("<b>Inspector</b>"))
        self._hint_label = QLabel("")
        self._hint_label.setWordWrap(True)
        self._hint_label.setStyleSheet("font-style: italic; color: #aaa; padding: 4px; background: rgba(255,255,255,0.05); border-radius:4px;")
        inspector_layout.addWidget(self._hint_label)

        # System variables quick-reference (collapsible)
        self._vars_panel = self._build_vars_panel()
        inspector_layout.addWidget(self._vars_panel)

        # ── Static fields (always shown for every step) ────────────────
        static_form = QFormLayout()
        static_form.setLabelAlignment(Qt.AlignmentFlag.AlignRight)
        static_form.setSpacing(4)
        self.input_node_id = QLineEdit()
        self.input_node_id.setWhatsThis("Unique ID for this node. Referenced in edges and state. Changing it updates all connected edges.")
        self.input_label = QLineEdit()
        self.input_label.setWhatsThis("Display name shown on the canvas node card.")
        self.combo_step_type = QComboBox()
        self.combo_step_type.addItems(self._available_step_types())
        self.combo_step_type.currentTextChanged.connect(self._on_step_type_changed)
        self.combo_step_type.setWhatsThis("The type of operation this step performs. Changing the type rebuilds the input fields below.")
        self.input_output_key = QLineEdit()
        self.input_output_key.setWhatsThis("State key where this step's result is stored. Reference it in later steps with {this_key}.")
        for lbl, w in [("ID", self.input_node_id), ("Label", self.input_label),
                        ("Type", self.combo_step_type), ("Output Key", self.input_output_key)]:
            static_form.addRow(lbl, w)
        inspector_layout.addLayout(static_form)

        # ── Dynamic inputs section (rebuilt when step type changes) ────
        self._dynamic_widgets: dict = {}   # field_key → widget
        self._dynamic_section = QWidget()
        self._dynamic_layout = QFormLayout(self._dynamic_section)
        self._dynamic_layout.setLabelAlignment(Qt.AlignmentFlag.AlignRight)
        self._dynamic_layout.setSpacing(4)
        inspector_layout.addWidget(self._dynamic_section)

        # ── LLM Properties section (only for LLM step types) ──────────
        from PySide6.QtWidgets import QGroupBox
        self._llm_group = QGroupBox("LLM Properties")
        llm_form = QFormLayout(self._llm_group)
        llm_form.setLabelAlignment(Qt.AlignmentFlag.AlignRight)
        llm_form.setSpacing(4)

        self.input_model = QLineEdit()
        self.input_model.setPlaceholderText("{selected_model}")
        self.input_model.setToolTip("AI model to use. {selected_model} = whatever's chosen in the toolbar. Or type a model name like 'llama3'.")

        self.input_prompt_key = QLineEdit()
        self.input_prompt_key.setPlaceholderText("e.g. Citation Extractor")
        self.input_prompt_key.setToolTip("Load a saved system prompt from Prompt Manager. Leave blank to use the System field below instead.")
        _prompt_key_row = QWidget()
        _pk_hl = QHBoxLayout(_prompt_key_row)
        _pk_hl.setContentsMargins(0, 0, 0, 0)
        _pk_hl.setSpacing(3)
        _pk_hl.addWidget(self.input_prompt_key)
        self.btn_open_prompt = QPushButton("Open")
        self.btn_open_prompt.setFixedWidth(48)
        self.btn_open_prompt.setFixedHeight(24)
        self.btn_open_prompt.setToolTip("Open this prompt key in the Prompt Editor")
        self.btn_open_prompt.clicked.connect(self._open_prompt_in_editor)
        _pk_hl.addWidget(self.btn_open_prompt)
        self._prompt_key_row_widget = _prompt_key_row

        self.input_system = QTextEdit()
        self.input_system.setMaximumHeight(80)
        self.input_system.setPlaceholderText("System prompt text (or leave blank to use Prompt Key above)")
        self.input_system.setToolTip("System prompt sent to the model. Use {state_key} to inject dynamic values. If Prompt Key is set, this is appended after it.")
        _system_wrap = QWidget()
        _sys_vl = QVBoxLayout(_system_wrap)
        _sys_vl.setContentsMargins(0, 0, 0, 0)
        _sys_vl.setSpacing(2)
        _sys_vl.addWidget(self.input_system)
        self.btn_save_as_prompt = QPushButton("Save as Prompt Key…")
        self.btn_save_as_prompt.setFixedHeight(22)
        self.btn_save_as_prompt.setToolTip("Save the text above as a named prompt in the Prompt Manager so it can be reused or exported")
        self.btn_save_as_prompt.clicked.connect(self._save_system_as_prompt)
        _sys_vl.addWidget(self.btn_save_as_prompt)
        self._system_wrap_widget = _system_wrap

        self.input_llm_options = QTextEdit()
        self.input_llm_options.setMaximumHeight(56)
        self.input_llm_options.setPlaceholderText('{"temperature": 0.7, "num_predict": 2048}')
        self.input_llm_options.setToolTip("JSON object with generation parameters.\n  temperature (0.0–1.0): creativity\n  num_predict: max output tokens\n  num_ctx: context window\n  json_mode: true/false")

        self.input_schema = QTextEdit()
        self.input_schema.setMaximumHeight(70)
        self.input_schema.setPlaceholderText('{"claims": [{"claim": "", "type": ""}]}')
        self.input_schema.setToolTip("Expected JSON output structure. Forces JSON mode on the model. Leave blank for free-text output.")

        self.check_inline_citations = QCheckBox("Inline Citations")
        self.check_inline_citations.setToolTip("Weave citation source bubbles inline into the streamed output (requires a preceding RAG Search step).")
        self.input_citation_source_key = QLineEdit()
        self.input_citation_source_key.setPlaceholderText("e.g. rag_results")
        self.input_citation_source_key.setToolTip("State key holding RAG search results to use as citation sources. Usually the output key of a preceding RAG Search step.")

        for lbl, w in [
            ("Model", self.input_model),
            ("Prompt Key", self._prompt_key_row_widget),
            ("System", self._system_wrap_widget),
            ("LLM Options", self.input_llm_options),
            ("Output Schema", self.input_schema),
            ("", self.check_inline_citations),
            ("Cite Src Key", self.input_citation_source_key),
        ]:
            llm_form.addRow(lbl, w)
        self._llm_group.setVisible(False)
        inspector_layout.addWidget(self._llm_group)

        # ── UI Output section (only for GUI output nodes) ──────────────
        self._ui_group = QGroupBox("GUI Output Settings")
        ui_form = QFormLayout(self._ui_group)
        ui_form.setLabelAlignment(Qt.AlignmentFlag.AlignRight)
        ui_form.setSpacing(4)

        self.combo_ui_format = QComboBox()
        self.combo_ui_format.addItems([
            "silent", "status", "live_stream", "search_terms", "chat_widgets",
            "data_table", "card_grid", "workspace_graph", "nested_outline",
            "results_dialog", "bias_metrics",
        ])
        self.combo_ui_format.setToolTip(
            "How this node renders output:\n"
            "  silent — no output shown\n"
            "  status — show progress only, not the raw result\n"
            "  live_stream — streaming text\n"
            "  chat_widgets — citation bubbles\n"
            "  search_terms — clickable search cards\n"
            "  data_table — spreadsheet grid\n"
            "  card_grid — browsable card grid\n"
            "  workspace_graph — import nodes/edges\n"
            "  results_dialog — popup browse dialog\n"
            "  nested_outline — collapsible outline"
        )
        self.input_ui_target = QLineEdit()
        self.input_ui_target.setPlaceholderText("floating / chat_tab / custom_tools_tab / ...")
        self.input_ui_target.setToolTip(
            "Where to render the output:\n"
            "  floating — overlay on top of the current view\n"
            "  chat_tab — Chat tab of the Research dock\n"
            "  custom_tools_tab — Custom Tools tab\n"
            "  search_tab — Search tab\n"
            "  data_dock_workflow — Data Dock workflow panel\n"
            "  notes_dock_workflow — Notes Dock workflow panel"
        )
        self.input_ui_title = QLineEdit()
        self.input_ui_title.setPlaceholderText("Dialog / bubble title")
        self.input_ui_schema = QTextEdit()
        self.input_ui_schema.setMaximumHeight(70)
        self.input_ui_schema.setPlaceholderText('{"field": "description"} or null')
        self.input_ui_schema.setToolTip("Output schema for structured GUI nodes (data_table, card_grid, etc.).")

        for lbl, w in [
            ("UI Format", self.combo_ui_format),
            ("UI Target", self.input_ui_target),
            ("UI Title", self.input_ui_title),
            ("Output Schema", self.input_ui_schema),
        ]:
            ui_form.addRow(lbl, w)
        self._ui_group.setVisible(False)
        inspector_layout.addWidget(self._ui_group)

        # ── Action buttons ─────────────────────────────────────────────
        self.btn_apply = QPushButton("Apply Changes")
        self.btn_apply.clicked.connect(self.apply_inspector)
        inspector_layout.addWidget(self.btn_apply)
        self.btn_save_reusable = QPushButton("Save as Reusable Step")
        self.btn_save_reusable.setToolTip("Save this step configuration to the reusable step library (exportable via Pack Manager)")
        self.btn_save_reusable.clicked.connect(self.save_selected_step_template)
        inspector_layout.addWidget(self.btn_save_reusable)
        inspector_layout.addStretch()
        inspector_scroll.setWidget(inspector_inner)
        self.splitter.addWidget(inspector_scroll)

        self.splitter.setSizes([175, 650, 360])

        # Keyboard shortcuts
        QShortcut(QKeySequence.StandardKey.Delete, self).activated.connect(self.delete_selected)
        QShortcut(QKeySequence("Backspace"), self).activated.connect(self.delete_selected)
        QShortcut(QKeySequence("Ctrl++"), self).activated.connect(self._zoom_in)
        QShortcut(QKeySequence("Ctrl+="), self).activated.connect(self._zoom_in)
        QShortcut(QKeySequence("Ctrl+-"), self).activated.connect(self._zoom_out)
        QShortcut(QKeySequence("Ctrl+0"), self).activated.connect(self._zoom_fit)
        QShortcut(QKeySequence("Ctrl+D"), self).activated.connect(self.duplicate_selected)

        self.update_theme(self.theme)

    # ------------------------------------------------------------------
    # ------------------------------------------------------------------
    # Inspector helpers: prompt editor + vars panel
    # ------------------------------------------------------------------

    def _build_vars_panel(self) -> "QWidget":
        """Collapsible quick-reference panel for system state variables."""
        from PySide6.QtWidgets import QFrame, QToolButton
        container = QWidget()
        vl = QVBoxLayout(container)
        vl.setContentsMargins(0, 0, 0, 0)
        vl.setSpacing(0)

        toggle = QToolButton()
        toggle.setText("▶ State Variables Reference")
        toggle.setCheckable(True)
        toggle.setChecked(False)
        toggle.setStyleSheet("font-size:11px; color:#aaa; border:none; background:transparent;")
        vl.addWidget(toggle)

        content = QFrame()
        content.setVisible(False)
        content.setStyleSheet("background: rgba(255,255,255,0.03); border:1px solid #333; border-radius:4px;")
        cl = QVBoxLayout(content)
        cl.setContentsMargins(6, 6, 6, 6)
        cl.setSpacing(2)

        _VARS = [
            ("{user_input}", "User's typed message when launching the blueprint"),
            ("{selected_text}", "Text highlighted in PDF viewer (source_viewer actions)"),
            ("{source_path}", "File path of the active/selected document"),
            ("{selected_model}", "Model selected in the toolbar"),
            ("{active_rag_docs}", "List of docs currently enabled for RAG"),
            ("{project_manifest}", "JSON summary of all project documents"),
            ("{workspace_data}", "JSON of the active workspace graph"),
            ("{rag_context}", "Combined text from the latest RAG search step"),
            ("{item}", "Current loop item inside a FOREACH body"),
            ("{prompt:Key Name}", "Inline-expanded prompt from Prompt Manager"),
        ]
        for var, desc in _VARS:
            lbl = QLabel(f"<code>{var}</code> — {desc}")
            lbl.setWordWrap(True)
            lbl.setStyleSheet("font-size:11px; color:#bbb; padding: 1px 0;")
            cl.addWidget(lbl)
        vl.addWidget(content)

        def _toggle(checked):
            toggle.setText(("▼ " if checked else "▶ ") + "State Variables Reference")
            content.setVisible(checked)
        toggle.toggled.connect(_toggle)
        return container

    def _open_prompt_in_editor(self) -> None:
        """Open the PromptEditorDialog and jump to the current prompt key."""
        key = self.input_prompt_key.text().strip()
        try:
            from gui.components.dialogs.prompt_editor_dialog import PromptEditorDialog
            pm = getattr(self, "_prompt_manager", None)
            if pm is None:
                from core.app_context import AppContext
                ctx = AppContext.instance()
                pm = getattr(ctx, "prompt_manager", None) if ctx else None
            if pm is None:
                return
            dlg = PromptEditorDialog(pm, parent=self)
            dlg.show()
            dlg.raise_()
            if key:
                from PySide6.QtCore import Qt
                items = dlg.tree.findItems(key, Qt.MatchFlag.MatchExactly | Qt.MatchFlag.MatchRecursive)
                if items:
                    dlg.tree.setCurrentItem(items[0])
                    dlg.tree.scrollToItem(items[0])
        except Exception as exc:
            print(f"[BlueprintEditor] Could not open prompt editor: {exc}")

    def _save_system_as_prompt(self) -> None:
        """Save the current system prompt text to the PromptManager under a new key."""
        text = self.input_system.toPlainText().strip()
        if not text:
            return
        from PySide6.QtWidgets import QInputDialog
        key, ok = QInputDialog.getText(
            self, "Save as Prompt Key",
            "Enter a name for this prompt (will appear in Prompt Editor):",
            text=self.input_prompt_key.text().strip() or "",
        )
        if not ok or not key.strip():
            return
        key = key.strip()
        try:
            from core.app_context import AppContext
            ctx = AppContext.instance()
            pm = getattr(ctx, "prompt_manager", None) if ctx else None
            if pm and hasattr(pm, "set_prompt"):
                pm.set_prompt(key, text)
                self.input_prompt_key.setText(key)
                self.input_system.clear()
        except Exception as exc:
            print(f"[BlueprintEditor] Could not save prompt: {exc}")

    # ------------------------------------------------------------------
    # Zoom / navigation
    # ------------------------------------------------------------------

    def _zoom_in(self):
        self.view.scale(1.2, 1.2)

    def _zoom_out(self):
        self.view.scale(1 / 1.2, 1 / 1.2)

    def _zoom_fit(self):
        if self.graph and self.graph.nodes:
            self.view.fitInView(self.scene.itemsBoundingRect().adjusted(-40, -40, 40, 40),
                                Qt.AspectRatioMode.KeepAspectRatio)
        else:
            self.view.resetTransform()

    def _wheel_zoom(self, event):
        factor = 1.15 if event.angleDelta().y() > 0 else 1 / 1.15
        self.view.scale(factor, factor)

    # ------------------------------------------------------------------
    # Auto layout
    # ------------------------------------------------------------------

    def _auto_layout(self):
        if not self.graph:
            return
        service = self.graph_service
        # Topological sort by next-edges
        by_id = {n.id: n for n in self.graph.nodes}
        next_edges = {e.source_node_id: e.target_node_id for e in self.graph.edges
                      if e.source_port == "result" and e.target_port == "next"}
        targeted = set(next_edges.values())
        roots = [n for n in self.graph.nodes if n.id not in targeted and service.is_step_node(n)]
        ui_nodes = [n for n in self.graph.nodes if service.is_ui_node(n)]

        # Layout step nodes in sequence columns
        visited = set()
        col = 0
        row_by_col: dict[int, int] = {}
        def place(node, depth):
            if node.id in visited:
                return
            visited.add(node.id)
            r = row_by_col.get(depth, 0)
            node.x = depth * 240 + 60
            node.y = r * 140 + 80
            row_by_col[depth] = r + 1
            nxt_id = next_edges.get(node.id)
            if nxt_id and nxt_id in by_id:
                place(by_id[nxt_id], depth + 1)

        for root in (roots or list(by_id.values())):
            place(root, col)
            col = max(row_by_col.keys(), default=0) + 1 if row_by_col else 0

        # Place UI nodes below their source step
        ui_by_source = {}
        for e in self.graph.edges:
            if e.target_port == "render" and e.target_node_id in {n.id for n in ui_nodes}:
                ui_by_source[e.target_node_id] = e.source_node_id

        for ui_node in ui_nodes:
            src_id = ui_by_source.get(ui_node.id)
            src = by_id.get(src_id)
            if src:
                ui_node.x = src.x
                ui_node.y = src.y + 160
            else:
                ui_node.x = 60
                ui_node.y = 300

        self.render_graph()

    # ------------------------------------------------------------------
    # Blueprint I/O
    # ------------------------------------------------------------------

    def load_blueprint(self, blueprint: AIActionBlueprint):
        self.graph = self.graph_service.blueprint_to_graph(blueprint)
        self.render_graph()

    def to_blueprint(self, name: str, description: str, expected_inputs=None,
                     mount_points=None, active_contexts=None) -> AIActionBlueprint:
        if not self.graph:
            return AIActionBlueprint(name=name, description=description)
        self.graph.name = name
        self.graph.description = description
        if expected_inputs is not None:
            self.graph.expected_inputs = expected_inputs
        if mount_points is not None:
            self.graph.mount_points = mount_points
        if active_contexts is not None:
            self.graph.active_contexts = active_contexts
        return self.graph_service.graph_to_blueprint(self.graph)

    # ------------------------------------------------------------------
    # Node operations
    # ------------------------------------------------------------------

    def add_step(self, step_type: str):
        if not self.graph:
            self.graph = WorkflowGraph(id="custom_workflow", name="Custom Workflow")
        index = 1 + len([n for n in self.graph.nodes if self.graph_service.is_step_node(n)])
        self.graph.nodes.append(self.graph_service.create_step_node(step_type, index))
        self.render_graph()

    def add_step_type(self, type_id: str):
        if not self.graph:
            self.graph = WorkflowGraph(id="custom_workflow", name="Custom Workflow")
        node_type = self.node_type_registry.get(type_id) if self.node_type_registry else None
        if not node_type:
            step_type = NODE_TYPE_TO_STEP_TYPE.get(type_id, "LLM_QUERY")
            self.add_step(step_type)
            return
        index = 1 + len([n for n in self.graph.nodes if self.graph_service.is_step_node(n)])
        self.graph.nodes.append(self.graph_service.create_step_node_from_type(node_type, index))
        self.render_graph()

    def add_library_step(self, step_ref: str):
        if not self.step_manager:
            return
        library_step = self.step_manager.get_step(step_ref)
        if not library_step:
            return
        if not self.graph:
            self.graph = WorkflowGraph(id="custom_workflow", name="Custom Workflow")
        index = 1 + len([n for n in self.graph.nodes if self.graph_service.is_step_node(n)])
        self.graph.nodes.append(self.graph_service.create_library_step_node(step_ref, library_step, index))
        self.render_graph()

    def add_ui_component(self, component_id: str):
        if not self.graph:
            self.graph = WorkflowGraph(id="custom_workflow", name="Custom Workflow")
        index = 1 + len([n for n in self.graph.nodes if self.graph_service.is_ui_node(n)])
        self.graph.nodes.append(self.graph_service.create_ui_node(component_id, index))
        self.render_graph()

    def connect_selected(self):
        selected = [item for item in self.scene.selectedItems() if isinstance(item, WorkflowNodeItem)]
        if len(selected) != 2 or not self.graph:
            return
        source, target = sorted(selected, key=lambda item: item.pos().x())
        source_port, target_port = self.combo_connection_mode.currentData()
        if target.is_ui_node:
            source_port, target_port = "result", "render"
        self.graph.edges.append(WorkflowEdge(str(uuid.uuid4()), source.node.id, source_port, target.node.id, target_port))
        self.render_graph()

    def delete_selected(self):
        if not self.graph:
            return
        selected = [item for item in self.scene.selectedItems() if isinstance(item, WorkflowNodeItem)]
        selected_ids = {item.node.id for item in selected}
        if not selected_ids:
            return
        self.graph.nodes = [n for n in self.graph.nodes if n.id not in selected_ids]
        self.graph.edges = [
            e for e in self.graph.edges
            if e.source_node_id not in selected_ids and e.target_node_id not in selected_ids
        ]
        self.render_graph()

    def duplicate_selected(self):
        if not self.graph:
            return
        selected = [item for item in self.scene.selectedItems() if isinstance(item, WorkflowNodeItem)]
        if not selected:
            return
        for item in selected:
            old = item.node
            new_id = old.id + "_copy"
            new_node = WorkflowNode(
                id=new_id,
                type_id=old.type_id,
                label=old.label + " (copy)",
                inputs=copy.deepcopy(old.inputs),
                x=old.x + 30,
                y=old.y + 30,
            )
            if new_node.inputs.get("step"):
                new_node.inputs["step"]["step_id"] = new_id
            self.graph.nodes.append(new_node)
        self.render_graph()

    # ------------------------------------------------------------------
    # Render
    # ------------------------------------------------------------------

    def render_graph(self):
        self.scene.clear()
        self.scene.edge_items = []
        self.node_items.clear()
        self.edge_items.clear()
        if not self.graph:
            return
        for node in self.graph.nodes:
            item = WorkflowNodeItem(node, self.graph_service.is_ui_node(node), self.theme)
            self.scene.addItem(item)
            self.node_items[node.id] = item
        for edge in self.graph.edges:
            source = self.node_items.get(edge.source_node_id)
            target = self.node_items.get(edge.target_node_id)
            if source and target:
                edge_item = WorkflowEdgeItem(source, target, edge, self.theme)
                self.scene.addItem(edge_item)
                self.scene.addItem(edge_item.label)
                self.scene.edge_items.append(edge_item)
                self.edge_items[edge.id] = edge_item

    # ------------------------------------------------------------------
    # Inspector population
    # ------------------------------------------------------------------

    def _on_step_type_changed(self, step_type: str):
        nt = self._get_node_type(step_type)
        hint = (nt.description if nt else None) or _STEP_HINTS.get(step_type, "")
        self._hint_label.setText(hint)
        self._rebuild_dynamic_inputs(step_type, nt, step_data=None)
        self._llm_group.setVisible(step_type in _LLM_STEP_TYPES)
        self._ui_group.setVisible(False)

    def _on_selection_changed(self):
        selected = [item for item in self.scene.selectedItems() if isinstance(item, WorkflowNodeItem)]
        if selected:
            self.populate_inspector(selected[0])

    def populate_inspector(self, item: WorkflowNodeItem):
        node = item.node
        self.input_node_id.setText(node.id)
        self.input_label.setText(node.label)
        is_ui = self.graph_service.is_ui_node(node)

        self._ui_group.setVisible(is_ui)
        self._dynamic_section.setVisible(not is_ui)
        self.combo_step_type.setVisible(not is_ui)
        self.input_output_key.setEnabled(not is_ui)
        self.btn_save_reusable.setEnabled(not is_ui)

        if is_ui:
            self._llm_group.setVisible(False)
            self.combo_ui_format.setCurrentText(node.inputs.get("ui_format", "live_stream"))
            self.input_ui_target.setText(node.inputs.get("ui_target", "floating"))
            self.input_ui_title.setText(node.inputs.get("ui_title", ""))
            schema = node.inputs.get("output_schema")
            self.input_ui_schema.setPlainText(json.dumps(schema, indent=2) if schema else "")
            self._hint_label.setText("GUI output node. Connect a step via the 'render' edge. Choose UI Format + Target.")
        else:
            step = self.graph_service.node_to_step(node)
            if step.step_type not in [self.combo_step_type.itemText(i) for i in range(self.combo_step_type.count())]:
                self.combo_step_type.addItem(step.step_type)
            self.combo_step_type.setCurrentText(step.step_type)
            self.input_output_key.setText(step.output_key)
            nt = self._get_node_type(step.step_type)
            hint = (nt.description if nt else None) or _STEP_HINTS.get(step.step_type, "")
            self._hint_label.setText(hint)
            # Rebuild dynamic inputs populated with current step data
            self._rebuild_dynamic_inputs(step.step_type, nt, step_data=step.inputs or {})
            # LLM section
            is_llm = step.step_type in _LLM_STEP_TYPES
            self._llm_group.setVisible(is_llm)
            if is_llm:
                self.input_model.setText(step.model or "")
                self.input_prompt_key.setText(step.prompt_key or "")
                self.input_system.setPlainText(step.system_prompt or "")
                self.input_llm_options.setPlainText(json.dumps(step.llm_options, indent=2) if step.llm_options else "")
                self.input_schema.setPlainText(json.dumps(step.output_schema, indent=2) if step.output_schema else "")
                self.check_inline_citations.setChecked(bool(step.inline_citations))
                self.input_citation_source_key.setText(step.citation_source_key or "")

    # ------------------------------------------------------------------
    # Inspector apply
    # ------------------------------------------------------------------

    def apply_inspector(self):
        selected = [item for item in self.scene.selectedItems() if isinstance(item, WorkflowNodeItem)]
        if not selected:
            return
        item = selected[0]
        node = item.node
        old_id = node.id
        new_id = self.input_node_id.text().strip() or old_id
        node.id = new_id
        node.label = self.input_label.text().strip() or new_id
        if self.graph and old_id != new_id:
            for edge in self.graph.edges:
                if edge.source_node_id == old_id:
                    edge.source_node_id = new_id
                if edge.target_node_id == old_id:
                    edge.target_node_id = new_id

        if self.graph_service.is_ui_node(node):
            node.inputs["ui_format"] = self.combo_ui_format.currentText()
            node.inputs["ui_target"] = self.input_ui_target.text().strip() or "floating"
            node.inputs["ui_title"] = self.input_ui_title.text().strip() or node.label
            node.inputs["output_schema"] = self._parse_json_or_none(self.input_ui_schema.toPlainText())
        else:
            step = self.graph_service.node_to_step(node)
            step.step_id = new_id
            step.step_type = self.combo_step_type.currentText()
            from core.engine.workflow_graph_service import STEP_TYPE_TO_NODE_TYPE
            step.node_type_id = next((k for k, v in NODE_TYPE_TO_STEP_TYPE.items() if v == step.step_type),
                                     STEP_TYPE_TO_NODE_TYPE.get(step.step_type, step.node_type_id))
            step.output_key = self.input_output_key.text().strip() or "result"
            # Read dynamic inputs
            step.inputs = self._get_dynamic_inputs(step.step_type)
            # LLM properties (only applied when visible)
            if self._llm_group.isVisible():
                step.model = self.input_model.text().strip() or "{selected_model}"
                step.prompt_key = self.input_prompt_key.text().strip() or None
                step.system_prompt = self.input_system.toPlainText().strip() or None
                step.llm_options = self._parse_json_or_none(self.input_llm_options.toPlainText()) or {}
                step.output_schema = self._parse_json_or_none(self.input_schema.toPlainText())
                step.inline_citations = self.check_inline_citations.isChecked()
                step.citation_source_key = self.input_citation_source_key.text().strip() or None
            node.type_id = step.node_type_id
            node.inputs["step"] = dataclasses.asdict(step)
        self.render_graph()

    # ------------------------------------------------------------------
    # Dynamic inspector helpers
    # ------------------------------------------------------------------

    def _get_node_type(self, step_type: str):
        """Look up WorkflowNodeType from registry by step_type string."""
        if self.node_type_registry:
            return self.node_type_registry.get_by_step_type(step_type)
        return None

    def _rebuild_dynamic_inputs(self, step_type: str, node_type=None, step_data: dict = None):
        """Clear and rebuild the dynamic inputs form from node_type.input_schema."""
        # Clear existing rows
        while self._dynamic_layout.rowCount():
            self._dynamic_layout.removeRow(0)
        self._dynamic_widgets.clear()

        schema = {}
        if node_type and node_type.input_schema:
            schema = node_type.input_schema
        if not schema:
            return

        step_data = step_data or {}
        for field_key, field_schema in schema.items():
            label_text = field_schema.get("label", field_key)
            if field_schema.get("required"):
                label_text += " *"
            value = step_data.get(field_key, field_schema.get("default", ""))
            widget = self._make_field_widget(field_key, field_schema, value)
            tooltip = field_schema.get("description", "")
            if tooltip:
                widget.setToolTip(tooltip)
            self._dynamic_widgets[field_key] = widget
            self._dynamic_layout.addRow(label_text, widget)

    def _make_field_widget(self, key: str, schema: dict, value=None) -> "QWidget":
        """Create the right widget for a given field type."""
        field_type = schema.get("type", "text")
        placeholder = schema.get("placeholder", "")

        if field_type == "event_signal":
            w = QComboBox()
            w.setEditable(True)
            signals = self._get_event_signals()
            for sig_name, sig_tip in signals:
                w.addItem(sig_name)
                w.setItemData(w.count() - 1, sig_tip, Qt.ItemDataRole.ToolTipRole)
            if value:
                w.setCurrentText(str(value))
            # When signal changes, update the intent combo if there is one
            w.currentTextChanged.connect(lambda sig: self._update_intent_combo(sig))
            return w

        if field_type == "event_intent":
            w = QComboBox()
            w.setEditable(True)
            # Populate initial choices from current signal selection (if any)
            sig_widget = self._dynamic_widgets.get("signal_name")
            if isinstance(sig_widget, QComboBox):
                for name, tip in self._get_intent_choices(sig_widget.currentText()):
                    w.addItem(name)
                    w.setItemData(w.count() - 1, tip, Qt.ItemDataRole.ToolTipRole)
            if value:
                w.setCurrentText(str(value))
            return w

        if field_type == "choice":
            w = QComboBox()
            for opt in schema.get("choices", []):
                w.addItem(str(opt))
            default = schema.get("default", "")
            if value:
                w.setCurrentText(str(value))
            elif default:
                w.setCurrentText(str(default))
            return w

        if field_type == "db_table":
            w = QComboBox()
            w.setEditable(True)
            for tbl in self._get_db_tables():
                w.addItem(tbl)
            if value:
                w.setCurrentText(str(value))
            if placeholder:
                w.lineEdit().setPlaceholderText(placeholder)
            return w

        if field_type == "boolean":
            w = QCheckBox()
            w.setChecked(bool(value))
            return w

        if field_type == "integer":
            w = QLineEdit()
            if placeholder:
                w.setPlaceholderText(placeholder)
            elif schema.get("default") is not None:
                w.setPlaceholderText(str(schema["default"]))
            if value is not None and str(value).strip():
                w.setText(str(value))
            return w

        if field_type in ("json", "object", "array"):
            w = QTextEdit()
            w.setMaximumHeight(90)
            ph = placeholder or schema.get("description", "")[:80]
            w.setPlaceholderText(ph)
            if isinstance(value, (dict, list)):
                w.setPlainText(json.dumps(value, indent=2))
            elif value:
                w.setPlainText(str(value))
            return w

        if field_type in ("textarea", "code"):
            w = QTextEdit()
            h = 120 if field_type == "code" else 80
            w.setMaximumHeight(h)
            if placeholder:
                w.setPlaceholderText(placeholder)
            if isinstance(value, (dict, list)):
                w.setPlainText(json.dumps(value, indent=2))
            elif value:
                w.setPlainText(str(value))
            return w

        # Default: QLineEdit for "text" and "string"
        w = QLineEdit()
        if placeholder:
            w.setPlaceholderText(placeholder)
        if value is not None and str(value).strip():
            w.setText(str(value))
        return w

    def _get_dynamic_inputs(self, step_type: str) -> dict:
        """Read current values from all dynamic widgets into a dict for step.inputs."""
        result = {}
        for key, widget in self._dynamic_widgets.items():
            if isinstance(widget, QComboBox):
                val = widget.currentText().strip()
            elif isinstance(widget, QTextEdit):
                val = widget.toPlainText().strip()
            elif isinstance(widget, QLineEdit):
                val = widget.text().strip()
            elif isinstance(widget, QCheckBox):
                val = widget.isChecked()
            else:
                val = ""
            # Try to parse JSON for json-type fields
            if isinstance(val, str) and val.startswith(("{", "[")):
                parsed = self._parse_json_or_none(val)
                if parsed is not None:
                    val = parsed
            if val != "" and val is not None:
                result[key] = val
        return result

    def _get_event_signals(self):
        """Return list of (signal_name, tooltip) from SIGNAL_CONTRACTS."""
        try:
            from core.events.event_payloads import SIGNAL_CONTRACTS
            import dataclasses as dc
            import enum
            result = []
            for sig_name, contract in SIGNAL_CONTRACTS.items():
                intent_cls = contract[0] if contract else None
                payload_cls = contract[1] if len(contract) > 1 else None
                lines = [f"Signal: {sig_name}"]
                if intent_cls and issubclass(intent_cls, enum.Enum):
                    vals = [e.name for e in intent_cls]
                    lines.append(f"Intents: {', '.join(vals[:6])}" + (" …" if len(vals) > 6 else ""))
                if payload_cls and dc.is_dataclass(payload_cls):
                    fields = [f.name for f in dc.fields(payload_cls)]
                    lines.append(f"Payload fields: {', '.join(fields)}")
                result.append((sig_name, "\n".join(lines)))
            return result
        except Exception:
            return []

    def _get_intent_choices(self, signal_name: str):
        """Return list of (intent_name, tooltip) for the given signal."""
        try:
            from core.events.event_payloads import SIGNAL_CONTRACTS
            import enum
            contract = SIGNAL_CONTRACTS.get(signal_name)
            if not contract:
                return []
            intent_cls = contract[0]
            if not issubclass(intent_cls, enum.Enum):
                return []
            return [(e.name, str(e.value) if isinstance(e.value, str) else e.name) for e in intent_cls]
        except Exception:
            return []

    def _update_intent_combo(self, signal_name: str):
        """Called when the signal_name combo changes — refreshes the intent dropdown."""
        intent_widget = self._dynamic_widgets.get("intent")
        if not isinstance(intent_widget, QComboBox):
            return
        intent_widget.clear()
        for name, tip in self._get_intent_choices(signal_name):
            intent_widget.addItem(name)
            intent_widget.setItemData(intent_widget.count() - 1, tip, Qt.ItemDataRole.ToolTipRole)

    def _get_db_tables(self):
        """Return list of table names from the open project database."""
        try:
            import sqlite3
            from core.app_context import AppContext
            ctx = AppContext.instance()
            pm = getattr(ctx, "project_manager", None) if ctx else None
            db_path = getattr(pm, "project_filepath", None) or getattr(pm, "db_path", None)
            if not db_path:
                return []
            conn = sqlite3.connect(db_path, timeout=3)
            cursor = conn.cursor()
            cursor.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")
            tables = [row[0] for row in cursor.fetchall()]
            conn.close()
            return tables
        except Exception:
            return ["annotations", "notes", "tags", "documents"]

    # ------------------------------------------------------------------
    # Reusable step management
    # ------------------------------------------------------------------

    def save_selected_step_template(self):
        if not self.step_manager:
            return
        selected = [item for item in self.scene.selectedItems() if isinstance(item, WorkflowNodeItem)]
        if not selected or self.graph_service.is_ui_node(selected[0].node):
            return
        step = self.graph_service.node_to_step(selected[0].node)
        default_name = step.step_ref or step.step_id or "custom_step"
        name, ok = QInputDialog.getText(
            self, "Save Reusable Step",
            "Save this step configuration as:\n(Reusable steps are exportable via Pack Manager)",
            text=default_name,
        )
        if not ok or not name.strip():
            return
        saved_ref = self.step_manager.save_step(name.strip(), step)
        # Update step_ref in the dynamic form if LIBRARY_REF is selected
        step_ref_widget = self._dynamic_widgets.get("step_ref")
        if isinstance(step_ref_widget, QLineEdit):
            step_ref_widget.setText(saved_ref)
        self._refresh_reusable_steps()

    def delete_reusable_step(self, step_ref: str):
        if not self.step_manager or not hasattr(self.step_manager, "library"):
            return
        self.step_manager.library.pop(step_ref, None)
        self.step_manager.save_library()
        self._refresh_reusable_steps()

    # ------------------------------------------------------------------
    # Node type helpers
    # ------------------------------------------------------------------

    def _iter_step_node_types(self):
        if self.node_type_registry:
            return list(self.node_type_registry.all())
        from core.engine.workflow_model import WorkflowNodeType
        from core.engine.workflow_graph_service import STEP_TYPE_TO_NODE_TYPE
        return [
            WorkflowNodeType(type_id, label, category, step_type, description=_STEP_HINTS.get(step_type, ""))
            for step_type, type_id, label, category in [
                ("LLM_QUERY", "workflow.llm_query", "LLM Query", "LLM"),
                ("LLM_SCHEMA_QUERY", "workflow.llm_schema_query", "LLM Schema Query", "LLM"),
                ("RAG_SEARCH", "workflow.rag_search", "RAG Search", "Search"),
                ("SOURCE_STATISTICS", "workflow.source_statistics", "Source Statistics", "Context"),
                ("ONTOLOGY_CATALOG", "workflow.ontology_catalog", "Ontology Catalog", "Context"),
                ("FOREACH", "workflow.foreach", "For Each", "Flow"),
                ("BRANCH", "workflow.branch", "Branch", "Flow"),
                ("USER_INPUT", "workflow.user_input", "User Input", "Flow"),
                ("PYTHON_SCRIPT", "workflow.python_script", "Python Script", "Code / Data"),
                ("DATABASE_WRITE", "workflow.database_write", "Database Write", "Code / Data"),
                ("ANALYSIS_CONTRACT", "workflow.analysis_contract", "Analysis Contract", "Analysis"),
                ("DOCUMENT_CHUNK", "workflow.document_chunk", "Document Chunk", "Analysis"),
                ("ANALYSIS_COMPACT", "workflow.analysis_compact", "Analysis Compact", "Analysis"),
                ("ANALYSIS_FINALIZE", "workflow.analysis_finalize", "Analysis Finalize", "Analysis"),
                ("ANALYSIS_SEND_TO_WORKSPACE", "workflow.analysis_send_to_workspace", "Send to Workspace", "Analysis"),
                ("GRAPH_VALIDATOR", "workflow.graph_validator", "Graph Validator", "Analysis"),
                ("ONTOLOGY_UPSERT", "workflow.ontology_upsert", "Ontology Upsert", "Ontology"),
                ("DISPATCH_EVENT", "workflow.dispatch_event", "Dispatch Event", "Events"),
                ("AWAIT_EVENT", "workflow.await_event", "Await Event", "Events"),
                ("LIBRARY_REF", "workflow.library_ref", "Reusable Step", "Library"),
            ]
        ]

    def _available_step_types(self):
        seen = []
        for nt in self._iter_step_node_types():
            if nt.step_type not in seen:
                seen.append(nt.step_type)
        return seen or ["LLM_QUERY"]

    def _refresh_reusable_steps(self):
        if not hasattr(self, "reusable_steps_layout"):
            return
        while self.reusable_steps_layout.count():
            it = self.reusable_steps_layout.takeAt(0)
            w = it.widget()
            if w:
                w.deleteLater()
        if not self.step_manager or not hasattr(self.step_manager, "list_steps"):
            lbl = QLabel("No step library")
            lbl.setStyleSheet("font-style: italic;")
            self.reusable_steps_layout.addWidget(lbl)
            return
        steps = self.step_manager.list_steps()
        if not steps:
            lbl = QLabel("No saved steps")
            lbl.setStyleSheet("font-style: italic;")
            self.reusable_steps_layout.addWidget(lbl)
            return
        for step_ref, step in steps:
            row = QHBoxLayout()
            btn = QPushButton(step_ref)
            btn.setToolTip(f"{step.step_type} — click to add to canvas")
            btn.clicked.connect(lambda checked=False, ref=step_ref: self.add_library_step(ref))
            row.addWidget(btn)
            del_btn = QPushButton("✕")
            del_btn.setFixedWidth(22)
            del_btn.setToolTip("Remove from library")
            del_btn.clicked.connect(lambda checked=False, ref=step_ref: self.delete_reusable_step(ref))
            row.addWidget(del_btn)
            self.reusable_steps_layout.addLayout(row)

    # ------------------------------------------------------------------
    # Utilities
    # ------------------------------------------------------------------

    def _parse_json_or_none(self, text: str):
        text = (text or "").strip()
        if not text:
            return None
        try:
            return json.loads(text)
        except Exception:
            return None

    def _parse_csv(self, text: str):
        return [part.strip() for part in (text or "").split(",") if part.strip()]

    def update_theme(self, theme: dict):
        self.theme = theme or {}
        panel = self.theme.get("bg_panel", "#333")
        main = self.theme.get("bg_main", "#1e1e1e")
        text = self.theme.get("text_main", "#fff")
        border = self.theme.get("border", "#444")
        self.setStyleSheet(
            f"QFrame {{ background-color: {panel}; color: {text}; }}"
            f"QGraphicsView {{ background-color: {main}; border: 1px solid {border}; }}"
            f"QLineEdit, QTextEdit, QComboBox {{ background-color: {main}; color: {text}; border: 1px solid {border}; border-radius: 4px; padding: 4px; }}"
            f"QPushButton {{ background-color: {panel}; color: {text}; border: 1px solid {border}; border-radius: 4px; padding: 5px; }}"
            f"QLabel {{ color: {text}; }}"
            f"QScrollArea {{ background-color: {panel}; border: none; }}"
            f"QCheckBox {{ color: {text}; }}"
        )
