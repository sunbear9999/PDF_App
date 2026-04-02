# gui/components/annotation_manager.py
import fitz
import uuid
from PyQt6.QtWidgets import QGraphicsRectItem, QInputDialog, QWidget, QMenu
from PyQt6.QtGui import QColor, QBrush, QPen, QAction
from PyQt6.QtCore import Qt, QRectF, QObject, pyqtSignal

class AnnotationManager(QObject):
    note_added = pyqtSignal()

    def __init__(self, viewer):
        super().__init__()
        self.viewer = viewer
        
        self.is_selecting = False
        self.start_word_idx = None
        self.current_page_idx = -1
        self.page_words = [] 
        self.temp_highlights = [] 
        self.selected_words = []

    def toggle_search(self):
        if hasattr(self.viewer, 'toggle_search_bar'):
            self.viewer.toggle_search_bar()

    def _get_page_at_pos(self, scene_pos):
        for i, item in enumerate(self.viewer.page_items):
            if item.sceneBoundingRect().contains(scene_pos):
                return i, item
        return -1, None

    def _get_word_at_pos(self, local_pos, zoom):
        if not self.page_words: return None
        
        pdf_x, pdf_y = local_pos.x() / zoom, local_pos.y() / zoom
        best_idx = None
        best_dist = float('inf')
        
        for i, w in enumerate(self.page_words):
            rect = fitz.Rect(w[:4])
            if rect.contains(fitz.Point(pdf_x, pdf_y)):
                return i
                
            cx, cy = (w[0] + w[2]) / 2, (w[1] + w[3]) / 2
            dist = (pdf_x - cx)**2 + ((pdf_y - cy) * 4)**2 
            if dist < best_dist:
                best_dist = dist
                best_idx = i
                
        return best_idx if best_dist < 5000 else None

    def clear_selection(self):
        for h in self.temp_highlights:
            try:
                if h.scene():
                    self.viewer.scene.removeItem(h)
            except RuntimeError:
                pass # The C++ object was already deleted, safe to ignore
        self.temp_highlights.clear()
        self.selected_words.clear()
        self.start_word_idx = None

    def has_selection(self):
        return len(self.selected_words) > 0

    def is_pos_in_selection(self, scene_pos):
        if not self.temp_highlights: return False
        for h in self.temp_highlights:
            try:
                if h.sceneBoundingRect().contains(scene_pos):
                    return True
            except RuntimeError:
                pass
        return False

    def start_selection(self, event):
        self.clear_selection()
        scene_pos = self.viewer.mapToScene(event.pos())
        self.current_page_idx, self.active_page_item = self._get_page_at_pos(scene_pos)
        
        if self.current_page_idx != -1 and self.viewer.doc:
            self.is_selecting = True
            page = self.viewer.doc.load_page(self.current_page_idx)
            
            words = page.get_text("words")
            words.sort(key=lambda w: (w[5], w[6], w[7]))
            self.page_words = words
            
            local_pos = self.active_page_item.mapFromScene(scene_pos)
            self.start_word_idx = self._get_word_at_pos(local_pos, self.viewer.base_zoom)

    def update_selection(self, event):
        if self.is_selecting and self.start_word_idx is not None:
            scene_pos = self.viewer.mapToScene(event.pos())
            local_pos = self.active_page_item.mapFromScene(scene_pos)
            end_word_idx = self._get_word_at_pos(local_pos, self.viewer.base_zoom)
            
            if end_word_idx is not None:
                self._draw_temp_selection(self.start_word_idx, end_word_idx)

    def _draw_temp_selection(self, start_idx, end_idx):
        for h in self.temp_highlights:
            try:
                if h.scene():
                    self.viewer.scene.removeItem(h)
            except RuntimeError:
                pass
        self.temp_highlights.clear()

        lo, hi = sorted([start_idx, end_idx])
        zoom = self.viewer.base_zoom
        
        for w in self.page_words[lo:hi+1]:
            qt_rect = QRectF(w[0] * zoom, w[1] * zoom, (w[2]-w[0]) * zoom, (w[3]-w[1]) * zoom)
            scene_rect = self.active_page_item.mapToScene(qt_rect).boundingRect()
            
            h_item = QGraphicsRectItem(scene_rect)
            h_item.setBrush(QBrush(QColor(51, 153, 255, 100))) 
            h_item.setPen(QPen(Qt.PenStyle.NoPen))
            self.viewer.scene.addItem(h_item)
            self.temp_highlights.append(h_item)

    def finish_selection(self, event):
        if not self.is_selecting: return
        self.is_selecting = False
        
        if self.start_word_idx is not None and self.temp_highlights:
            scene_pos = self.viewer.mapToScene(event.pos())
            local_pos = self.active_page_item.mapFromScene(scene_pos)
            end_word_idx = self._get_word_at_pos(local_pos, self.viewer.base_zoom)
            
            if end_word_idx is not None:
                lo, hi = sorted([self.start_word_idx, end_word_idx])
                self.selected_words = self.page_words[lo:hi+1]

    def show_context_menu(self, global_pos):
        menu = QMenu(self.viewer)
        menu.setStyleSheet("""
            QMenu { background-color: #2b2b2b; color: white; border: 1px solid #444; font-weight: bold; } 
            QMenu::item:selected { background-color: #0078D7; }
        """)
        
        colors = [
            ("Yellow", (1.0, 0.9, 0.0)),
            ("Green", (0.0, 0.8, 0.4)),
            ("Blue", (0.2, 0.6, 1.0)),
            ("Purple", (0.7, 0.4, 1.0)),
            ("Red", (1.0, 0.3, 0.3))
        ]
        
        hl_menu = menu.addMenu("🖍️ Highlight...")
        for name, rgb in colors:
            action = QAction(f"{name}", self.viewer)
            action.triggered.connect(lambda checked, c=rgb: self.apply_highlight(c))
            hl_menu.addAction(action)
            
        menu.addSeparator()
        ai_action = menu.addAction("🤖 Ask AI About Selection")
        ai_action.triggered.connect(self.ask_ai_about_selection)
        
        menu.exec(global_pos)

    def apply_highlight(self, color_tuple):
        if not self.selected_words: return
        
        extracted_text = " ".join(w[4] for w in self.selected_words)
        text, ok = QInputDialog.getText(self.viewer, "Add Note", "Enter a note for this highlight (Optional):")
        
        if ok:
            try:
                page = self.viewer.doc.load_page(self.current_page_idx)
                quads = [fitz.Rect(w[:4]).quad for w in self.selected_words]
                
                annot = page.add_highlight_annot(quads)
                annot.set_colors(stroke=color_tuple)
                
                annot_info = {
                    "title": f"UserNote|{uuid.uuid4()}",
                    "content": text if text else "",
                    "subject": extracted_text
                }
                annot.set_info(info=annot_info)
                annot.update()
                
                self.viewer.reload_page(self.current_page_idx)
                self.note_added.emit()
            except Exception as e:
                print(f"Error saving highlight: {e}")
                
        self.clear_selection()

    def ask_ai_about_selection(self):
        if not self.selected_words: return
        extracted_text = " ".join(w[4] for w in self.selected_words)
        
        main_window = self.viewer.window()
        main_window.tool_buttons["LLM Chat"].setChecked(True)
        main_window.toggle_tool_panel("LLM Chat")
        
        llm_tab = main_window.tabs["LLM Chat"]
        llm_tab.chat_input.setText(f"Explain this text: \"{extracted_text}\"")
        llm_tab.chat_input.setFocus()
        
        self.clear_selection()