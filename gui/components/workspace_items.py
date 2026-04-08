# gui/components/workspace_items.py
import uuid
from PyQt6.QtWidgets import (QGraphicsRectItem, QGraphicsTextItem, QGraphicsLineItem, QGraphicsItem, 
                             QInputDialog, QColorDialog, QGraphicsProxyWidget,
                             QPushButton, QHBoxLayout, QWidget)
from PyQt6.QtCore import Qt, QLineF, QPointF, QTimer
from PyQt6.QtGui import QColor, QPen, QBrush, QFont, QTextDocument

from models.workspace_models import EdgeData, NodeData

from gui.theme import ThemeManager

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
    def __init__(self, source_node, dest_node, label_text="", edge_id=None, color="#888888", weight=2, edge_data=None):
        super().__init__()
        self.source_node = source_node
        self.dest_node = dest_node

        if isinstance(edge_data, EdgeData):
            self.label_text = label_text or edge_data.label
            self.edge_id = edge_id or edge_data.edge_id
            self.base_color = QColor(edge_data.color if color == "#888888" else color)
            self.weight = weight if weight != 2 else edge_data.weight
        else:
            self.label_text = label_text
            self.edge_id = edge_id or str(uuid.uuid4())
            self.base_color = QColor(color)
            self.weight = weight
        
        self.setZValue(-1) 
        self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsSelectable, True)
        self.setPen(QPen(self.base_color, self.weight, Qt.PenStyle.SolidLine))
        
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
        # FIX: Map the center of the node's true rectangle to the scene.
        # This prevents the edges from connecting to external toolbars or resize handles that stretch the BoundingRect.
        start = self.source_node.mapToScene(self.source_node.rect().center())
        end = self.dest_node.mapToScene(self.dest_node.rect().center())
        self.setLine(QLineF(start, end))
        
        center_x = (start.x() + end.x()) / 2
        center_y = (start.y() + end.y()) / 2
        text_rect = self.text_item.boundingRect()
        self.text_item.setPos(center_x - text_rect.width() / 2, center_y - text_rect.height() / 2 - 10)

    def trigger_edit(self):
        view = self.scene().view if self.scene() and hasattr(self.scene(), 'view') else None
        if view:
            view.save_state_for_undo()
            
        text, ok = QInputDialog.getText(view, "Edit Label", "Enter new text:", text=self.label_text)
        if ok:
            self.label_text = text
            self.text_item.setPlainText(text)
            self.update_position()
            if view:
                view.main_window.persistence_controller.mark_dirty("workspace")

    def trigger_color_change(self):
        view = self.scene().view if self.scene() and hasattr(self.scene(), 'view') else None
        
        # Explicitly set the view as the parent so the dialog cannot hide or be suppressed
        color = QColorDialog.getColor(self.base_color, view, "Select Line Color")
        
        if color.isValid():
            if view:
                view.save_state_for_undo()
            self.base_color = color
            self.setPen(QPen(self.base_color, self.weight + 2 if self.isSelected() else self.weight, Qt.PenStyle.SolidLine))
            if view:
                view.main_window.persistence_controller.mark_dirty("workspace")

    def trigger_weight_change(self):
        view = self.scene().view if self.scene() and hasattr(self.scene(), 'view') else None
        
        weight, ok = QInputDialog.getInt(view, "Line Weight", "Enter line weight (1-10):", self.weight, 1, 10)
        if ok:
            if view:
                view.save_state_for_undo()
            self.weight = weight
            self.setPen(QPen(self.base_color, self.weight + 2 if self.isSelected() else self.weight, Qt.PenStyle.SolidLine))
            if view:
                view.main_window.persistence_controller.mark_dirty("workspace")

    def itemChange(self, change, value):
        if change == QGraphicsItem.GraphicsItemChange.ItemSelectedHasChanged:
            if self.isSelected():
                # Fix: Don't turn the line white! Keep the chosen color but make it thicker to show selection
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
            self.clearFocus() # This now safely triggers focusOutEvent to commit the save
            return
        super().keyPressEvent(event)

    def focusOutEvent(self, event):
        # Automatically save text whenever the user clicks away or loses focus
        super().focusOutEvent(event)
        self.node.finish_in_place_edit()

class ResizeHandle(QGraphicsRectItem):
    def __init__(self, parent):
        super().__init__(0, 0, 16, 16, parent)
        # Make the grabber slightly more visible and obvious
        self.setBrush(QBrush(QColor(100, 100, 100, 255)))
        self.setPen(QPen(QColor(255, 255, 255, 200), 1))
        self.setCursor(Qt.CursorShape.SizeFDiagCursor)
        # FIX: We no longer rely on Qt's ItemIsMovable flag because Qt will hijack the event 
        # and drag the parent instead of resizing if the parent happens to be "selected".
        self._is_resizing = False
        self._start_pos = None

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self._is_resizing = True
            # Log initial sizes so we can add the mouse movement offsets directly
            self._start_pos = self.parentItem().mapFromScene(event.scenePos())
            self._start_w = self.parentItem().base_width
            self._start_h = self.parentItem().base_height
            if self.scene() and hasattr(self.scene(), 'view'):
                self.scene().view.save_state_for_undo()
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
    def __init__(self, node_id=None, quote="", note="", color=None, is_custom=False, width=150, height=80, pdf_path=None, page_num=None, manual_font_size=None, node_data=None):
        if isinstance(node_data, NodeData):
            data = node_data
            node_id = data.node_id
            quote = data.quote
            note = data.note
            color = data.color
            is_custom = data.is_custom
            width = data.width
            height = data.height
            pdf_path = data.pdf_path
            page_num = data.page_num
            manual_font_size = data.manual_font_size

        super().__init__(0, 0, width, height)
        self.node_id = node_id or str(uuid.uuid4())
        self.is_custom = is_custom
        self.quote = quote if quote else ""
        self.note = note if note else ""
        
        # If no color is provided, default to theme's panel color
        theme = ThemeManager().get_theme()
        if not color or color == "#333333":
            color = theme['bg_panel']
            
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
        # Fix: Disable clipping so the resize handle can sit completely outside the main box
        self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemClipsChildrenToShape, False)
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
        btn_font = QPushButton("📏 Size")
        btn_connect = QPushButton("🔗 Connect")
        
        buttons = [btn_edit, btn_color, btn_font, btn_connect]
        
        if self.pdf_path is not None:
            self.btn_jump = QPushButton("📄 Jump to PDF")
            buttons.append(self.btn_jump)
        
        for btn in buttons:
            btn.setStyleSheet(f"background-color: {theme['bg_panel']}; color: {theme['text_main']}; border-radius: 4px; padding: 2px 6px; font-size: 10px; font-weight: bold; border: 1px solid {theme['border']};")
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
        if view:
            if view.connecting_node and view.connecting_node != self:
                view.save_state_for_undo()
                view.finish_connection(self)
                event.accept()
                return
            else:
                view.save_state_for_undo()
        super().mousePressEvent(event)

    def trigger_jump(self):
        if self.pdf_path and self.page_num is not None:
            if self.scene() and hasattr(self.scene(), 'view'):
                main_win = self.scene().view.main_window
                pdf_path = self.pdf_path
                page_num = self.page_num
                annot_id = self.node_id
                
                if "Notes" in main_win.tabs:
                    main_win.tabs["Notes"].save_workspace_state()
                
                def do_jump():
                    main_win.switch_to_pdf(pdf_path)
                    if hasattr(main_win.viewer, "jump_to_annotation"):
                        main_win.viewer.jump_to_annotation(page_num, annot_id)
                    else:
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
            
        # Ensure the resize handle is ALWAYS visible, pegged diagonally outside the bottom right edge
        self.resize_handle.show()
        self.resize_handle.setPos(self.rect().width(), self.rect().height())
        self.resize_handle.setZValue(10)
        
        # FIX: Since node size actively changes here when hovered/unhovered, force edges to reposition
        for edge in self.edges:
            edge.update_position()

    def update_size(self, width, height):
        self.base_width = width
        self.base_height = height
        self.refresh_layout()
        if self.scene() and hasattr(self.scene(), 'view'):
            self.scene().view.main_window.persistence_controller.mark_dirty("workspace")

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
            if self.scene() and hasattr(self.scene(), 'view'):
                self.scene().view.main_window.persistence_controller.mark_dirty("workspace")
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
        if self.scene() and hasattr(self.scene(), 'view'):
            self.scene().view.save_state_for_undo()
        self.text_item.setPlainText(self.note)
        self.text_item.setDefaultTextColor(QColor(get_text_color_for_bg(self.color)))
        self.text_item.setTextInteractionFlags(Qt.TextInteractionFlag.TextEditorInteraction)
        self.text_item.setFocus()
        
        # FIX: Also set focus to the view so Qt knows where to send keyboard inputs!
        if self.scene() and hasattr(self.scene(), 'view'):
            self.scene().view.setFocus()

    def finish_in_place_edit(self):
        # Guard clause: Prevents saving logic from firing twice if multiple events close the editor
        if not (self.text_item.textInteractionFlags() & Qt.TextInteractionFlag.TextEditorInteraction):
            return
            
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
            self.scene().view.main_window.persistence_controller.mark_dirty("workspace")
            
        self.hoverLeaveEvent(None)

    def trigger_color_change(self):
        color = QColorDialog.getColor(QColor(self.color))
        if color.isValid():
            if self.scene() and hasattr(self.scene(), 'view'):
                self.scene().view.save_state_for_undo()
            self.color = color.name()
            self.setBrush(QBrush(QColor(self.color)))
            self.refresh_layout() 
            
            if self.scene() and hasattr(self.scene(), 'view'):
                self.scene().view.main_window.tabs["Notes"].save_workspace_state()
            
            if not self.is_custom and self.pdf_path is not None:
                notes_tab = self.scene().view.main_window.tabs["Notes"]
                notes_tab._modify_note(self.pdf_path, self.page_num, self.node_id, action="color", color=color.getRgbF()[:3], refresh=False)
                
            if self.scene() and hasattr(self.scene(), 'view'):
                self.scene().view.main_window.persistence_controller.mark_dirty("workspace")

    def trigger_font_size_change(self):
        current = self.manual_font_size if self.manual_font_size else 12
        val, ok = QInputDialog.getInt(None, "Font Size", "Enter static font size (8-72)\nCancel to Auto-Scale:", current, 8, 72)
        if ok:
            if self.scene() and hasattr(self.scene(), 'view'):
                self.scene().view.save_state_for_undo()
            self.manual_font_size = val
        else:
            if self.scene() and hasattr(self.scene(), 'view'):
                self.scene().view.save_state_for_undo()
            self.manual_font_size = None
        self.refresh_layout()
        if self.scene() and hasattr(self.scene(), 'view'):
            self.scene().view.main_window.persistence_controller.mark_dirty("workspace")