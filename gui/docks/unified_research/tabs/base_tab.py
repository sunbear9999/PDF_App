# gui/docks/base_tab.py
from PySide6.QtWidgets import QWidget
from core.engine.action_model import AIActionBlueprint

class BaseTab(QWidget):
    def __init__(self, main_window, parent=None):
        super().__init__(parent)
        self.main_window = main_window
        
        # Pull theme and blueprint managers safely from main_window
        self.theme_manager = getattr(main_window, 'theme_manager', None)
        self.theme = self.theme_manager.get_theme() if self.theme_manager else {}
        self.blueprint_manager = getattr(main_window, 'blueprint_manager', None)
        self.prompt_manager = getattr(main_window, 'prompt_manager', None)
        self.project_manager = getattr(main_window, 'project_manager', None)

    def send_to_pipeline(self, blueprint: AIActionBlueprint, variables: dict, output_workspace: bool = False):
        """
        Standardized execution router. Injects global flags into the initial state 
        and dispatches the blueprint to the MasterActionRunner loop.
        """
        # Ensure initial state fields are normalized across all engines
        initial_state = {**variables}
        initial_state["output_workspace"] = output_workspace
        
        # Inject standard selected model fallback if not already provided explicitly
        if "selected_model" not in initial_state and hasattr(self, "model_combo"):
            initial_state["selected_model"] = self.model_combo.currentText()
        elif "selected_model" not in initial_state and hasattr(self, "combo_models"):
            initial_state["selected_model"] = self.combo_models.currentText()

        self.main_window.execute_ai_blueprint(blueprint, initial_state)

    def update_theme(self, theme):
        """
        Universal base theme configuration. Individual tabs can override 
        or extend this to repaint their custom interface fields.
        """
        self.theme = theme
        bg_main = theme.get('bg_main', '#1e1e1e')
        text_main = theme.get('text_main', '#fff')
        self.setStyleSheet(f"background-color: {bg_main}; color: {text_main};")