import fitz
import uuid
from PyQt6.QtWidgets import QGraphicsRectItem, QInputDialog, QWidget
from PyQt6.QtGui import QColor, QBrush, QPen
from PyQt6.QtCore import Qt, QRectF, QObject, pyqtSignal

class AnnotationManager(QObject):
    # Signal emitted when a note is successfully saved
    note_added = pyqtSignal()

    def __init__(self, viewer):
        super().__init__()
        self.viewer = viewer
        
        self.is_dragging = False
        self.start_word_idx = None
        self.current_page_idx = -1
        self.page_words = [] # Stores PyMuPDF word data for the active page
        
        self.temp_highlights = [] # List of QGraphicsRectItems for individual words

    def _get_page_at_pos(self, scene_pos):
        for i, item in enumerate(self.viewer.page_items):
            if item.sceneBoundingRect().contains(scene_pos):
                return i, item
        return -1, None

    def _get_word_at_pos(self, local_pos, zoom):
        """Finds the closest word to the mouse cursor."""
        if not self.page_words: return None
        
        # Convert Qt local pos to PyMuPDF document pos
        pdf_x, pdf_y = local_pos.x() / zoom, local_pos.y() / zoom
        
        best_idx = None
        best_dist = float('inf')
        
        for i, w in enumerate(self.page_words):
            cx, cy = (w[0] + w[2]) / 2, (w[1] + w[3]) / 2
            # Prioritize Y-axis proximity to keep selection on the same line
            dist = (pdf_x - cx)**2 + ((pdf_y - cy) * 4)**2 
            if dist < best_dist:
                best_dist = dist
                best_idx = i
                
        # Only snap if reasonably close (e.g., distance < 50)
        return best_idx if best_dist < 5000 else None

    def handle_mouse_press(self, event):
        scene_pos = self.viewer.mapToScene(event.pos())
        self.current_page_idx, self.active_page_item = self._get_page_at_pos(scene_pos)
        
        if self.current_page_idx != -1 and self.viewer.doc:
            self.is_dragging = True
            
            # Pre-load word data for this page for fast snapping
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
        # Clear previous temp highlights
        for h in self.temp_highlights:
            self.viewer.scene.removeItem(h)
        self.temp_highlights.clear()

        lo, hi = sorted([start_idx, end_idx])
        zoom = self.viewer.base_zoom
        
        # Draw a blue rect over every word in the range
        for w in self.page_words[lo:hi+1]:
            qt_rect = QRectF(w[0] * zoom, w[1] * zoom, (w[2]-w[0]) * zoom, (w[3]-w[1]) * zoom)
            scene_rect = self.active_page_item.mapToScene(qt_rect).boundingRect()
            
            h_item = QGraphicsRectItem(scene_rect)
            h_item.setBrush(QBrush(QColor(51, 153, 255, 100))) # Chrome-blue selection
            h_item.setPen(QPen(Qt.PenStyle.NoPen))
            self.viewer.scene.addItem(h_item)
            self.temp_highlights.append(h_item)

    def handle_mouse_release(self, event):
        if not self.is_dragging: return
        self.is_dragging = False
        
        if not self.temp_highlights: return
        
        # Extract the actual words selected
        scene_pos = self.viewer.mapToScene(event.pos())
        local_pos = self.active_page_item.mapFromScene(scene_pos)
        end_word_idx = self._get_word_at_pos(local_pos, self.viewer.base_zoom)
        
        if end_word_idx is not None and self.start_word_idx is not None:
            lo, hi = sorted([self.start_word_idx, end_word_idx])
            selected_words = self.page_words[lo:hi+1]
            
            self._finalize_highlight(selected_words)
            
        # Clean up temporary drag highlights
        for h in self.temp_highlights:
            self.viewer.scene.removeItem(h)
        self.temp_highlights.clear()

    def _finalize_highlight(self, selected_words):
        if not selected_words: return
        
        extracted_text = " ".join(w[4] for w in selected_words)
        
        # Prompt user
        text, ok = QInputDialog.getText(self.viewer, "Add Note", "Enter a note for this highlight:")
        
        if ok:
            page = self.viewer.doc.load_page(self.current_page_idx)
            quads = [fitz.Rect(w[:4]).quad for w in selected_words]
            
            # Save to PDF
            annot = page.add_highlight_annot(quads)
            annot.set_colors(stroke=(0, 0.8, 0.4))
            annot.set_info(title=f"UserNote|{uuid.uuid4()}", content=text if text else "", subject=extracted_text)
            annot.update()
            
            # Draw permanent green highlight on the Qt Canvas
            zoom = self.viewer.base_zoom
            for w in selected_words:
                qt_rect = QRectF(w[0] * zoom, w[1] * zoom, (w[2]-w[0]) * zoom, (w[3]-w[1]) * zoom)
                scene_rect = self.active_page_item.mapToScene(qt_rect).boundingRect()
                perm = QGraphicsRectItem(scene_rect)
                perm.setBrush(QBrush(QColor(0, 204, 102, 100)))
                perm.setPen(QPen(Qt.PenStyle.NoPen))
                self.viewer.scene.addItem(perm)

            # TELL THE UI TO UPDATE THE NOTES TAB
            self.note_added.emit()