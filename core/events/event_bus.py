# core/events/event_bus.py
from PySide6.QtCore import QObject, Signal

class EventBus(QObject):
    """
    Central Nervous System for the application. 
    Publishers emit signals here. Subscribers connect to these signals.
    """
    # --- Document & Content Events ---
    highlight_created = Signal(dict)       # Payload: highlight_data
    highlight_deleted = Signal(str)        # Payload: annot_id
    highlight_updated = Signal(str, dict)  # Payload: annot_id, changes (e.g., {'color': '#fff'})
    pdf_switched = Signal(str)             # Payload: new_pdf_path
    pdf_renamed = Signal(str, str)         # Payload: old_path, new_path
    pdf_removed = Signal(str)              # Payload: doc_path
    project_loaded = Signal()              # Payload: None (Triggered when a new project opens)
    # --- UI & Project Events ---
    theme_changed = Signal(dict)           # Payload: theme_dict
    project_saved = Signal()
    project_loaded = Signal()

    _instance = None

    @classmethod
    def get_instance(cls):
        """Ensures every file talks to the exact same EventBus."""
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance