# gui/components/workspace_items.py
import uuid
from PySide6.QtWidgets import (QGraphicsRectItem, QGraphicsTextItem, QGraphicsLineItem, QGraphicsItem, 
                             QInputDialog, QColorDialog, QGraphicsProxyWidget,
                             QPushButton, QHBoxLayout, QVBoxLayout, QWidget)
from PySide6.QtCore import Qt, QLineF, QPointF, QTimer, QRectF
from PySide6.QtGui import QColor, QPen, QBrush, QFont, QTextDocument, QPainter, QTextCursor

from gui.theme.theme import ThemeManager

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
    """Custom inline editor that tracks the exact moment a human modifies AI text."""
    def __init__(self, parent_node):
        super().__init__(parent_node)
        self.parent_node = parent_node

    def focusOutEvent(self, event):
        super().focusOutEvent(event)
        self.setTextInteractionFlags(Qt.TextInteractionFlag.NoTextInteraction)
        
        new_text = self.toPlainText()
        if new_text != self.parent_node.note:
            view = self.scene().view if self.scene() and hasattr(self.scene(), "view") else None
            if view: view.save_state_for_undo()
                
            current_orig = getattr(self.parent_node, "original_text", self.parent_node.note)
            if current_orig == self.parent_node.note:
                self.parent_node.original_text = self.parent_node.note
                
            self.parent_node.note = new_text
            self.parent_node.refresh_layout()
            
            # Simplified save!
            if view:
                view._mark_workspace_dirty(autosave=True)

class Edge(QGraphicsLineItem):
    def __init__(self, source_node, dest_node, label_text="", edge_id=None, color="#888888", weight=2):
        super().__init__()
        self.source_node = source_node
        self.dest_node = dest_node
        self.label_text = label_text
        self.edge_id = edge_id or str(uuid.uuid4())
        self.base_color = QColor(color)
        self.weight = weight
        
        self.setZValue(-1) 
        self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsSelectable, True)
        self.setPen(QPen(self.base_color, self.weight, Qt.PenStyle.SolidLine))
        
        self.text_item = EditableTextItem(self)
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
        
        # 🔥 FIX 7: Create a fat, 15px invisible hitbox for easy right-clicking
        stroker = QPainterPathStroker()
        stroker.setWidth(15) 
        stroked_path = stroker.createStroke(path)
        
        if self.text_item.scene():
            text_rect = self.text_item.mapRectToParent(self.text_item.boundingRect())
            stroked_path.addRect(text_rect)
        return stroked_path

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
        """Activates true native inline editing directly on the canvas."""
        # Enable inline editing
        self.text_item.setTextInteractionFlags(Qt.TextInteractionFlag.TextEditorInteraction)
        self.text_item.setFocus()
        
        # Automatically move the typing cursor to the end of the text
        cursor = self.text_item.textCursor()
        cursor.movePosition(QTextCursor.MoveOperation.End)
        self.text_item.setTextCursor(cursor)

    def trigger_color_change(self):
        view = self.scene().view if self.scene() and hasattr(self.scene(), 'view') else None
        
        # Explicitly set the view as the parent so the dialog cannot hide or be suppressed
        color = QColorDialog.getColor(self.base_color, view, "Select Line Color")
        
        if color.isValid():
            if view:
                view.save_state_for_undo()
                view._mark_workspace_dirty(autosave=True)
            self.base_color = color
           
            self.setPen(QPen(self.base_color, self.weight + 2 if self.isSelected() else self.weight, Qt.PenStyle.SolidLine))
            if view:
                view.main_window.project_manager.mark_dirty("workspace")

    def trigger_weight_change(self):
        view = self.scene().view if self.scene() and hasattr(self.scene(), 'view') else None
        
        weight, ok = QInputDialog.getInt(view, "Line Weight", "Enter line weight (1-10):", self.weight, 1, 10)
        if ok:
            if view:
                view.save_state_for_undo()
                view._mark_workspace_dirty(autosave=True)
            self.weight = weight
            self.setPen(QPen(self.base_color, self.weight + 2 if self.isSelected() else self.weight, Qt.PenStyle.SolidLine))
            if view:
                view.main_window.project_manager.mark_dirty("workspace")

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
    def __init__(self, node_id, quote, note, color=None, is_custom=False, width=150, height=80, pdf_path=None, page_num=None, manual_font_size=None, highlight_id=None,node_origin="human", is_verified=0, original_text=None):
        super().__init__(0, 0, width, height)
        self.node_id = node_id
        self.highlight_id = highlight_id
        self.is_custom = is_custom
        self.quote = quote if quote else ""
        self.note = note if note else ""
        self.node_origin = node_origin
        self.is_verified = bool(is_verified)
        self.original_text = original_text if original_text is not None else note # <--- ADD THIS
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
        self.tag_colors = []
        self.tag_badges = []
        self._tag_colors_loaded = False
        
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
        
        # Stack the buttons in two rows so they fit inside the node
        t_layout = QVBoxLayout(self.toolbar_widget)
        t_layout.setContentsMargins(0,0,0,0)
        t_layout.setSpacing(2)
        
        row1 = QHBoxLayout()
        row2 = QHBoxLayout()
        row1.setContentsMargins(0,0,0,0)
        row2.setContentsMargins(0,0,0,0)
        
        btn_edit = QPushButton("✏️ Edit")
        btn_color = QPushButton("🎨 Color")
        btn_font = QPushButton("📏 Size")
        btn_connect = QPushButton("🔗 Connect")
        
        row1.addWidget(btn_edit)
        row1.addWidget(btn_color)
        row1.addWidget(btn_font)
        row2.addWidget(btn_connect)
        
        buttons = [btn_edit, btn_color, btn_font, btn_connect]
        
        if self.pdf_path is not None:
            self.btn_jump = QPushButton("📄 Jump to PDF")
            row2.addWidget(self.btn_jump)
            buttons.append(self.btn_jump)

        if self.node_origin == "ai":
            self.btn_verify = QPushButton("🛡️ Verified" if self.is_verified else "⚠️ Verify AI")
            row2.addWidget(self.btn_verify)
            buttons.append(self.btn_verify)
            
        t_layout.addLayout(row1)
        t_layout.addLayout(row2)
            
        for btn in buttons:
            if self.node_origin == "ai" and btn == getattr(self, "btn_verify", None) and not self.is_verified:
                # Explicit Red Styling for unverified AI notes
                btn.setStyleSheet(f"background-color: #aa0000; color: white; border-radius: 4px; padding: 2px 6px; font-size: 10px; font-weight: bold; border: 1px solid #ff4444;")
            else:
                btn.setStyleSheet(f"background-color: {theme['bg_panel']}; color: {theme['text_main']}; border-radius: 4px; padding: 2px 6px; font-size: 10px; font-weight: bold; border: 1px solid {theme['border']};")
            
        btn_edit.clicked.connect(self.trigger_edit)
        btn_color.clicked.connect(self.trigger_color_change)
        btn_font.clicked.connect(self.trigger_font_size_change)
        btn_connect.clicked.connect(self.trigger_connect)
        
        if self.pdf_path is not None:
            self.btn_jump.clicked.connect(self.trigger_jump)
            
        self.proxy_toolbar = QGraphicsProxyWidget(self)
        self.proxy_toolbar.setWidget(self.toolbar_widget)
        self.proxy_toolbar.hide()
        
        if self.node_origin == "ai":
            self.btn_verify.clicked.connect(self.trigger_verify)
        
        self.refresh_layout()

    def trigger_verify(self):
        """Toggles the verification status of an AI note."""
        self.is_verified = not self.is_verified
        
        # Update UI Button
        theme = ThemeManager().get_theme()
        if self.is_verified:
            self.btn_verify.setText("🛡️ Verified")
            self.btn_verify.setStyleSheet(f"background-color: {theme['bg_panel']}; color: {theme['text_main']}; border-radius: 4px; padding: 2px 6px; font-size: 10px; font-weight: bold; border: 1px solid {theme['border']};")
        else:
            self.btn_verify.setText("⚠️ Verify AI")
            self.btn_verify.setStyleSheet(f"background-color: #aa0000; color: white; border-radius: 4px; padding: 2px 6px; font-size: 10px; font-weight: bold; border: 1px solid #ff4444;")
        
        # Update the database
        view = self.scene().view if self.scene() and hasattr(self.scene(), "view") else None
        if view and hasattr(view.main_window, 'project_manager'):
            view.save_state_for_undo()
            view.main_window.project_manager.set_node_verification(self.node_id, self.is_verified)
            view._mark_workspace_dirty(autosave=True)
            
        self.update()

    def mousePressEvent(self, event):
        view = self.scene().view if self.scene() and hasattr(self.scene(), 'view') else None
        if view and event and event.button() == Qt.MouseButton.LeftButton:
            clicked_tag = self._tag_name_at_pos(event.pos())
            if clicked_tag:
                view.apply_tag_filter(clicked_tag)
                event.accept()
                return

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
                from PySide6.QtCore import QTimer
                view = self.scene().view
                main_win = view.main_window
                
                # 1. Save the workspace state directly!
                if hasattr(view, 'save_workspace_state'):
                    view.save_workspace_state()
                
                annot_id = getattr(self, 'highlight_id', None) or getattr(self, 'node_id', None)
                
                def do_jump():
                    # 2. Switch the PDF (takes exactly 1 argument: the path)
                    if hasattr(main_win, 'switch_to_pdf'):
                        main_win.switch_to_pdf(self.pdf_path)
                    
                    # 3. Tell the viewer to jump to the coordinate
                    if hasattr(main_win.viewer, "jump_to_annotation"):
                        main_win.viewer.jump_to_annotation(self.page_num, annot_id)
                    else:
                        main_win.viewer.jump_to_page(self.page_num)
                    
                # Execute jump safely in the next event loop cycle
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

    # gui/components/workspace_items.py -> Node class
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
            
        # 🔥 FIX: Show BOTH the note and the quote in the collapsed view so the AI's context is never hidden!
        if self.note and self.quote and self.quote != self.note and not self.is_custom:
            collapsed_text = f'{self.note}\n\n"{self.quote}"'
        elif self.note:
            collapsed_text = self.note
        elif self.quote:
            collapsed_text = f'"{self.quote}"'
        else:
            collapsed_text = ""
            
        if self.is_hovered:
            needed_width = max(self.base_width, 320) 
            self.text_item.setTextWidth(needed_width - (margin * 2))
            
            font_size = self.manual_font_size if self.manual_font_size else 12
            self.text_item.setFont(QFont("Arial", font_size))
            
            self.text_item.setDefaultTextColor(text_color)
            self.text_item.setPlainText(expanded_text)
            
            doc_height = self.text_item.document().size().height()
            needed_height = max(self.base_height, doc_height + (margin * 2) + 60) # Extra room for buttons
            
            self.setRect(0, 0, needed_width, needed_height)
            self.proxy_toolbar.setPos(margin, needed_height - 55) # Adjust toolbar position
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
        self._mark_workspace_dirty(autosave=True)

    def _load_tag_colors(self):
        badges = []
        try:
            view = self.scene().view if self.scene() and hasattr(self.scene(), "view") else None
            pm = view.main_window.project_manager if view and hasattr(view.main_window, "project_manager") else None
            if pm:
                tags = pm.get_tags_for_node(self.node_id)
                badges = [
                    {"name": t.get("name") or "", "color": t.get("color") or "#808080"}
                    for t in tags
                ]
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

    # gui/components/workspace_items.py -> Node class
    def _get_tag_dot_regions(self):
        if not self._tag_colors_loaded:
            self._load_tag_colors()

        max_dots = 5
        spacing = 4
        shown = self.tag_badges[:max_dots]
        
        # 🔥 FIX: Define simple 10x10px circles
        dot_radius = 5
        dot_diam = dot_radius * 2
        
        # Start from top-right corner
        x = self.rect().right() - 8 - dot_diam
        y = self.rect().top() + 8

        regions = []
        for badge in shown:
            regions.append((QRectF(x, y, dot_diam, dot_diam), badge.get("name") or ""))
            x -= (dot_diam + spacing) # Move left for the next dot
        return regions

    

   

    def _tag_name_at_pos(self, pos):
        for rect, tag_name in self._get_tag_dot_regions():
            if tag_name and rect.contains(pos):
                return tag_name
        return None

    def _mark_workspace_dirty(self, autosave=True):
        view = self.scene().view if self.scene() and hasattr(self.scene(), "view") else None
        if view and hasattr(view, "_mark_workspace_dirty"):
            view._mark_workspace_dirty(autosave=autosave)
        elif view:
            view.main_window.project_manager.mark_dirty("workspace")

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
            self._mark_workspace_dirty(autosave=True)
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

        # Draw ghost placeholder text directly on the canvas if empty
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

        if not self.tag_badges:
            return
        if self.node_origin == "ai":
            painter.save()
            shield_icon = "🛡️" if self.is_verified else "⚠️"
            
            # Draw it in the top left corner
            rect = QRectF(4, 4, 20, 20)
            
            # If unverified, draw a subtle red glow warning
            if not self.is_verified:
                painter.setBrush(QBrush(QColor(255, 0, 0, 40)))
                painter.setPen(Qt.PenStyle.NoPen)
                painter.drawEllipse(rect.center(), 12, 12)
                
            painter.setPen(QPen(QColor(255, 255, 255)))
            font = QFont("Arial", 10)
            painter.setFont(font)
            painter.drawText(rect, Qt.AlignmentFlag.AlignCenter, shield_icon)
            painter.restore()
        # 🔥 FIX: Draw clean, borderless colored dots
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.setPen(Qt.PenStyle.NoPen) # No borders for a minimalist look

        for rect, tag_name in self._get_tag_dot_regions():
            color_hex = next((b.get("color") for b in self.tag_badges if b.get("name") == tag_name), "#808080")
            painter.setBrush(QBrush(QColor(color_hex)))
            painter.drawEllipse(rect)

        painter.restore()

    def trigger_connect(self):
        if self.scene() and hasattr(self.scene(), 'view'):
            self.scene().view.start_connection(self)

    def trigger_edit(self):
        if self.scene() and hasattr(self.scene(), 'view'):
            self.scene().view.save_state_for_undo()
            self.scene().view._mark_workspace_dirty(autosave=True)
            
        # Switch to plain text editing mode natively on the canvas
        self.text_item.setPlainText(self.note)
        self.text_item.setDefaultTextColor(QColor(get_text_color_for_bg(self.color)))
        self.text_item.setTextInteractionFlags(Qt.TextInteractionFlag.TextEditorInteraction)
        self.text_item.setFocus()
        
        # Automatically move the typing cursor to the end of the text
        cursor = self.text_item.textCursor()
        cursor.movePosition(QTextCursor.MoveOperation.End)
        self.text_item.setTextCursor(cursor)
        
        if self.scene() and hasattr(self.scene(), 'view'):
            self.scene().view.setFocus()

    def finish_in_place_edit(self):
        if not (self.text_item.textInteractionFlags() & Qt.TextInteractionFlag.TextEditorInteraction):
            return
            
        self.text_item.setTextInteractionFlags(Qt.TextInteractionFlag.NoTextInteraction)
        new_text = self.text_item.toPlainText().strip()
        
        if new_text != self.note:
            current_orig = getattr(self, "original_text", self.note)
            if current_orig == self.note:
                self.original_text = self.note
                
            self.note = new_text
            
            # Simplified save!
            view = self.scene().view if self.scene() and hasattr(self.scene(), "view") else None
            if view:
                view._mark_workspace_dirty(autosave=True)

        self.refresh_layout()
        self.hoverLeaveEvent(None)

    def trigger_color_change(self):
        color = QColorDialog.getColor(QColor(self.color))
        if color.isValid():
            if self.scene() and hasattr(self.scene(), 'view'):
                self.scene().view.save_state_for_undo()
                self.scene().view._mark_workspace_dirty(autosave=True)
            self.color = color.name()
            self.setBrush(QBrush(QColor(self.color)))
            self.refresh_layout() 
            
            if self.scene() and hasattr(self.scene(), 'view'):
                view = self.scene().view
                main_window = view.main_window
                
                # 1. Save workspace state directly via the view
                if hasattr(view, 'save_workspace_state'):
                    view.save_workspace_state()
                
                # 2. Broadcast the content edit to all active Notes docks
                if not getattr(self, 'is_custom', False) and getattr(self, 'pdf_path', None) is not None:
                    annot_id = getattr(self, 'highlight_id', None) or getattr(self, 'node_id', None)
                    if hasattr(main_window, 'notes_docks'):
                        for notes_dock in main_window.notes_docks:
                            notes_dock._modify_note(
                                self.pdf_path, 
                                self.page_num, 
                                annot_id, 
                                action="edit_content", 
                                content=getattr(self, 'note', ''), 
                                refresh=False
                            )
                
            if self.scene() and hasattr(self.scene(), 'view'):
                self.scene().view.main_window.project_manager.mark_dirty("workspace")

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
        self._mark_workspace_dirty(autosave=True)