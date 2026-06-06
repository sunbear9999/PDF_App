# core/services/ai_bootstrap_service.py
from PySide6.QtCore import QObject, QThread, QTimer

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
            # --- THE PRE-WARM FIX ---
            # Boot the heavy graphics engine using a Page instead of a View.
            from PySide6.QtWebEngineCore import QWebEnginePage
            self._dummy_browser = QWebEnginePage(self)

            llm_manager = getattr(self.main_window, 'shared_llm_manager', None)
            if not llm_manager or not getattr(llm_manager, 'ai_enabled', False): 
                return
            
            active_model = "gemma4:e2b" # Fallback default
            
            dock_manager = getattr(self.main_window, 'dock_manager', None)
            if dock_manager:
                research_docks = dock_manager.get_inner_widgets("research")
                if research_docks and hasattr(research_docks[0], 'model_combo'):
                    active_model = research_docks[0].model_combo.currentText()
                        
            self._preload_worker = PreloadWorker(llm_manager, active_model, self)
            self._preload_worker.finished.connect(self._preload_worker.deleteLater)
            self._preload_worker.start()
        except Exception as e:
            print(f"Preload setup failed: {e}")