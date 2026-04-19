# core/render_worker.py
import fitz
from PySide6.QtCore import QThread, Signal
from PySide6.QtGui import QImage
from queue import Queue

class RenderWorker(QThread):
    page_ready = Signal(int, QImage)
    finished_rendering = Signal()

    def __init__(self, pdf_path, zoom, page_queue_or_list, pixel_ratio=1.0):
        super().__init__()
        # 🔥 FIX 3: Store string path, NEVER the fitz.Document object!
        self.pdf_path = pdf_path 
        self.zoom = zoom
        self.pixel_ratio = pixel_ratio if pixel_ratio and pixel_ratio > 0 else 1.0
        self._is_running = True
        self.page_queue = page_queue_or_list

    def run(self):
        if not self.pdf_path:
            self.finished_rendering.emit()
            return
            
        try:
            # 🔥 Thread opens its OWN completely isolated copy of the C-pointers
            local_doc = fitz.open(self.pdf_path)
        except Exception as e:
            print(f"Background PDF thread failed to open {self.pdf_path}: {e}")
            self.finished_rendering.emit()
            return

        mat = fitz.Matrix(self.zoom * self.pixel_ratio, self.zoom * self.pixel_ratio)
        
        try:
            if hasattr(self.page_queue, 'get'):
                while self._is_running:
                    try:
                        page_num = self.page_queue.get(timeout=0.2)
                    except Exception:
                        continue  
                    if not self._is_running:
                        break
                    if 0 <= page_num < len(local_doc):
                        page = local_doc.load_page(page_num)
                        pix = page.get_pixmap(matrix=mat)
                        img = QImage(pix.samples, pix.width, pix.height, pix.stride, QImage.Format.Format_RGB888).copy()
                        img.setDevicePixelRatio(self.pixel_ratio)
                        self.page_ready.emit(page_num, img.copy())
            else:
                for page_num in self.page_queue:
                    if not self._is_running:
                        break
                    if 0 <= page_num < len(local_doc):
                        page = local_doc.load_page(page_num)
                        pix = page.get_pixmap(matrix=mat)
                        img = QImage(pix.samples, pix.width, pix.height, pix.stride, QImage.Format.Format_RGB888).copy()
                        img.setDevicePixelRatio(self.pixel_ratio)
                        self.page_ready.emit(page_num, img.copy())
        finally:
            local_doc.close() # Clean up C-pointers safely

        self.finished_rendering.emit()

    def stop(self):
        self._is_running = False