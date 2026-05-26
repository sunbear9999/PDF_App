# gui/components/ui_router.py
from PySide6.QtCore import QObject
from PySide6.QtWidgets import QWidget, QGraphicsView
from gui.docks.unified_research.components.chat_streamer import ChatMessageWidget
from gui.docks.unified_research.components.note_bubble import NoteBubbleWidget
from gui.docks.unified_research.components.user_input_form import UserInputFormWidget
import json
import re 

class BlueprintUIRouter(QObject):
    def __init__(self, main_window):
        super().__init__()
        self.main_window = main_window
        self.theme = main_window.theme_manager.get_theme() if hasattr(main_window, 'theme_manager') else {}
        self.runner = None
        self.active_chat_widget = None

    def attach_runner(self, runner):
        self.runner = runner
        self.runner.progress_update.connect(self._handle_stream)
        self.runner.step_complete.connect(self._handle_step_complete)
        self.runner.step_started.connect(self._handle_step_started)
        
        # NEW: Catch the pause signal
        if hasattr(self.runner, 'user_input_requested'):
            self.runner.user_input_requested.connect(self._handle_user_input)
            
        self.active_chat_widget = None 

    def _handle_user_input(self, step_id, expected_inputs):
        """Spawns the interactive form directly inline in the active chat layout."""
        if not self.runner: return
        
        # Ensure we target the right UI area based on the current executing step
        current_step = getattr(self.runner, 'current_executing_step', None)
        target = getattr(current_step, 'ui_target', 'chat_dock') if current_step else 'chat_dock'
        
        # Create the dynamic form widget
        form_widget = UserInputFormWidget(step_id, expected_inputs, theme=self.theme)
        
        # Connect the form's output directly to the Runner's wake-up method
        form_widget.form_submitted.connect(self.runner.submit_user_input)
        
        # Inject it inline like a chat message
        if target == "chat_dock":
            tab = next((c for c in self.main_window.findChildren(QWidget) if c.__class__.__name__ == "ChatTab"), None)
            if tab and hasattr(tab, 'add_message_widget'): tab.add_message_widget(form_widget)
                
        elif target == "brainstorm_dock":
            tab = next((c for c in self.main_window.findChildren(QWidget) if c.__class__.__name__ == "BrainstormTab"), None)
            if tab and hasattr(tab, 'add_message_widget'): tab.add_message_widget(form_widget)
            
        elif target == "custom_tools_tab":
            tab = next((c for c in self.main_window.findChildren(QWidget) if c.__class__.__name__ == "CustomToolsTab"), None)
            if tab and hasattr(tab, 'add_message_widget'): tab.add_message_widget(form_widget)

    def _handle_step_started(self, step_id):
        if not self.runner: return
        
        status_map = {
            "generate_keywords": "Brainstorming search keywords...",
            "deep_rag_search": "Scanning documents deeply...",
            "basic_rag_search": "Scanning local documents...",
            "chat_response": "Drafting response...",
            "extract_citations": "Generating interactive citations...",
            "auto_build_graph": "Building workspace graph...",
            "gather_context": "Gathering project context...",
            "brainstorm_reply": "Formulating strategy...",
            "generate_queries": "Designing semantic search queries...",
            "analyze_chunk": "Analyzing document section..."
        }
        
        text = status_map.get(step_id, f"Running {step_id}...")
        current_step = getattr(self.runner, 'current_executing_step', None)
        target = getattr(current_step, 'ui_target', 'chat_dock') if current_step else 'chat_dock'
        
        if target in ["floating", "search_tab", "analysis_tab"]: return
            
        widget = self._get_or_create_chat_widget(target)
        if hasattr(widget, 'update_status'):
            widget.update_status(text)
    
    def _get_or_create_chat_widget(self, target):
        if not self.active_chat_widget:
            self.active_chat_widget = ChatMessageWidget("AI Agent", theme=self.theme)
            
            if target == "chat_dock":
                tab = next((c for c in self.main_window.findChildren(QWidget) if c.__class__.__name__ == "ChatTab"), None)
                if tab and hasattr(tab, 'add_message_widget'): tab.add_message_widget(self.active_chat_widget)
                    
            elif target == "brainstorm_dock":
                tab = next((c for c in self.main_window.findChildren(QWidget) if c.__class__.__name__ == "BrainstormTab"), None)
                if tab and hasattr(tab, 'add_message_widget'): tab.add_message_widget(self.active_chat_widget)
                    
            # --- NEW: Route to Custom Tools Tab ---
            elif target == "custom_tools_tab":
                tab = next((c for c in self.main_window.findChildren(QWidget) if c.__class__.__name__ == "CustomToolsTab"), None)
                if tab and hasattr(tab, 'add_message_widget'): tab.add_message_widget(self.active_chat_widget)
                    
            elif target == "floating" and hasattr(self.main_window, 'universal_overlay'):
                self.main_window.universal_overlay.clear_content()
                self.main_window.universal_overlay.content_layout.addWidget(self.active_chat_widget)
                self.main_window.universal_overlay.show()
                self.main_window.universal_overlay.raise_()
                
        return self.active_chat_widget
                
        return self.active_chat_widget

    def _handle_stream(self, chunk):
        if not self.runner or not hasattr(self.runner, 'current_executing_step'): return
        current_step = self.runner.current_executing_step 
        if current_step and getattr(current_step, 'ui_format', 'silent') == "live_stream":
            target = getattr(current_step, 'ui_target', 'floating')
            widget = self._get_or_create_chat_widget(target)
            widget.append_chunk(chunk)

    def _handle_step_complete(self, step_id, result_str):
        if not self.runner: return
        
        # --- THE FIX: Check if the step is an executing sub-step before falling back ---
        step = next((s for s in self.runner.blueprint.steps if s.step_id == step_id), None)
        if not step and hasattr(self.runner, 'current_executing_step') and self.runner.current_executing_step and self.runner.current_executing_step.step_id == step_id:
            step = self.runner.current_executing_step

        ui_format = getattr(step, 'ui_format', 'silent') if step else 'silent'
        ui_target = getattr(step, 'ui_target', 'floating')

        # GLOBAL GOAL INTERCEPT: Silently update Brainstorm tab if AI pivots the goal
        match = re.search(r'<UPDATE_GOAL>(.*?)</UPDATE_GOAL>', result_str, re.DOTALL)
        if match:
            b_tab = next((c for c in self.main_window.findChildren(QWidget) if c.__class__.__name__ == "BrainstormTab"), None)
            if b_tab and hasattr(b_tab, 'goal_edit'):
                b_tab.goal_edit.setText(match.group(1).strip())
            # Strip the tag from the UI text browser if it was streaming
            if self.active_chat_widget:
                browser = self.active_chat_widget.main_browser
                clean_text = browser.toPlainText().replace(match.group(0), "")
                browser.setMarkdown(clean_text)
        
        if self.runner and self.runner.blueprint.steps[-1].step_id == step_id:
            if self.active_chat_widget and hasattr(self.active_chat_widget, 'hide_status'):
                self.active_chat_widget.hide_status()
                
            # --- THE FIX: Explicitly notify the Analysis Tab when the entire blueprint is done ---
            if getattr(self.runner.blueprint, 'name', '') == "Document Analysis":
                analysis_tab = next((c for c in self.main_window.findChildren(QWidget) if c.__class__.__name__ == "AnalysisTab"), None)
                if analysis_tab and hasattr(analysis_tab, 'status_lbl'):
                    analysis_tab.status_lbl.setText("✅ Full Document Analysis Complete.")

        # --- THE UNIVERSAL WIDGET FACTORY ---
        target_widget = None

        if ui_format == "data_table":
            from gui.docks.unified_research.components.dynamic_data_table import DynamicDataTableWidget
            target_widget = DynamicDataTableWidget(result_str, self.theme)

        elif ui_format == "card_grid" or ui_format == "search_terms":
            from gui.docks.unified_research.components.dynamic_card_grid import DynamicCardGridWidget
            target_widget = DynamicCardGridWidget(result_str, self.theme)

        elif ui_format == "chat_widgets":
            # This handles Citation/Note bubbles inline in the chat
            try:
                
                # Strip markdown/filler just in case it bypassed the Master Runner
                match = re.search(r'(\[.*\]|\{.*\})', result_str, re.DOTALL)
                cleaned_str = match.group(0) if match else result_str
                
                items = json.loads(cleaned_str)
                
                # Handle dictionary wrappers (e.g., {"citations": [...]})
                if isinstance(items, dict):
                    for val in items.values():
                        if isinstance(val, list): items = val; break
                    if isinstance(items, dict): items = [items] 
                
                widget = self._get_or_create_chat_widget(ui_target)
                for item in items:
                    if isinstance(item, dict):
                        widget.add_bubble(
                            doc_name=item.get("doc_name", "Unknown Document"),
                            quote=item.get("quote", item.get("text", "")),
                            note=item.get("note", item.get("reason", ""))
                        )
            except Exception as e: 
                print(f"[UI Router] Failed to parse chat widgets JSON. Result was: {result_str[:100]}... Error: {e}")

        elif ui_format == "workspace_graph":
            from PySide6.QtWidgets import QGraphicsView
            workspace_view = next((c for c in self.main_window.findChildren(QGraphicsView) if c.__class__.__name__ == "WorkspaceView"), None)
            if workspace_view:
                if hasattr(workspace_view, 'review_and_apply_ai_graph_update'): workspace_view.review_and_apply_ai_graph_update(result_str)

        # --- DYNAMIC INJECTION ---
        # If a widget was generated, inject it into whatever tab the blueprint specified
        if target_widget:
            if ui_target == "chat_dock":
                tab = next((c for c in self.main_window.findChildren(QWidget) if c.__class__.__name__ == "ChatTab"), None)
                if tab and hasattr(tab, 'add_message_widget'): tab.add_message_widget(target_widget)
            elif ui_target == "search_tab":
                tab = next((c for c in self.main_window.findChildren(QWidget) if c.__class__.__name__ == "SearchTab"), None)
                if tab and hasattr(tab, 'results_layout'): tab.results_layout.insertWidget(tab.results_layout.count() - 1, target_widget)
            elif ui_target == "analysis_tab":
                tab = next((c for c in self.main_window.findChildren(QWidget) if c.__class__.__name__ == "AnalysisTab"), None)
                if tab and hasattr(tab, 'results_layout'): tab.results_layout.addWidget(target_widget)
            elif ui_target == "custom_tools_tab":
                tab = next((c for c in self.main_window.findChildren(QWidget) if c.__class__.__name__ == "CustomToolsTab"), None)
                if tab and hasattr(tab, 'add_message_widget'): tab.add_message_widget(target_widget)