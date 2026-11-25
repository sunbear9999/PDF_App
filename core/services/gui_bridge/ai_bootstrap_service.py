# core/services/ai_bootstrap_service.py
from PySide6.QtCore import QObject, QThread, QTimer
import os

# The web-engine pre-warm starts a Chromium helper that stays resident.
WEB_PREWARM_BYTES = 300 * 1024 * 1024

class PreloadWorker(QThread):
    def __init__(self, llm_manager, model, parent=None):
        super().__init__(parent)
        self.llm_manager = llm_manager
        self.model = model

    def run(self):
        self.llm_manager.preload_model(self.model)

class AIBootstrapService(QObject):
    """Handles silent background boot sequences so the UI thread doesn't lag."""
    def __init__(self, main_window):
        super().__init__(main_window)
        self.main_window = main_window
        # Automatically wait 1.5s after boot before warming up the AI
        QTimer.singleShot(1500, self._trigger_background_preload)

    def _trigger_background_preload(self):
        try:
            if os.environ.get("PAPYRUS_PRELOAD_AI", "1").strip().lower() in {"0", "false", "no", "off"}:
                return
            # --- THE PRE-WARM FIX ---
            # Boot the heavy graphics engine using a Page instead of a View,
            # only when memory allows: it costs a resident helper process.
            memory = getattr(getattr(self.main_window, "core", None), "memory_manager_service", None)
            if memory is None or memory.has_headroom(WEB_PREWARM_BYTES):
                from PySide6.QtWebEngineCore import QWebEnginePage
                self._dummy_browser = QWebEnginePage(self)

            llm_manager = getattr(self.main_window, 'shared_llm_manager', None)
            if not llm_manager or not getattr(llm_manager, 'ai_enabled', False): 
                return
            
            active_model = (
                self.main_window._get_active_ai_model()
                if hasattr(self.main_window, "_get_active_ai_model")
                else ""
            )
            if not active_model:
                # No user-selected model to warm up — never preload a hardcoded one.
                return

            self._preload_worker = PreloadWorker(llm_manager, active_model, self)
            self._preload_worker.finished.connect(self._preload_worker.deleteLater)
            self._preload_worker.start()
        except Exception as e:
            print(f"Preload setup failed: {e}")
