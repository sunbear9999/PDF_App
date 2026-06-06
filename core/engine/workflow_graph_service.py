from __future__ import annotations

import dataclasses
import copy
import uuid
from typing import Dict, List, Optional

from core.engine.action_model import ActionStep, AIActionBlueprint
from core.engine.workflow_model import WorkflowEdge, WorkflowGraph, WorkflowNode


GUI_COMPONENTS = {
    "workflow.ui.live_stream": {
        "label": "Live Text",
        "ui_format": "live_stream",
        "ui_target": "custom_tools_tab",
        "description": "Stream text into a tab or overlay.",
    },
    "workflow.ui.search_terms": {
        "label": "Search Cards",
        "ui_format": "search_terms",
        "ui_target": "search_tab",
        "description": "Render search terms as interactive search cards.",
        "output_schema": {"search_terms": [{"term": "boolean search string", "reason": "Why it helps"}]},
    },
    "workflow.ui.citation_cards": {
        "label": "Citation Bubbles",
        "ui_format": "chat_widgets",
        "ui_target": "chat_dock",
        "description": "Render source quote cards.",
    },
    "workflow.ui.data_table": {
        "label": "Data Table",
        "ui_format": "data_table",
        "ui_target": "custom_tools_tab",
        "description": "Render a flat JSON array as a table.",
    },
    "workflow.ui.card_grid": {
        "label": "Card Grid",
        "ui_format": "card_grid",
        "ui_target": "custom_tools_tab",
        "description": "Render JSON objects as cards.",
    },
    "workflow.ui.workspace_graph": {
        "label": "Workspace Graph",
        "ui_format": "workspace_graph",
        "ui_target": "floating",
        "description": "Route JSON graph output into the workspace graph importer.",
    },
    "workflow.ui.overlay": {
        "label": "Floating Overlay",
        "ui_format": "nested_outline",
        "ui_target": "floating",
        "description": "Show the result in the floating overlay.",
    },
}


STEP_TYPE_TO_NODE_TYPE = {
    "LLM_QUERY": "workflow.llm_query",
    "RAG_SEARCH": "workflow.rag_search",
    "FOREACH": "workflow.foreach",
    "BRANCH": "workflow.branch",
    "PYTHON_SCRIPT": "workflow.python_script",
    "USER_INPUT": "workflow.user_input",
    "DATABASE_WRITE": "workflow.database_write",
    "LIBRARY_REF": "workflow.library_ref",
}

NODE_TYPE_TO_STEP_TYPE = {value: key for key, value in STEP_TYPE_TO_NODE_TYPE.items()}


class WorkflowGraphService:
    def blueprint_to_graph(self, blueprint: AIActionBlueprint, graph_id: Optional[str] = None) -> WorkflowGraph:
        graph = WorkflowGraph(
            id=graph_id or self._safe_id(blueprint.name),
            name=blueprint.name,
            description=blueprint.description,
            expected_inputs=list(blueprint.expected_inputs),
            mount_points=list(blueprint.mount_points),
            active_contexts=list(blueprint.active_contexts),
        )
        previous_node_id = None
        for idx, step in enumerate(blueprint.steps):
            node = self._step_to_node(step, idx, 80 + idx * 260, 140)
            node_id = node.id
            graph.nodes.append(node)
            if previous_node_id:
                graph.edges.append(WorkflowEdge(
                    id=str(uuid.uuid4()),
                    source_node_id=previous_node_id,
                    source_port="result",
                    target_node_id=node_id,
                    target_port="next",
                ))
            previous_node_id = node_id

            self._append_nested_step_nodes(graph, step.if_true, node_id, "true", "branch_true", 80 + idx * 260, 20)
            self._append_nested_step_nodes(graph, step.if_false, node_id, "false", "branch_false", 80 + idx * 260, 260)
            sub_blueprint = step.inputs.get("sub_blueprint") if isinstance(step.inputs, dict) else None
            if hasattr(sub_blueprint, "steps"):
                self._append_nested_step_nodes(graph, sub_blueprint.steps, node_id, "each", "foreach_body", 80 + idx * 260, 480)

            if step.ui_format and step.ui_format != "silent":
                component_id = self._component_id_for_step(step)
                ui_node_id = f"{node_id}__ui"
                component = GUI_COMPONENTS.get(component_id, GUI_COMPONENTS["workflow.ui.live_stream"])
                graph.nodes.append(WorkflowNode(
                    id=ui_node_id,
                    type_id=component_id,
                    label=component["label"],
                    inputs={
                        "ui_format": step.ui_format,
                        "ui_target": step.ui_target,
                        "ui_title": step.ui_title,
                        "output_schema": step.output_schema,
                        "inline_citations": step.inline_citations,
                        "citation_source_key": step.citation_source_key,
                    },
                    x=80 + idx * 260,
                    y=340,
                ))
                graph.edges.append(WorkflowEdge(
                    id=str(uuid.uuid4()),
                    source_node_id=node_id,
                    source_port="result",
                    target_node_id=ui_node_id,
                    target_port="render",
                ))
        return graph

    def graph_to_blueprint(self, graph: WorkflowGraph) -> AIActionBlueprint:
        step_nodes = [node for node in graph.nodes if self.is_step_node(node)]
        child_node_ids = self._child_step_node_ids(graph)
        top_level_nodes = [node for node in step_nodes if node.id not in child_node_ids]
        step_nodes = self._ordered_step_nodes(top_level_nodes, graph.edges)
        ui_by_step = self._ui_nodes_by_step(graph)
        steps: List[ActionStep] = []

        for node in step_nodes:
            step = self._compile_step_node(node, graph, ui_by_step)
            steps.append(step)

        return AIActionBlueprint(
            name=graph.name,
            description=graph.description,
            mount_points=list(graph.mount_points),
            active_contexts=list(graph.active_contexts),
            expected_inputs=list(graph.expected_inputs),
            steps=steps,
        )

    def is_step_node(self, node: WorkflowNode) -> bool:
        return node.type_id in NODE_TYPE_TO_STEP_TYPE or node.inputs.get("step") is not None

    def is_ui_node(self, node: WorkflowNode) -> bool:
        return node.type_id in GUI_COMPONENTS

    def node_to_step(self, node: WorkflowNode) -> ActionStep:
        raw_step = dict(node.inputs.get("step") or {})
        if not raw_step:
            raw_step = {
                "step_id": node.id,
                "step_type": NODE_TYPE_TO_STEP_TYPE.get(node.type_id, "LLM_QUERY"),
                "node_type_id": node.type_id,
                "inputs": {},
                "output_key": "result",
            }
        raw_step["step_id"] = node.id
        raw_step["node_type_id"] = node.type_id
        if raw_step.get("step_type") != "LIBRARY_REF":
            raw_step["step_type"] = NODE_TYPE_TO_STEP_TYPE.get(node.type_id, raw_step.get("step_type", "LLM_QUERY"))
        return self._step_from_dict(raw_step)

    def apply_ui_node_to_step(self, step: ActionStep, ui_node: WorkflowNode):
        component = GUI_COMPONENTS.get(ui_node.type_id, {})
        step.ui_format = ui_node.inputs.get("ui_format") or component.get("ui_format", "silent")
        step.ui_target = ui_node.inputs.get("ui_target") or component.get("ui_target", "floating")
        step.ui_title = ui_node.inputs.get("ui_title") or step.ui_title
        if ui_node.inputs.get("output_schema") is not None:
            step.output_schema = ui_node.inputs.get("output_schema")
            step.llm_options["json_mode"] = True
        elif component.get("output_schema") is not None:
            step.output_schema = component.get("output_schema")
            step.llm_options["json_mode"] = True
        step.inline_citations = bool(ui_node.inputs.get("inline_citations", step.inline_citations))
        step.citation_source_key = ui_node.inputs.get("citation_source_key") or step.citation_source_key

    def create_step_node(self, step_type: str, index: int) -> WorkflowNode:
        step_id = f"step_{index}"
        node_type = STEP_TYPE_TO_NODE_TYPE.get(step_type, "workflow.llm_query")
        step = ActionStep(step_id=step_id, step_type=step_type, node_type_id=node_type, output_key=f"{step_id}_result")
        return WorkflowNode(
            id=step_id,
            type_id=node_type,
            label=step_id,
            inputs={"step": dataclasses.asdict(step)},
            x=120 + index * 32,
            y=120 + index * 32,
        )

    def create_step_node_from_type(self, node_type, index: int) -> WorkflowNode:
        step_id = f"step_{index}"
        step = ActionStep(
            step_id=step_id,
            step_type=node_type.step_type,
            node_type_id=node_type.id,
            inputs=copy.deepcopy(node_type.default_inputs),
            output_key=node_type.default_output_key or f"{step_id}_result",
            ui_format=node_type.default_ui_format or "silent",
        )
        return WorkflowNode(
            id=step_id,
            type_id=node_type.id,
            label=node_type.label,
            inputs={"step": dataclasses.asdict(step)},
            x=120 + index * 32,
            y=120 + index * 32,
        )

    def create_library_step_node(self, step_ref: str, library_step: ActionStep, index: int) -> WorkflowNode:
        step_id = f"step_{index}"
        node_type = library_step.node_type_id or STEP_TYPE_TO_NODE_TYPE.get(library_step.step_type, "workflow.library_ref")
        step = ActionStep(
            step_id=step_id,
            step_type="LIBRARY_REF",
            node_type_id=node_type,
            step_ref=step_ref,
            output_key=library_step.output_key or f"{step_id}_result",
        )
        return WorkflowNode(
            id=step_id,
            type_id=node_type,
            label=step_ref,
            inputs={"step": dataclasses.asdict(step)},
            x=120 + index * 32,
            y=120 + index * 32,
        )

    def create_ui_node(self, component_id: str, index: int) -> WorkflowNode:
        component = GUI_COMPONENTS.get(component_id, GUI_COMPONENTS["workflow.ui.live_stream"])
        return WorkflowNode(
            id=f"ui_{index}",
            type_id=component_id,
            label=component["label"],
            inputs={
                "ui_format": component["ui_format"],
                "ui_target": component["ui_target"],
                "ui_title": component["label"],
                "output_schema": component.get("output_schema"),
            },
            x=160 + index * 32,
            y=360 + index * 32,
        )

    def _ui_nodes_by_step(self, graph: WorkflowGraph) -> Dict[str, WorkflowNode]:
        nodes_by_id = {node.id: node for node in graph.nodes}
        result = {}
        for edge in graph.edges:
            target = nodes_by_id.get(edge.target_node_id)
            if target and self.is_ui_node(target):
                result[edge.source_node_id] = target
        return result

    def _compile_step_node(self, node: WorkflowNode, graph: WorkflowGraph, ui_by_step: Dict[str, WorkflowNode]) -> ActionStep:
        step = self.node_to_step(node)
        ui_node = ui_by_step.get(node.id)
        if ui_node:
            self.apply_ui_node_to_step(step, ui_node)

        true_nodes = self._ordered_port_children(node.id, "true", graph)
        false_nodes = self._ordered_port_children(node.id, "false", graph)
        foreach_nodes = self._ordered_port_children(node.id, "each", graph)

        if true_nodes:
            step.if_true = [self._compile_step_node(child, graph, ui_by_step) for child in true_nodes]
        if false_nodes:
            step.if_false = [self._compile_step_node(child, graph, ui_by_step) for child in false_nodes]
        if foreach_nodes:
            sub_steps = [self._compile_step_node(child, graph, ui_by_step) for child in foreach_nodes]
            step.inputs["sub_blueprint"] = AIActionBlueprint(
                name=f"{step.step_id} Body",
                description="Compiled foreach body.",
                steps=sub_steps,
            )
        return step

    def _ordered_port_children(self, source_node_id: str, source_port: str, graph: WorkflowGraph) -> List[WorkflowNode]:
        by_id = {node.id: node for node in graph.nodes if self.is_step_node(node)}
        direct = [
            by_id[edge.target_node_id]
            for edge in graph.edges
            if edge.source_node_id == source_node_id and edge.source_port == source_port and edge.target_node_id in by_id
        ]
        if not direct:
            return []
        descendants = set()
        for start in direct:
            descendants.update(self._collect_sequence_ids(start.id, graph))
        child_nodes = [node for node_id, node in by_id.items() if node_id in descendants]
        return self._ordered_step_nodes(child_nodes, graph.edges)

    def _collect_sequence_ids(self, start_id: str, graph: WorkflowGraph) -> set:
        result = set()
        next_by_source = {
            edge.source_node_id: edge.target_node_id
            for edge in graph.edges
            if edge.source_port == "result" and edge.target_port == "next"
        }
        current_id = start_id
        while current_id and current_id not in result:
            result.add(current_id)
            current_id = next_by_source.get(current_id)
        return result

    def _child_step_node_ids(self, graph: WorkflowGraph) -> set:
        child_ids = set()
        for edge in graph.edges:
            if edge.source_port in {"true", "false", "each"}:
                child_ids.update(self._collect_sequence_ids(edge.target_node_id, graph))
        return child_ids

    def _ordered_step_nodes(self, step_nodes: List[WorkflowNode], edges: List[WorkflowEdge]) -> List[WorkflowNode]:
        by_id = {node.id: node for node in step_nodes}
        next_by_source = {
            edge.source_node_id: edge.target_node_id
            for edge in edges
            if edge.source_port == "result" and edge.target_port == "next" and edge.source_node_id in by_id and edge.target_node_id in by_id
        }
        targeted = set(next_by_source.values())
        starts = [node for node in step_nodes if node.id not in targeted]
        ordered = []
        visited = set()
        for start in starts or step_nodes:
            current = start
            while current and current.id not in visited:
                ordered.append(current)
                visited.add(current.id)
                current = by_id.get(next_by_source.get(current.id))
        for node in step_nodes:
            if node.id not in visited:
                ordered.append(node)
        return ordered

    def _component_id_for_step(self, step: ActionStep) -> str:
        for component_id, component in GUI_COMPONENTS.items():
            if component["ui_format"] == step.ui_format:
                return component_id
        return "workflow.ui.live_stream"

    def _safe_id(self, name: str) -> str:
        return "".join(ch.lower() if ch.isalnum() else "_" for ch in (name or "workflow")).strip("_") or "workflow"

    def _step_to_node(self, step: ActionStep, idx: int, x: float, y: float) -> WorkflowNode:
        return WorkflowNode(
            id=step.step_id or f"step_{idx + 1}",
            type_id=step.node_type_id or STEP_TYPE_TO_NODE_TYPE.get(step.step_type, "workflow.llm_query"),
            label=step.step_id or f"Step {idx + 1}",
            inputs={"step": dataclasses.asdict(step)},
            x=x,
            y=y,
        )

    def _append_nested_step_nodes(self, graph: WorkflowGraph, steps: List[ActionStep], parent_id: str, source_port: str, target_port: str, x: float, y: float):
        previous_id = None
        for idx, step in enumerate(steps or []):
            node = self._step_to_node(step, idx, x + 220 + idx * 240, y)
            graph.nodes.append(node)
            if idx == 0:
                graph.edges.append(WorkflowEdge(str(uuid.uuid4()), parent_id, source_port, node.id, target_port))
            elif previous_id:
                graph.edges.append(WorkflowEdge(str(uuid.uuid4()), previous_id, "result", node.id, "next"))
            previous_id = node.id
            self._append_nested_step_nodes(graph, step.if_true, node.id, "true", "branch_true", node.x, y - 120)
            self._append_nested_step_nodes(graph, step.if_false, node.id, "false", "branch_false", node.x, y + 120)

    def _step_from_dict(self, data: dict) -> ActionStep:
        def parse_children(items):
            return [self._step_from_dict(item) if isinstance(item, dict) else item for item in (items or [])]

        clean = dict(data)
        clean["if_true"] = parse_children(clean.get("if_true", []))
        clean["if_false"] = parse_children(clean.get("if_false", []))
        return ActionStep(**clean)
