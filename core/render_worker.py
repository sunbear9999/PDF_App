# core/render_worker.py
import fitz
from PyQt6.QtCore import QThread, pyqtSignal
from PyQt6.QtGui import QImage

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