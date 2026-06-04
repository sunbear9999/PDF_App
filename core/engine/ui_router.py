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
        self.active_chat_widget = None

    def attach_runner(self, runner):
        # We NO LONGER set self.runner here. It breaks the queue!
        # Connect signals directly to slots. We use self.sender() to track the active job.
        runner.progress_update.connect(self._handle_stream)
        runner.step_complete.connect(self._handle_step_complete)
        runner.step_started.connect(self._handle_step_started)
        runner.error.connect(self._handle_error)
        if hasattr(runner, 'user_input_requested'):
            runner.user_input_requested.connect(self._handle_user_input)

    def _get_runner(self):
        """Safely retrieves the runner that emitted the current signal."""
        sender = self.sender()
        if sender: return sender
        # Fallback to active job if sender is somehow lost
        registry = getattr(self.main_window, 'process_registry', None)
        if registry and registry.active_job:
            return registry.active_job.runner
        return None

    def _handle_user_input(self, step_id, expected_inputs):
        runner = self._get_runner()
        if not runner: return
        
        current_step = getattr(runner, 'current_executing_step', None)
        target = getattr(current_step, 'ui_target', 'chat_dock') if current_step else 'chat_dock'
        
        form_widget = UserInputFormWidget(step_id, expected_inputs, theme=self.theme)
        form_widget.form_submitted.connect(runner.submit_user_input)
        
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
        runner = self._get_runner()
        if not runner: return
        
        # Reset the active chat widget if this is the start of a NEW job
        if runner.blueprint.steps and runner.blueprint.steps[0].step_id == step_id:
            self.active_chat_widget = None

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
        current_step = getattr(runner, 'current_executing_step', None)
        target = getattr(current_step, 'ui_target', 'chat_dock') if current_step else 'chat_dock'
        
        if target in ["floating", "search_tab", "analysis_tab"]: return
            
        widget = self._get_or_create_chat_widget(target)
        try:
            if hasattr(widget, 'update_status'):
                widget.update_status(text)
        except RuntimeError:
            # Catch PySide6 C++ deletion if user cleared the chat tab mid-stream
            self.active_chat_widget = None
            widget = self._get_or_create_chat_widget(target)
            if hasattr(widget, 'update_status'):
                widget.update_status(text)
    
    def _get_or_create_chat_widget(self, target):
        # Validate that the widget wasn't destroyed by PySide C++ cleanup
        try:
            import shiboken6
            if self.active_chat_widget and not shiboken6.isValid(self.active_chat_widget):
                self.active_chat_widget = None
        except Exception:
            pass

        if not self.active_chat_widget:
            self.active_chat_widget = ChatMessageWidget("AI Agent", theme=self.theme)
            
            if target == "chat_dock":
                tab = next((c for c in self.main_window.findChildren(QWidget) if c.__class__.__name__ == "ChatTab"), None)
                if tab and hasattr(tab, 'add_message_widget'): tab.add_message_widget(self.active_chat_widget)
                    
            elif target == "brainstorm_dock":
                tab = next((c for c in self.main_window.findChildren(QWidget) if c.__class__.__name__ == "BrainstormTab"), None)
                if tab and hasattr(tab, 'add_message_widget'): tab.add_message_widget(self.active_chat_widget)
                    
            elif target == "custom_tools_tab":
                tab = next((c for c in self.main_window.findChildren(QWidget) if c.__class__.__name__ == "CustomToolsTab"), None)
                if tab and hasattr(tab, 'add_message_widget'): tab.add_message_widget(self.active_chat_widget)
                    
            elif target == "floating" and hasattr(self.main_window, 'universal_overlay'):
                self.main_window.universal_overlay.clear_content()
                self.main_window.universal_overlay.content_layout.addWidget(self.active_chat_widget)
                self.main_window.universal_overlay.show()
                self.main_window.universal_overlay.raise_()
                
        return self.active_chat_widget

    def _handle_stream(self, chunk):
        runner = self._get_runner()
        if not runner or not hasattr(runner, 'current_executing_step'): return
        current_step = runner.current_executing_step 
        if current_step and getattr(current_step, 'ui_format', 'silent') == "live_stream":
            target = getattr(current_step, 'ui_target', 'floating')
            widget = self._get_or_create_chat_widget(target)
            try:
                widget.append_chunk(chunk)
            except RuntimeError:
                # Force recreate if it was deleted by layout clearing
                self.active_chat_widget = None
                widget = self._get_or_create_chat_widget(target)
                if widget: widget.append_chunk(chunk)

    def _handle_step_complete(self, step_id, result_str, state_snapshot):
        runner = self._get_runner()
        if not runner: return
        
        step = next((s for s in runner.blueprint.steps if s.step_id == step_id), None)
        if not step and hasattr(runner, 'current_executing_step') and runner.current_executing_step and runner.current_executing_step.step_id == step_id:
            step = runner.current_executing_step

        ui_format = getattr(step, 'ui_format', 'silent') if step else 'silent'
        ui_target = getattr(step, 'ui_target', 'floating')

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
                    for k, v in new_data.items():
                        if v is None:
                            current_manifest.pop(k, None)
                        else:
                            current_manifest[k] = v
                            
                    pm.set_metadata("project_manifest", json.dumps(current_manifest))
                
                try:
                    if self.active_chat_widget:
                        browser = self.active_chat_widget.main_browser
                        clean_text = browser.toPlainText().replace(match.group(0), "")
                        browser.setMarkdown(clean_text.strip())
                except RuntimeError: pass
                
                result_str = result_str.replace(match.group(0), "").strip()
                
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
        
        if runner and runner.blueprint.steps[-1].step_id == step_id:
            try:
                if self.active_chat_widget and hasattr(self.active_chat_widget, 'hide_status'):
                    self.active_chat_widget.hide_status()
            except RuntimeError: pass
                
            if getattr(runner.blueprint, 'name', '') == "Document Analysis":
                analysis_tab = next((c for c in self.main_window.findChildren(QWidget) if c.__class__.__name__ == "AnalysisTab"), None)
                if analysis_tab and hasattr(analysis_tab, 'status_lbl'):
                    analysis_tab.status_lbl.setText("✅ Full Document Analysis Complete.")

        cleaned_str = result_str
        if ui_format in ["data_table", "card_grid", "search_terms", "chat_widgets", "nested_outline","workspace_graph"]:
            
            no_thoughts = re.sub(r'<tool_call>.*?<tool_call>', '', result_str, flags=re.DOTALL)
            
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

        target_widget = None

        if ui_format == "nested_outline":
            from gui.docks.unified_research.components.dynamic_outlines import UniversalOutlineWidget
            title = getattr(step, 'ui_title', 'AI Analysis')
            if state_snapshot:
                from core.engine.master_runner import safe_format
                title = safe_format(title, state_snapshot)
                
            annot_manager = self.main_window.viewer.annot_manager if hasattr(self.main_window, 'viewer') else None
            target_widget = UniversalOutlineWidget(title, cleaned_str, self.theme, annot_manager)
            
            if ui_target == "analysis_tab":
                tab = next((c for c in self.main_window.findChildren(QWidget) if c.__class__.__name__ == "AnalysisTab"), None)
                if tab and hasattr(tab, 'save_chunk_to_db'):
                    tab.save_chunk_to_db(state_snapshot, cleaned_str)

        elif ui_format == "data_table":
            from gui.docks.unified_research.components.dynamic_data_table import DynamicDataTableWidget
            target_widget = DynamicDataTableWidget(cleaned_str, self.theme)

        elif ui_format == "card_grid":
            from gui.docks.unified_research.components.dynamic_card_grid import DynamicCardGridWidget
            target_widget = DynamicCardGridWidget(cleaned_str, self.theme)

        elif ui_format == "search_terms":
            tab = next((c for c in self.main_window.findChildren(QWidget) if c.__class__.__name__ == "SearchTab"), None)
            if tab and hasattr(tab, 'render_search_terms'):
                try: tab.render_search_terms(json.loads(cleaned_str))
                except Exception as e: print(f"[UI Router] Failed to render search terms: {e}")

        elif ui_format == "chat_widgets":
            try:
                items = json.loads(cleaned_str)
                if isinstance(items, dict):
                    for val in items.values():
                        if isinstance(val, list): items = val; break
                    if isinstance(items, dict): items = [items] 
                
                widget = self._get_or_create_chat_widget(ui_target)
                for item in items:
                    if isinstance(item, dict):
                        try:
                            widget.add_bubble(
                                doc_name=item.get("doc_name", "Unknown Document"),
                                quote=item.get("quote", item.get("text", "")),
                                note=item.get("note", item.get("reason", ""))
                            )
                        except RuntimeError: pass
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
                
        if ui_target in ["chat_dock", "brainstorm_dock"] and ui_format in ["live_stream", "chat_widgets"]:
            pm = getattr(self.main_window, 'project_manager', None)
            if pm: pm.save_chat_message(ui_target, "ai", cleaned_str, ui_format)

    def _open_manifest_editor(self):
        try:
            from gui.docks.unified_research.unified_dock import ProjectBriefDialog
            pm = getattr(self.main_window, 'project_manager', None)
            if pm:
                dialog = ProjectBriefDialog(pm, self.theme, self.main_window)
                dialog.exec()
        except Exception as err:
            print(f"[UI Router] Failed to launch manifest editor: {err}")

    def _handle_error(self, err_msg):
        runner = self._get_runner()
        if not runner: return
        
        current_step = getattr(runner, 'current_executing_step', None)
        target = getattr(current_step, 'ui_target', 'floating') if current_step else 'floating'

        try:
            if self.active_chat_widget and hasattr(self.active_chat_widget, 'update_status'):
                self.active_chat_widget.update_status(f"❌ Error: {err_msg}")
        except RuntimeError: pass

        if target == "search_tab":
            tab = next((c for c in self.main_window.findChildren(QWidget) if c.__class__.__name__ == "SearchTab"), None)
            if tab:
                if hasattr(tab, 'status_lbl'):
                    tab.status_lbl.setText(f"❌ Pipeline Failed: {err_msg}")
                    tab.status_lbl.setStyleSheet("font-weight: bold; color: #ff4444;") 
                if hasattr(tab, 'btn_generate'):
                    tab.btn_generate.setEnabled(True) 

        elif target == "analysis_tab":
            tab = next((c for c in self.main_window.findChildren(QWidget) if c.__class__.__name__ == "AnalysisTab"), None)
            if tab and hasattr(tab, 'status_lbl'):
                tab.status_lbl.setText(f"❌ Pipeline Failed: {err_msg}")
                tab.status_lbl.setStyleSheet("font-weight: bold; color: #ff4444;")