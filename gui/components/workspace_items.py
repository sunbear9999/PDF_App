# gui/components/workspace_items.py
import uuid
from PySide6.QtWidgets import (QGraphicsRectItem, QGraphicsTextItem, QGraphicsLineItem, QGraphicsItem,
                             QGraphicsProxyWidget, QHBoxLayout, QMenu, QToolButton, QWidget)
from PySide6.QtCore import Qt, QLineF, QRectF
from PySide6.QtGui import QColor, QPen, QBrush, QFont, QTextDocument, QPainter, QTextCursor

from gui.theme.theme import ThemeManager
from core.events.event_bus import EventBus
from core.events.domains.workspace_events import WorkspaceEvent, WorkspaceEventPayload, WorkspaceIntent, WorkspacePayload
from core.services.workspace_registries import build_default_workspace_node_type_registry, infer_workspace_node_type_id
from core.models.ontology_model import EntityType
from core.ontology.registry import OntologyRegistry

def get_text_color_for_bg(bg_color):
    try:
        if isinstance(bg_color, (tuple, list)):
            c = QColor(int(bg_color[0]*255), int(bg_color[1]*255), int(bg_color[2]*255))
        else:
            c = QColor(bg_color)
        brightness = (c.red() * 299 + c.green() * 587 + c.blue() * 114) / 1000
        return "#000000" if brightness > 140 else "#ffffff"
    except:
        return "#ffffff"

class EditableTextItem(QGraphicsTextItem):
    def __init__(self, parent_node):
        super().__init__(parent_node)
        self.parent_node = parent_node
        self.bus = EventBus.get_instance()

    def focusOutEvent(self, event):
        super().focusOutEvent(event)
        self.setTextInteractionFlags(Qt.TextInteractionFlag.NoTextInteraction)
        new_text = self.toPlainText()

        if isinstance(self.parent_node, Edge):
            if new_text != self.parent_node.label_text:
                self.bus.workspace_action_requested.emit(
                    WorkspaceIntent.EDGE_TEXT_COMMITTED,
                    WorkspacePayload(edge_id=self.parent_node.edge_id, text=new_text),
                )
            return

        current_note = self.parent_node.editable_note_text()
        if new_text != current_note:
            self.bus.workspace_action_requested.emit(
                WorkspaceIntent.NODE_TEXT_COMMITTED,
                WorkspacePayload(node_id=self.parent_node.node_id, text=new_text),
            )

class Edge(QGraphicsLineItem):
    def __init__(
        self,
        source_node,
        dest_node,
        label_text="",
        edge_id=None,
        color="#888888",
        weight=2,
        relation_type="relation.basic",
        evidence_ids=None,
        relation_properties=None,
        relation_state=None,
    ):
        super().__init__()
        self.source_node = source_node
        self.dest_node = dest_node
        self.label_text = label_text
        self.edge_id = edge_id or str(uuid.uuid4())
        self.base_color = QColor(color)
        self.weight = weight
        self.relation_type = relation_type or "relation.basic"
        self.evidence_ids = list(evidence_ids or [])
        self.relation_properties = dict(relation_properties or {})
        self.relation_state = dict(relation_state or {})
        self.bus = EventBus.get_instance()

        self.setZValue(-1)
        self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsSelectable, True)
        self.setPen(QPen(self.base_color, self.weight, Qt.PenStyle.SolidLine))

        self.text_item = EditableTextItem(self)
        self.text_item.setPlainText(self.label_text)
        self.text_item.setDefaultTextColor(QColor("#ffffff"))
        self.text_item.setFont(QFont("Arial", 10, QFont.Weight.Bold))
        self.text_item.setAcceptedMouseButtons(Qt.MouseButton.NoButton)

        self.source_node.add_edge(self)
        self.dest_node.add_edge(self)
        self.update_position()

    def shape(self):
        from PySide6.QtGui import QPainterPath, QPainterPathStroker
        path = QPainterPath()
        path.moveTo(self.line().p1())
        path.lineTo(self.line().p2())

        stroker = QPainterPathStroker()
        stroker.setWidth(15)
        stroked_path = stroker.createStroke(path)

        if self.text_item.scene():
            text_rect = self.text_item.mapRectToParent(self.text_item.boundingRect())
            stroked_path.addRect(text_rect)
        return stroked_path

    def update_position(self):
        start = self.source_node.mapToScene(self.source_node.rect().center())
        end = self.dest_node.mapToScene(self.dest_node.rect().center())
        self.setLine(QLineF(start, end))

        center_x = (start.x() + end.x()) / 2
        center_y = (start.y() + end.y()) / 2
        text_rect = self.text_item.boundingRect()
        self.text_item.setPos(center_x - text_rect.width() / 2, center_y - text_rect.height() / 2 - 10)

    def trigger_edit(self):
        self.text_item.setTextInteractionFlags(Qt.TextInteractionFlag.TextEditorInteraction)
        self.text_item.setFocus()
        cursor = self.text_item.textCursor()
        cursor.movePosition(QTextCursor.MoveOperation.End)
        self.text_item.setTextCursor(cursor)

    def trigger_color_change(self):
        self.bus.workspace_action_requested.emit(WorkspaceIntent.EDGE_COLOR_REQUEST, WorkspacePayload(edge_id=self.edge_id))

    def trigger_weight_change(self):
        self.bus.workspace_action_requested.emit(WorkspaceIntent.EDGE_WEIGHT_REQUEST, WorkspacePayload(edge_id=self.edge_id))

    def trigger_details(self):
        self.bus.workspace_action_requested.emit(
            WorkspaceIntent.EDGE_DETAILS_REQUEST,
            WorkspacePayload(
                edge_id=self.edge_id,
                extra={
                    "relation_type": self.relation_type,
                    "label": self.label_text,
                    "evidence_ids": list(self.evidence_ids or []),
                    "properties": dict(self.relation_properties or {}),
                    "state": dict(self.relation_state or {}),
                },
            ),
        )

    def mouseDoubleClickEvent(self, event):
        self.trigger_details()
        if event:
            event.accept()

    def itemChange(self, change, value):
        if change == QGraphicsItem.GraphicsItemChange.ItemSelectedHasChanged:
            if self.isSelected():
                self.setPen(QPen(self.base_color, self.weight + 2, Qt.PenStyle.SolidLine))
            else:
                self.setPen(QPen(self.base_color, self.weight, Qt.PenStyle.SolidLine))
        return super().itemChange(change, value)

class InPlaceTextItem(QGraphicsTextItem):
    def __init__(self, node, text=""):
        super().__init__(text, node)
        self.node = node

    def keyPressEvent(self, event):
        if event.key() == Qt.Key.Key_Return and not (event.modifiers() & Qt.KeyboardModifier.ShiftModifier):
            self.clearFocus()
            return
        super().keyPressEvent(event)

    def focusOutEvent(self, event):
        super().focusOutEvent(event)
        self.node.finish_in_place_edit()

class ResizeHandle(QGraphicsRectItem):
    def __init__(self, parent):
        super().__init__(0, 0, 16, 16, parent)
        self.setBrush(QBrush(QColor(100, 100, 100, 255)))
        self.setPen(QPen(QColor(255, 255, 255, 200), 1))
        self.setCursor(Qt.CursorShape.SizeFDiagCursor)
        self._is_resizing = False
        self._start_pos = None
        self.bus = EventBus.get_instance()

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self._is_resizing = True
            self._start_pos = self.parentItem().mapFromScene(event.scenePos())
            self._start_w = self.parentItem().base_width
            self._start_h = self.parentItem().base_height
            self.bus.workspace_action_requested.emit(WorkspaceIntent.UNDO_CHECKPOINT_REQUESTED, WorkspacePayload())
            event.accept()
        else:
            super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if self._is_resizing:
            current_pos = self.parentItem().mapFromScene(event.scenePos())
            delta = current_pos - self._start_pos
            new_w = max(50, self._start_w + delta.x())
            new_h = max(30, self._start_h + delta.y())
            self.parentItem().update_size(new_w, new_h)
            event.accept()
        else:
            super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        if self._is_resizing and event.button() == Qt.MouseButton.LeftButton:
            self._is_resizing = False
            event.accept()
        else:
            super().mouseReleaseEvent(event)

class Node(QGraphicsRectItem):
    def __init__(
        self,
        node_id,
        quote,
        note,
        color=None,
        is_custom=False,
        width=150,
        height=80,
        pdf_path=None,
        page_num=None,
        manual_font_size=None,
        highlight_id=None,
        node_origin="human",
        is_verified=0,
        original_text=None,
        node_type_id=None,
        node_type_registry=None,
        action_registry=None,
        ontology_registry=None,
        entity_type=None,
        source_id=None,
        entity_properties=None,
        entity_state=None,
    ):
        super().__init__(0, 0, width, height)
        self.node_id = node_id
        self.highlight_id = highlight_id
        self.is_custom = is_custom
        self.quote = quote if quote else ""
        self.note = note if note else ""
        self.node_origin = node_origin
        self.is_verified = bool(is_verified)
        self.original_text = original_text if original_text is not None else note
        self.pdf_path = pdf_path
        self.page_num = page_num
        self.manual_font_size = manual_font_size
        self.bus = EventBus.get_instance()
        self.node_type_registry = node_type_registry or build_default_workspace_node_type_registry()
        self.action_registry = action_registry
        self.ontology_registry = ontology_registry or OntologyRegistry()
        self.entity_type = entity_type or self._infer_entity_type(node_type_id)
        self.source_id = source_id
        self.entity_properties = dict(entity_properties or {})
        self.entity_state = dict(entity_state or {})
        self.entity_properties.setdefault("quote", self.quote)
        self.entity_properties.setdefault("exact_text", self.quote)
        self.entity_properties.setdefault("note_text", self.note)
        self.entity_properties.setdefault("text", self.note if not self.is_source_backed_entity() else self.quote)
        self.entity_properties.setdefault("pdf_path", pdf_path)
        self.entity_properties.setdefault("page_num", page_num)
        self.entity_properties.setdefault("highlight_id", highlight_id)
        if source_id:
            self.entity_properties.setdefault("source_id", source_id)
        self.entity_state.setdefault("is_verified", bool(is_verified))
        self.entity_state.setdefault("ai_generated", node_origin == "ai")
        self.entity_state.setdefault("origin", node_origin)
        self._normalize_source_backed_note()

        theme = ThemeManager().get_theme()
        if not color or color == "#333333":
            color = theme['bg_panel']

        self.color = color if isinstance(color, str) else QColor(int(color[0]*255), int(color[1]*255), int(color[2]*255)).name()
        self.node_type_id = node_type_id or infer_workspace_node_type_id(self)
        self.edges = []
        self.base_width = width
        self.base_height = height
        self.is_hovered = False
        self.tag_colors = []
        self.tag_badges = []
        self._tag_colors_loaded = False
        self.metric_badges = []

        self.setBrush(QBrush(QColor(self.color)))
        self.setPen(QPen(QColor("#555555"), 2))
        self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsMovable)
        self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsSelectable)
        self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemSendsGeometryChanges)
        self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemClipsChildrenToShape, False)
        self.setAcceptHoverEvents(True)

        self.text_item = QGraphicsTextItem(self)
        self.text_item.setZValue(2)
        self.text_item.setTextInteractionFlags(Qt.TextInteractionFlag.NoTextInteraction)
        self.text_item.setAcceptedMouseButtons(Qt.MouseButton.NoButton)

        self.note_edit_item = InPlaceTextItem(self)
        self.note_edit_item.setZValue(25)
        self.note_edit_item.setTextInteractionFlags(Qt.TextInteractionFlag.NoTextInteraction)
        self.note_edit_item.hide()
        self.resize_handle = ResizeHandle(self)

        self.toolbar_widget = QWidget()
        self.toolbar_widget.setStyleSheet("background: transparent;")

        t_layout = QHBoxLayout(self.toolbar_widget)
        t_layout.setContentsMargins(0, 0, 0, 0)
        t_layout.setSpacing(4)

        self.toolbar_buttons = {}
        buttons = self._build_toolbar_buttons(t_layout)

        for btn in buttons:
            if btn == getattr(self, "btn_verify", None) and not self.is_verified:
                btn.setStyleSheet(self._button_stylesheet(theme, alert=True))
            else:
                btn.setStyleSheet(self._button_stylesheet(theme))

        self.proxy_toolbar = QGraphicsProxyWidget(self)
        self.proxy_toolbar.setWidget(self.toolbar_widget)
        self.proxy_toolbar.setZValue(30)
        self.proxy_toolbar.hide()

        if self.should_show_verify_action():
            self.btn_verify.clicked.connect(self.trigger_verify)

        self.refresh_layout()

    def _build_toolbar_buttons(self, layout):
        callbacks = {
            "node.edit": self.trigger_edit,
            "entity.edit": self.trigger_edit,
            "node.color": self.trigger_color_change,
            "entity.color": self.trigger_color_change,
            "node.font_size": self.trigger_font_size_change,
            "entity.resize": self.trigger_font_size_change,
            "entity.change_type": self.trigger_change_type,
            "node.connect": self.trigger_connect,
            "entity.connect": self.trigger_connect,
            "entity.toggle_children": self.trigger_toggle_children,
            "node.jump": self.trigger_jump,
            "entity.jump_source": self.trigger_jump,
            "node.copy_citation": self.trigger_copy_citation,
            "entity.copy_citation": self.trigger_copy_citation,
            "node.verify": self.trigger_verify,
            "entity.verify": self.trigger_verify,
        }
        action_ids = self._resolve_action_ids()
        if not self.has_source_reference():
            action_ids = [action_id for action_id in action_ids if action_id not in {"node.jump", "node.copy_citation", "entity.jump_source", "entity.copy_citation"}]

        primary_ids = [
            action_id for action_id in [
                "entity.edit", "node.edit",
                "entity.connect", "node.connect",
                "entity.toggle_children",
                "entity.change_type",
                "entity.jump_source", "node.jump",
                "entity.verify", "node.verify",
            ]
            if action_id in action_ids
        ]
        primary_ids = self._dedupe(primary_ids)
        menu_ids = [action_id for action_id in action_ids if action_id not in primary_ids]

        buttons = []
        for action_id in primary_ids:
            action_def = self._get_action_definition(action_id)
            label = self._action_button_text(action_id, action_def)
            tooltip = self._action_tooltip(action_id, action_def)
            callback = callbacks.get(action_id) or (lambda checked=False, a_id=action_id: self.trigger_generic_action(a_id))
            btn = QToolButton()
            btn.setText(label)
            btn.setToolTip(tooltip)
            btn.setObjectName("NodeToolButton")
            btn.setAutoRaise(True)
            btn.setFixedSize(self._button_width(action_id), 28)
            btn.clicked.connect(callback)
            layout.addWidget(btn)
            buttons.append(btn)
            self.toolbar_buttons[action_id] = btn
            if action_id == "node.jump":
                self.btn_jump = btn
            elif action_id == "entity.jump_source":
                self.btn_jump = btn
            elif action_id == "node.verify":
                self.btn_verify = btn
            elif action_id == "entity.verify":
                self.btn_verify = btn
        if menu_ids:
            more_btn = QToolButton()
            more_btn.setText("⋯")
            more_btn.setToolTip("More actions")
            more_btn.setObjectName("NodeToolButton")
            more_btn.setAutoRaise(True)
            more_btn.setFixedSize(30, 28)
            menu = QMenu(more_btn)
            for action_id in menu_ids:
                action_def = self._get_action_definition(action_id)
                label = self._action_tooltip(action_id, action_def)
                action = menu.addAction(label)
                callback = callbacks.get(action_id)
                if callback:
                    action.triggered.connect(callback)
                else:
                    action.triggered.connect(lambda checked=False, a_id=action_id: self.trigger_generic_action(a_id))
            more_btn.setMenu(menu)
            more_btn.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
            layout.addWidget(more_btn)
            buttons.append(more_btn)
        return buttons

    def _action_button_text(self, action_id, action_def):
        if action_id in {"entity.verify", "node.verify"}:
            return "Verified" if self.is_verified else "Verify"
        label = action_def.label if action_def and action_def.label else self._action_tooltip(action_id, action_def)
        icon = self._action_icon(action_id, action_def)
        if action_id in {"entity.edit", "node.edit"}:
            return f"{icon} Edit"
        if action_id in {"entity.connect", "node.connect"}:
            return f"{icon} Link"
        if action_id == "entity.toggle_children":
            return "Show" if self.entity_state.get("children_collapsed") else "Hide"
        if action_id == "entity.change_type":
            return f"{icon} Type"
        if action_id in {"entity.jump_source", "node.jump"}:
            return f"{icon} Source"
        return label

    def _button_width(self, action_id):
        if action_id in {"entity.connect", "node.connect", "entity.jump_source", "node.jump", "entity.verify", "node.verify", "entity.toggle_children"}:
            return 66
        if action_id in {"entity.change_type", "entity.edit", "node.edit"}:
            return 56
        return 44

    def _get_action_definition(self, action_id):
        try:
            return self.ontology_registry.get_action_definition(action_id)
        except Exception:
            return None

    def _action_icon(self, action_id, action_def):
        if action_id in {"entity.verify", "node.verify"}:
            return "✓" if self.is_verified else "!"
        if action_def and action_def.icon:
            return action_def.icon
        fallback = {
            "node.edit": "✎",
            "node.color": "●",
            "node.font_size": "↕",
            "node.connect": "⛓",
            "entity.toggle_children": "▸" if self.entity_state.get("children_collapsed") else "▾",
            "node.jump": "↗",
            "node.copy_citation": "C",
        }
        return fallback.get(action_id, "•")

    def _action_tooltip(self, action_id, action_def):
        if action_def and action_def.tooltip:
            return action_def.tooltip
        if action_def:
            return action_def.label
        return action_id

    def _resolve_action_ids(self):
        try:
            blueprint = self.ontology_registry.get_entity_blueprint(self.entity_type)
            action_ids = list(blueprint.action_ids)
            if self.has_source_reference():
                action_ids.extend(["entity.jump_source", "entity.copy_citation"])
        except Exception:
            node_type = self.node_type_registry.resolve(self)
            action_ids = list(node_type.action_ids)

        if self.should_show_verify_action() and "entity.verify" not in action_ids and "node.verify" not in action_ids:
            action_ids.append("entity.verify")
        return self._dedupe(action_ids)

    def _dedupe(self, items):
        seen = set()
        result = []
        for item in items:
            if item in seen:
                continue
            seen.add(item)
            result.append(item)
        return result

    def _infer_entity_type(self, node_type_id):
        if node_type_id and str(node_type_id).startswith("entity."):
            return node_type_id
        if self.pdf_path or self.highlight_id or self.quote:
            return EntityType.QUOTE.value
        return EntityType.TEXT.value

    def should_show_verify_action(self):
        return not bool(self.entity_state.get("is_verified", self.is_verified))

    def refresh_verify_button(self):
        if not hasattr(self, "btn_verify"):
            return
        theme = ThemeManager().get_theme()
        if self.is_verified:
            self.btn_verify.setText("✓")
            self.btn_verify.setToolTip("Verified")
            self.btn_verify.setStyleSheet(self._button_stylesheet(theme))
        else:
            self.btn_verify.setText("!")
            self.btn_verify.setToolTip("Verify entity")
            self.btn_verify.setStyleSheet(self._button_stylesheet(theme, alert=True))

    def refresh_child_button(self):
        btn = self.toolbar_buttons.get("entity.toggle_children") if hasattr(self, "toolbar_buttons") else None
        if not btn:
            return
        btn.setText(self._action_button_text("entity.toggle_children", self._get_action_definition("entity.toggle_children")))
        btn.setToolTip(self._action_tooltip("entity.toggle_children", self._get_action_definition("entity.toggle_children")))

    def _button_stylesheet(self, theme, alert=False):
        if alert:
            return """
                QPushButton#NodeToolButton {
                    background-color: #b33232;
                    color: #ffffff;
                    border-radius: 5px;
                    padding: 0;
                    font-size: 13px;
                    font-weight: 700;
                    border: 1px solid #ff6565;
                }
                QPushButton#NodeToolButton:hover { background-color: #cf3f3f; }
                QToolButton#NodeToolButton {
                    background-color: #b33232;
                    color: #ffffff;
                    border-radius: 6px;
                    padding: 0 6px;
                    font-size: 11px;
                    font-weight: 700;
                    border: 1px solid #ff6565;
                }
                QToolButton#NodeToolButton:hover { background-color: #cf3f3f; }
            """
        return f"""
            QPushButton#NodeToolButton {{
                background-color: {theme['bg_panel']};
                color: {theme['text_main']};
                border-radius: 5px;
                padding: 0;
                font-size: 13px;
                font-weight: 700;
                border: 1px solid {theme['border']};
            }}
            QPushButton#NodeToolButton:hover {{
                background-color: {theme['accent']};
                color: #ffffff;
                border-color: {theme['accent_hover']};
            }}
            QToolButton#NodeToolButton {{
                background-color: rgba(255, 255, 255, 34);
                color: #ffffff;
                border-radius: 6px;
                padding: 0 6px;
                font-size: 11px;
                font-weight: 700;
                border: 1px solid rgba(255, 255, 255, 86);
            }}
            QToolButton#NodeToolButton:hover {{
                background-color: {theme['accent']};
                border-color: {theme['accent_hover']};
            }}
            QToolButton#NodeToolButton::menu-indicator {{ image: none; width: 0; }}
        """

    def trigger_verify(self):
        self.bus.workspace_action_requested.emit(WorkspaceIntent.NODE_VERIFY_TOGGLE, WorkspacePayload(node_id=self.node_id))

    def trigger_toggle_children(self):
        self.bus.workspace_action_requested.emit(WorkspaceIntent.NODE_CHILDREN_TOGGLE, WorkspacePayload(node_id=self.node_id))

    def has_collapsible_children(self):
        return bool((self.entity_properties.get("graph_children") or {}).get("child_ids"))

    def has_source_reference(self):
        return bool(self.source_id or self.pdf_path or self.highlight_id or (self.quote and not self.is_custom))

    def is_source_backed_entity(self):
        return self.entity_type in {EntityType.QUOTE.value, EntityType.EVIDENCE.value} or self.has_source_reference()

    def quote_text(self):
        return (
            self.entity_properties.get("exact_text")
            or self.entity_properties.get("quote")
            or self.quote
            or ""
        )

    def editable_note_text(self):
        return self.entity_properties.get("note_text", self.note) or ""

    def _normalize_source_backed_note(self):
        if not self.is_source_backed_entity():
            return
        quote_text = self.quote_text().strip()
        note_text = self.entity_properties.get("note_text")
        if note_text is None:
            note_text = self.note
        if quote_text and str(note_text or "").strip() == quote_text:
            note_text = ""
        self.note = str(note_text or "")
        self.entity_properties["note_text"] = self.note
        self.entity_properties["quote"] = self.quote_text()
        self.entity_properties["exact_text"] = self.quote_text()
        self.entity_properties["text"] = self.quote_text()

    def mousePressEvent(self, event):
        if event and event.button() == Qt.MouseButton.LeftButton:
            clicked_tag = self._tag_name_at_pos(event.pos())
            if clicked_tag:
                self.bus.workspace_action_requested.emit(WorkspaceIntent.TAG_FILTER_APPLY, WorkspacePayload(tag_name=clicked_tag))
                event.accept()
                return

        self.bus.workspace_action_requested.emit(WorkspaceIntent.NODE_PRESSED, WorkspacePayload(node_id=self.node_id))
        super().mousePressEvent(event)

    def trigger_jump(self):
        self.bus.workspace_action_requested.emit(WorkspaceIntent.NODE_JUMP_REQUEST, WorkspacePayload(node_id=self.node_id))

    def trigger_copy_citation(self):
        self.bus.workspace_action_requested.emit(WorkspaceIntent.NODE_CITATION_COPY, WorkspacePayload(node_id=self.node_id))

    def add_edge(self, edge):
        self.edges.append(edge)

    def calculate_best_fit(self, text, max_w, max_h):
        if not text: return 12, ""
        doc = QTextDocument()
        doc.setTextWidth(max_w)

        def check_fit(text_to_test, size_to_test):
            doc.setDefaultFont(QFont("Arial", size_to_test, QFont.Weight.Bold))
            doc.setPlainText(text_to_test)
            return doc.size().height() <= max_h

        if self.manual_font_size is not None:
            if check_fit(text, self.manual_font_size):
                return self.manual_font_size, text
            return self.manual_font_size, self.truncate_to_fit(text, max_w, max_h, self.manual_font_size)

        for size in range(24, 7, -1):
            if check_fit(text, size):
                return size, text
        return 8, self.truncate_to_fit(text, max_w, max_h, 8)

    def truncate_to_fit(self, text, max_w, max_h, font_size):
        if not text: return ""
        doc = QTextDocument()
        doc.setTextWidth(max_w)
        doc.setDefaultFont(QFont("Arial", font_size, QFont.Weight.Bold))

        words = text.split()
        low, high = 0, len(words)
        best = ""

        while low <= high:
            mid = (low + high) // 2
            test_text = " ".join(words[:mid]) + "..." if mid < len(words) else " ".join(words)
            doc.setPlainText(test_text)
            if doc.size().height() <= max_h:
                best = test_text
                low = mid + 1
            else:
                high = mid - 1
        return best if best else "..."

    def refresh_layout(self):
        margin = 8
        status_h = self._status_band_height()
        metric_badges = self._collect_metric_badges()
        metric_h = self._metric_badge_band_height(metric_badges)
        text_color = QColor(get_text_color_for_bg(self.color))
        expanded_text = ""
        editing_note = self.is_note_editing()
        self.text_item.setZValue(20)
        self.text_item.setVisible(True)

        expanded_text = self.build_ontology_display_text(expanded=True)
        collapsed_text = self.build_ontology_display_text(expanded=False)

        if editing_note:
            needed_width = max(self.base_width, 340)
            toolbar_width = self.toolbar_widget.sizeHint().width()
            needed_width = max(needed_width, toolbar_width + (margin * 2))
            content_w = max(10, needed_width - (margin * 2))
            font_size = self.manual_font_size if self.manual_font_size else 12
            editor_font = QFont("Arial", font_size)
            self.note_edit_item.setVisible(True)
            self.note_edit_item.setPos(margin, margin + status_h + metric_h)
            self.note_edit_item.setTextWidth(content_w)
            self.note_edit_item.setFont(editor_font)
            self.note_edit_item.setDefaultTextColor(text_color)
            editor_height = max(42, self.note_edit_item.document().size().height() + 8)

            if self.is_source_backed_entity() and self.quote_text():
                quote_text = self.build_reference_display_text(expanded=True)
                self.text_item.setVisible(True)
                self.text_item.setPos(margin, margin + status_h + metric_h + editor_height + 8)
                self.text_item.setTextWidth(content_w)
                quote_font = QFont("Arial", max(9, font_size - 1))
                quote_font.setItalic(True)
                self.text_item.setFont(quote_font)
                self.text_item.setDefaultTextColor(text_color)
                self.text_item.setPlainText(quote_text)
                display_height = self.text_item.document().size().height()
            else:
                self.text_item.hide()
                display_height = 0

            needed_height = max(self.base_height, editor_height + display_height + (margin * 2) + status_h + metric_h + 58)
            self.setRect(0, 0, needed_width, needed_height)
            self.proxy_toolbar.setPos(max(margin, (needed_width - toolbar_width) / 2), needed_height - 36)
            self.proxy_toolbar.show()
        elif self.is_hovered:
            self.note_edit_item.hide()
            needed_width = max(self.base_width, 320)
            toolbar_width = self.toolbar_widget.sizeHint().width()
            needed_width = max(needed_width, toolbar_width + (margin * 2))
            self.text_item.setPos(margin, margin + status_h + metric_h)
            self.text_item.setTextWidth(max(10, needed_width - (margin * 2)))
            font_size = self.manual_font_size if self.manual_font_size else 12
            self.text_item.setFont(QFont("Arial", font_size))
            self.text_item.setDefaultTextColor(text_color)
            self.text_item.setPlainText(expanded_text)

            doc_height = self.text_item.document().size().height()
            needed_height = max(self.base_height, doc_height + (margin * 2) + status_h + metric_h + 46)

            self.setRect(0, 0, needed_width, needed_height)
            self.proxy_toolbar.setPos(max(margin, (needed_width - toolbar_width) / 2), needed_height - 36)
            self.proxy_toolbar.show()
        else:
            self.note_edit_item.hide()
            self.proxy_toolbar.hide()
            self.setRect(0, 0, self.base_width, self.base_height)
            max_w = max(10, self.base_width - (margin * 2))
            max_h = max(10, self.base_height - (margin * 2) - status_h - metric_h)
            self.text_item.setPos(margin, margin + status_h + metric_h)
            self.text_item.setTextWidth(max_w)

            best_size, fitted_text = self.calculate_best_fit(collapsed_text, max_w, max_h)
            self.text_item.setFont(QFont("Arial", best_size, QFont.Weight.Bold))
            self.text_item.setDefaultTextColor(text_color)
            self.text_item.setPlainText(fitted_text)

        self.metric_badges = self._layout_metric_badges(metric_badges, self.rect().width(), margin, status_h)
        self.resize_handle.show()
        self.resize_handle.setPos(self.rect().width(), self.rect().height())
        self.resize_handle.setZValue(10)

        for edge in self.edges:
            edge.update_position()

    def update_size(self, width, height):
        self.base_width = width
        self.base_height = height
        self.refresh_layout()
        self.bus.workspace_node_updated.emit(
            WorkspaceEvent.NODE_UPDATED,
            WorkspaceEventPayload(node_id=self.node_id, changes={"width": width, "height": height}),
        )

    def _load_tag_colors(self):
        badges = []
        try:
            view = self.scene().view if self.scene() and hasattr(self.scene(), "view") else None
            if view and hasattr(view, "get_node_tag_badges"):
                badges = view.get_node_tag_badges(self.node_id)
        except Exception:
            badges = []

        self.tag_badges = badges
        self.tag_colors = [b.get("color") or "#808080" for b in badges]
        self._tag_colors_loaded = True

    def get_tag_names(self):
        if not self._tag_colors_loaded:
            self._load_tag_colors()
        return [b.get("name") for b in self.tag_badges if b.get("name")]

    def refresh_tag_badges(self):
        self._tag_colors_loaded = False
        self._load_tag_colors()
        self.update()

    def _get_tag_dot_regions(self):
        if not self._tag_colors_loaded:
            self._load_tag_colors()

        max_dots = 5
        spacing = 4
        shown = self.tag_badges[:max_dots]
        dot_radius = 5
        dot_diam = dot_radius * 2

        x = self.rect().right() - 8 - dot_diam
        y = self.rect().top() + 8

        regions = []
        for badge in shown:
            regions.append((QRectF(x, y, dot_diam, dot_diam), badge.get("name") or ""))
            x -= (dot_diam + spacing)
        return regions

    def _tag_name_at_pos(self, pos):
        for rect, tag_name in self._get_tag_dot_regions():
            if tag_name and rect.contains(pos):
                return tag_name
        return None

    def hoverEnterEvent(self, event):
        self.is_hovered = True
        if not self.isSelected():
            self.setZValue(100)
        self.refresh_layout()
        if event:
            super().hoverEnterEvent(event)

    def hoverLeaveEvent(self, event):
        if self.is_note_editing(): return
        self.is_hovered = False
        if not self.isSelected():
            self.setZValue(1)
        self.refresh_layout()
        if event:
            super().hoverLeaveEvent(event)

    def itemChange(self, change, value):
        if change == QGraphicsItem.GraphicsItemChange.ItemPositionHasChanged:
            for edge in self.edges:
                edge.update_position()
            self.bus.workspace_node_updated.emit(
                WorkspaceEvent.NODE_UPDATED,
                WorkspaceEventPayload(node_id=self.node_id, changes={"x": self.pos().x(), "y": self.pos().y()}),
            )
        elif change == QGraphicsItem.GraphicsItemChange.ItemSelectedHasChanged:
            if self.isSelected():
                self.setPen(QPen(QColor("#ffffff"), 4))
                self.setZValue(150)
            else:
                self.setPen(QPen(QColor("#555555"), 2))
                self.setZValue(1 if not self.is_hovered else 100)
        return super().itemChange(change, value)

    def paint(self, painter, option, widget=None):
        super().paint(painter, option, widget)

        if not self.quote and not self.note and not self.is_note_editing():
            painter.save()
            painter.setPen(QPen(QColor(150, 150, 150, 150)))
            font = QFont("Arial", 12, QFont.Weight.Bold)
            font.setItalic(True)
            painter.setFont(font)
            painter.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, "[Empty Note]")
            painter.restore()

        self._paint_type_badge(painter)
        self._paint_metric_badges(painter)

        if not self._tag_colors_loaded:
            self._load_tag_colors()

        if self.entity_state.get("ai_generated") or not self.entity_state.get("is_verified", True):
            painter.save()
            shield_icon = "🛡️" if self.is_verified else "⚠️"
            rect = QRectF(self.rect().right() - 28, 6, 20, 20)
            if not self.is_verified:
                painter.setBrush(QBrush(QColor(255, 0, 0, 40)))
                painter.setPen(Qt.PenStyle.NoPen)
                painter.drawEllipse(rect.center(), 12, 12)

            painter.setPen(QPen(QColor(255, 255, 255)))
            painter.setFont(QFont("Arial", 10))
            painter.drawText(rect, Qt.AlignmentFlag.AlignCenter, shield_icon)
            painter.restore()

        if not self.tag_badges:
            return

        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.setPen(Qt.PenStyle.NoPen)

        for rect, tag_name in self._get_tag_dot_regions():
            color_hex = next((b.get("color") for b in self.tag_badges if b.get("name") == tag_name), "#808080")
            painter.setBrush(QBrush(QColor(color_hex)))
            painter.drawEllipse(rect)

        painter.restore()

    def trigger_connect(self):
        self.bus.workspace_action_requested.emit(WorkspaceIntent.NODE_CONNECT_START, WorkspacePayload(node_id=self.node_id))

    def trigger_edit(self):
        self.bus.workspace_action_requested.emit(WorkspaceIntent.UNDO_CHECKPOINT_REQUESTED, WorkspacePayload())
        self._normalize_source_backed_note()
        self.is_hovered = True
        if not self.isSelected():
            self.setZValue(100)
        self.note_edit_item.setPlainText(self.editable_note_text())
        self.note_edit_item.setDefaultTextColor(QColor(get_text_color_for_bg(self.color)))
        self.note_edit_item.setTextInteractionFlags(Qt.TextInteractionFlag.TextEditorInteraction)
        self.note_edit_item.show()
        self.refresh_layout()
        self.note_edit_item.setFocus()

        cursor = self.note_edit_item.textCursor()
        cursor.movePosition(QTextCursor.MoveOperation.End)
        self.note_edit_item.setTextCursor(cursor)

    def finish_in_place_edit(self):
        if not self.is_note_editing():
            return
        self.note_edit_item.setTextInteractionFlags(Qt.TextInteractionFlag.NoTextInteraction)
        new_text = self.note_edit_item.toPlainText().strip()
        self.note_edit_item.hide()

        if new_text != self.editable_note_text():
            self.bus.workspace_action_requested.emit(
                WorkspaceIntent.NODE_TEXT_COMMITTED,
                WorkspacePayload(node_id=self.node_id, text=new_text),
            )

        self.refresh_layout()
        self.hoverLeaveEvent(None)

    def trigger_color_change(self):
        self.bus.workspace_action_requested.emit(WorkspaceIntent.NODE_COLOR_REQUEST, WorkspacePayload(node_ids=[self.node_id]))

    def trigger_font_size_change(self):
        self.bus.workspace_action_requested.emit(WorkspaceIntent.NODE_FONT_REQUEST, WorkspacePayload(node_id=self.node_id))

    def trigger_generic_action(self, action_id):
        self.bus.workspace_action_requested.emit(
            WorkspaceIntent.NODE_EDIT_START,
            WorkspacePayload(node_id=self.node_id, extra={"action": action_id}),
        )

    def trigger_change_type(self):
        self.bus.workspace_action_requested.emit(
            WorkspaceIntent.NODE_EDIT_START,
            WorkspacePayload(node_id=self.node_id, extra={"action": "change_type"}),
        )

    def build_ontology_display_text(self, expanded: bool = False) -> str:
        try:
            blueprint = self.ontology_registry.get_entity_blueprint(self.entity_type)
        except Exception:
            blueprint = None

        if blueprint:
            values = []
            if self.entity_type in {EntityType.QUOTE.value, EntityType.EVIDENCE.value}:
                note_text = self.editable_note_text()
                quote_text = self.quote_text()
                if note_text and note_text != quote_text:
                    values.append(note_text)
                if quote_text:
                    values.append(f'"{quote_text}"' if expanded else quote_text)
                if values:
                    return "\n\n".join(values if expanded else values[:2])
            for block in blueprint.render_blocks:
                if block.block_type not in {"header", "text", "metric", "badge"}:
                    continue
                value = self._resolve_display_source(block.source)
                if value in (None, "", [], {}):
                    continue
                if block.block_type == "metric":
                    metric_key = block.source.split(".", 1)[1] if "." in block.source else block.source
                    label = block.label or (self.entity_properties.get("computed_metric_labels") or {}).get(metric_key) or metric_key
                    values.append(f"{label}: {self._format_metric_value(metric_key, value)}")
                else:
                    values.append(str(value))
            if values:
                return "\n\n".join(values if expanded else values[:2])

        node_type = self.node_type_registry.resolve(self)
        return node_type.build_display_text(self, expanded=expanded)

    def build_reference_display_text(self, expanded: bool = False) -> str:
        quote_text = self.quote_text()
        if not quote_text:
            return ""
        return f'"{quote_text}"' if expanded else quote_text

    def is_note_editing(self):
        return bool(
            hasattr(self, "note_edit_item")
            and self.note_edit_item.isVisible()
            and (self.note_edit_item.textInteractionFlags() & Qt.TextInteractionFlag.TextEditorInteraction)
        )

    def _resolve_display_source(self, source: str):
        if not source:
            return None
        if source.startswith("properties."):
            return self.entity_properties.get(source.split(".", 1)[1])
        if source.startswith("state."):
            return self.entity_state.get(source.split(".", 1)[1])
        if source.startswith("metrics."):
            return (self.entity_properties.get("computed_metrics") or {}).get(source.split(".", 1)[1])
        return getattr(self, source, None)

    def _format_metric_value(self, key, value):
        if key == "computed_confidence":
            try:
                return f"{int(round(float(value) * 100))}%"
            except Exception:
                return str(value)
        return str(value)

    def _collect_metric_badges(self):
        try:
            blueprint = self.ontology_registry.get_entity_blueprint(self.entity_type)
        except Exception:
            return []
        badges = []
        for block in getattr(blueprint, "render_blocks", []):
            if block.block_type != "metric_badge":
                continue
            value = self._resolve_display_source(block.source)
            if value in (None, "", [], {}):
                continue
            metric_key = block.source.split(".", 1)[1] if "." in block.source else block.source
            labels = self.entity_properties.get("computed_metric_labels") or {}
            badges.append({
                "key": metric_key,
                "label": block.label or labels.get(metric_key) or metric_key,
                "value": self._format_metric_value(metric_key, value),
            })
        return badges

    def _metric_badge_band_height(self, badges):
        return 22 if badges else 0

    def _layout_metric_badges(self, badges, width, margin, y_offset):
        if not badges:
            return []
        painter_font = QFont("Arial", 8, QFont.Weight.Bold)
        doc = QTextDocument()
        doc.setDefaultFont(painter_font)
        x = margin
        y = y_offset + 2
        rows = []
        for badge in badges:
            text = f"{badge['label']} {badge['value']}"
            doc.setPlainText(text)
            badge_w = min(max(42, doc.idealWidth() + 14), max(42, width - (margin * 2)))
            if x + badge_w > width - margin and x > margin:
                break
            rows.append({"rect": QRectF(x, y, badge_w, 18), "text": text, "key": badge["key"]})
            x += badge_w + 5
        return rows

    def _paint_metric_badges(self, painter):
        if not self.metric_badges:
            return
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.setFont(QFont("Arial", 8, QFont.Weight.Bold))
        text_color = QColor(get_text_color_for_bg(self.color))
        for badge in self.metric_badges:
            rect = badge["rect"]
            painter.setPen(QPen(QColor(255, 255, 255, 70), 1))
            painter.setBrush(QBrush(QColor(0, 0, 0, 80)))
            painter.drawRoundedRect(rect, 5, 5)
            painter.setPen(QPen(text_color))
            painter.drawText(rect.adjusted(6, 0, -6, 0), Qt.AlignmentFlag.AlignCenter, badge["text"])
        painter.restore()

    def _paint_type_badge(self, painter):
        try:
            label = self.ontology_registry.get_entity_blueprint(self.entity_type).display_name
        except Exception:
            label = self.entity_type.replace("entity.", "").replace("_", " ").title()
        if not label:
            return
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        badge_text = label[:18]
        font = QFont("Arial", 8, QFont.Weight.Bold)
        painter.setFont(font)
        metrics = painter.fontMetrics()
        width = min(self.rect().width() - 16, metrics.horizontalAdvance(badge_text) + 14)
        x = self.rect().width() - width - 8 if self.is_hovered else 8
        y = 8
        rect = QRectF(x, y, width, 16)
        painter.setBrush(QBrush(QColor(0, 0, 0, 90)))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.drawRoundedRect(rect, 4, 4)
        painter.setPen(QPen(QColor("#ffffff")))
        painter.drawText(rect, Qt.AlignmentFlag.AlignCenter, badge_text)
        painter.restore()

    def _status_band_height(self):
        return 22 if self._has_top_badges() else 0

    def _has_top_badges(self):
        return bool(self.entity_type or self.entity_state.get("ai_generated") or not self.entity_state.get("is_verified", True))
