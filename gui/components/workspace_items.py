# gui/components/workspace_items.py
import uuid
from PySide6.QtWidgets import (QGraphicsRectItem, QGraphicsTextItem, QGraphicsLineItem, QGraphicsItem,
                             QGraphicsProxyWidget, QPushButton, QHBoxLayout, QVBoxLayout, QWidget)
from PySide6.QtCore import Qt, QLineF, QRectF
from PySide6.QtGui import QColor, QPen, QBrush, QFont, QTextDocument, QPainter, QTextCursor

from gui.theme.theme import ThemeManager
from core.events.event_bus import EventBus
from core.events.domains.workspace_events import WorkspaceEvent, WorkspaceEventPayload, WorkspaceIntent, WorkspacePayload
from core.services.workspace_registries import build_default_workspace_node_type_registry, infer_workspace_node_type_id

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

        if new_text != self.parent_node.note:
            self.bus.workspace_action_requested.emit(
                WorkspaceIntent.NODE_TEXT_COMMITTED,
                WorkspacePayload(node_id=self.parent_node.node_id, text=new_text),
            )

class Edge(QGraphicsLineItem):
    def __init__(self, source_node, dest_node, label_text="", edge_id=None, color="#888888", weight=2):
        super().__init__()
        self.source_node = source_node
        self.dest_node = dest_node
        self.label_text = label_text
        self.edge_id = edge_id or str(uuid.uuid4())
        self.base_color = QColor(color)
        self.weight = weight
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
    def __init__(self, node_id, quote, note, color=None, is_custom=False, width=150, height=80, pdf_path=None, page_num=None, manual_font_size=None, highlight_id=None, node_origin="human", is_verified=0, original_text=None, node_type_id=None, node_type_registry=None, action_registry=None):
        super().__init__(0, 0, width, height)
        self.node_id = node_id
        self.highlight_id = highlight_id
        self.is_custom = is_custom
        self.quote = quote if quote else ""
        self.note = note if note else ""
        self.node_origin = node_origin
        self.is_verified = bool(is_verified)
        self.original_text = original_text if original_text is not None else note
        self.bus = EventBus.get_instance()
        self.node_type_registry = node_type_registry or build_default_workspace_node_type_registry()
        self.action_registry = action_registry

        theme = ThemeManager().get_theme()
        if not color or color == "#333333":
            color = theme['bg_panel']

        self.color = color if isinstance(color, str) else QColor(int(color[0]*255), int(color[1]*255), int(color[2]*255)).name()
        self.pdf_path = pdf_path
        self.page_num = page_num
        self.manual_font_size = manual_font_size
        self.node_type_id = node_type_id or infer_workspace_node_type_id(self)
        self.edges = []
        self.base_width = width
        self.base_height = height
        self.is_hovered = False
        self.tag_colors = []
        self.tag_badges = []
        self._tag_colors_loaded = False

        self.setBrush(QBrush(QColor(self.color)))
        self.setPen(QPen(QColor("#555555"), 2))
        self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsMovable)
        self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsSelectable)
        self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemSendsGeometryChanges)
        self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemClipsChildrenToShape, False)
        self.setAcceptHoverEvents(True)

        self.text_item = InPlaceTextItem(self)
        self.text_item.setZValue(2)
        self.resize_handle = ResizeHandle(self)

        self.toolbar_widget = QWidget()
        self.toolbar_widget.setStyleSheet("background: transparent;")

        t_layout = QVBoxLayout(self.toolbar_widget)
        t_layout.setContentsMargins(0,0,0,0)
        t_layout.setSpacing(2)

        row1 = QHBoxLayout()
        row2 = QHBoxLayout()
        row1.setContentsMargins(0,0,0,0)
        row2.setContentsMargins(0,0,0,0)

        self.toolbar_buttons = {}
        buttons = self._build_toolbar_buttons(row1, row2)

        t_layout.addLayout(row1)
        t_layout.addLayout(row2)

        for btn in buttons:
            if self.node_origin == "ai" and btn == getattr(self, "btn_verify", None) and not self.is_verified:
                btn.setStyleSheet(f"background-color: #aa0000; color: white; border-radius: 4px; padding: 2px 6px; font-size: 10px; font-weight: bold; border: 1px solid #ff4444;")
            else:
                btn.setStyleSheet(f"background-color: {theme['bg_panel']}; color: {theme['text_main']}; border-radius: 4px; padding: 2px 6px; font-size: 10px; font-weight: bold; border: 1px solid {theme['border']};")

        self.proxy_toolbar = QGraphicsProxyWidget(self)
        self.proxy_toolbar.setWidget(self.toolbar_widget)
        self.proxy_toolbar.setZValue(30)
        self.proxy_toolbar.hide()

        if self.node_origin == "ai":
            self.btn_verify.clicked.connect(self.trigger_verify)

        self.refresh_layout()

    def _build_toolbar_buttons(self, row1, row2):
        action_specs = {
            "node.edit": ("✏️ Edit", self.trigger_edit, row1),
            "node.color": ("🎨 Color", self.trigger_color_change, row1),
            "node.font_size": ("📏 Size", self.trigger_font_size_change, row1),
            "node.connect": ("🔗 Connect", self.trigger_connect, row2),
            "node.jump": ("📄 Jump to PDF", self.trigger_jump, row2),
            "node.copy_citation": ("📋 Cite", self.trigger_copy_citation, row2),
            "node.verify": ("🛡️ Verified" if self.is_verified else "⚠️ Verify AI", self.trigger_verify, row2),
        }
        node_type = self.node_type_registry.resolve(self)
        action_ids = list(node_type.action_ids)
        if self.node_origin == "ai" and "node.verify" not in action_ids:
            action_ids.append("node.verify")
        if not self.has_source_reference():
            action_ids = [action_id for action_id in action_ids if action_id not in {"node.jump", "node.copy_citation"}]

        buttons = []
        for action_id in action_ids:
            spec = action_specs.get(action_id)
            if not spec:
                continue
            label, callback, row = spec
            btn = QPushButton(label)
            btn.clicked.connect(callback)
            row.addWidget(btn)
            buttons.append(btn)
            self.toolbar_buttons[action_id] = btn
            if action_id == "node.jump":
                self.btn_jump = btn
            elif action_id == "node.verify":
                self.btn_verify = btn
        return buttons

    def refresh_verify_button(self):
        if not hasattr(self, "btn_verify"):
            return
        theme = ThemeManager().get_theme()
        if self.is_verified:
            self.btn_verify.setText("🛡️ Verified")
            self.btn_verify.setStyleSheet(f"background-color: {theme['bg_panel']}; color: {theme['text_main']}; border-radius: 4px; padding: 2px 6px; font-size: 10px; font-weight: bold; border: 1px solid {theme['border']};")
        else:
            self.btn_verify.setText("⚠️ Verify AI")
            self.btn_verify.setStyleSheet("background-color: #aa0000; color: white; border-radius: 4px; padding: 2px 6px; font-size: 10px; font-weight: bold; border: 1px solid #ff4444;")

    def trigger_verify(self):
        self.bus.workspace_action_requested.emit(WorkspaceIntent.NODE_VERIFY_TOGGLE, WorkspacePayload(node_id=self.node_id))

    def has_source_reference(self):
        node_type = self.node_type_registry.resolve(self)
        return node_type.inherits_from("workspace.node.quote") and bool(self.pdf_path or self.highlight_id or (self.quote and not self.is_custom))

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
        text_color = QColor(get_text_color_for_bg(self.color))
        expanded_text = ""
        self.text_item.setPos(margin, margin)
        self.text_item.setZValue(20)
        self.text_item.setVisible(True)

        node_type = self.node_type_registry.resolve(self)
        expanded_text = node_type.build_display_text(self, expanded=True)
        collapsed_text = node_type.build_display_text(self, expanded=False)

        if self.is_hovered:
            needed_width = max(self.base_width, 320)
            self.text_item.setTextWidth(max(10, needed_width - (margin * 2)))
            font_size = self.manual_font_size if self.manual_font_size else 12
            self.text_item.setFont(QFont("Arial", font_size))
            self.text_item.setDefaultTextColor(text_color)
            self.text_item.setPlainText(expanded_text)

            doc_height = self.text_item.document().size().height()
            needed_height = max(self.base_height, doc_height + (margin * 2) + 60)

            self.setRect(0, 0, needed_width, needed_height)
            self.proxy_toolbar.setPos(margin, needed_height - 55)
            self.proxy_toolbar.show()
        else:
            self.proxy_toolbar.hide()
            self.setRect(0, 0, self.base_width, self.base_height)
            max_w = max(10, self.base_width - (margin * 2))
            max_h = max(10, self.base_height - (margin * 2))
            self.text_item.setTextWidth(max_w)

            best_size, fitted_text = self.calculate_best_fit(collapsed_text, max_w, max_h)
            self.text_item.setFont(QFont("Arial", best_size, QFont.Weight.Bold))
            self.text_item.setDefaultTextColor(text_color)
            self.text_item.setPlainText(fitted_text)

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
        if self.text_item.hasFocus(): return
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

        if not self.quote and not self.note and not self.text_item.hasFocus():
            painter.save()
            painter.setPen(QPen(QColor(150, 150, 150, 150)))
            font = QFont("Arial", 12, QFont.Weight.Bold)
            font.setItalic(True)
            painter.setFont(font)
            painter.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, "[Empty Note]")
            painter.restore()

        if not self._tag_colors_loaded:
            self._load_tag_colors()

        if self.node_origin == "ai":
            painter.save()
            shield_icon = "🛡️" if self.is_verified else "⚠️"
            rect = QRectF(4, 4, 20, 20)
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
        self.text_item.setPlainText(self.note)
        self.text_item.setDefaultTextColor(QColor(get_text_color_for_bg(self.color)))
        self.text_item.setTextInteractionFlags(Qt.TextInteractionFlag.TextEditorInteraction)
        self.text_item.setFocus()

        cursor = self.text_item.textCursor()
        cursor.movePosition(QTextCursor.MoveOperation.End)
        self.text_item.setTextCursor(cursor)

    def finish_in_place_edit(self):
        if not (self.text_item.textInteractionFlags() & Qt.TextInteractionFlag.TextEditorInteraction):
            return
        self.text_item.setTextInteractionFlags(Qt.TextInteractionFlag.NoTextInteraction)
        new_text = self.text_item.toPlainText().strip()

        if new_text != self.note:
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
