from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, Iterable, List, Optional, Type

from PySide6.QtCore import Qt, QRectF
from PySide6.QtGui import QColor, QPainter, QPen

from core.events.domains.ontology_events import OntologyEvent, OntologyEventPayload
from core.models.ontology_model import EntityType, RelationType
from core.ontology.registry import OntologyRegistry
from gui.theme.theme import ThemeManager


@dataclass(frozen=True)
class UIActionSpec:
    action_id: str
    label: str
    tooltip: str = ""
    icon: str = ""


def _theme_color(key: str, fallback: str) -> QColor:
    try:
        return QColor(ThemeManager().get_theme().get(key, fallback))
    except Exception:
        return QColor(fallback)


class NodeUIDelegate:
    type_key = "*"

    def bounding_rect(self, node) -> QRectF:
        return node.rect()

    def paint(self, node, painter: QPainter, option, widget=None):
        node.paint_base_shell(painter, option, widget)

    def get_context_menu_actions(self, node) -> List[UIActionSpec]:
        return []

    def get_custom_widgets(self, node) -> List[object]:
        return []

    def get_metrics_display(self, node) -> List[dict]:
        return node.collect_ontology_metric_badges()

    def related_nodes_for_collapse(self, node, view) -> Iterable[str]:
        graph_children = (getattr(node, "entity_properties", {}) or {}).get("graph_children") or {}
        return graph_children.get("child_ids", [])

    def handle_action(self, node, action_id: str):
        if action_id == "entity.generate_counterargument":
            node.bus.ontology_action_requested.emit(
                OntologyEvent.BLUEPRINT_REQUESTED,
                OntologyEventPayload(
                    node_id=node.node_id,
                    entity_type=getattr(node, "entity_type", None),
                    blueprint_id="Generate Counter-Argument",
                    data={"source": "workspace_node_context_menu"},
                ),
            )
            return True
        return False


class ClaimNodeUIDelegate(NodeUIDelegate):
    type_key = EntityType.CLAIM.value

    def get_context_menu_actions(self, node) -> List[UIActionSpec]:
        return [
            UIActionSpec("entity.toggle_children", "Toggle Evidence", "Collapse or expand evidence connected to this claim", "▾"),
            UIActionSpec("entity.generate_counterargument", "Generate Counter-Argument", "Run the counter-argument blueprint for this claim", "!"),
        ]

    def related_nodes_for_collapse(self, node, view) -> Iterable[str]:
        related = set(super().related_nodes_for_collapse(node, view))
        for edge in getattr(view, "edges", []):
            if getattr(edge.dest_node, "node_id", None) != node.node_id:
                continue
            if edge.relation_type not in {RelationType.SUPPORTS.value, RelationType.REFUTES.value, RelationType.CONTRADICTS.value}:
                continue
            source = getattr(edge, "source_node", None)
            if source and getattr(source, "entity_type", None) == EntityType.EVIDENCE.value:
                related.add(source.node_id)
        return sorted(related)

    def handle_action(self, node, action_id: str):
        if action_id == "entity.toggle_children":
            node.bus.ontology_action_requested.emit(
                OntologyEvent.NODE_COLLAPSE_REQUESTED,
                OntologyEventPayload(node_id=node.node_id, entity_type=node.entity_type),
            )
            return True
        return super().handle_action(node, action_id)


class EdgeUIDelegate:
    type_key = "*"

    def bounding_rect(self, edge) -> QRectF:
        return edge.base_bounding_rect()

    def shape(self, edge):
        return edge.base_shape()

    def paint(self, edge, painter: QPainter, option, widget=None):
        edge.paint_base_shell(painter, option, widget, self.pen(edge))

    def pen(self, edge) -> QPen:
        style = self.style_for(edge)
        width = max(1, int(style.get("weight") or edge.weight or 2))
        width += 2 if edge.isSelected() else 0
        pen = QPen(QColor(style.get("color") or edge.base_color), width, self._pen_style(style.get("line_style")))
        if style.get("line_style") == "dash":
            pen.setDashPattern([6, 4])
        elif style.get("line_style") == "dot":
            pen.setDashPattern([2, 4])
        elif style.get("line_style") == "dashdot":
            pen.setDashPattern([7, 4, 2, 4])
        return pen

    def center_icon(self, edge) -> str:
        return str(self.style_for(edge).get("icon") or "")

    def default_label(self, edge) -> str:
        return str(self.style_for(edge).get("label") or "")

    def get_context_menu_actions(self, edge) -> List[UIActionSpec]:
        return []

    def style_for(self, edge) -> dict:
        props = dict(getattr(edge, "relation_properties", {}) or {})
        try:
            blueprint = OntologyRegistry().get_relation_blueprint(getattr(edge, "relation_type", "relation.basic"))
            defaults = blueprint.build_default_properties()
            visual = getattr(blueprint, "visual_style", None)
        except Exception:
            blueprint = None
            defaults = {}
            visual = None
        style = {
            "label": defaults.get("label") or getattr(blueprint, "display_name", ""),
            "color": getattr(visual, "color", None) or defaults.get("color") or getattr(edge, "base_color", QColor("#888888")).name(),
            "line_style": getattr(visual, "line_style", None) or defaults.get("line_style") or "solid",
            "icon": getattr(visual, "icon", None) or defaults.get("icon") or "",
            "weight": getattr(visual, "weight", None) or defaults.get("weight") or getattr(edge, "weight", 2),
        }
        for key in ("label", "color", "line_style", "icon", "weight"):
            if props.get(key) not in (None, ""):
                style[key] = props[key]
        return style

    def _pen_style(self, line_style) -> Qt.PenStyle:
        return {
            "dash": Qt.PenStyle.DashLine,
            "dot": Qt.PenStyle.DotLine,
            "dashdot": Qt.PenStyle.DashDotLine,
        }.get(str(line_style or "solid").lower(), Qt.PenStyle.SolidLine)


class SupportsEdgeUIDelegate(EdgeUIDelegate):
    type_key = RelationType.SUPPORTS.value

    def pen(self, edge) -> QPen:
        width = max(2, edge.weight) + (2 if edge.isSelected() else 0)
        return QPen(_theme_color("success", "#00cc66"), width, Qt.PenStyle.SolidLine)

    def center_icon(self, edge) -> str:
        return "+"


class RefutesEdgeUIDelegate(EdgeUIDelegate):
    type_key = RelationType.REFUTES.value

    def pen(self, edge) -> QPen:
        width = max(2, edge.weight) + (2 if edge.isSelected() else 0)
        pen = QPen(_theme_color("error", "#ff4444"), width, Qt.PenStyle.DashLine)
        pen.setDashPattern([6, 4])
        return pen

    def center_icon(self, edge) -> str:
        return "-"


class ContradictsEdgeUIDelegate(RefutesEdgeUIDelegate):
    type_key = RelationType.CONTRADICTS.value


class OntologyUIRegistry:
    _instance: Optional["OntologyUIRegistry"] = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance.node_delegates = {}
            cls._instance.edge_delegates = {}
            cls._instance.register_node_delegate(NodeUIDelegate)
            cls._instance.register_node_delegate(ClaimNodeUIDelegate)
            cls._instance.register_edge_delegate(EdgeUIDelegate)
            cls._instance.register_edge_delegate(SupportsEdgeUIDelegate)
            cls._instance.register_edge_delegate(RefutesEdgeUIDelegate)
            cls._instance.register_edge_delegate(ContradictsEdgeUIDelegate)
        return cls._instance

    def register_node_delegate(self, delegate_cls: Type[NodeUIDelegate]):
        self.node_delegates[delegate_cls.type_key] = delegate_cls()

    def register_edge_delegate(self, delegate_cls: Type[EdgeUIDelegate]):
        self.edge_delegates[delegate_cls.type_key] = delegate_cls()

    def node_delegate_for(self, type_key: str) -> NodeUIDelegate:
        return self.node_delegates.get(type_key) or self.node_delegates["*"]

    def edge_delegate_for(self, type_key: str) -> EdgeUIDelegate:
        return self.edge_delegates.get(type_key) or self.edge_delegates["*"]
