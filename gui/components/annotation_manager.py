import fitz
import uuid
from PyQt6.QtWidgets import QGraphicsRectItem, QInputDialog, QWidget
from PyQt6.QtGui import QColor, QBrush, QPen
from PyQt6.QtCore import Qt, QRectF, QObject, pyqtSignal

class AnnotationManager(QObject):
    note_added = pyqtSignal()

    def __init__(self, viewer):
        super().__init__()
        self.viewer = viewer
        
        self.is_dragging = False
        self.start_word_idx = None
        self.current_page_idx = -1
        self.page_words = [] 
        self.temp_highlights = [] 

    def toggle_search(self):
        # Delegate to the modern Search Bar in the viewer
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
            cx, cy = (w[0] + w[2]) / 2, (w[1] + w[3]) / 2
            dist = (pdf_x - cx)**2 + ((pdf_y - cy) * 4)**2 
            if dist < best_dist:
                best_dist = dist
                best_idx = i
                
        return best_idx if best_dist < 5000 else None

    def handle_mouse_press(self, event):
        scene_pos = self.viewer.mapToScene(event.pos())
        self.current_page_idx, self.active_page_item = self._get_page_at_pos(scene_pos)
        
        if self.current_page_idx != -1 and self.viewer.doc:
            self.is_dragging = True
            page = self.viewer.doc.load_page(self.current_page_idx)
            self.page_words = page.get_text("words", sort=True)
            local_pos = self.active_page_item.mapFromScene(scene_pos)
            self.start_word_idx = self._get_word_at_pos(local_pos, self.viewer.base_zoom)

    def handle_mouse_move(self, event):
        if self.is_dragging and self.start_word_idx is not None:
            scene_pos = self.viewer.mapToScene(event.pos())
            local_pos = self.active_page_item.mapFromScene(scene_pos)
            end_word_idx = self._get_word_at_pos(local_pos, self.viewer.base_zoom)
            
            if end_word_idx is not None:
                self._draw_temp_selection(self.start_word_idx, end_word_idx)

    def _draw_temp_selection(self, start_idx, end_idx):
        for h in self.temp_highlights:
            self.viewer.scene.removeItem(h)
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

    def handle_mouse_release(self, event):
        if not self.is_dragging: return
        self.is_dragging = False
        if not self.temp_highlights: return
        
        scene_pos = self.viewer.mapToScene(event.pos())
        local_pos = self.active_page_item.mapFromScene(scene_pos)
        end_word_idx = self._get_word_at_pos(local_pos, self.viewer.base_zoom)
        
        if end_word_idx is not None and self.start_word_idx is not None:
            lo, hi = sorted([self.start_word_idx, end_word_idx])
            selected_words = self.page_words[lo:hi+1]
            self._finalize_highlight(selected_words)
            
        for h in self.temp_highlights:
            self.viewer.scene.removeItem(h)
        self.temp_highlights.clear()

    def _finalize_highlight(self, selected_words):
        if not selected_words: return
        
        extracted_text = " ".join(w[4] for w in selected_words)
        text, ok = QInputDialog.getText(self.viewer, "Add Note", "Enter a note for this highlight:")
        
        if ok:
            try:
                page = self.viewer.doc.load_page(self.current_page_idx)
                quads = [fitz.Rect(w[:4]).quad for w in selected_words]
                
                annot = page.add_highlight_annot(quads)
                annot.set_colors(stroke=(1.0, 0.9, 0.0))
                
                # CRITICAL FIX: Build a dictionary to prevent PyMuPDF memory corruption and segfaults
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