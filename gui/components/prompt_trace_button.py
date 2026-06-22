from PySide6.QtWidgets import QApplication, QPushButton
from gui.managers.dialog_manager import exec_as_modal, get_for_widget


class PromptTraceButton(QPushButton):
    def __init__(self, trace_id=None, trace_record=None, app_context=None, theme=None, parent=None):
        super().__init__("Trace", parent)
        self.trace_id = trace_id
        self.trace_record = trace_record
        self._ctx = app_context
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

    def _resolve_app_context(self):
        if self._ctx:
            return self._ctx
        for candidate in (self.window(), QApplication.activeWindow()):
            if candidate and hasattr(candidate, "app_context"):
                return candidate.app_context
        return None

    def _resolve_prompt_manager(self):
        ctx = self._resolve_app_context()
        if ctx and getattr(ctx, 'prompt_manager', None):
            return ctx.prompt_manager
        window = self.window()
        if window and hasattr(window, "prompt_manager"):
            return window.prompt_manager
        active = QApplication.activeWindow()
        if active and hasattr(active, "prompt_manager"):
            return active.prompt_manager
        return None

    def open_trace(self):
        pm = self._resolve_prompt_manager()
        if not pm or not (self.trace_id or self.trace_record):
            return
        from gui.components.dialogs.prompt_editor_dialog import PromptEditorDialog
        dialog = PromptEditorDialog(
            pm,
            self.window(),
            app_context=self._resolve_app_context(),
            trace_id=self.trace_id,
            trace_record=self.trace_record,
        )
        dm = get_for_widget(self)
        if dm:
            dm.show_instance(dialog)
        else:
            exec_as_modal(dialog)
