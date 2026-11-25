"""Shared model installation/import/validation worker for every catalog surface."""
import threading
from PySide6.QtCore import QThread, Signal

MAX_ERROR_CHARS = 4000


class ModelDependencyWorker(QThread):
    done = Signal(str, bool, str)
    progress = Signal(str, int, int, str)
    log = Signal(str, str)

    def __init__(self, service, dep_id, parent=None, *, action="install", source_dir=""):
        super().__init__(parent)
        self._service, self._dep_id, self.action, self.source_dir = service, dep_id, action, source_dir
        self._cancel = threading.Event()
        self.finished.connect(self.deleteLater)

    def cancel(self):
        self._cancel.set()

    def run(self):
        try:
            if self._cancel.is_set():
                return
            if hasattr(self._service, "perform_operation"):
                result = self._service.perform_operation(self._dep_id, action=self.action, source_dir=self.source_dir,
                    log=lambda message: self.log.emit(self._dep_id, str(message)),
                    progress=lambda current, total, message: self.progress.emit(self._dep_id, int(current), int(total), str(message)),
                    cancel_check=self._cancel.is_set)
            elif self.action == "install":
                result = self._service.install(self._dep_id, cancel_check=self._cancel.is_set)
            elif self.action == "import":
                result = self._service.import_model_folder(self._dep_id, self.source_dir)
            elif self.action == "validate":
                result = self._service.validate(self._dep_id)
            else:
                raise ValueError("Unknown model operation.")
            self.done.emit(self._dep_id, bool(result.get("ok")), str(result.get("error") or "")[-MAX_ERROR_CHARS:])
        except Exception as exc:
            self.done.emit(self._dep_id, False, str(exc)[-MAX_ERROR_CHARS:])
