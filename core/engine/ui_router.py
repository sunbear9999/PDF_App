# gui/components/ui_router.py
from PySide6.QtCore import QObject
from PySide6.QtWidgets import QWidget, QGraphicsView
from gui.docks.unified_research.components.chat_streamer import ChatMessageWidget
from gui.docks.unified_research.components.note_bubble import NoteBubbleWidget
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
        self.active_chat_widget = None 
        self.runner.step_started.connect(self._handle_step_started)

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
        step = next((s for s in self.runner.blueprint.steps if s.step_id == step_id), None)
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

        # 1. Update the Workspace Graph
        if ui_format == "workspace_graph":
            workspace_view = next((c for c in self.main_window.findChildren(QGraphicsView) if c.__class__.__name__ == "WorkspaceView"), None)
            if workspace_view:
                if hasattr(workspace_view, 'review_and_apply_ai_graph_update'): workspace_view.review_and_apply_ai_graph_update(result_str)
                elif hasattr(workspace_view, 'apply_ai_graph_update'): workspace_view.apply_ai_graph_update(result_str)

        # 2. Draw Universal Note Bubbles
        elif ui_format == "chat_widgets":
            try:
                if isinstance(result_str, str):
                    match = re.search(r'\[.*\]', result_str, re.DOTALL)
                    cleaned_str = match.group(0) if match else result_str.replace("```json", "").replace("```", "").strip()
                    items = json.loads(cleaned_str)
                else: items = result_str
                
                if isinstance(items, dict):
                    for val in items.values():
                        if isinstance(val, list): items = val; break
                    if isinstance(items, dict): items = [items] 
                
                widget = self._get_or_create_chat_widget(ui_target)
                for item in items:
                    # --- THE FIX: Bulletproof Type Check ---
                    if isinstance(item, dict):
                        widget.add_bubble(
                            doc_name=item.get("doc_name", "Unknown Document"),
                            quote=item.get("quote", item.get("text", "")),
                            note=item.get("note", item.get("reason", ""))
                        )
            except Exception as e: print(f"[UI Router] Failed to parse chat widgets JSON: {e}")
        elif ui_format == "custom_view" and hasattr(self.main_window, 'universal_overlay'):
            template = getattr(step, 'html_template', None)
            
            # If no template is provided, just wrap the JSON in a pretty standard pre tag
            if not template:
                html = f"<html><body style='color:#fff; font-family:sans-serif;'><h3>Data Result</h3><pre>{result_str}</pre></body></html>"
            else:
                # Safely parse the result and inject it into the HTML
                try:
                    data = json.loads(result_str)
                    html = safe_format(template, data) if isinstance(data, dict) else template.replace("{result}", result_str)
                except:
                    html = template.replace("{result}", result_str)
            
            self.main_window.universal_overlay.clear_content()
            self.main_window.universal_overlay.lbl_title.setText(getattr(step, 'ui_title', 'Custom Dashboard'))
            
            from PySide6.QtWidgets import QTextBrowser
            browser = QTextBrowser()
            browser.setStyleSheet("background-color: transparent; border: none;")
            browser.setHtml(html)
            self.main_window.universal_overlay.content_layout.addWidget(browser)
            
            self.main_window.universal_overlay.show()
            self.main_window.universal_overlay.raise_()

        # 3. Handle Search Terms Generation
        elif ui_format == "search_terms":
            try:
                match = re.search(r'\[.*\]', result_str, re.DOTALL)
                cleaned = match.group(0) if match else result_str.replace("```json", "").replace("```", "").strip()
                terms = json.loads(cleaned)
                search_tab = next((c for c in self.main_window.findChildren(QWidget) if c.__class__.__name__ == "SearchTab"), None)
                if search_tab and hasattr(search_tab, 'render_search_terms'):
                    search_tab.render_search_terms(terms)
            except Exception as e: print(f"[UI Router] Error routing search terms: {e}")

        # 4. Handle Analysis Chunking
        elif ui_format == "analysis_chunk":
            try:
                match = re.search(r'\{.*\}', result_str, re.DOTALL)
                cleaned = match.group(0) if match else result_str.replace("```json", "").replace("```", "").strip()
                data = json.loads(cleaned)
                
                # Extract meta-data injected by the FOREACH step
                item = self.runner.state.get('item', {})
                page_range = item.get('page_range', 'Unknown')
                chunk_idx = item.get('chunk_index', 0)
                doc_path = item.get('doc_path', '')
                template_id = item.get('template_id', '')
                
                analysis_tab = next((c for c in self.main_window.findChildren(QWidget) if c.__class__.__name__ == "AnalysisTab"), None)
                if analysis_tab and hasattr(analysis_tab, 'render_and_save_chunk'):
                    analysis_tab.render_and_save_chunk(doc_path, template_id, chunk_idx, data, page_range)
            except Exception as e: print(f"[UI Router] Error routing analysis chunk: {e}")

        # 5. Handle Full-Screen Static Documents
        elif ui_format == "static_document" and hasattr(self.main_window, 'universal_overlay'):
            self.main_window.universal_overlay.clear_content()
            self.main_window.universal_overlay.lbl_title.setText(getattr(step, 'ui_title', 'AI Result'))
            msg_widget = ChatMessageWidget("Final Document", theme=self.theme)
            msg_widget.append_chunk(result_str)
            self.main_window.universal_overlay.content_layout.addWidget(msg_widget)
            self.main_window.universal_overlay.show()
            self.main_window.universal_overlay.raise_()