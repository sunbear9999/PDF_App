# gui/docks/unified_research/tabs/base_tab.py
from PySide6.QtWidgets import QWidget
from PySide6.QtCore import QTimer
import json
from core.engine.action_model import AIActionBlueprint
from gui.docks.unified_research.components.chat_streamer import ChatMessageWidget

class BaseTab(QWidget):
    def __init__(self, main_window, target_id=None, parent=None):
        super().__init__(parent)
        self.main_window = main_window
        self.target_id = target_id
        
        self.theme_manager = getattr(main_window, 'theme_manager', None)
        self.theme = self.theme_manager.get_theme() if self.theme_manager else {}
        self.blueprint_manager = getattr(main_window, 'blueprint_manager', None)
        self.prompt_manager = getattr(main_window, 'prompt_manager', None)
        self.project_manager = getattr(main_window, 'project_manager', None)

        if self.target_id and hasattr(main_window, 'ui_router'):
            main_window.ui_router.register_target(self.target_id, self)

    def receive_ai_widget(self, widget):
        """Universal UI Router receiver."""
        if hasattr(self, 'chat_layout'):
            count = self.chat_layout.count()
            if count > 0: self.chat_layout.insertWidget(count - 1, widget)
            else: self.chat_layout.addWidget(widget)
            
            if hasattr(self, 'scroll_area'):
                QTimer.singleShot(50, lambda: self.scroll_area.verticalScrollBar().setValue(self.scroll_area.verticalScrollBar().maximum()))
        elif hasattr(self, 'results_layout'):
            self.results_layout.addWidget(widget)

    def safe_load_history(self):
        """Universally loads history safely without destroying active stream widgets."""
        if not self.project_manager or not hasattr(self, 'chat_layout'): return
        
        # Prevent destructive wiping if widgets are already active
        if self.chat_layout.count() > 1: return 

        history = self.project_manager.get_chat_history(self.target_id)
        for msg in history:
            is_user = (msg['role'] == "user")
            sender = "You" if is_user else "AI Agent"
            
            if msg["ui_format"] == "chat_widgets":
                try:
                    items = json.loads(msg["content"])
                    if isinstance(items, dict):
                        for val in items.values():
                            if isinstance(val, list): items = val; break
                        if isinstance(items, dict): items = [items] 
                    
                    widget = ChatMessageWidget(sender, theme=self.theme, is_user=is_user)
                    for item in items:
                        if isinstance(item, dict):
                            widget.add_bubble(
                                doc_name=item.get("doc_name", "Unknown Document"),
                                quote=item.get("quote", item.get("text", "")),
                                note=item.get("note", item.get("reason", ""))
                            )
                    self.receive_ai_widget(widget)
                except Exception: pass
            else:
                widget = ChatMessageWidget(sender, theme=self.theme, is_user=is_user)
                widget.append_chunk(msg['content'])
                if not is_user and hasattr(widget, 'hide_status'):
                    widget.hide_status()
                self.receive_ai_widget(widget)

    def send_to_pipeline(self, blueprint: AIActionBlueprint, variables: dict, output_workspace: bool = False):
        initial_state = {**variables}
        initial_state["output_workspace"] = output_workspace
        
        # Global Context Injection
        if self.project_manager:
            try:
                settings = json.loads(self.project_manager.get_metadata("global_ai_settings", "{}"))
                for k, v in settings.items():
                    if k not in initial_state:
                        initial_state[k] = v
            except Exception: pass
            
            initial_state["project_manifest"] = self.project_manager.get_metadata("project_manifest", "{}")
            
            try:
                from core.api.workspace_ai import WorkspaceAIApi
                ws_data = self.project_manager.get_workspace_data()
                api = WorkspaceAIApi(self.project_manager)
                initial_state["workspace_data"] = api.build_ai_context(ws_data)
            except Exception as e:
                initial_state["workspace_data"] = "{}"

        if "selected_model" not in initial_state and hasattr(self, "model_combo"):
            initial_state["selected_model"] = self.model_combo.currentText()
        elif "selected_model" not in initial_state and hasattr(self, "combo_models"):
            initial_state["selected_model"] = self.combo_models.currentText()

        self.main_window.execute_ai_blueprint(blueprint, initial_state)

    def update_theme(self, theme):
        self.theme = theme
        self.setStyleSheet(f"background-color: {theme.get('bg_main', '#1e1e1e')}; color: {theme.get('text_main', '#fff')};")