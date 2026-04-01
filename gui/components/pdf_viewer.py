import fitz
from PyQt6.QtWidgets import QGraphicsView, QGraphicsScene, QGraphicsPixmapItem, QFrame, QVBoxLayout, QLabel
from PyQt6.QtGui import QImage, QPixmap, QPainter, QColor
from PyQt6.QtCore import Qt, QThread, pyqtSignal, QRectF, QTimer

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
            if not self._is_running: break
            page = self.doc.load_page(i)
            pix = page.get_pixmap(matrix=mat)
            img = QImage(pix.samples, pix.width, pix.height, pix.stride, QImage.Format.Format_RGB888)
            self.page_ready.emit(i, img.copy())
        self.finished_rendering.emit()

    def stop(self):
        self._is_running = False

class PDFViewer(QGraphicsView):
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
        
        # Tracks jumps requested before the target page has finished rendering
        self.pending_jump = None 

    def load_document(self, doc):
        if self.worker and self.worker.isRunning():
            self.worker.stop()
            self.worker.wait()

        self.doc = doc
        self.scene.clear()
        self.page_items.clear()
        self.pending_jump = None 
        
        self.worker = RenderWorker(self.doc, self.base_zoom)
        self.worker.page_ready.connect(self._on_page_ready)
        self.worker.start()
        return True

    def _on_page_ready(self, page_num, qimage):
        pixmap = QPixmap.fromImage(qimage)
        item = QGraphicsPixmapItem(pixmap)
        y_offset = sum(p.boundingRect().height() + 20 for p in self.page_items)
        item.setPos(0, y_offset)
        self.scene.addItem(item)
        self.page_items.append(item)
        self.scene.setSceneRect(self.scene.itemsBoundingRect())

        # CRITICAL FIX: If a jump was queued for this newly rendered page, execute it!
        if self.pending_jump and self.pending_jump[0] == page_num:
            p_num, a_id = self.pending_jump
            self.pending_jump = None
            # Defer the UI jump by 100ms to let the Qt rendering engine calculate boundaries
            QTimer.singleShot(100, lambda: self._execute_jump(p_num, a_id))

    def reload_page(self, page_num):
        if not self.doc or page_num < 0 or page_num >= len(self.page_items): return
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
            scene_pos = self.mapToScene(event.pos())
            page_idx, page_item = self.annot_manager._get_page_at_pos(scene_pos)
            
            if page_idx != -1 and self.doc:
                local_pos = page_item.mapFromScene(scene_pos)
                pdf_x, pdf_y = local_pos.x() / self.base_zoom, local_pos.y() / self.base_zoom
                point = fitz.Point(pdf_x, pdf_y)
                page = self.doc.load_page(page_idx)
                for annot in page.annots():
                    if annot.rect.contains(point) and (annot.info.get("title", "").startswith("UserNote") or annot.info.get("title", "").startswith("AINote")):
                        self.annotation_clicked.emit(annot.info.get("title"))
                        return 
            super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if self.dragMode() == QGraphicsView.DragMode.NoDrag:
            self.annot_manager.handle_mouse_move(event)
        else: super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        if self.dragMode() == QGraphicsView.DragMode.NoDrag:
            self.annot_manager.handle_mouse_release(event)
        super().mouseReleaseEvent(event)
        
    def jump_to_page(self, page_num):
        if 0 <= page_num < len(self.page_items):
            target_item = self.page_items[page_num]
            top_edge = QRectF(target_item.scenePos().x(), target_item.scenePos().y(), target_item.boundingRect().width(), 1)
            self.ensureVisible(top_edge, 50, 10)

    def jump_to_annotation(self, page_num, annot_id):
        # If the page hasn't rendered yet (e.g., just switched PDFs), queue the jump
        if page_num >= len(self.page_items):
            self.pending_jump = (page_num, annot_id)
        else:
            self._execute_jump(page_num, annot_id)

    def _execute_jump(self, page_num, annot_id):
        if 0 <= page_num < len(self.page_items) and self.doc:
            target_item = self.page_items[page_num]
            page = self.doc.load_page(page_num)
            for annot in page.annots():
                if annot.info.get("title") == annot_id:
                    r = annot.rect
                    z = self.base_zoom
                    qt_rect = QRectF(r.x0 * z, r.y0 * z, (r.x1 - r.x0) * z, (r.y1 - r.y0) * z)
                    scene_rect = target_item.mapToScene(qt_rect).boundingRect()
                    
                    # Aggressively center the view on the specific highlight
                    self.ensureVisible(scene_rect, 300, 300)
                    self.centerOn(scene_rect.center())
                    return
            
            self.jump_to_page(page_num)

    def zoom_in(self): self.scale(1.2, 1.2)
    def zoom_out(self): self.scale(1 / 1.2, 1 / 1.2)
    def zoom_reset(self):
        if not self.page_items: return
        self.resetTransform()
        view_width = self.viewport().width()
        doc_width = self.page_items[0].boundingRect().width()
        if doc_width > 0:
            target_scale = (view_width - 40) / doc_width
            self.scale(target_scale, target_scale)