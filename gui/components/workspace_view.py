import uuid
from PyQt6.QtWidgets import (QGraphicsView, QGraphicsScene, QGraphicsRectItem, 
                             QGraphicsTextItem, QGraphicsLineItem, QGraphicsItem, 
                             QInputDialog, QColorDialog, QMenu, QGraphicsProxyWidget,
                             QPushButton, QHBoxLayout, QWidget)
from PyQt6.QtCore import Qt, QRectF, QPointF, QLineF
from PyQt6.QtGui import QColor, QPen, QBrush, QFont, QPainter, QCursor, QTextDocument

# Calculates optimal text color (black or white) based on background luminance
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

class Edge(QGraphicsLineItem):
    def __init__(self, source_node, dest_node, label_text="", edge_id=None, color="#888888"):
        super().__init__()
        self.source_node = source_node
        self.dest_node = dest_node
        self.label_text = label_text
        self.edge_id = edge_id or str(uuid.uuid4())
        self.base_color = QColor(color)
        
        self.setZValue(-1) 
        self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsSelectable, True)
        self.setPen(QPen(self.base_color, 2, Qt.PenStyle.SolidLine))
        
        self.text_item = QGraphicsTextItem(label_text, self)
        self.text_item.setDefaultTextColor(QColor("#ffffff"))
        self.text_item.setFont(QFont("Arial", 10, QFont.Weight.Bold))
        # FIXED: Pass clicks through the text so the line itself gets selected
        self.text_item.setAcceptedMouseButtons(Qt.MouseButton.NoButton)
        
        self.source_node.add_edge(self)
        self.dest_node.add_edge(self)
        self.update_position()

    # Expands the clickable area of the line to include the text label bounding box
    def shape(self):
        path = super().shape()
        if self.text_item.scene():
            text_rect = self.text_item.mapRectToParent(self.text_item.boundingRect())
            path.addRect(text_rect)
        return path

    def update_position(self):
        start = self.source_node.sceneBoundingRect().center()
        end = self.dest_node.sceneBoundingRect().center()
        self.setLine(QLineF(start, end))
        
        center_x = (start.x() + end.x()) / 2
        center_y = (start.y() + end.y()) / 2
        text_rect = self.text_item.boundingRect()
        self.text_item.setPos(center_x - text_rect.width() / 2, center_y - text_rect.height() / 2 - 10)

    def itemChange(self, change, value):
        if change == QGraphicsItem.GraphicsItemChange.ItemSelectedHasChanged:
            if self.isSelected():
                self.setPen(QPen(QColor("#ffffff"), 4, Qt.PenStyle.SolidLine))
            else:
                self.setPen(QPen(self.base_color, 2, Qt.PenStyle.SolidLine))
        return super().itemChange(change, value)

    def contextMenuEvent(self, event):
        menu = QMenu()
        edit_action = menu.addAction("Edit Connection Text")
        color_action = menu.addAction("Change Line Color")
        delete_action = menu.addAction("Delete Connection")
        
        action = menu.exec(event.screenPos())
        if action == edit_action:
            text, ok = QInputDialog.getText(None, "Edit Label", "Enter new text:", text=self.label_text)
            if ok:
                self.label_text = text
                self.text_item.setPlainText(text)
                self.update_position()
                self.scene().view.main_window.project_manager.mark_dirty("workspace")
        elif action == color_action:
            color = QColorDialog.getColor(self.base_color)
            if color.isValid():
                self.base_color = color
                self.setPen(QPen(self.base_color, 4 if self.isSelected() else 2, Qt.PenStyle.SolidLine))
                self.scene().view.main_window.project_manager.mark_dirty("workspace")
        elif action == delete_action:
            self.source_node.edges.remove(self)
            self.dest_node.edges.remove(self)
            self.scene().removeItem(self)
            self.scene().view.edges.remove(self)
            self.scene().view.main_window.project_manager.mark_dirty("workspace")


class InPlaceTextItem(QGraphicsTextItem):
    def __init__(self, node, text=""):
        super().__init__(text, node)
        self.node = node

    def keyPressEvent(self, event):
        if event.key() == Qt.Key.Key_Return and not (event.modifiers() & Qt.KeyboardModifier.ShiftModifier):
            self.clearFocus()
            self.node.finish_in_place_edit()
            return
        super().keyPressEvent(event)


class ResizeHandle(QGraphicsRectItem):
    def __init__(self, parent):
        super().__init__(0, 0, 10, 10, parent)
        self.setBrush(QBrush(QColor("#ffffff")))
        self.setCursor(Qt.CursorShape.SizeFDiagCursor)
        self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsMovable, True)
        self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemSendsGeometryChanges, True)
        self._is_resizing = False

    def itemChange(self, change, value):
        if change == QGraphicsItem.GraphicsItemChange.ItemPositionChange and not self._is_resizing:
            parent = self.parentItem()
            if parent and not parent.is_hovered:
                self._is_resizing = True
                new_w = max(50, value.x() + 10)
                new_h = max(30, value.y() + 10)
                parent.update_size(new_w, new_h)
                self._is_resizing = False
                return QPointF(new_w - 10, new_h - 10)
        return super().itemChange(change, value)


class Node(QGraphicsRectItem):
    def __init__(self, node_id, quote, note, color="#333333", is_custom=False, width=150, height=80, pdf_path=None, page_num=None, manual_font_size=None):
        super().__init__(0, 0, width, height)
        self.node_id = node_id
        self.is_custom = is_custom
        self.quote = quote
        self.note = note
        
        # Ensure color is safely parsed to a hex string
        self.color = color if isinstance(color, str) else QColor(int(color[0]*255), int(color[1]*255), int(color[2]*255)).name()
        
        self.pdf_path = pdf_path
        self.page_num = page_num
        self.manual_font_size = manual_font_size
        self.edges = []
        
        self.base_width = width
        self.base_height = height
        self.is_hovered = False
        
        self.setBrush(QBrush(QColor(self.color)))
        self.setPen(QPen(QColor("#555555"), 2))
        self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsMovable)
        self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsSelectable)
        self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemSendsGeometryChanges)
        self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemClipsChildrenToShape, True)
        self.setAcceptHoverEvents(True)
        
        self.text_item = InPlaceTextItem(self)
        self.resize_handle = ResizeHandle(self)
        
        # Action Toolbar
        self.toolbar_widget = QWidget()
        self.toolbar_widget.setStyleSheet("background: transparent;")
        t_layout = QHBoxLayout(self.toolbar_widget)
        t_layout.setContentsMargins(0,0,0,0)
        t_layout.setSpacing(5)
        
        btn_edit = QPushButton("✏️ Edit")
        btn_color = QPushButton("🎨 Color")
        btn_font = QPushButton("🔠 Size")
        for btn in [btn_edit, btn_color, btn_font]:
            btn.setStyleSheet("background-color: #444; color: white; border-radius: 4px; padding: 2px 6px; font-size: 10px;")
            t_layout.addWidget(btn)
            
        btn_edit.clicked.connect(self.trigger_edit)
        btn_color.clicked.connect(self.trigger_color_change)
        btn_font.clicked.connect(self.trigger_font_size_change)
        
        self.proxy_toolbar = QGraphicsProxyWidget(self)
        self.proxy_toolbar.setWidget(self.toolbar_widget)
        self.proxy_toolbar.hide()
        
        self.refresh_layout()

    def add_edge(self, edge):
        self.edges.append(edge)

    def _get_html(self, text_content, show_quote=False):
        text_color = get_text_color_for_bg(self.color)
        html = ""
        quote_color = "#444444" if text_color == "#000000" else "#cccccc"
        
        # Only render the distinct quote if it exists AND is different from the main text_content
        if show_quote and self.quote and not self.is_custom and text_content != self.quote:
            html += f"<span style='color:{quote_color}; font-size:11px;'><i>\"{self.quote}\"</i></span><br><br>"
            
        if text_content:
            html += f"<b style='color:{text_color};'>{text_content}</b>"
            
        return html

    def calculate_best_fit(self, plain_note, max_w, max_h):
        if not plain_note: return 12, ""
        
        doc = QTextDocument()
        doc.setTextWidth(max_w)
        
        def check_fit(text_to_test, size_to_test):
            doc.setDefaultFont(QFont("Arial", size_to_test))
            doc.setHtml(self._get_html(text_to_test, show_quote=False))
            return doc.size().height() <= max_h
            
        if self.manual_font_size is not None:
            if check_fit(plain_note, self.manual_font_size):
                return self.manual_font_size, plain_note
            return self.manual_font_size, self.truncate_to_fit(plain_note, max_w, max_h, self.manual_font_size)
            
        # Binary search for dynamic scaling
        for size in range(32, 7, -1):
            if check_fit(plain_note, size):
                return size, plain_note
                
        return 8, self.truncate_to_fit(plain_note, max_w, max_h, 8)

    def truncate_to_fit(self, text, max_w, max_h, font_size):
        if not text: return ""
        doc = QTextDocument()
        doc.setTextWidth(max_w)
        doc.setDefaultFont(QFont("Arial", font_size))
        
        words = text.split()
        low = 0
        high = len(words)
        best = ""
        
        while low <= high:
            mid = (low + high) // 2
            test_text = " ".join(words[:mid]) + "..." if mid < len(words) else " ".join(words)
            doc.setHtml(self._get_html(test_text, show_quote=False))
            if doc.size().height() <= max_h:
                best = test_text
                low = mid + 1
            else:
                high = mid - 1
                
        return best if best else "..."

    def refresh_layout(self):
        margin = 8
        max_w = max(10, self.base_width - (margin * 2))
        max_h = max(10, self.base_height - (margin * 2))
        
        self.text_item.setPos(margin, margin)
        self.text_item.setTextWidth(max_w)
        
        # Fallback to display the quote if the user left the note entirely empty
        plain_note = self.note if self.note else (self.quote if self.quote else "")
        if not plain_note: plain_note = ""
        
        if self.is_hovered:
            self.resize_handle.hide()
            font_size = self.manual_font_size if self.manual_font_size else 12
            self.text_item.setFont(QFont("Arial", font_size))
            self.text_item.setHtml(self._get_html(plain_note, show_quote=True))
            
            doc_height = self.text_item.document().size().height()
            needed_height = doc_height + (margin * 2) + 35 
            
            expanded_height = max(self.base_height, needed_height)
            self.setRect(0, 0, self.base_width, expanded_height)
            
            self.proxy_toolbar.setPos(margin, expanded_height - 30)
            self.proxy_toolbar.show()
        else:
            self.resize_handle.show()
            self.resize_handle.setPos(self.base_width - 10, self.base_height - 10)
            self.proxy_toolbar.hide()
            self.setRect(0, 0, self.base_width, self.base_height)
            
            best_size, fitted_note = self.calculate_best_fit(plain_note, max_w, max_h)
            self.text_item.setFont(QFont("Arial", best_size))
            self.text_item.setHtml(self._get_html(fitted_note, show_quote=False))

    def update_size(self, width, height):
        self.base_width = width
        self.base_height = height
        self.refresh_layout()
        if self.scene() and hasattr(self.scene(), 'view'):
            self.scene().view.main_window.project_manager.mark_dirty("workspace")

    def hoverEnterEvent(self, event):
        self.is_hovered = True
        self.setZValue(100) 
        self.refresh_layout()
        super().hoverEnterEvent(event)

    def hoverLeaveEvent(self, event):
        if self.text_item.hasFocus(): return 
        self.is_hovered = False
        self.setZValue(1)
        self.refresh_layout()
        super().hoverLeaveEvent(event)

    def itemChange(self, change, value):
        if change == QGraphicsItem.GraphicsItemChange.ItemPositionHasChanged:
            for edge in self.edges:
                edge.update_position()
            if self.scene() and hasattr(self.scene(), 'view'):
                self.scene().view.main_window.project_manager.mark_dirty("workspace")
        elif change == QGraphicsItem.GraphicsItemChange.ItemSelectedHasChanged:
            self.setPen(QPen(QColor("#ffffff") if self.isSelected() else QColor("#555555"), 2))
        return super().itemChange(change, value)

    def mouseDoubleClickEvent(self, event):
        if self.scene() and hasattr(self.scene(), 'view'):
            self.scene().view.start_connection(self)
        super().mouseDoubleClickEvent(event)

    def trigger_edit(self):
        self.text_item.setPlainText(self.note)
        self.text_item.setDefaultTextColor(QColor(get_text_color_for_bg(self.color)))
        self.text_item.setTextInteractionFlags(Qt.TextInteractionFlag.TextEditorInteraction)
        self.text_item.setFocus()

    def finish_in_place_edit(self):
        self.text_item.setTextInteractionFlags(Qt.TextInteractionFlag.NoTextInteraction)
        new_text = self.text_item.toPlainText().strip()
        self.note = new_text
        self.refresh_layout()
        
        if not self.is_custom and self.pdf_path is not None:
            notes_tab = self.scene().view.main_window.tabs["Notes"]
            notes_tab._modify_note(self.pdf_path, self.page_num, self.node_id, action="edit_content", content=self.note)
        else:
            self.scene().view.main_window.project_manager.mark_dirty("workspace")
            
        self.hoverLeaveEvent(None)

    def trigger_color_change(self):
        color = QColorDialog.getColor(QColor(self.color))
        if color.isValid():
            self.color = color.name()
            self.setBrush(QBrush(QColor(self.color)))
            self.refresh_layout() 
            
            if not self.is_custom and self.pdf_path is not None:
                notes_tab = self.scene().view.main_window.tabs["Notes"]
                notes_tab._modify_note(self.pdf_path, self.page_num, self.node_id, action="color", color=color.getRgbF()[:3])
            else:
                self.scene().view.main_window.project_manager.mark_dirty("workspace")

    def trigger_font_size_change(self):
        current = self.manual_font_size if self.manual_font_size else 12
        val, ok = QInputDialog.getInt(None, "Font Size", "Enter static font size (8-72)\nCancel to Auto-Scale:", current, 8, 72)
        if ok:
            self.manual_font_size = val
        else:
            self.manual_font_size = None
        self.refresh_layout()
        self.scene().view.main_window.project_manager.mark_dirty("workspace")


class WorkspaceView(QGraphicsView):
    def __init__(self, main_window):
        super().__init__()
        self.main_window = main_window
        self.scene_obj = QGraphicsScene(self)
        self.setScene(self.scene_obj)
        self.scene_obj.view = self 
        
        self.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        self.setBackgroundBrush(QBrush(QColor("#1a1a1a")))
        self.setDragMode(QGraphicsView.DragMode.RubberBandDrag)
        
        self.nodes = {}
        self.edges = []
        self.connecting_node = None

    def wheelEvent(self, event):
        if event.modifiers() in (Qt.KeyboardModifier.ControlModifier, Qt.KeyboardModifier.ShiftModifier):
            if event.angleDelta().y() > 0:
                self.zoom_in()
            else:
                self.zoom_out()
        else:
            super().wheelEvent(event)

    def zoom_in(self):
        self.scale(1.15, 1.15)
        
    def zoom_out(self):
        self.scale(1 / 1.15, 1 / 1.15)

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.MiddleButton or (event.button() == Qt.MouseButton.LeftButton and event.modifiers() == Qt.KeyboardModifier.AltModifier):
            self.setDragMode(QGraphicsView.DragMode.ScrollHandDrag)
            event.accept()
        else:
            item = self.itemAt(event.pos())
            if self.connecting_node:
                if isinstance(item, Node) and item != self.connecting_node:
                    self.finish_connection(item)
                else:
                    self.connecting_node.setPen(QPen(QColor("#555555"), 2))
                    self.connecting_node = None
            super().mousePressEvent(event)

    def mouseReleaseEvent(self, event):
        self.setDragMode(QGraphicsView.DragMode.RubberBandDrag)
        super().mouseReleaseEvent(event)

    def start_connection(self, node):
        self.connecting_node = node
        self.connecting_node.setPen(QPen(QColor("#00ff00"), 3, Qt.PenStyle.DashLine))

    def finish_connection(self, target_node):
        text, ok = QInputDialog.getText(self, "Connection Label", "Enter text for connection:")
        if ok:
            edge = Edge(self.connecting_node, target_node, text)
            self.scene_obj.addItem(edge)
            self.edges.append(edge)
            self.main_window.project_manager.mark_dirty("workspace")
            
        self.connecting_node.setPen(QPen(QColor("#555555"), 2))
        self.connecting_node = None

    def add_custom_bubble(self):
        node_id = f"custom_{uuid.uuid4()}"
        node = Node(node_id, quote="", note="", color="#005577", is_custom=True, width=180, height=80)
        
        view_center = self.mapToScene(self.viewport().rect().center())
        node.setPos(view_center)
        
        self.scene_obj.addItem(node)
        self.nodes[node_id] = node
        self.main_window.project_manager.mark_dirty("workspace")
        
        node.hoverEnterEvent(None)
        node.trigger_edit()

    def sync_with_project(self, workspace_data, pdf_annotations):
        self.scene_obj.clear()
        self.nodes.clear()
        self.edges.clear()

        saved_nodes = workspace_data.get("nodes", {})
        for n_id, data in saved_nodes.items():
            node = Node(n_id, data.get("quote", ""), data.get("note", ""), data["color"], data["is_custom"], 
                        data["width"], data["height"], data.get("pdf_path"), data.get("page_num"), data.get("manual_font_size"))
            node.setPos(data["x"], data["y"])
            self.scene_obj.addItem(node)
            self.nodes[n_id] = node

        y_offset = 50
        for annot in pdf_annotations:
            if annot["id"] not in self.nodes:
                l = len(annot["content"])
                w = 200 if l < 50 else (250 if l < 150 else 300)
                h = 70 if l < 50 else (110 if l < 150 else 160)
                
                node = Node(annot["id"], annot["subject"], annot["content"], color="#442255", is_custom=False, 
                            width=w, height=h, pdf_path=annot["pdf_path"], page_num=annot["page_num"])
                node.setPos(50, y_offset)
                y_offset += 100
                self.scene_obj.addItem(node)
                self.nodes[annot["id"]] = node

        for edge_data in workspace_data.get("edges", []):
            if edge_data["source"] in self.nodes and edge_data["target"] in self.nodes:
                src = self.nodes[edge_data["source"]]
                tgt = self.nodes[edge_data["target"]]
                edge = Edge(src, tgt, edge_data["label"], edge_data["id"], edge_data.get("color", "#888888"))
                self.scene_obj.addItem(edge)
                self.edges.append(edge)

    def serialize_workspace(self):
        data = {"nodes": {}, "edges": []}
        for n_id, node in self.nodes.items():
            data["nodes"][n_id] = {
                "quote": node.quote,
                "note": node.note,
                "color": node.color,
                "is_custom": node.is_custom,
                "pdf_path": node.pdf_path,
                "page_num": node.page_num,
                "manual_font_size": node.manual_font_size,
                "x": node.pos().x(),
                "y": node.pos().y(),
                "width": node.base_width,
                "height": node.base_height
            }
        for edge in self.edges:
            data["edges"].append({
                "id": edge.edge_id,
                "source": edge.source_node.node_id,
                "target": edge.dest_node.node_id,
                "label": edge.label_text,
                "color": edge.base_color.name()
            })
        return data