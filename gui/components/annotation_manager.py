# gui/components/annotation_manager.py
import fitz
import uuid
import weakref  # [PERF FIX] Avoid circular references
from PyQt6.QtWidgets import QGraphicsRectItem
from PyQt6.QtGui import QColor, QBrush, QPen
from PyQt6.QtCore import Qt, QRectF, QObject, pyqtSignal

from gui.components.annotation.reword import RewordDialog
from gui.components.annotation.context_menu import AnnotationContextMenu

class AnnotationManager(QObject):
    note_added = pyqtSignal()

    def __init__(self, viewer):
        super().__init__()
        # [PERF FIX] Use weakref to avoid circular reference with viewer
        self.viewer_ref = weakref.ref(viewer)
        
        self.is_selecting = False
        self.start_word_idx = None
        self.current_page_idx = -1
        self.page_words = [] 
        self.temp_highlights = [] 
        self.selected_words = []
        self.context_menu = AnnotationContextMenu(self)

    @property
    def viewer(self):
        # [PERF FIX] Safe access to viewer through weakref
        v = self.viewer_ref()
        if v is None:
            raise RuntimeError("Viewer has been deleted")
        return v

    def toggle_search(self):
        if hasattr(self.viewer, 'toggle_search_bar'):
            self.viewer.toggle_search_bar()

    def add_annotations_for_page(self, page_num, pixmap_item):
        """Support cached page rendering without crashing if no annotation overlay is needed."""
        return

    def _get_page_at_pos(self, scene_pos):
        # [PERF FIX] Safely iterate page items which can be a dict now
        page_items = self.viewer.page_items
        if isinstance(page_items, dict):
            for page_num in sorted(page_items.keys()):
                item = page_items[page_num]
                if item.sceneBoundingRect().contains(scene_pos):
                    return page_num, item
        else:
            for i, item in enumerate(page_items):
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
        # [PERF FIX] Safe cleanup of graphics items and list references
        for h in self.temp_highlights:
            try:
                if h and h.scene():
                    self.viewer.scene.removeItem(h)
            except (RuntimeError, AttributeError):
                pass  # Item already deleted, safely ignore
        # [PERF FIX] Immediately clear references
        self.temp_highlights.clear()
        self.selected_words.clear()
        self.start_word_idx = None

    def has_selection(self):
        return len(self.selected_words) > 0

    def is_pos_in_selection(self, scene_pos):
        if not self.temp_highlights: return False
        for h in self.temp_highlights:
            try:
                if h and h.sceneBoundingRect().contains(scene_pos):
                    return True
            except (RuntimeError, AttributeError):
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
        self.context_menu.show_context_menu(global_pos)

    def apply_highlight(self, color_tuple):
        self.context_menu.apply_highlight(color_tuple)

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

    def reword_selection(self):
        if not self.selected_words: return
        extracted_text = " ".join(w[4] for w in self.selected_words)
        
        main_window = self.viewer.window()
        llm_tab = main_window.tabs.get("LLM Chat")
        
        if llm_tab:
            llm_manager = llm_tab.llm_manager
            model = llm_tab.model_combo.currentText()
            
            # Keep a reference to the dialog so it isn't garbage collected
            self.reword_dialog = RewordDialog(extracted_text, llm_manager, model, self.viewer)
            self.reword_dialog.show()
        
        self.clear_selection()