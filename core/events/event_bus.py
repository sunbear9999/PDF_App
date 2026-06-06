# core/events/event_bus.py
from PySide6.QtCore import QObject, Signal


class EventBus(QObject):
    _instance = None
    """
    Central Nervous System for the application.
    Publishers emit signals here. Subscribers connect to these signals.
    """
    # --- Document & Content Events ---
    highlight_created = Signal(object, object)
    highlight_deleted = Signal(object, object)
    highlight_updated = Signal(object, object)
    pdf_switched = Signal(object, object)
    pdf_renamed = Signal(object, object)
    pdf_removed = Signal(object, object)
    project_loaded = Signal(object, object)
    # --- UI & Project Events ---
    theme_changed = Signal(object, object)
    project_saved = Signal(object, object)

    # --- Workspace Events ---
    workspace_loaded = Signal(object, object)
    workspace_changed = Signal(object, object)
    workspace_saved = Signal(object, object)
    workspace_node_added = Signal(object, object)
    workspace_node_updated = Signal(object, object)
    workspace_node_deleted = Signal(object, object)
    workspace_edge_added = Signal(object, object)
    workspace_edge_updated = Signal(object, object)
    workspace_edge_deleted = Signal(object, object)
    workspace_selection_changed = Signal(object, object)
    workspace_filter_changed = Signal(object, object)
    workspace_action_requested = Signal(object, object)
    workflow_action_requested = Signal(object, object)
    workspace_state_restored = Signal(object, object)
    run_ai_tool = Signal(object, object)
    ai_graph_generated = Signal(object, object)
    active_model_changed = Signal(object, object)
    # --- App & Project Lifecycle Intents ---
    project_action_requested = Signal(object, object) # Enum, Dataclass
    project_clearing_started = Signal(object, object)

    # --- Document Intents ---
    document_action_requested = Signal(object, object) # Enum, Dataclass
    document_opened = Signal(object, object)

    # --- Annotation Intents ---
    annotation_action_requested = Signal(object, object)
    # --- Tool Intents ---
    tts_action_requested = Signal(object, object)
    ocr_action_requested = Signal(object, object)
    # --- Tool Results (UI Listeners) ---
    tts_status_updated = Signal(object, object)
    ocr_status_updated = Signal(object, object)
    # --- Dictionary Domain ---
    dictionary_action_requested = Signal(object, object)
    dictionary_results_ready = Signal(object, object)
    dictionary_status_updated = Signal(object, object)

    # --- Citation Domain ---
    citation_action_requested = Signal(object, object)
    citation_table_data_ready = Signal(object, object)
    citation_status_updated = Signal(object, object)
    # --- Notes Domain ---
    notes_action_requested = Signal(object, object)
    notes_data_ready = Signal(object, object)
    # --- Tool & Dialog Domains ---
    tag_action_requested = Signal(object, object)
    tag_data_updated = Signal(object, object)

    prompt_action_requested = Signal(object, object)
    prompt_data_updated = Signal(object, object)
    @classmethod
    def get_instance(cls):
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance
