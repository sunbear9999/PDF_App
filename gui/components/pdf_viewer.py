import fitz
from PyQt6.QtWidgets import QGraphicsView, QGraphicsScene, QGraphicsPixmapItem, QFrame, QVBoxLayout, QLabel
from PyQt6.QtGui import QImage, QPixmap, QPainter, QColor
from PyQt6.QtCore import Qt, QThread, pyqtSignal, QRectF

from gui.components.annotation_manager import AnnotationManager

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
            
            img = QImage(pix.samples, pix.width, pix.height, pix.stride, QImage.Format.Format_RGB888)
            self.page_ready.emit(i, img.copy())
        self.finished_rendering.emit()

    def stop(self):
        self._is_running = False


class PDFViewer(QGraphicsView):
    # Emits the string UUID of the annotation when clicked natively on the PDF
    annotation_clicked = pyqtSignal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.scene = QGraphicsScene(self)
        self.setScene(self.scene)
        
        self.setRenderHint(QPainter.RenderHint.Antialiasing)
        self.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        self.setDragMode(QGraphicsView.DragMode.ScrollHandDrag)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOn)
        
        self.doc = None
        self.base_zoom = 1.5
        self.page_items = []
        self.worker = None
        
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
            
            self.worker = RenderWorker(self.doc, self.base_zoom)
            self.worker.page_ready.connect(self._on_page_ready)
            self.worker.start()
            return True
        except Exception as e:
            print(f"Failed to load: {e}")
            return False

    def _on_page_ready(self, page_num, qimage):
        pixmap = QPixmap.fromImage(qimage)
        item = QGraphicsPixmapItem(pixmap)
        
        y_offset = sum(p.boundingRect().height() + 20 for p in self.page_items)
        item.setPos(0, y_offset)
        
        self.scene.addItem(item)
        self.page_items.append(item)
        self.scene.setSceneRect(self.scene.itemsBoundingRect())

    def reload_page(self, page_num):
        """Synchronously re-renders a single page. Used instantly after adding/deleting annotations."""
        if not self.doc or page_num < 0 or page_num >= len(self.page_items):
            return
            
        page = self.doc.load_page(page_num)
        mat = fitz.Matrix(self.base_zoom, self.base_zoom)
        pix = page.get_pixmap(matrix=mat)
        
        img = QImage(pix.samples, pix.width, pix.height, pix.stride, QImage.Format.Format_RGB888)
        self.page_items[page_num].setPixmap(QPixmap.fromImage(img.copy()))

    def mousePressEvent(self, event):
        if event.modifiers() == Qt.KeyboardModifier.ShiftModifier:
            self.setDragMode(QGraphicsView.DragMode.NoDrag)
            self.annot_manager.handle_mouse_press(event)
        else:
            self.setDragMode(QGraphicsView.DragMode.ScrollHandDrag)
            
            # --- Check if we clicked on an existing annotation ---
            scene_pos = self.mapToScene(event.pos())
            page_idx, page_item = self.annot_manager._get_page_at_pos(scene_pos)
            
            if page_idx != -1 and self.doc:
                # Convert the click from screen pixels back into the PDF's native coordinate plane
                local_pos = page_item.mapFromScene(scene_pos)
                pdf_x, pdf_y = local_pos.x() / self.base_zoom, local_pos.y() / self.base_zoom
                point = fitz.Point(pdf_x, pdf_y)
                
                page = self.doc.load_page(page_idx)
                for annot in page.annots():
                    if annot.rect.contains(point) and annot.info.get("title", "").startswith("UserNote"):
                        # We hit one! Tell the main window to open the Notes tab
                        self.annotation_clicked.emit(annot.info.get("title"))
                        return # Consume the event so we don't accidentally drag the page

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
            # Precise manipulation so the requested page sits exactly at the top of the viewport
            self.verticalScrollBar().setValue(int(target_item.pos().y()) - 10)

    def zoom_in(self):
        self.scale(1.2, 1.2)

    def zoom_out(self):
        self.scale(1 / 1.2, 1 / 1.2)

    def zoom_reset(self):
        if not self.page_items: return
        self.resetTransform()
        view_width = self.viewport().width()
        doc_width = self.page_items[0].boundingRect().width()
        if doc_width > 0:
            target_scale = (view_width - 40) / doc_width
            self.scale(target_scale, target_scale)