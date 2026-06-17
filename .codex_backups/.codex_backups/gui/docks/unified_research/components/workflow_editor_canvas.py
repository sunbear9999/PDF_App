from __future__ import annotations

import dataclasses
import json
import uuid

from PySide6.QtCore import Qt, QRectF
from PySide6.QtGui import QColor, QPen, QBrush, QPainter, QPainterPath
from PySide6.QtWidgets import (
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
    QSplitter,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from core.engine.action_model import ActionStep, AIActionBlueprint
from core.engine.workflow_graph_service import GUI_COMPONENTS, NODE_TYPE_TO_STEP_TYPE, WorkflowGraphService
from core.engine.workflow_model import WorkflowEdge, WorkflowGraph, WorkflowNode


class WorkflowNodeItem(QGraphicsRectItem):
    def __init__(self, node: WorkflowNode, is_ui_node: bool, theme: dict):
        super().__init__(0, 0, 190, 92)
        self.node = node
        self.is_ui_node = is_ui_node
        self.theme = theme
        self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsMovable, True)
        self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsSelectable, True)
        self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemSendsGeometryChanges, True)
        self.setBrush(QBrush(QColor(theme.get("bg_panel", "#333"))))
        border = theme.get("accent", "#b366ff") if is_ui_node else theme.get("border", "#555")
        self.setPen(QPen(QColor(border), 2))
        self.title = QGraphicsTextItem(self)
        self.subtitle = QGraphicsTextItem(self)
        self.title.setDefaultTextColor(QColor(theme.get("text_main", "#fff")))
        self.subtitle.setDefaultTextColor(QColor(theme.get("text_muted", "#aaa")))
        self.title.setTextWidth(170)
        self.subtitle.setTextWidth(170)
        self.title.setPos(10, 8)
        self.subtitle.setPos(10, 42)
        self.refresh()
        self.setPos(node.x, node.y)

    def refresh(self):
        self.title.setPlainText(self.node.label or self.node.id)
        kind = "GUI Component" if self.is_ui_node else NODE_TYPE_TO_STEP_TYPE.get(self.node.type_id, self.node.type_id)
        self.subtitle.setPlainText(kind)

    def itemChange(self, change, value):
        if change == QGraphicsItem.GraphicsItemChange.ItemPositionHasChanged:
            self.node.x = self.pos().x()
            self.node.y = self.pos().y()
            scene = self.scene()
            if scene and hasattr(scene, "refresh_edges"):
                scene.refresh_edges()
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
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self.splitter = QSplitter(Qt.Orientation.Horizontal)
        layout.addWidget(self.splitter)

        self.sidebar = QFrame()
        self.sidebar.setFixedWidth(170)
        sidebar_layout = QVBoxLayout(self.sidebar)
        sidebar_layout.setContentsMargins(8, 8, 8, 8)
        sidebar_layout.addWidget(QLabel("<b>Step Types</b>"))
        for node_type in self._iter_step_node_types():
            if node_type.step_type == "LIBRARY_REF":
                continue
            btn = QPushButton(node_type.label)
            btn.setToolTip(node_type.description or node_type.step_type)
            btn.clicked.connect(lambda checked=False, type_id=node_type.id: self.add_step_type(type_id))
            sidebar_layout.addWidget(btn)

        sidebar_layout.addSpacing(10)
        sidebar_layout.addWidget(QLabel("<b>Reusable Steps</b>"))
        self.reusable_steps_layout = QVBoxLayout()
        sidebar_layout.addLayout(self.reusable_steps_layout)
        self._refresh_reusable_steps()

        sidebar_layout.addSpacing(10)
        sidebar_layout.addWidget(QLabel("<b>GUI Components</b>"))
        for component_id, component in GUI_COMPONENTS.items():
            btn = QPushButton(component["label"])
            btn.clicked.connect(lambda checked=False, cid=component_id: self.add_ui_component(cid))
            sidebar_layout.addWidget(btn)
        sidebar_layout.addSpacing(10)
        sidebar_layout.addWidget(QLabel("<b>Wiring</b>"))
        self.combo_connection_mode = QComboBox()
        self.combo_connection_mode.addItem("Sequence: result -> next", ("result", "next"))
        self.combo_connection_mode.addItem("Render: result -> GUI", ("result", "render"))
        self.combo_connection_mode.addItem("Branch: true path", ("true", "branch_true"))
        self.combo_connection_mode.addItem("Branch: false path", ("false", "branch_false"))
        self.combo_connection_mode.addItem("Loop: foreach body", ("each", "foreach_body"))
        sidebar_layout.addWidget(self.combo_connection_mode)
        connect_btn = QPushButton("Connect Selected")
        connect_btn.clicked.connect(self.connect_selected)
        sidebar_layout.addWidget(connect_btn)
        delete_btn = QPushButton("Remove Selected")
        delete_btn.clicked.connect(self.delete_selected)
        sidebar_layout.addWidget(delete_btn)
        sidebar_layout.addStretch()
        self.splitter.addWidget(self.sidebar)

        self.scene = WorkflowScene(self)
        self.scene.setSceneRect(-1000, -700, 2200, 1400)
        self.scene.selectionChanged.connect(self._on_selection_changed)
        self.view = QGraphicsView(self.scene)
        self.view.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        self.view.setDragMode(QGraphicsView.DragMode.ScrollHandDrag)
        self.view.setTransformationAnchor(QGraphicsView.ViewportAnchor.AnchorUnderMouse)
        self.splitter.addWidget(self.view)

        self.inspector = QFrame()
        self.inspector.setFixedWidth(300)
        inspector_layout = QVBoxLayout(self.inspector)
        inspector_layout.setContentsMargins(8, 8, 8, 8)
        inspector_layout.addWidget(QLabel("<b>Inspector</b>"))
        self.inspector_form = QFormLayout()
        self.input_node_id = QLineEdit()
        self.input_label = QLineEdit()
        self.combo_step_type = QComboBox()
        self.combo_step_type.addItems(self._available_step_types())
        self.input_step_ref = QLineEdit()
        self.input_model = QLineEdit()
        self.input_output_key = QLineEdit()
        self.input_prompt_key = QLineEdit()
        self.input_required_context = QLineEdit()
        self.input_permissions = QLineEdit()
        self.input_query = QTextEdit()
        self.input_query.setMaximumHeight(82)
        self.input_system = QTextEdit()
        self.input_system.setMaximumHeight(62)
        self.input_llm_options = QTextEdit()
        self.input_llm_options.setMaximumHeight(62)
        self.input_schema = QTextEdit()
        self.input_schema.setMaximumHeight(82)
        self.combo_ui_format = QComboBox()
        self.combo_ui_format.addItems(["silent", "live_stream", "search_terms", "chat_widgets", "data_table", "card_grid", "workspace_graph", "nested_outline"])
        self.input_ui_target = QLineEdit()
        for label, widget in [
            ("ID", self.input_node_id),
            ("Label", self.input_label),
            ("Step Type", self.combo_step_type),
            ("Step Ref", self.input_step_ref),
            ("Model", self.input_model),
            ("Output Key", self.input_output_key),
            ("Prompt Key", self.input_prompt_key),
            ("Context", self.input_required_context),
            ("Permissions", self.input_permissions),
            ("Query/Input", self.input_query),
            ("System", self.input_system),
            ("LLM Options", self.input_llm_options),
            ("Schema", self.input_schema),
            ("UI Format", self.combo_ui_format),
            ("UI Target", self.input_ui_target),
        ]:
            self.inspector_form.addRow(label, widget)
        inspector_layout.addLayout(self.inspector_form)
        self.btn_apply = QPushButton("Apply")
        self.btn_apply.clicked.connect(self.apply_inspector)
        inspector_layout.addWidget(self.btn_apply)
        self.btn_save_reusable = QPushButton("Save as Reusable Step")
        self.btn_save_reusable.clicked.connect(self.save_selected_step_template)
        inspector_layout.addWidget(self.btn_save_reusable)
        inspector_layout.addStretch()
        self.splitter.addWidget(self.inspector)
        self.splitter.setSizes([170, 700, 300])
        self.update_theme(self.theme)

    def load_blueprint(self, blueprint: AIActionBlueprint):
        self.graph = self.graph_service.blueprint_to_graph(blueprint)
        self.render_graph()

    def to_blueprint(self, name: str, description: str, expected_inputs=None) -> AIActionBlueprint:
        if not self.graph:
            return AIActionBlueprint(name=name, description=description)
        self.graph.name = name
        self.graph.description = description
        if expected_inputs is not None:
            self.graph.expected_inputs = expected_inputs
        return self.graph_service.graph_to_blueprint(self.graph)

    def add_step(self, step_type: str):
        if not self.graph:
            self.graph = WorkflowGraph(id="custom_workflow", name="Custom Workflow")
        index = 1 + len([node for node in self.graph.nodes if self.graph_service.is_step_node(node)])
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
        index = 1 + len([node for node in self.graph.nodes if self.graph_service.is_step_node(node)])
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
        index = 1 + len([node for node in self.graph.nodes if self.graph_service.is_step_node(node)])
        self.graph.nodes.append(self.graph_service.create_library_step_node(step_ref, library_step, index))
        self.render_graph()

    def add_ui_component(self, component_id: str):
        if not self.graph:
            self.graph = WorkflowGraph(id="custom_workflow", name="Custom Workflow")
        index = 1 + len([node for node in self.graph.nodes if self.graph_service.is_ui_node(node)])
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
        self.graph.nodes = [node for node in self.graph.nodes if node.id not in selected_ids]
        self.graph.edges = [
            edge for edge in self.graph.edges
            if edge.source_node_id not in selected_ids and edge.target_node_id not in selected_ids
        ]
        self.render_graph()

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

    def _on_selection_changed(self):
        selected = [item for item in self.scene.selectedItems() if isinstance(item, WorkflowNodeItem)]
        if selected:
            self.populate_inspector(selected[0])

    def populate_inspector(self, item: WorkflowNodeItem):
        node = item.node
        self.input_node_id.setText(node.id)
        self.input_label.setText(node.label)
        is_ui = self.graph_service.is_ui_node(node)
        self.combo_step_type.setEnabled(not is_ui)
        self.input_output_key.setEnabled(not is_ui)
        self.input_prompt_key.setEnabled(not is_ui)
        self.input_step_ref.setEnabled(not is_ui)
        self.input_model.setEnabled(not is_ui)
        self.input_required_context.setEnabled(not is_ui)
        self.input_permissions.setEnabled(not is_ui)
        self.input_query.setEnabled(not is_ui)
        self.input_system.setEnabled(not is_ui)
        self.input_llm_options.setEnabled(not is_ui)
        self.btn_save_reusable.setEnabled(not is_ui)
        self.combo_ui_format.setEnabled(is_ui)
        self.input_ui_target.setEnabled(is_ui)
        if is_ui:
            self.combo_ui_format.setCurrentText(node.inputs.get("ui_format", "live_stream"))
            self.input_ui_target.setText(node.inputs.get("ui_target", "floating"))
            self.input_schema.setText(json.dumps(node.inputs.get("output_schema"), indent=2) if node.inputs.get("output_schema") else "")
        else:
            step = self.graph_service.node_to_step(node)
            if step.step_type not in [self.combo_step_type.itemText(i) for i in range(self.combo_step_type.count())]:
                self.combo_step_type.addItem(step.step_type)
            self.combo_step_type.setCurrentText(step.step_type)
            self.input_step_ref.setText(step.step_ref or "")
            self.input_model.setText(step.model or "")
            self.input_output_key.setText(step.output_key)
            self.input_prompt_key.setText(step.prompt_key or "")
            self.input_required_context.setText(", ".join(step.required_context or []))
            self.input_permissions.setText(", ".join(step.permissions or []))
            self.input_query.setText(step.inputs.get("query") or json.dumps(step.inputs, indent=2))
            self.input_system.setText(step.system_prompt or "")
            self.input_llm_options.setText(json.dumps(step.llm_options, indent=2))
            self.input_schema.setText(json.dumps(step.output_schema, indent=2) if step.output_schema else "")

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
            node.inputs["ui_title"] = node.label
            node.inputs["output_schema"] = self._parse_json_or_none(self.input_schema.toPlainText())
        else:
            step = self.graph_service.node_to_step(node)
            step.step_id = new_id
            step.step_type = self.combo_step_type.currentText()
            step.node_type_id = next((k for k, v in NODE_TYPE_TO_STEP_TYPE.items() if v == step.step_type), step.node_type_id)
            step.step_ref = self.input_step_ref.text().strip() or None
            step.model = self.input_model.text().strip() or "{selected_model}"
            step.output_key = self.input_output_key.text().strip() or "result"
            step.prompt_key = self.input_prompt_key.text().strip() or None
            step.required_context = self._parse_csv(self.input_required_context.text())
            step.permissions = self._parse_csv(self.input_permissions.text()) or ["all"]
            query_text = self.input_query.toPlainText().strip()
            if query_text.startswith("{"):
                step.inputs = self._parse_json_or_none(query_text) or {}
            else:
                step.inputs["query"] = query_text
            step.system_prompt = self.input_system.toPlainText().strip() or None
            step.llm_options = self._parse_json_or_none(self.input_llm_options.toPlainText()) or {}
            step.output_schema = self._parse_json_or_none(self.input_schema.toPlainText())
            node.type_id = step.node_type_id
            node.inputs["step"] = dataclasses.asdict(step)
        self.render_graph()

    def save_selected_step_template(self):
        if not self.step_manager:
            return
        selected = [item for item in self.scene.selectedItems() if isinstance(item, WorkflowNodeItem)]
        if not selected or self.graph_service.is_ui_node(selected[0].node):
            return
        step = self.graph_service.node_to_step(selected[0].node)
        default_name = step.step_ref or step.step_id or "custom_step"
        name, ok = QInputDialog.getText(self, "Reusable Step", "Save step as:", text=default_name)
        if not ok or not name.strip():
            return
        saved_ref = self.step_manager.save_step(name.strip(), step)
        self.input_step_ref.setText(saved_ref)
        self._refresh_reusable_steps()

    def _iter_step_node_types(self):
        if self.node_type_registry:
            return list(self.node_type_registry.all())
        from core.engine.workflow_model import WorkflowNodeType
        return [
            WorkflowNodeType(type_id, label, "Core", step_type)
            for step_type, type_id, label in [
                ("LLM_QUERY", "workflow.llm_query", "LLM Query"),
                ("RAG_SEARCH", "workflow.rag_search", "RAG Search"),
                ("FOREACH", "workflow.foreach", "For Each"),
                ("BRANCH", "workflow.branch", "Branch"),
                ("USER_INPUT", "workflow.user_input", "User Input"),
                ("PYTHON_SCRIPT", "workflow.python_script", "Python Script"),
                ("DATABASE_WRITE", "workflow.database_write", "Database Write"),
                ("LIBRARY_REF", "workflow.library_ref", "Reusable Step"),
            ]
        ]

    def _available_step_types(self):
        step_types = []
        for node_type in self._iter_step_node_types():
            if node_type.step_type not in step_types:
                step_types.append(node_type.step_type)
        return step_types or ["LLM_QUERY"]

    def _refresh_reusable_steps(self):
        if not hasattr(self, "reusable_steps_layout"):
            return
        while self.reusable_steps_layout.count():
            item = self.reusable_steps_layout.takeAt(0)
            widget = item.widget()
            if widget:
                widget.deleteLater()
        if not self.step_manager or not hasattr(self.step_manager, "list_steps"):
            empty = QLabel("No step library")
            empty.setStyleSheet("font-style: italic;")
            self.reusable_steps_layout.addWidget(empty)
            return
        steps = self.step_manager.list_steps()
        if not steps:
            empty = QLabel("No reusable steps")
            empty.setStyleSheet("font-style: italic;")
            self.reusable_steps_layout.addWidget(empty)
            return
        for step_ref, step in steps:
            btn = QPushButton(step_ref)
            btn.setToolTip(f"{step.step_type} reusable step")
            btn.clicked.connect(lambda checked=False, ref=step_ref: self.add_library_step(ref))
            self.reusable_steps_layout.addWidget(btn)

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
        )
