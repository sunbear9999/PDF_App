import uuid
import json
import re
from PyQt6.QtWidgets import (QGraphicsView, QGraphicsScene, QGraphicsRectItem, 
                             QGraphicsTextItem, QGraphicsLineItem, QGraphicsItem, 
                             QInputDialog, QColorDialog, QMenu, QGraphicsProxyWidget,
                             QPushButton, QHBoxLayout, QWidget, QMessageBox)
from PyQt6.QtCore import Qt, QRectF, QPointF, QLineF, QThread, pyqtSignal, QTimer
from PyQt6.QtGui import QColor, QPen, QBrush, QFont, QPainter, QCursor, QTextDocument

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

class AIOrganizeWorker(QThread):
    finished = pyqtSignal(object, str)

    def __init__(self, llm_manager, model, nodes_data):
        super().__init__()
        self.llm_manager = llm_manager
        self.model = model
        self.nodes_data = nodes_data

    def run(self):
        prompt = (
            "You are a strict JSON data processing API. You must group the following text snippets into 2-4 logical categories based on semantic similarity.\n"
            "Respond ONLY with a valid, raw JSON array. No markdown formatting, no backticks, no conversational text.\n"
            "Schema:\n"
            "[\n"
            "  {\n"
            "    \"cluster_name\": \"Category Name\",\n"
            "    \"node_ids\": [\"id1\", \"id2\"]\n"
            "  }\n"
            "]\n\n"
            f"Input Data:\n{json.dumps(self.nodes_data, indent=2)}\n\n"
            "OUTPUT STRICTLY JSON:"
        )
        
        response_text = ""
        def callback(chunk):
            nonlocal response_text
            response_text += chunk

        try:
            self.llm_manager.query(prompt, self.model, allowed_docs=None, callback=callback, rag_enabled=False)
            
            match = re.search(r'\[\s*\{.*?\}\s*\]', response_text, re.DOTALL)
            if not match:
                raise ValueError("No JSON array found in output.")
                
            cleaned = match.group(0)
            cleaned = re.sub(r',\s*([\]}])', r'\1', cleaned) 
            clusters = json.loads(cleaned)
            self.finished.emit(clusters, "")
        except Exception as e:
            err = f"Failed to parse LLM Output. It must be valid JSON.\nError: {str(e)}\n\nRaw Output:\n{response_text[:150]}..."
            self.finished.emit(None, err)


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
        self.text_item.setAcceptedMouseButtons(Qt.MouseButton.NoButton)
        
        self.source_node.add_edge(self)
        self.dest_node.add_edge(self)
        self.update_position()

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
        super().__init__(0, 0, 16, 16, parent)
        self.setBrush(QBrush(QColor(255, 255, 255, 150)))
        self.setPen(QPen(QColor(255, 255, 255, 200), 1))
        self.setCursor(Qt.CursorShape.SizeFDiagCursor)
        self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsMovable, True)
        self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemSendsGeometryChanges, True)
        self._is_resizing = False

    def itemChange(self, change, value):
        if change == QGraphicsItem.GraphicsItemChange.ItemPositionChange and not self._is_resizing:
            parent = self.parentItem()
            if parent:
                self._is_resizing = True
                new_w = max(50, value.x() + 16)
                new_h = max(30, value.y() + 16)
                parent.update_size(new_w, new_h)
                self._is_resizing = False
                return QPointF(new_w - 16, new_h - 16)
        return super().itemChange(change, value)


class Node(QGraphicsRectItem):
    def __init__(self, node_id, quote, note, color="#333333", is_custom=False, width=150, height=80, pdf_path=None, page_num=None, manual_font_size=None):
        super().__init__(0, 0, width, height)
        self.node_id = node_id
        self.is_custom = is_custom
        self.quote = quote if quote else ""
        self.note = note if note else ""
        
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
        
        self.toolbar_widget = QWidget()
        self.toolbar_widget.setStyleSheet("background: transparent;")
        t_layout = QHBoxLayout(self.toolbar_widget)
        t_layout.setContentsMargins(0,0,0,0)
        t_layout.setSpacing(5)
        
        btn_edit = QPushButton("✏️ Edit")
        btn_color = QPushButton("🎨 Color")
        btn_font = QPushButton("🔠 Size")
        btn_connect = QPushButton("🔗 Connect")
        
        buttons = [btn_edit, btn_color, btn_font, btn_connect]
        
        if self.pdf_path is not None:
            self.btn_jump = QPushButton("📄 Jump to PDF")
            buttons.append(self.btn_jump)
        
        for btn in buttons:
            btn.setStyleSheet("background-color: #444; color: white; border-radius: 4px; padding: 2px 6px; font-size: 10px; font-weight: bold;")
            t_layout.addWidget(btn)
            
        btn_edit.clicked.connect(self.trigger_edit)
        btn_color.clicked.connect(self.trigger_color_change)
        btn_font.clicked.connect(self.trigger_font_size_change)
        btn_connect.clicked.connect(self.trigger_connect)
        
        if self.pdf_path is not None:
            self.btn_jump.clicked.connect(self.trigger_jump)
        
        self.proxy_toolbar = QGraphicsProxyWidget(self)
        self.proxy_toolbar.setWidget(self.toolbar_widget)
        self.proxy_toolbar.hide()
        
        self.refresh_layout()

    def mousePressEvent(self, event):
        view = self.scene().view if self.scene() and hasattr(self.scene(), 'view') else None
        if view and view.connecting_node and view.connecting_node != self:
            view.finish_connection(self)
            event.accept()
            return
        super().mousePressEvent(event)

    def trigger_jump(self):
        if self.pdf_path and self.page_num is not None:
            if self.scene() and hasattr(self.scene(), 'view'):
                main_win = self.scene().view.main_window
                
                # CRITICAL FIX: To prevent a C++ Segfault, we MUST delay the execution 
                # of the scene wipe until after this button's click event has fully completed.
                pdf_path = self.pdf_path
                page_num = self.page_num
                
                def do_jump():
                    main_win.switch_to_pdf(pdf_path)
                    main_win.viewer.jump_to_page(page_num)
                    
                QTimer.singleShot(0, do_jump)

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
        low = 0
        high = len(words)
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
        if self.note:
            expanded_text += self.note
            
        if self.quote and self.quote != self.note and not self.is_custom:
            if expanded_text:
                expanded_text += "\n\n"
            expanded_text += f'"{self.quote}"'
            
        if not expanded_text.strip():
            expanded_text = "[Empty Note]"
            
        collapsed_text = self.note if self.note else (f'"{self.quote}"' if self.quote else "[Empty Note]")
            
        if self.is_hovered:
            needed_width = max(self.base_width, 320) 
            self.text_item.setTextWidth(needed_width - (margin * 2))
            
            font_size = self.manual_font_size if self.manual_font_size else 12
            self.text_item.setFont(QFont("Arial", font_size))
            
            self.text_item.setDefaultTextColor(text_color)
            self.text_item.setPlainText(expanded_text)
            
            doc_height = self.text_item.document().size().height()
            needed_height = max(self.base_height, doc_height + (margin * 2) + 35)
            
            self.setRect(0, 0, needed_width, needed_height)
            self.proxy_toolbar.setPos(margin, needed_height - 30)
            self.proxy_toolbar.show()
            self.resize_handle.hide()
            
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
            self.resize_handle.setPos(self.base_width - 16, self.base_height - 16)
            self.resize_handle.setZValue(10)

    def update_size(self, width, height):
        self.base_width = width
        self.base_height = height
        self.refresh_layout()
        if self.scene() and hasattr(self.scene(), 'view'):
            self.scene().view.main_window.project_manager.mark_dirty("workspace")

    def hoverEnterEvent(self, event):
        self.is_hovered = True
        if not self.isSelected():
            self.setZValue(100) 
        self.refresh_layout()
        super().hoverEnterEvent(event)

    def hoverLeaveEvent(self, event):
        if self.text_item.hasFocus(): return 
        self.is_hovered = False
        if not self.isSelected():
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
            if self.isSelected():
                self.setPen(QPen(QColor("#ffffff"), 4))
                self.setZValue(150) 
            else:
                self.setPen(QPen(QColor("#555555"), 2))
                self.setZValue(1 if not self.is_hovered else 100)
        return super().itemChange(change, value)

    def trigger_connect(self):
        if self.scene() and hasattr(self.scene(), 'view'):
            self.scene().view.start_connection(self)

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
        
        if self.scene() and hasattr(self.scene(), 'view'):
            self.scene().view.main_window.tabs["Notes"].save_workspace_state()
            
        if not self.is_custom and self.pdf_path is not None:
            notes_tab = self.scene().view.main_window.tabs["Notes"]
            notes_tab._modify_note(self.pdf_path, self.page_num, self.node_id, action="edit_content", content=self.note, refresh=False)
            
        if self.scene() and hasattr(self.scene(), 'view'):
            self.scene().view.main_window.project_manager.mark_dirty("workspace")
            
        self.hoverLeaveEvent(None)

    def trigger_color_change(self):
        color = QColorDialog.getColor(QColor(self.color))
        if color.isValid():
            self.color = color.name()
            self.setBrush(QBrush(QColor(self.color)))
            self.refresh_layout() 
            
            if self.scene() and hasattr(self.scene(), 'view'):
                self.scene().view.main_window.tabs["Notes"].save_workspace_state()
            
            if not self.is_custom and self.pdf_path is not None:
                notes_tab = self.scene().view.main_window.tabs["Notes"]
                notes_tab._modify_note(self.pdf_path, self.page_num, self.node_id, action="color", color=color.getRgbF()[:3], refresh=False)
                
            if self.scene() and hasattr(self.scene(), 'view'):
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
        
        self.setDragMode(QGraphicsView.DragMode.ScrollHandDrag)
        
        self.nodes = {}
        self.edges = []
        self.connecting_node = None
        self.loading_indicator = None

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
        if self.connecting_node:
            item = self.itemAt(event.pos())
            is_node = False
            current = item
            while current:
                if isinstance(current, Node):
                    is_node = True
                    break
                current = current.parentItem()
                
            if not is_node:
                self.connecting_node.setPen(QPen(QColor("#555555"), 2))
                self.connecting_node = None
                event.accept()
                return

        if event.button() == Qt.MouseButton.LeftButton and event.modifiers() == Qt.KeyboardModifier.ShiftModifier:
            self.setDragMode(QGraphicsView.DragMode.RubberBandDrag)
            item = self.itemAt(event.pos())
            while item and not isinstance(item, (Node, Edge)):
                item = item.parentItem()
            if isinstance(item, Node):
                item.setSelected(not item.isSelected())
                event.accept()
                return
        elif event.button() == Qt.MouseButton.LeftButton:
            self.setDragMode(QGraphicsView.DragMode.ScrollHandDrag)

        if event.button() == Qt.MouseButton.MiddleButton or (event.button() == Qt.MouseButton.LeftButton and event.modifiers() == Qt.KeyboardModifier.AltModifier):
            self.setDragMode(QGraphicsView.DragMode.ScrollHandDrag)
            event.accept()
            return

        super().mousePressEvent(event)

    def mouseReleaseEvent(self, event):
        super().mouseReleaseEvent(event)
        self.setDragMode(QGraphicsView.DragMode.ScrollHandDrag)

    def delete_edge(self, edge):
        if edge in edge.source_node.edges:
            edge.source_node.edges.remove(edge)
        if edge in edge.dest_node.edges:
            edge.dest_node.edges.remove(edge)
            
        self.scene_obj.removeItem(edge)
        if edge in self.edges:
            self.edges.remove(edge)
            
        self.main_window.project_manager.mark_dirty("workspace")

    def delete_node(self, node):
        for edge in list(node.edges):
            self.delete_edge(edge)
            
        self.scene_obj.removeItem(node)
        if node.node_id in self.nodes:
            del self.nodes[node.node_id]
            
        if not node.is_custom and node.pdf_path is not None:
            self.main_window.tabs["Notes"].save_workspace_state()
            self.main_window.tabs["Notes"].delete_note(node.pdf_path, node.page_num, node.node_id)
            
        self.main_window.project_manager.mark_dirty("workspace")

    def contextMenuEvent(self, event):
        item = self.itemAt(event.pos())
        
        while item and not isinstance(item, (Node, Edge)):
            item = item.parentItem()

        selected_nodes = [n for n in self.scene_obj.selectedItems() if isinstance(n, Node)]

        if len(selected_nodes) > 1 and isinstance(item, Node) and item in selected_nodes:
            menu = QMenu()
            ai_action = menu.addAction("✨ AI Organize Selected")
            del_action = menu.addAction("🗑️ Delete Selected Nodes")
            
            action = menu.exec(event.globalPos())
            if action == ai_action:
                self.trigger_ai_organize(selected_nodes)
            elif action == del_action:
                for n in selected_nodes:
                    self.delete_node(n)
            return

        if isinstance(item, Node):
            menu = QMenu()
            edit_action = menu.addAction("✏️ Edit Note Text")
            color_action = menu.addAction("🎨 Change Color")
            del_action = menu.addAction("🗑️ Delete Note")
            
            action = menu.exec(event.globalPos())
            if action == edit_action:
                item.trigger_edit()
            elif action == color_action:
                item.trigger_color_change()
            elif action == del_action:
                self.delete_node(item)
            return
            
        if isinstance(item, Edge):
            menu = QMenu()
            edit_action = menu.addAction("✏️ Edit Connection Text")
            color_action = menu.addAction("🎨 Change Line Color")
            del_action = menu.addAction("🗑️ Delete Connection")
            
            action = menu.exec(event.globalPos())
            if action == edit_action:
                text, ok = QInputDialog.getText(self, "Edit Label", "Enter new text:", text=item.label_text)
                if ok:
                    item.label_text = text
                    item.text_item.setPlainText(text)
                    item.update_position()
                    self.main_window.project_manager.mark_dirty("workspace")
            elif action == color_action:
                color = QColorDialog.getColor(item.base_color)
                if color.isValid():
                    item.base_color = color
                    item.setPen(QPen(item.base_color, 4 if item.isSelected() else 2, Qt.PenStyle.SolidLine))
                    self.main_window.project_manager.mark_dirty("workspace")
            elif action == del_action:
                self.delete_edge(item)
            return

        super().contextMenuEvent(event)

    def trigger_ai_organize(self, selected_nodes):
        if self.loading_indicator: return

        nodes_data = [{"id": n.node_id, "text": n.note or n.quote} for n in selected_nodes]
        llm_manager = self.main_window.tabs["LLM Chat"].llm_manager
        model = self.main_window.tabs["LLM Chat"].model_combo.currentText()

        self.loading_indicator = self.scene_obj.addText("✨ AI is organizing notes...", QFont("Arial", 16, QFont.Weight.Bold))
        self.loading_indicator.setDefaultTextColor(QColor("#00cc66"))
        
        view_center = self.mapToScene(self.viewport().rect().center())
        self.loading_indicator.setPos(view_center.x() - 150, view_center.y())
        self.loading_indicator.setZValue(1000)

        self.worker = AIOrganizeWorker(llm_manager, model, nodes_data)
        self.worker.finished.connect(self._on_ai_organize_finished)
        self.worker.start()

    def _on_ai_organize_finished(self, clusters, error_msg):
        if self.loading_indicator:
            self.scene_obj.removeItem(self.loading_indicator)
            self.loading_indicator = None

        if error_msg or not clusters:
            QMessageBox.warning(self, "AI Organize Failed", error_msg)
            return

        try:
            selected_nodes = [n for n in self.nodes.values() if n.isSelected()]
            if not selected_nodes: return

            avg_x = sum(n.pos().x() for n in selected_nodes) / len(selected_nodes)
            avg_y = sum(n.pos().y() for n in selected_nodes) / len(selected_nodes)

            start_x = avg_x - (len(clusters) * 125)
            current_x = start_x
            start_y = avg_y - 150

            for cluster in clusters:
                c_name = cluster.get("cluster_name", "Cluster")
                n_ids = cluster.get("node_ids", [])
                if not n_ids: continue

                cluster_node_id = f"custom_{uuid.uuid4()}"
                cluster_node = Node(cluster_node_id, quote="", note=c_name, color="#0078D7", is_custom=True, width=180, height=60)
                cluster_node.setPos(current_x, start_y)
                self.scene_obj.addItem(cluster_node)
                self.nodes[cluster_node_id] = cluster_node

                child_y = start_y + 120
                for nid in n_ids:
                    if nid in self.nodes:
                        child = self.nodes[nid]
                        child.setPos(current_x, child_y)
                        child_y += child.base_height + 25
                        
                        edge = Edge(cluster_node, child, "")
                        self.scene_obj.addItem(edge)
                        self.edges.append(edge)
                        
                        child.setSelected(False) 

                current_x += 280

            self.main_window.project_manager.mark_dirty("workspace")
        except Exception as e:
            QMessageBox.warning(self, "Layout Error", str(e))

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

        annot_dict = {a["id"]: a for a in pdf_annotations}

        saved_nodes = workspace_data.get("nodes", {})
        for n_id, data in saved_nodes.items():
            quote = data.get("quote", "")
            note = data.get("note", "")

            if n_id in annot_dict:
                quote = annot_dict[n_id]["subject"] or ""
                note = annot_dict[n_id]["content"] or ""

            node = Node(n_id, quote, note, data["color"], data["is_custom"], 
                        data["width"], data["height"], data.get("pdf_path"), data.get("page_num"), data.get("manual_font_size"))
            node.setPos(data["x"], data["y"])
            self.scene_obj.addItem(node)
            self.nodes[n_id] = node

        y_offset = 50
        for annot in pdf_annotations:
            if annot["id"] not in self.nodes:
                quote = annot["subject"] or ""
                note = annot["content"] or ""
                
                l = len(note + quote)
                w = 200 if l < 50 else (250 if l < 150 else 300)
                h = 70 if l < 50 else (110 if l < 150 else 160)
                
                color = "#2d2238" if annot["id"].startswith("AINote") else "#2b2b2b"
                
                node = Node(annot["id"], quote, note, color=color, is_custom=False, 
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