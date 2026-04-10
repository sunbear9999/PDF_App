# core/render_worker.py
import fitz
from PyQt6.QtCore import QThread, pyqtSignal
from PyQt6.QtGui import QImage


from queue import Queue

class RenderWorker(QThread):
    page_ready = pyqtSignal(int, QImage)
    finished_rendering = pyqtSignal()

    def __init__(self, doc, zoom, page_queue_or_list, pixel_ratio=1.0):
        super().__init__()
        self.doc = doc
        self.zoom = zoom
        self.pixel_ratio = pixel_ratio if pixel_ratio and pixel_ratio > 0 else 1.0
        self._is_running = True
        self.page_queue = page_queue_or_list

    def run(self):
        mat = fitz.Matrix(self.zoom * self.pixel_ratio, self.zoom * self.pixel_ratio)
        # Accepts either a Queue or a list
        if hasattr(self.page_queue, 'get'):
            # Assume it's a Queue
            while self._is_running:
                try:
                    page_num = self.page_queue.get(timeout=0.2)
                except Exception:
                    continue  # Instead of break, keep looping for new requests
                if not self._is_running:
                    break
                if 0 <= page_num < len(self.doc):
                    page = self.doc.load_page(page_num)
                    pix = page.get_pixmap(matrix=mat)
                    img = QImage(pix.samples, pix.width, pix.height, pix.stride, QImage.Format.Format_RGB888).copy()
                    img.setDevicePixelRatio(self.pixel_ratio)
                    self.page_ready.emit(page_num, img.copy())
            self.finished_rendering.emit()
        else:
            # Assume it's a list
            for page_num in self.page_queue:
                if not self._is_running:
                    break
                if 0 <= page_num < len(self.doc):
                    page = self.doc.load_page(page_num)
                    pix = page.get_pixmap(matrix=mat)
                    img = QImage(pix.samples, pix.width, pix.height, pix.stride, QImage.Format.Format_RGB888).copy()
                    img.setDevicePixelRatio(self.pixel_ratio)
                    self.page_ready.emit(page_num, img.copy())
            self.finished_rendering.emit()

    def stop(self):
        self._is_running = False