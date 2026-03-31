import fitz
from PyQt6.QtWidgets import QGraphicsView, QGraphicsScene, QGraphicsPixmapItem, QGraphicsRectItem, QFrame, QVBoxLayout, QLabel
from PyQt6.QtGui import QImage, QPixmap, QPainter, QColor
from PyQt6.QtCore import Qt, QThread, pyqtSignal, QRectF

from gui.components.annotation_manager import AnnotationManager

# ------------------------------------------------------------------
# Background worker to render PDF pages without freezing the UI
# ------------------------------------------------------------------
class RenderWorker(QThread):
    page_ready = pyqtSignal(int, QImage)
    finished_rendering = pyqtSignal()

    def __init__(self, doc, zoom):
        super().__init__()
        self.doc = doc
        self.zoom = zoom
        self._is_running = True

    def run(self):
        mat = fitz.Matrix(self.zoom, self.zoom)
        for i in range(len(self.doc)):
            if not self._is_running:
                break
            page = self.doc.load_page(i)
            pix = page.get_pixmap(matrix=mat)
            
            # Convert PyMuPDF's raw bytes into a high-speed Qt Image
            img = QImage(pix.samples, pix.width, pix.height, pix.stride, QImage.Format.Format_RGB888)
            
            # We must copy the image because PyMuPDF will free the original memory
            self.page_ready.emit(i, img.copy())
        self.finished_rendering.emit()

    def stop(self):
        self._is_running = False


class PDFViewer(QGraphicsView):
    def __init__(self, parent=None):
        super().__init__(parent)
        
        # QGraphicsScene is Qt's hardware-accelerated canvas
        self.scene = QGraphicsScene(self)
        self.setScene(self.scene)
        
        # UI Optimizations for buttery smooth scrolling
        self.setRenderHint(QPainter.RenderHint.Antialiasing)
        self.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        self.setDragMode(QGraphicsView.DragMode.ScrollHandDrag)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOn)
        
        self.doc = None
        self.base_zoom = 1.5
        self.page_items = []
        self.worker = None
        
        # Attach our new PyQt6 Annotation Manager
        self.annot_manager = AnnotationManager(self)

    def load_document(self, pdf_path):
        if self.worker and self.worker.isRunning():
            self.worker.stop()
            self.worker.wait()

        if self.doc:
            self.doc.close()

        try:
            self.doc = fitz.open(pdf_path)
            self.scene.clear()
            self.page_items.clear()
            
            # Start background rendering
            self.worker = RenderWorker(self.doc, self.base_zoom)
            self.worker.page_ready.connect(self._on_page_ready)
            self.worker.start()
            return True
        except Exception as e:
            print(f"Failed to load: {e}")
            return False

    def _on_page_ready(self, page_num, qimage):
        """Called safely from the background thread when a page is done rendering."""
        pixmap = QPixmap.fromImage(qimage)
        item = QGraphicsPixmapItem(pixmap)
        
        # Stack pages vertically with a 20px gap
        y_offset = sum(p.boundingRect().height() + 20 for p in self.page_items)
        item.setPos(0, y_offset)
        
        self.scene.addItem(item)
        self.page_items.append(item)
        
        # Adjust scene boundaries dynamically
        self.scene.setSceneRect(self.scene.itemsBoundingRect())

    # ------------------------------------------------------------------
    # Pass mouse events to AnnotationManager for highlighting
    # ------------------------------------------------------------------
    def mousePressEvent(self, event):
        if event.modifiers() == Qt.KeyboardModifier.ShiftModifier:
            self.setDragMode(QGraphicsView.DragMode.NoDrag)
            self.annot_manager.handle_mouse_press(event)
        else:
            self.setDragMode(QGraphicsView.DragMode.ScrollHandDrag)
            super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if self.dragMode() == QGraphicsView.DragMode.NoDrag:
            self.annot_manager.handle_mouse_move(event)
        else:
            super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        if self.dragMode() == QGraphicsView.DragMode.NoDrag:
            self.annot_manager.handle_mouse_release(event)
        super().mouseReleaseEvent(event)
        
    def jump_to_page(self, page_num):
        if 0 <= page_num < len(self.page_items):
            target_item = self.page_items[page_num]
            # Smoothly auto-scroll to the top of the requested page
            self.ensureVisible(target_item, 0, 50)
    # Add these methods inside your PDFViewer class in pdf_viewer.py

    def zoom_in(self):
        self.scale(1.2, 1.2) # Zoom in by 20%

    def zoom_out(self):
        self.scale(1 / 1.2, 1 / 1.2) # Zoom out by 20%

    def zoom_reset(self):
        """Calculates the exact scale to fit the PDF width to the window"""
        if not self.page_items: return
        
        # Reset any existing transformations
        self.resetTransform()
        
        # Get width of viewport vs width of the actual PDF image
        view_width = self.viewport().width()
        doc_width = self.page_items[0].boundingRect().width()
        
        if doc_width > 0:
            # Leave a 40px buffer for the scrollbar and margins
            target_scale = (view_width - 40) / doc_width
            self.scale(target_scale, target_scale)