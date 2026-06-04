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
        self.runner.error.connect(self._handle_error)
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

    def _handle_step_complete(self, step_id, result_str, state_snapshot):
        if not self.runner: return
        
        step = next((s for s in self.runner.blueprint.steps if s.step_id == step_id), None)
        if not step and hasattr(self.runner, 'current_executing_step') and self.runner.current_executing_step and self.runner.current_executing_step.step_id == step_id:
            step = self.runner.current_executing_step

        ui_format = getattr(step, 'ui_format', 'silent') if step else 'silent'
        ui_target = getattr(step, 'ui_target', 'floating')

        # GLOBAL GOAL INTERCEPT: Silently update Brainstorm tab if AI pivots the goal
        import re
        # GLOBAL MANIFEST INTERCEPT: Silently update JSON dictionary if AI pivots the strategy
        match = re.search(r'<UPDATE_MANIFEST>\s*(.*?)\s*</UPDATE_MANIFEST>', result_str, re.DOTALL)
        if match:
            try:
                raw_json_str = match.group(1).strip()
                
                if raw_json_str.startswith("```"):
                    raw_json_str = re.sub(r'^```(?:json)?|```$', '', raw_json_str, flags=re.MULTILINE).strip()

    
                new_data = json.loads(raw_json_str)
                pm = getattr(self.main_window, 'project_manager', None)
                
                if pm:
                    current_manifest = json.loads(pm.get_metadata("project_manifest", "{}"))
                    
                    # Process deletions (null values) alongside updates
                    for k, v in new_data.items():
                        if v is None:
                            current_manifest.pop(k, None)
                        else:
                            current_manifest[k] = v
                            
                    pm.set_metadata("project_manifest", json.dumps(current_manifest))
                
                if self.active_chat_widget:
                    browser = self.active_chat_widget.main_browser
                    clean_text = browser.toPlainText().replace(match.group(0), "")
                    browser.setMarkdown(clean_text.strip())
                
                result_str = result_str.replace(match.group(0), "").strip()
                
                # --- NEW: Spawn the Unified Manifest Widget ---
                from gui.docks.unified_research.components.manifest_bubble import ManifestUpdateWidget
                manifest_widget = ManifestUpdateWidget(new_data, self.theme)
                
                manifest_widget.btn_open.clicked.connect(lambda: self._open_manifest_editor())

                if ui_target == "chat_dock":
                    tab = next((c for c in self.main_window.findChildren(QWidget) if c.__class__.__name__ == "ChatTab"), None)
                    if tab and hasattr(tab, 'add_message_widget'): tab.add_message_widget(manifest_widget)
                elif ui_target == "brainstorm_dock":
                    tab = next((c for c in self.main_window.findChildren(QWidget) if c.__class__.__name__ == "BrainstormTab"), None)
                    if tab and hasattr(tab, 'add_message_widget'): tab.add_message_widget(manifest_widget)

            except Exception as e:
                print(f"[UI Router] Failed to parse manifest update: {e}")
        
        
        if self.runner and self.runner.blueprint.steps[-1].step_id == step_id:
            if self.active_chat_widget and hasattr(self.active_chat_widget, 'hide_status'):
                self.active_chat_widget.hide_status()
                
            if getattr(self.runner.blueprint, 'name', '') == "Document Analysis":
                analysis_tab = next((c for c in self.main_window.findChildren(QWidget) if c.__class__.__name__ == "AnalysisTab"), None)
                if analysis_tab and hasattr(analysis_tab, 'status_lbl'):
                    analysis_tab.status_lbl.setText("✅ Full Document Analysis Complete.")

       # --- UNIVERSAL JSON CLEANER (Anti-Freeze Edition) ---
        cleaned_str = result_str
        if ui_format in ["data_table", "card_grid", "search_terms", "chat_widgets", "nested_outline","workspace_graph"]:
            import re
            
            # 1. Strip reasoning tags
            no_thoughts = re.sub(r'<think>.*?</think>', '', result_str, flags=re.DOTALL)
            
            # 2. Extract JSON using fast string index math to prevent regex lockup
            first_brace = no_thoughts.find('{')
            last_brace = no_thoughts.rfind('}')
            first_bracket = no_thoughts.find('[')
            last_bracket = no_thoughts.rfind(']')
            
            is_dict = first_brace != -1 and last_brace != -1 and (first_bracket == -1 or first_brace < first_bracket)
            is_list = first_bracket != -1 and last_bracket != -1 and (first_brace == -1 or first_bracket < first_brace)
            
            if is_dict:
                cleaned_str = no_thoughts[first_brace:last_brace+1]
            elif is_list:
                cleaned_str = no_thoughts[first_bracket:last_bracket+1]
            else:
                cleaned_str = no_thoughts

        # --- THE UNIVERSAL WIDGET FACTORY ---
        target_widget = None

        if ui_format == "nested_outline":
            from gui.docks.unified_research.components.dynamic_outlines import UniversalOutlineWidget
            
            title = getattr(step, 'ui_title', 'AI Analysis')
            # THE FIX: Use the frozen snapshot, not the active runner state!
            if state_snapshot:
                from core.engine.master_runner import safe_format
                title = safe_format(title, state_snapshot)
                
            annot_manager = self.main_window.viewer.annot_manager if hasattr(self.main_window, 'viewer') else None
            target_widget = UniversalOutlineWidget(title, cleaned_str, self.theme, annot_manager)
            
            if ui_target == "analysis_tab":
                tab = next((c for c in self.main_window.findChildren(QWidget) if c.__class__.__name__ == "AnalysisTab"), None)
                # THE FIX: Pass the frozen snapshot to the database saver
                if tab and hasattr(tab, 'save_chunk_to_db'):
                    tab.save_chunk_to_db(state_snapshot, cleaned_str)

        elif ui_format == "data_table":
            from gui.docks.unified_research.components.dynamic_data_table import DynamicDataTableWidget
            target_widget = DynamicDataTableWidget(cleaned_str, self.theme)

        elif ui_format == "card_grid":
            from gui.docks.unified_research.components.dynamic_card_grid import DynamicCardGridWidget
            target_widget = DynamicCardGridWidget(cleaned_str, self.theme)

        elif ui_format == "search_terms":
            # Route directly to the native Search Tab renderer so it uses your custom buttons
            tab = next((c for c in self.main_window.findChildren(QWidget) if c.__class__.__name__ == "SearchTab"), None)
            if tab and hasattr(tab, 'render_search_terms'):
                try:
                    tab.render_search_terms(json.loads(cleaned_str))
                except Exception as e:
                    print(f"[UI Router] Failed to render search terms: {e}")
            target_widget = None # Prevent standard dynamic injection

        elif ui_format == "chat_widgets":
            try:
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
                print(f"[UI Router] Failed to parse chat widgets JSON: {e}")

        elif ui_format == "workspace_graph":
            from PySide6.QtWidgets import QGraphicsView
            workspace_view = next((c for c in self.main_window.findChildren(QGraphicsView) if c.__class__.__name__ == "WorkspaceView"), None)
            if workspace_view:
                try:
                    if hasattr(workspace_view, 'review_and_apply_ai_graph_update'):
                        workspace_view.review_and_apply_ai_graph_update(cleaned_str)
                    elif hasattr(workspace_view, 'apply_ai_graph_update'):
                        workspace_view.apply_ai_graph_update(cleaned_str)
                except Exception as e:
                    print(f"[UI Router] Failed to launch graph review: {e}")
                    if hasattr(workspace_view, 'apply_ai_graph_update'):
                        workspace_view.apply_ai_graph_update(cleaned_str)

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
        # --- NEW: Save AI output to persistent chat history ---
        if ui_target in ["chat_dock", "brainstorm_dock"] and ui_format in ["live_stream", "chat_widgets"]:
            pm = getattr(self.main_window, 'project_manager', None)
            if pm:
                pm.save_chat_message(ui_target, "ai", cleaned_str, ui_format)
    def _open_manifest_editor(self):
        """Safely launches the project manifest dialog from anywhere."""
        try:
            from gui.docks.unified_research.unified_dock import ProjectBriefDialog
            pm = getattr(self.main_window, 'project_manager', None)
            if pm:
                dialog = ProjectBriefDialog(pm, self.theme, self.main_window)
                dialog.exec()
        except Exception as err:
            print(f"[UI Router] Failed to launch manifest editor: {err}")
    def _handle_error(self, err_msg):
        """Universally routes errors to the active UI component."""
        if not self.runner: return
        
        current_step = getattr(self.runner, 'current_executing_step', None)
        target = getattr(current_step, 'ui_target', 'floating') if current_step else 'floating'

        # 1. Update the chat widget if one was currently active/streaming
        if self.active_chat_widget and hasattr(self.active_chat_widget, 'update_status'):
            self.active_chat_widget.update_status(f"❌ Error: {err_msg}")

        # 2. Update specific static tabs (like Search and Analysis)
        if target == "search_tab":
            tab = next((c for c in self.main_window.findChildren(QWidget) if c.__class__.__name__ == "SearchTab"), None)
            if tab:
                if hasattr(tab, 'status_lbl'):
                    tab.status_lbl.setText(f"❌ Pipeline Failed: {err_msg}")
                    tab.status_lbl.setStyleSheet("font-weight: bold; color: #ff4444;") # Red error text
                if hasattr(tab, 'btn_generate'):
                    tab.btn_generate.setEnabled(True) # Unlock the UI so they can try again

        elif target == "analysis_tab":
            tab = next((c for c in self.main_window.findChildren(QWidget) if c.__class__.__name__ == "AnalysisTab"), None)
            if tab and hasattr(tab, 'status_lbl'):
                tab.status_lbl.setText(f"❌ Pipeline Failed: {err_msg}")
                tab.status_lbl.setStyleSheet("font-weight: bold; color: #ff4444;")