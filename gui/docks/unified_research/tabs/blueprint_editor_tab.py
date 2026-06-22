import json
import re
import dataclasses
import uuid
from PySide6.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QPushButton, QLabel,
                             QComboBox, QFrame, QTextEdit, QLineEdit, QDialog,
                             QInputDialog, QStackedWidget, QSplitter, QTabWidget, QMessageBox)
from PySide6.QtCore import Qt
from gui.managers.dialog_manager import exec_as_modal, get_for_widget
from PySide6.QtGui import QCursor

from core.engine.action_model import AIActionBlueprint
from core.engine.default_blueprints import DefaultBlueprints
from core.events.event_bus import EventBus
from core.events.domains.workflow_events import WorkflowEvent, WorkflowIntent, WorkflowPayload
from gui.docks.unified_research.components.workflow_editor_canvas import VisualWorkflowEditor


class BlueprintHelpDialog(QDialog):
    """Comprehensive help and API reference for the Blueprint Builder."""
    def __init__(self, theme, parent=None):
        super().__init__(parent)
        self.theme = theme
        self.setWindowTitle("Blueprint Builder — Documentation")
        self.resize(920, 720)
        self._build_ui()

    def _build_ui(self):
        layout = QVBoxLayout(self)
        self.setStyleSheet(f"""
            QDialog {{ background-color: {self.theme.get('bg_main', '#1e1e1e')}; color: {self.theme.get('text_main', '#fff')}; }}
            QTabWidget::pane {{ border: 1px solid {self.theme.get('border', '#444')}; }}
            QTabBar::tab {{ background: {self.theme.get('bg_panel', '#333')}; color: #aaa; padding: 8px 14px; border: 1px solid {self.theme.get('border', '#444')}; border-bottom: none; }}
            QTabBar::tab:selected {{ background: {self.theme.get('bg_input', '#2b2b2b')}; color: #fff; font-weight: bold; border-top: 2px solid {self.theme.get('accent', '#b366ff')}; }}
            QTextEdit {{ background-color: {self.theme.get('bg_input', '#252525')}; border: none; font-size: 13px; padding: 8px; }}
        """)
        tabs = QTabWidget()

        def _make_tab(html: str) -> QTextEdit:
            t = QTextEdit()
            t.setReadOnly(True)
            t.setHtml(html)
            return t

        tabs.addTab(_make_tab("""
            <h2 style='color:#b366ff;'>Blueprint Builder Overview</h2>
            <p>A blueprint is a multi-step AI pipeline that can be launched from any dock, the toolbar, or a plugin.
            Each step produces output saved to the shared <b>state</b> dictionary and downstream steps can reference it via <code>{key}</code>.</p>
            <h3>How to build a workflow:</h3>
            <ol>
                <li>Click a step type in the left sidebar to add it to the canvas.</li>
                <li>Select two nodes and click <b>Connect Selected</b> (or press the wiring mode) to link them.</li>
                <li>Click a node to edit its properties in the inspector panel on the right.</li>
                <li>Click <b>Apply Changes</b> in the inspector to commit edits.</li>
                <li>Fill in the blueprint Name + Description at the top, then click <b>Save Blueprint</b>.</li>
            </ol>
            <h3>Canvas shortcuts:</h3>
            <ul>
                <li><b>Del / Backspace</b> — delete selected nodes</li>
                <li><b>Ctrl+D</b> — duplicate selected nodes</li>
                <li><b>Ctrl++ / Ctrl+-</b> — zoom in/out</li>
                <li><b>Ctrl+0</b> — fit all nodes in view</li>
                <li><b>Scroll wheel</b> — zoom at cursor</li>
                <li><b>Middle-click drag</b> — pan canvas</li>
            </ul>
            <h3>Reusable Steps (Step Library):</h3>
            <p>Any step can be saved to the <b>Reusable Step Library</b> via the "Save as Reusable Step" button in the inspector.
            Saved steps appear in the sidebar and can be added to any workflow. The step library is exported with your project via the Pack Manager.</p>
            <h3>Plugin steps:</h3>
            <p>Installed plugins automatically appear in the sidebar under their registered categories. Plugin-contributed step types,
            GUI output nodes, and inspector field schemas are all injected at runtime through the WorkflowNodeTypeRegistry.</p>
        """), "Overview")

        tabs.addTab(_make_tab("""
            <h2 style='color:#b366ff;'>All Step Types</h2>

            <h3 style='color:#7c4dff;'>LLM Query</h3>
            <p>Calls the language model. Set <b>Prompt Key</b> to use a saved prompt template, or write a <b>System Prompt</b> directly.
            <b>Query/Input</b> can contain <code>{state_key}</code> references for dynamic context injection.</p>
            <p>Useful fields: model, prompt_key, system_prompt, required_context, llm_options (temperature, num_predict, num_ctx, json_mode).</p>

            <h3 style='color:#7c4dff;'>LLM Schema Query</h3>
            <p>Same as LLM Query but forces JSON output mode. Define an <b>Output Schema</b> in the inspector — the LLM must match it exactly.</p>

            <h3 style='color:#0288d1;'>RAG Search</h3>
            <p>Searches your indexed documents using embedding similarity. Returns text context for downstream LLM steps.</p>
            <p><b>queries</b>: list of search strings (or state key holding a list).<br>
            <b>n_results</b>: chunks retrieved per query (default 5, increase for more thorough coverage).<br>
            <b>tag_filters</b>: comma-separated document tags to restrict search scope.<br>
            <b>tag_logic</b>: AND (all tags must match) or OR (any tag matches).</p>

            <h3 style='color:#e65100;'>Branch</h3>
            <p>Evaluates a condition and routes to <b>true</b> or <b>false</b> child steps.
            Write the condition in <b>Query/Input</b> using Python-style expressions: <code>{confidence} &gt; 0.8</code>,
            <code>{tag} == "yes"</code>. Connect child steps via the 'true' and 'false' ports.</p>

            <h3 style='color:#f57c00;'>For Each</h3>
            <p>Iterates over a list in state. <b>Query/Input</b> = the state key holding the list. Connect child steps via the 'each' port.
            Each item is available as <code>{item}</code> in child steps.</p>

            <h3 style='color:#00897b;'>User Input</h3>
            <p>Pauses the workflow and shows a dialog asking the user for text input.
            <b>Query/Input</b> = the message shown in the dialog. Result = user's typed response.</p>

            <h3 style='color:#558b2f;'>Python Script</h3>
            <p>Runs sandboxed Python code. Access state via the <code>state</code> dict. Set <code>result = your_value</code>.
            Fire events via <code>workflow_api.emit_event(event_type, payload)</code>.</p>

            <h3 style='color:#37474f;'>Database Write</h3>
            <p>Writes to the project SQLite database. Inputs: <code>table</code> (string) and <code>payload</code> (dict of column → value).
            Returns "Success" or "Failed: reason".</p>

            <h3 style='color:#ad1457;'>Ontology Upsert</h3>
            <p>Inserts or updates entities and relations in the project knowledge graph.
            Input must be JSON with <code>entities: [...]</code> and <code>relations: [...]</code> lists matching the ontology schema.</p>

            <h3 style='color:#c62828;'>Dispatch Event</h3>
            <p>Fires a named event on the application event bus. Inputs: <code>signal_name</code>, <code>intent</code>, <code>payload</code> (dict).
            Useful for triggering dock actions, switching tabs, or calling other workflows.</p>

            <h3 style='color:#b71c1c;'>Await Event</h3>
            <p>Blocks the workflow until a named event fires. Inputs: <code>signal_name</code>, <code>timeout_ms</code> (default 30000).
            Returns the event payload, or <code>{"timed_out": true}</code> on timeout.</p>

            <h3 style='color:#4527a0;'>Analysis Steps (Contract → Chunk → Compact → Finalize)</h3>
            <p>Chain these steps for document analysis pipelines:
            <b>Analysis Contract</b> defines expected outputs → <b>Document Chunk</b> splits documents →
            <b>Analysis Compact</b> merges partial results → <b>Analysis Finalize</b> emits results to workspace.</p>

            <h3 style='color:#546e7a;'>Reusable Step (Library Ref)</h3>
            <p>Runs a saved step from the step library by name. Set <b>Step Ref</b> to the saved step's name.
            Library steps are exportable via Pack Manager and shareable across projects.</p>
        """), "Step Types")

        tabs.addTab(_make_tab("""
            <h2 style='color:#b366ff;'>State Variables &amp; Context Injection</h2>
            <p>Every step reads from and writes to a shared <b>state dict</b>. Reference any key using <code>{key_name}</code> in prompts,
            queries, or system prompts. The runner resolves these at execution time.</p>

            <h3>Built-in variables injected automatically:</h3>
            <table style='border-collapse:collapse; width:100%;'>
              <tr style='background:#333;'><th style='padding:6px; text-align:left;'>Key</th><th style='padding:6px; text-align:left;'>Value</th></tr>
              <tr><td style='padding:5px;'><code>{user_input}</code></td><td style='padding:5px;'>The user's typed message when launching the blueprint</td></tr>
              <tr style='background:#2a2a2a;'><td style='padding:5px;'><code>{selected_model}</code></td><td style='padding:5px;'>The AI model currently selected in the UI</td></tr>
              <tr><td style='padding:5px;'><code>{doc_path}</code></td><td style='padding:5px;'>Path to the active document</td></tr>
              <tr style='background:#2a2a2a;'><td style='padding:5px;'><code>{project_manifest}</code></td><td style='padding:5px;'>JSON project strategy / manifest</td></tr>
              <tr><td style='padding:5px;'><code>{workspace_data}</code></td><td style='padding:5px;'>Current workspace graph JSON</td></tr>
              <tr style='background:#2a2a2a;'><td style='padding:5px;'><code>{selected_text}</code></td><td style='padding:5px;'>Text selected in the PDF viewer (if launched from selection context menu)</td></tr>
              <tr><td style='padding:5px;'><code>{annotation_text}</code></td><td style='padding:5px;'>Text of a note annotation (if launched from notes dock)</td></tr>
            </table>

            <h3 style='margin-top:16px;'>Required Context:</h3>
            <p>The <b>Context Keys</b> field in the inspector tells the runner which state keys this step needs.
            If a required key is missing, the step is skipped with a warning. List keys comma-separated.</p>

            <h3>Custom inputs via Blueprint Settings:</h3>
            <p>Define <b>Expected Inputs</b> in the Blueprint Settings panel (below Name/Desc) to ask the user for values
            before the workflow starts. Each input becomes a state variable automatically.</p>

            <h3>Prompt template resolution:</h3>
            <p>If a step has a <b>Prompt Key</b>, the runner loads that saved prompt text and resolves <code>{variables}</code> inside it.
            Prompts can be created and managed in the Prompt Manager settings tab.</p>
        """), "State Variables")

        tabs.addTab(_make_tab("""
            <h2 style='color:#00cc66;'>Python Script Environment</h2>
            <p>Python Script steps run in a sandboxed local environment. They're ideal for transforming data between LLM steps,
            computing derived values, filtering lists, or calling custom functions.</p>

            <h3>Available globals in your script:</h3>
            <ul>
                <li><code>state</code> — read-only snapshot of the current workflow state dict</li>
                <li><code>workflow_api</code> — WorkflowStepAPI instance for emitting events</li>
                <li><code>json</code>, <code>re</code>, <code>math</code>, <code>datetime</code> — pre-imported standard library modules</li>
                <li><code>result</code> — initialize this to set the step's output (starts as None)</li>
            </ul>

            <h3>Example — Parse LLM JSON and extract a list:</h3>
            <pre style='background:#111; padding:10px; border-radius:4px; color:#00cc66;'>
import json

raw = state.get("llm_analysis", "{}")
data = json.loads(raw)
topics = [item["topic"] for item in data.get("topics", [])]

# This gets saved under the step's output_key
result = topics
            </pre>

            <h3>Example — Emit a custom event:</h3>
            <pre style='background:#111; padding:10px; border-radius:4px; color:#00cc66;'>
workflow_api.emit_event("debug_log", {"msg": f"Found {len(state.get('items', []))} items"})
result = "done"
            </pre>

            <h3>WorkflowStepAPI methods:</h3>
            <ul>
                <li><code>workflow_api.get_state(key, default=None)</code> — read from state</li>
                <li><code>workflow_api.emit_event(event_type: str, payload: dict)</code> — fire an event on the bus</li>
            </ul>

            <h3>Important notes:</h3>
            <ul>
                <li>Scripts do NOT have access to PapyrusAPI, file system, or network by default</li>
                <li>Exceptions are caught and returned as error strings — check output in the Debugger tab</li>
                <li>Assign <code>result = ...</code> or the step output will be None</li>
            </ul>
        """), "Python API")

        tabs.addTab(_make_tab("""
            <h2 style='color:#3399ff;'>UI Output Formats &amp; Targets</h2>
            <p>Connect a <b>GUI Output Node</b> to a step via a "render" edge to control how results are displayed.</p>

            <h3>Output Formats:</h3>
            <table style='border-collapse:collapse; width:100%;'>
              <tr style='background:#333;'><th style='padding:6px;'>Format</th><th style='padding:6px;'>Description</th><th style='padding:6px;'>Requires JSON?</th></tr>
              <tr><td style='padding:5px;'><code>silent</code></td><td style='padding:5px;'>Store result in state only; no UI</td><td style='padding:5px;'>No</td></tr>
              <tr style='background:#2a2a2a;'><td style='padding:5px;'><code>live_stream</code></td><td style='padding:5px;'>Stream text token-by-token like ChatGPT</td><td style='padding:5px;'>No</td></tr>
              <tr><td style='padding:5px;'><code>nested_outline</code></td><td style='padding:5px;'>Floating overlay with formatted text/markdown</td><td style='padding:5px;'>No</td></tr>
              <tr style='background:#2a2a2a;'><td style='padding:5px;'><code>chat_widgets</code></td><td style='padding:5px;'>Citation bubbles with doc name, page, quote</td><td style='padding:5px;'>JSON array</td></tr>
              <tr><td style='padding:5px;'><code>search_terms</code></td><td style='padding:5px;'>Interactive search cards with clickable queries</td><td style='padding:5px;'>JSON: {search_terms: [{term, reason}]}</td></tr>
              <tr style='background:#2a2a2a;'><td style='padding:5px;'><code>data_table</code></td><td style='padding:5px;'>Sortable spreadsheet-style table</td><td style='padding:5px;'>JSON array of objects</td></tr>
              <tr><td style='padding:5px;'><code>card_grid</code></td><td style='padding:5px;'>Grid of visual cards</td><td style='padding:5px;'>JSON array of objects</td></tr>
              <tr style='background:#2a2a2a;'><td style='padding:5px;'><code>workspace_graph</code></td><td style='padding:5px;'>Import nodes/edges into workspace graph</td><td style='padding:5px;'>JSON graph</td></tr>
              <tr><td style='padding:5px;'><code>results_dialog</code></td><td style='padding:5px;'>Browse-and-jump document search results dialog</td><td style='padding:5px;'>JSON array of matches</td></tr>
              <tr style='background:#2a2a2a;'><td style='padding:5px;'><code>bias_metrics</code></td><td style='padding:5px;'>Scored bias assessment card</td><td style='padding:5px;'>JSON metrics object</td></tr>
            </table>

            <h3 style='margin-top:16px;'>UI Targets (where output appears):</h3>
            <ul>
                <li><code>floating</code> — floating overlay panel over the main window</li>
                <li><code>custom_tools_tab</code> — Custom Tools tab in Research Assistant</li>
                <li><code>chat_tab</code> / <code>chat_dock</code> — Chat tab output area</li>
                <li><code>search_tab</code> — Search tab (for search_terms format)</li>
                <li><code>data_dock_workflow</code> — Data Dock workflow panel</li>
                <li><code>notes_dock_workflow</code> — Notes Dock workflow panel</li>
                <li>Any custom target registered by a plugin or WorkflowPanel widget</li>
            </ul>

            <h3>Inline Citations:</h3>
            <p>Check <b>Inline Citations</b> in the inspector and set <b>Citation Src Key</b> to a state key holding RAG results.
            The runner will interleave source bubbles into the streamed output automatically.</p>
        """), "Output Formats")

        tabs.addTab(_make_tab("""
            <h2 style='color:#ff9800;'>Dock Mount Points</h2>
            <p>A blueprint's <b>Mount Points</b> list controls where it appears in the app's UI.
            Blueprints can be mounted in multiple locations simultaneously.</p>

            <h3>Workflow Panel mounts (appear in dock dropdowns):</h3>
            <table style='border-collapse:collapse; width:100%;'>
              <tr style='background:#333;'><th style='padding:6px;'>Mount Point</th><th style='padding:6px;'>Appears in</th></tr>
              <tr><td style='padding:5px;'><code>custom_tools_tab</code></td><td style='padding:5px;'>Research Assistant → Custom Tools tab (default)</td></tr>
              <tr style='background:#2a2a2a;'><td style='padding:5px;'><code>data_dock</code></td><td style='padding:5px;'>Data Dock workflow panel</td></tr>
              <tr><td style='padding:5px;'><code>notes_dock</code></td><td style='padding:5px;'>Notes Dock workflow panel</td></tr>
              <tr style='background:#2a2a2a;'><td style='padding:5px;'><code>essay_dock</code></td><td style='padding:5px;'>Essay Dock workflow panel</td></tr>
              <tr><td style='padding:5px;'><code>ocr_dock</code></td><td style='padding:5px;'>OCR Dock toolbar</td></tr>
            </table>

            <h3 style='margin-top:16px;'>Context menu mounts (appear in right-click menus):</h3>
            <table style='border-collapse:collapse; width:100%;'>
              <tr style='background:#333;'><th style='padding:6px;'>Mount Point</th><th style='padding:6px;'>Appears in</th></tr>
              <tr><td style='padding:5px;'><code>source_viewer:context_menu:text_selection</code></td><td style='padding:5px;'>PDF viewer text selection right-click menu</td></tr>
              <tr style='background:#2a2a2a;'><td style='padding:5px;'><code>essay_dock:context_menu:text_selection</code></td><td style='padding:5px;'>Essay editor text selection right-click</td></tr>
              <tr><td style='padding:5px;'><code>notes_dock:context_menu:annotation</code></td><td style='padding:5px;'>Notes annotation right-click menu</td></tr>
              <tr style='background:#2a2a2a;'><td style='padding:5px;'><code>data_dock:context_menu:cell</code></td><td style='padding:5px;'>Data table cell right-click menu</td></tr>
              <tr><td style='padding:5px;'><code>document_list:context_menu:item</code></td><td style='padding:5px;'>Document list item right-click menu</td></tr>
            </table>

            <h3 style='margin-top:16px;'>Active Contexts:</h3>
            <p>Active contexts auto-inject state variables when the blueprint runs. Common values:</p>
            <ul>
                <li><code>selected_document</code> — injects current doc path</li>
                <li><code>workspace</code> — injects workspace_data JSON</li>
                <li><code>project</code> — injects project_manifest JSON</li>
                <li><code>user_selection</code> — injects selected_text from PDF viewer</li>
            </ul>
        """), "Mount Points")

        tabs.addTab(_make_tab("""
            <h2 style='color:#00e5ff;'>Plugin Integration</h2>
            <p>The Blueprint Builder is fully plugin-ready. Plugins can extend it at multiple levels:</p>

            <h3>1. Register custom step types:</h3>
            <pre style='background:#111; padding:10px; border-radius:4px; color:#00cc66;'>
from core.engine.workflow_graph_service import register_plugin_step_type

# Register your step type → node type ID mapping
register_plugin_step_type("MY_CUSTOM_STEP", "myplugin.my_custom_step")

# Register the WorkflowNodeType with full metadata + inspector schema
api.register_workflow_step(MyCustomStep)
            </pre>
            <p>Your step will automatically appear in the sidebar under its registered category,
            with the correct inspector fields driven by its <code>input_schema</code>.</p>

            <h3>2. Register custom GUI output components:</h3>
            <pre style='background:#111; padding:10px; border-radius:4px; color:#00cc66;'>
from core.engine.workflow_graph_service import register_plugin_gui_component

register_plugin_gui_component("myplugin.ui.my_view", {
    "label": "My Custom View",
    "ui_format": "my_format",
    "ui_target": "custom_tools_tab",
    "description": "Renders results in a custom visualization.",
})
            </pre>

            <h3>3. Add reusable steps to the library:</h3>
            <pre style='background:#111; padding:10px; border-radius:4px; color:#00cc66;'>
step_manager.save_step("my_plugin_step", my_action_step)
            </pre>
            <p>These appear in the Reusable Steps sidebar and are exportable via Pack Manager.</p>

            <h3>4. Register dock actions (context menu / toolbar items):</h3>
            <p>Use <code>api.register_dock_action(DockActionSpec(...))</code> to inject workflow launchers
            into any dock's context menus or toolbars. The blueprint runs with full context from the dock.</p>
        """), "Plugin Integration")

        layout.addWidget(tabs)
        btn_close = QPushButton("Close")
        btn_close.clicked.connect(self.accept)
        btn_close.setStyleSheet(f"background-color: {self.theme.get('bg_panel', '#333')}; padding: 8px 20px; border-radius: 4px;")
        layout.addWidget(btn_close)


class BlueprintEditorTab(QWidget):
    def __init__(self, app_context, parent=None):
        super().__init__(parent)
        self.app_context = app_context
        self.theme = app_context.theme_manager.get_theme() if app_context.theme_manager else {}
        self.bpm = app_context.blueprint_manager
        self.blueprint_registry = app_context.blueprint_registry
        self.step_manager = app_context.step_manager

        self.current_blueprint = None
        self.bus = EventBus.get_instance()
        self._workflow_requests = {}
        self.bus.workflow_state_changed.connect(self._handle_workflow_state)

        # When a plugin loads, refresh the canvas node type list
        if hasattr(self.bus, "plugin_loaded"):
            self.bus.plugin_loaded.connect(self._on_plugin_loaded)

        self._build_ui()

    def _on_plugin_loaded(self, plugin_id: str = ""):
        """Refresh the canvas sidebar when a plugin registers new step types."""
        if hasattr(self, "visual_editor"):
            # Reload node type registry in case the plugin added new types
            ntr = getattr(self.app_context, "workflow_node_type_registry", None)
            self.visual_editor.node_type_registry = ntr
            # Rebuild the sidebar step-type buttons
            self.visual_editor._build_ui()
            self.visual_editor.update_theme(self.theme)

    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(4)

        # --- Top Bar ---
        top_bar = QHBoxLayout()
        top_bar.setSpacing(4)
        top_bar.addWidget(QLabel("<b>Blueprint:</b>"))
        self.combo_blueprints = QComboBox()
        self.combo_blueprints.currentIndexChanged.connect(self._load_selected_blueprint)
        top_bar.addWidget(self.combo_blueprints, 1)
        self.btn_restore = QPushButton("Restore Default")
        self.btn_restore.clicked.connect(self._restore_default)
        self.btn_restore.hide()
        top_bar.addWidget(self.btn_restore)
        self.btn_delete = QPushButton("Delete")
        self.btn_delete.clicked.connect(self._delete_tool)
        self.btn_delete.hide()
        top_bar.addWidget(self.btn_delete)
        btn_create = QPushButton("+ New Blueprint")
        btn_create.setStyleSheet(f"background-color: {self.theme.get('success', '#00cc66')}; color: white; font-weight: bold; border-radius: 4px; padding: 4px 8px;")
        btn_create.clicked.connect(self._create_new_tool)
        top_bar.addWidget(btn_create)
        btn_help = QPushButton("Docs")
        btn_help.clicked.connect(self._show_help)
        top_bar.addWidget(btn_help)
        layout.addLayout(top_bar)

        # --- Meta fields + Blueprint Settings ---
        meta_frame = QFrame()
        meta_frame.setFrameShape(QFrame.Shape.StyledPanel)
        meta_layout = QVBoxLayout(meta_frame)
        meta_layout.setContentsMargins(6, 4, 6, 4)
        meta_layout.setSpacing(3)

        row1 = QHBoxLayout()
        row1.addWidget(QLabel("Name:"))
        self.input_bp_name = QLineEdit()
        self.input_bp_name.setPlaceholderText("Blueprint name")
        self.input_bp_name.setWhatsThis("The display name for this blueprint. Shown in dropdowns throughout the app. Should be descriptive and unique.")
        row1.addWidget(self.input_bp_name, 1)
        row1.addWidget(QLabel("Desc:"))
        self.input_bp_desc = QLineEdit()
        self.input_bp_desc.setPlaceholderText("Short description")
        self.input_bp_desc.setWhatsThis("A brief description of what this blueprint does. Shown as a tooltip in mount point dropdowns.")
        row1.addWidget(self.input_bp_desc, 2)
        meta_layout.addLayout(row1)

        row2 = QHBoxLayout()
        row2.addWidget(QLabel("Mount Points:"))
        self.input_bp_mounts = QLineEdit()
        self.input_bp_mounts.setPlaceholderText("custom_tools_tab, data_dock, notes_dock, ...")
        self.input_bp_mounts.setToolTip("Comma-separated mount points where this blueprint appears. See Docs > Mount Points.")
        self.input_bp_mounts.setWhatsThis("Comma-separated list of locations where this blueprint appears in the UI. Common values: custom_tools_tab (Custom Tools tab), data_dock (Data Dock workflow panel), notes_dock (Notes Dock), essay_dock (Essay Dock). Context menu mounts: source_viewer:context_menu:text_selection, notes_dock:context_menu:annotation, etc. Press Docs for the full reference.")
        row2.addWidget(self.input_bp_mounts, 1)
        row2.addWidget(QLabel("Active Contexts:"))
        self.input_bp_contexts = QLineEdit()
        self.input_bp_contexts.setPlaceholderText("selected_document, workspace, project, ...")
        self.input_bp_contexts.setToolTip("Comma-separated context keys auto-injected into state. See Docs > State Variables.")
        self.input_bp_contexts.setWhatsThis("Comma-separated list of app context keys to auto-inject as state variables when this blueprint runs. Use: selected_document (injects doc path), workspace (injects workspace graph JSON), project (injects project manifest), user_selection (injects selected PDF text).")
        row2.addWidget(self.input_bp_contexts, 1)
        meta_layout.addLayout(row2)

        layout.addWidget(meta_frame)

        # --- Main Canvas + Assistants ---
        self.main_splitter = QSplitter(Qt.Orientation.Horizontal)

        self.visual_editor = VisualWorkflowEditor(
            self.theme,
            node_type_registry=getattr(self.app_context, "workflow_node_type_registry", None),
            step_manager=self.step_manager,
            parent=self,
        )

        self.right_tabs = QTabWidget()

        # AI Builder
        self.assistant_tab = QWidget()
        ast_lyt = QVBoxLayout(self.assistant_tab)
        self.txt_ast_chat = QTextEdit()
        self.txt_ast_chat.setReadOnly(True)
        self.txt_ast_chat.setStyleSheet("background: transparent; border: none; color: white;")
        self.txt_ast_chat.append(
            "<i>Describe the workflow you want to build and I'll generate the blueprint JSON automatically.</i><br>"
            "<i>Tip: Be specific about what data flows between steps and what output format you need.</i><br>"
        )
        ast_lyt.addWidget(self.txt_ast_chat)
        ast_input_lyt = QHBoxLayout()
        self.input_ast = QLineEdit()
        self.input_ast.setPlaceholderText("e.g., 'Search documents for X then summarize as bullets...'")
        self.input_ast.returnPressed.connect(self._send_chat)
        self.btn_ast_send = QPushButton("Build")
        self.btn_ast_send.clicked.connect(self._send_chat)
        ast_input_lyt.addWidget(self.input_ast)
        ast_input_lyt.addWidget(self.btn_ast_send)
        ast_lyt.addLayout(ast_input_lyt)
        self.right_tabs.addTab(self.assistant_tab, "AI Builder")

        # Debugger
        self.debugger_tab = QWidget()
        dbg_lyt = QVBoxLayout(self.debugger_tab)
        self.btn_test_run = QPushButton("Run Test in Debugger")
        self.btn_test_run.setStyleSheet(f"background-color: {self.theme.get('accent','#b366ff')}; color: white; padding: 5px;")
        self.btn_test_run.clicked.connect(self._run_debugger)
        dbg_lyt.addWidget(self.btn_test_run)
        self.txt_debugger = QTextEdit()
        self.txt_debugger.setReadOnly(True)
        self.txt_debugger.setStyleSheet("color: #00ff00; font-family: monospace; background: transparent; border: none;")
        dbg_lyt.addWidget(self.txt_debugger)
        self.right_tabs.addTab(self.debugger_tab, "Debugger")

        self.main_splitter.addWidget(self.visual_editor)
        self.main_splitter.addWidget(self.right_tabs)
        self.main_splitter.setSizes([750, 380])
        layout.addWidget(self.main_splitter, 1)

        # --- Bottom Bar ---
        bottom_bar = QHBoxLayout()
        self.btn_save = QPushButton("Save Blueprint")
        self.btn_save.clicked.connect(self._save_blueprints)
        bottom_bar.addStretch()
        bottom_bar.addWidget(self.btn_save)
        layout.addLayout(bottom_bar)

        self.update_theme(self.theme)
        if self.bpm:
            self._populate_combo_box()

    def _show_help(self):
        # Try to open the global help center at the blueprint overview topic
        try:
            from core.events.domains.help_events import HelpIntent, HelpPayload
            self.bus.help_action_requested.emit(
                HelpIntent.SHOW_TOPIC,
                HelpPayload(topic_id="core.blueprint.overview"),
            )
            return
        except Exception:
            pass
        # Fallback to local dialog
        dlg = BlueprintHelpDialog(self.theme, self)
        dm = get_for_widget(self)
        if dm:
            dm.show_instance(dlg)
        else:
            exec_as_modal(dlg)

    def _delete_tool(self):
        key = self._current_blueprint_key()
        if not self.bpm or not key or key not in self.bpm.blueprints:
            return
        
        reply = QMessageBox.question(self, 'Delete Tool', f"Are you sure you want to permanently delete the custom tool '{key}'?", QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No, QMessageBox.StandardButton.No)
        if reply == QMessageBox.StandardButton.Yes:
            self.combo_blueprints.blockSignals(True)
            if key in self.bpm.blueprints:
                del self.bpm.blueprints[key]
            
            self.current_blueprint = None 
            self._save_blueprints()
            self._populate_combo_box()
            self.combo_blueprints.blockSignals(False)
            self._load_selected_blueprint()

    def _restore_default(self):
        key = self._current_blueprint_key()
        if not self.bpm or not key or key not in self.bpm.blueprints or not self._registry_definition(key):
            return
        
        reply = QMessageBox.question(self, 'Restore Default', f"Are you sure you want to restore '{key}' to its default factory settings?\n\nThis will permanently overwrite any changes you made.", QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No, QMessageBox.StandardButton.No)
        if reply == QMessageBox.StandardButton.Yes:
            self.combo_blueprints.blockSignals(True)
            if key in self.bpm.blueprints:
                del self.bpm.blueprints[key]
                
            self.current_blueprint = None 
            self._save_blueprints()
            self._populate_combo_box()
            self._set_current_blueprint_key(key)
            self.combo_blueprints.blockSignals(False)
            self._load_selected_blueprint()

    def _send_chat(self):
        user_text = self.input_ast.text().strip()
        if not user_text: return
        self.input_ast.clear()
        self.btn_ast_send.setEnabled(False)
        self.txt_ast_chat.append(f"<br><b>You:</b> {user_text}<br><b>Architect:</b> ")
        
        if self.current_blueprint:
            self.current_blueprint = self.visual_editor.to_blueprint(
                self.input_bp_name.text().strip() or self.current_blueprint.name,
                self.input_bp_desc.text().strip(),
            )
            current_json = json.dumps(dataclasses.asdict(self.current_blueprint), indent=2)
        else:
            current_json = "{}"

        architect_bp = DefaultBlueprints.get_blueprint_architect(self.app_context.prompt_manager)
        state = {"user_text": user_text, "current_json": current_json}
        
        request_id = str(uuid.uuid4())
        self._workflow_requests[request_id] = {"kind": "architect"}
        self.bus.workflow_action_requested.emit(
            WorkflowIntent.RUN_BLUEPRINT,
            WorkflowPayload(
                blueprint=architect_bp,
                initial_state=state,
                job_id=request_id,
                job_name=architect_bp.name,
            ),
        )

    def _on_chat_complete(self, final_state):
        self.btn_ast_send.setEnabled(True)
        full_text = final_state.get("architect_response", "")
        
        pattern = r'`' * 3 + r'(?:json)?\s*(\{.*?\})\s*' + r'`' * 3
        match = re.search(pattern, full_text, re.DOTALL)
        
        if match:
            json_str = match.group(1).strip()
            try:
                json_str = re.sub(r',\s*}', '}', json_str)
                json_str = re.sub(r',\s*\]', ']', json_str)
                data = json.loads(json_str)
                
                if "expected_inputs" in data and isinstance(data["expected_inputs"], list):
                    for inp in data["expected_inputs"]:
                        if "name" in inp and "key" not in inp: inp["key"] = inp.pop("name")
                            
                new_bp = AIActionBlueprint.from_dict(data)
                
                if self.bpm:
                    self.current_blueprint = None 
                    self.bpm.blueprints[new_bp.name] = new_bp
                    self._populate_combo_box() 
                    self.combo_blueprints.setCurrentText(new_bp.name)
                    self._save_blueprints()
                    self.txt_ast_chat.append("<br><br><b style='color:#00cc66;'>✅ Successfully applied and saved the new blueprint!</b>")
            except Exception as e:
                self.txt_ast_chat.append(f"<br><br><b style='color:#ff4444;'>❌ Failed to parse JSON blueprint: {e}</b>")

    def _populate_combo_box(self):
        current_key = self._current_blueprint_key()
        self.combo_blueprints.blockSignals(True)
        self.combo_blueprints.clear()

        seen = set()
        if self.blueprint_registry:
            for definition in self.blueprint_registry.all():
                self.combo_blueprints.addItem(definition.label or definition.id, definition.id)
                seen.add(definition.id)
        if self.bpm:
            for key, blueprint in self.bpm.blueprints.items():
                if key in seen: continue
                self.combo_blueprints.addItem(blueprint.name or key, key)
                seen.add(key)

        self.combo_blueprints.blockSignals(False)
        if current_key and current_key in [self.combo_blueprints.itemData(i) for i in range(self.combo_blueprints.count())]:
            self._set_current_blueprint_key(current_key)
        else:
            self._load_selected_blueprint()

    def _create_new_tool(self):
        name, ok = QInputDialog.getText(self, "New Tool", "Enter a name for your custom tool:")
        if ok and name and name not in self.bpm.blueprints:
            new_bp = DefaultBlueprints.get_blank_custom_tool(name)
            self.bpm.blueprints[name] = new_bp
            self._populate_combo_box()
            self._set_current_blueprint_key(name)

    def _load_selected_blueprint(self):
        if not self.bpm:
            return
        key = self._current_blueprint_key()
        if not key:
            return

        is_custom_override = key in self.bpm.blueprints
        is_registered_default = self._registry_definition(key) is not None
        self.btn_restore.setVisible(is_registered_default)
        self.btn_restore.setEnabled(is_custom_override)
        self.btn_delete.setVisible(is_custom_override and not is_registered_default)

        if self.current_blueprint:
            self.current_blueprint = self.visual_editor.to_blueprint(
                self.input_bp_name.text().strip() or self.current_blueprint.name,
                self.input_bp_desc.text().strip(),
                mount_points=self._parse_csv(self.input_bp_mounts.text()),
                active_contexts=self._parse_csv(self.input_bp_contexts.text()),
            )
            self.bpm.blueprints[self.current_blueprint.name] = self.current_blueprint

        self.current_blueprint = self.bpm.get_blueprint(key, lambda: self._create_registered_blueprint(key))
        if not self.current_blueprint:
            self.current_blueprint = AIActionBlueprint(name=key, description="")

        self.current_blueprint.name = key
        self.input_bp_name.setText(self.current_blueprint.name)
        self.input_bp_desc.setText(self.current_blueprint.description)
        self.input_bp_mounts.setText(", ".join(self.current_blueprint.mount_points or []))
        self.input_bp_contexts.setText(", ".join(self.current_blueprint.active_contexts or []))
        self.visual_editor.load_blueprint(self.current_blueprint)

    def _current_blueprint_key(self):
        return self.combo_blueprints.currentData() or self.combo_blueprints.currentText()

    def _set_current_blueprint_key(self, key):
        for index in range(self.combo_blueprints.count()):
            if self.combo_blueprints.itemData(index) == key or self.combo_blueprints.itemText(index) == key:
                self.combo_blueprints.setCurrentIndex(index)
                return

    def _registry_definition(self, key):
        return self.blueprint_registry.get(key) if self.blueprint_registry else None

    def _create_registered_blueprint(self, key):
        if not self.blueprint_registry: return None
        return self.blueprint_registry.create(key, pm=self.app_context.prompt_manager)

    def _save_blueprints(self):
        if not self.bpm:
            return
        if self.current_blueprint:
            self.current_blueprint = self.visual_editor.to_blueprint(
                self.input_bp_name.text().strip() or self.current_blueprint.name,
                self.input_bp_desc.text().strip(),
                mount_points=self._parse_csv(self.input_bp_mounts.text()),
                active_contexts=self._parse_csv(self.input_bp_contexts.text()),
            )
            self.bpm.blueprints[self.current_blueprint.name] = self.current_blueprint

        out_data = {k: dataclasses.asdict(v) for k, v in self.bpm.blueprints.items()}
        with open(self.bpm.blueprint_file, 'w', encoding='utf-8') as f:
            json.dump(out_data, f, indent=4)
        if hasattr(self.bpm, "_register_custom_blueprints"):
            self.bpm._register_custom_blueprints()
            
        # Walk up to find UnifiedResearchDock which holds tab_custom
        p = self.parent()
        while p and not hasattr(p, 'tab_custom'):
            p = p.parent()
        if p and hasattr(p, 'tab_custom') and hasattr(p.tab_custom, 'refresh_tools'):
            p.tab_custom.refresh_tools()

    def _run_debugger(self):
        if not self.current_blueprint: return
        self.txt_debugger.clear()
        self.txt_debugger.append("<i>Initializing Test Run...</i>\n")
        
        self.current_blueprint = self.visual_editor.to_blueprint(
            self.input_bp_name.text().strip() or self.current_blueprint.name,
            self.input_bp_desc.text().strip(),
        )
            
        mock_state = {"user_input": "Test Input Data", "doc_path": "sample.pdf"}
        
        request_id = str(uuid.uuid4())
        self._workflow_requests[request_id] = {"kind": "debug"}
        self.bus.workflow_action_requested.emit(
            WorkflowIntent.RUN_BLUEPRINT,
            WorkflowPayload(
                blueprint=self.current_blueprint,
                initial_state=mock_state,
                job_id=request_id,
                job_name=self.current_blueprint.name,
            ),
        )

    def _handle_workflow_state(self, event, payload):
        request_id = payload.get("job_id") if hasattr(payload, "get") else None
        meta = self._workflow_requests.get(request_id)
        if not meta: return

        kind = meta.get("kind")
        if kind == "architect":
            if event == WorkflowEvent.PROGRESS:
                self.txt_ast_chat.insertPlainText((payload.get("data") or {}).get("chunk", ""))
            elif event == WorkflowEvent.COMPLETED:
                self._workflow_requests.pop(request_id, None)
                self._on_chat_complete(payload.get("initial_state") or {})
            elif event == WorkflowEvent.FAILED:
                self._workflow_requests.pop(request_id, None)
                self.btn_ast_send.setEnabled(True)
                self.txt_ast_chat.append(f"<br><b style='color:red;'>Error: {payload.get('errors')}</b>")
            return

        if kind == "debug":
            if event == WorkflowEvent.STATE_SNAPSHOT:
                data = payload.get("data") or {}
                self._on_debug_snapshot(data.get("step_id", ""), data.get("state_json", ""))
            elif event == WorkflowEvent.FAILED:
                self._workflow_requests.pop(request_id, None)
                self.txt_debugger.append(f"<span style='color:red;'>ERROR: {payload.get('errors')}</span>")
            elif event == WorkflowEvent.COMPLETED:
                self._workflow_requests.pop(request_id, None)
                self.txt_debugger.append("\n<b>[PIPELINE COMPLETE]</b>")

    def _on_debug_snapshot(self, step_id, state_json):
        self.txt_debugger.append(f"\n<b>--- STATE AFTER: {step_id} ---</b>")
        self.txt_debugger.append(state_json)

    def _parse_csv(self, text: str) -> list:
        return [p.strip() for p in (text or "").split(",") if p.strip()]

    def update_theme(self, theme):
        self.theme = theme
        self.setStyleSheet(f"background-color: {theme.get('bg_main', '#1e1e1e')}; color: {theme.get('text_main', '#fff')};")
        style = f"background-color: {theme.get('bg_input', '#2b2b2b')}; border: 1px solid {theme.get('border', '#444')}; border-radius: 4px; padding: 4px;"
        self.combo_blueprints.setStyleSheet(style)
        self.input_bp_name.setStyleSheet(style)
        self.input_bp_desc.setStyleSheet(style)
        self.input_bp_mounts.setStyleSheet(style)
        self.input_bp_contexts.setStyleSheet(style)
        btn_action_style = (f"background-color: {theme.get('bg_panel', '#333')}; font-weight: bold; color: white; "
                            f"border: 1px solid {theme.get('border', '#444')}; border-radius: 4px; padding: 4px 8px;")
        self.btn_restore.setStyleSheet(btn_action_style)
        self.btn_delete.setStyleSheet(btn_action_style)
        self.btn_save.setStyleSheet(f"background-color: {theme.get('accent', '#b366ff')}; font-weight: bold; color: white; border: none; border-radius: 4px; padding: 6px;")
        if hasattr(self, "visual_editor"):
            self.visual_editor.update_theme(theme)
        self.input_ast.setStyleSheet(style)
        self.btn_ast_send.setStyleSheet(f"background-color: {theme.get('accent', '#b366ff')}; color: white; padding: 4px;")