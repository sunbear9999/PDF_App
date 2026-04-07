# core/base_ai_worker.py
# [REFACTOR] Base class for all AI worker threads - consolidates common infrastructure

import json
import logging
from PyQt6.QtCore import QThread, pyqtSignal

logger = logging.getLogger(__name__)


class BaseAIWorker(QThread):
    """[REFACTOR] Base class for all AI task workers.
    
    Consolidates:
    - Common PyQt6 signals (progress, finished, error)
    - JSON parsing utilities with cleanup
    - Standard error handling and logging
    - Thread cleanup lifecycle
    
    Subclasses must:
    1. Define their specific task parameters in __init__
    2. Implement run() with try/except wrapper
    3. Emit signals at appropriate points
    """

    # =========================================================================
    # STANDARD SIGNALS - all AI workers emit these
    # =========================================================================
    progress = pyqtSignal(str)  # Emits progress messages (e.g., "Processing 5/20...")
    finished = pyqtSignal(object, str)  # (result: dict or list, error_msg: str or "")
    error = pyqtSignal(str)  # Emits error message string

    def __init__(self):
        """Initialize base worker with standard logging."""
        super().__init__()
        self.is_running = True
        logger.debug(f"{self.__class__.__name__} worker initialized")

    def run(self):
        """[REFACTOR] Override this in subclasses. Base implementation provides structure."""
        try:
            self.progress.emit("Starting task...")
            result = self.execute_task()
            self.finished.emit(result, "")
        except Exception as e:
            logger.error(f"{self.__class__.__name__} failed: {str(e)}", exc_info=True)
            self.error.emit(str(e))
            self.finished.emit(None, str(e))

    def execute_task(self):
        """[REFACTOR] Override this method in subclasses.
        
        Should contain actual task logic.
        Must return result object (dict, list, or custom).
        May emit self.progress(msg) during execution.
        """
        raise NotImplementedError("Subclasses must implement execute_task()")

    def stop(self):
        """[REFACTOR] Graceful worker shutdown."""
        self.is_running = False
        self.wait()

    # =========================================================================
    # UTILITY METHODS FOR JSON PARSING
    # =========================================================================

    @staticmethod
    def clean_and_parse_json(text, json_mode=False):
        """[REFACTOR] Parse JSON from LLM response, handling markdown code blocks.
        
        [AI OPTIMIZATION] Works with Ollama's json_mode=True output:
        - If json_mode=True: Ollama returns structured JSON wrapped in ```json
        - If json_mode=False: May be JSON inside markdown code fence
        
        Args:
            text: Raw LLM response text
            json_mode: If True, expect cleaner JSON without markdown
        
        Returns:
            dict or list: Parsed JSON object
        
        Raises:
            json.JSONDecodeError: If parsing fails
        """
        text = text.strip()

        # [REFACTOR] Remove markdown code fence if present
        if text.startswith("```json"):
            text = text[7:]  # Remove ```json prefix
        if text.startswith("```"):
            text = text[3:]  # Remove ``` prefix
        if text.endswith("```"):
            text = text[:-3]  # Remove ``` suffix

        text = text.strip()
        return json.loads(text)

    @staticmethod
    def safe_parse_json(text, default=None, json_mode=False):
        """[REFACTOR] Parse JSON with fallback to default on error.
        
        Args:
            text: Raw LLM response text
            default: Return value if parsing fails (default: None)
            json_mode: If True, expect cleaner JSON
        
        Returns:
            Parsed JSON or default value
        """
        try:
            return BaseAIWorker.clean_and_parse_json(text, json_mode=json_mode)
        except (json.JSONDecodeError, ValueError) as e:
            logger.warning(f"JSON parse failed: {str(e)}. Using default.")
            return default if default is not None else {}

    # =========================================================================
    # LIFECYCLE UTILITIES
    # =========================================================================

    def emit_progress(self, message, current=None, total=None):
        """[REFACTOR] Emit formatted progress message.
        
        Args:
            message: Base message (e.g., "Indexing PDFs")
            current: Current item index (optional)
            total: Total items (optional)
        """
        if current is not None and total is not None:
            msg = f"{message} ({current}/{total})"
        else:
            msg = message
        self.progress.emit(msg)

    def check_running(self):
        """[REFACTOR] Check if worker should continue (respects stop signal).
        
        Returns:
            bool: True if should continue, False if stopped
        """
        return self.is_running

    def cleanup(self):
        """[REFACTOR] Override in subclasses for resource cleanup (GPU, connections).
        
        Called automatically by base run() wrapper.
        """
        pass
