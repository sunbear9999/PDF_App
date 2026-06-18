from PySide6.QtWidgets import QApplication, QPushButton
from gui.managers.dialog_manager import exec_as_modal, get_for_widget


class PromptTraceButton(QPushButton):
    def __init__(self, trace_id=None, trace_record=None, main_window=None, theme=None, parent=None):
        super().__init__("Trace", parent)
        self.trace_id = trace_id
        self.trace_record = trace_record
        self.main_window = main_window
        self.theme = theme or {}
        self.setToolTip("View prompt trace")
        self.setFixedHeight(24)
        self.clicked.connect(self.open_trace)
        self.apply_theme()

    def set_trace_id(self, trace_id):
        self.trace_id = trace_id
        self.setVisible(bool(trace_id))

    def set_trace_record(self, trace_record):
        self.trace_record = trace_record
        if trace_record and hasattr(trace_record, "trace_id"):
            self.trace_id = trace_record.trace_id
        elif isinstance(trace_record, dict):
            self.trace_id = trace_record.get("trace_id", self.trace_id)
        self.setVisible(bool(self.trace_id or self.trace_record))

    def apply_theme(self):
        border = self.theme.get("border", "#444")
        muted = self.theme.get("text_muted", "#aaa")
        accent = self.theme.get("accent", "#b366ff")
        self.setStyleSheet(f"""
            QPushButton {{
                background: transparent;
                color: {muted};
                border: 1px solid {border};
                border-radius: 4px;
                padding: 2px 8px;
                font-size: 11px;
                font-weight: bold;
            }}
            QPushButton:hover {{
                color: {accent};
                border-color: {accent};
            }}
        """)

    def _resolve_main_window(self):
        if self.main_window and hasattr(self.main_window, "prompt_manager"):
            return self.main_window
        window = self.window()
        if window and hasattr(window, "prompt_manager"):
            return window
        active = QApplication.activeWindow()
        if active and hasattr(active, "prompt_manager"):
            return active
        return None

    def open_trace(self):
        main_window = self._resolve_main_window()
        if not main_window or not (self.trace_id or self.trace_record):
            return
        from gui.components.dialogs.prompt_editor_dialog import PromptEditorDialog
        dialog = PromptEditorDialog(
            main_window.prompt_manager,
            main_window,
            trace_id=self.trace_id,
            trace_record=self.trace_record,
        )
        dm = get_for_widget(self)
        if dm:
            dm.show_instance(dialog)
        else:
            exec_as_modal(dialog)
