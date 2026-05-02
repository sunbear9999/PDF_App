# gui/docks/essay_dock.py
import os
import sys
import uuid
import json
import fitz
import tempfile
from PySide6.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QLineEdit, 
                             QPushButton, QComboBox, QFileDialog, QLabel, QStackedWidget)
from PySide6.QtWebEngineWidgets import QWebEngineView
from PySide6.QtWebEngineCore import QWebEngineSettings, QWebEngineProfile
from PySide6.QtCore import QUrl, Qt, QMarginsF
from PySide6.QtGui import QPageLayout, QPageSize

class EssayTab(QWidget):
    def __init__(self, project_manager, main_window):
        super().__init__()
        self.project_manager = project_manager
        self.main_window = main_window
        self.current_essay_id = str(uuid.uuid4())
        
        self.layout = QVBoxLayout(self)
        self.layout.setContentsMargins(0, 0, 0, 0)
        self.layout.setSpacing(0)

        # --- 1. COMPACT TOP BAR ---
        self.top_bar = QWidget()
        self.top_bar.setMaximumHeight(45)
        self.top_layout = QHBoxLayout(self.top_bar)
        self.top_layout.setContentsMargins(8, 6, 8, 6)
        self.top_layout.setSpacing(8)
        
        self.essay_selector = QComboBox()
        self.essay_selector.setMinimumWidth(150)
        self.essay_selector.currentIndexChanged.connect(self._on_selector_changed)
        self.top_layout.addWidget(self.essay_selector)
        
        btn_new = QPushButton("➕ New")
        btn_new.clicked.connect(self._create_new_essay)
        self.top_layout.addWidget(btn_new)
        
        self.title_input = QLineEdit()
        self.title_input.setPlaceholderText("Essay Title...")
        self.title_input.setStyleSheet("font-size: 14px; font-weight: bold; padding: 4px; border: none; border-bottom: 1px solid #555;")
        self.top_layout.addWidget(self.title_input, stretch=1)

        # --- ZOOM CONTROLS ---
        self.zoom_selector = QComboBox()
        self.zoom_selector.setFixedWidth(70)
        self.zoom_selector.addItems(["50%", "75%", "100%", "125%", "150%", "200%"])
        self.zoom_selector.setCurrentText("100%")
        self.zoom_selector.currentTextChanged.connect(self._on_zoom_changed)
        self.top_layout.addWidget(self.zoom_selector)
        
        btn_export = QPushButton("📄 Export PDF")
        btn_export.clicked.connect(self.export_to_pdf)
        self.top_layout.addWidget(btn_export)
        
        self.layout.addWidget(self.top_bar)

        # --- 2. ENGINE SETUP & SPELLCHECK ---
        self.stack = QStackedWidget()
        self.loading_label = QLabel("Initializing Editor...")
        self.loading_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.stack.addWidget(self.loading_label)

        profile = QWebEngineProfile.defaultProfile()
        profile.setSpellCheckEnabled(True)
        profile.setSpellCheckLanguages(["en-US"])

        self.web_view = QWebEngineView()
        self.web_view.settings().setAttribute(QWebEngineSettings.WebAttribute.LocalContentCanAccessRemoteUrls, True)
        self.web_view.settings().setAttribute(QWebEngineSettings.WebAttribute.LocalContentCanAccessFileUrls, True)
        self.stack.addWidget(self.web_view)
        
        self.layout.addWidget(self.stack)

        self.web_view.loadFinished.connect(self._on_load_finished)

        self._init_editor()

    def _on_zoom_changed(self, text):
        if hasattr(self, 'web_view'):
            factor = float(text.replace("%", "")) / 100.0
            self.web_view.setZoomFactor(factor)

    def _on_load_finished(self, ok):
        if ok:
            self.stack.setCurrentWidget(self.web_view)
            
            if hasattr(self, 'current_theme') and self.current_theme:
                self.update_theme(self.current_theme)
                
            if not hasattr(self, '_initial_load_done'):
                self._initial_load_done = True
                self.refresh_essay_list(auto_load=True)

    def refresh_essay_list(self, auto_load=False):
        self.essay_selector.blockSignals(True)
        self.essay_selector.clear()
        
        essays = self.project_manager.get_all_essays()
        for essay in essays:
            self.essay_selector.addItem(essay["title"] or "Untitled Essay", essay["id"])
            
        idx = self.essay_selector.findData(self.current_essay_id)
        if idx >= 0:
            self.essay_selector.setCurrentIndex(idx)
        else:
            self.essay_selector.setCurrentIndex(-1)
            
        self.essay_selector.blockSignals(False)
        
        if auto_load and essays and self.current_essay_id not in [e["id"] for e in essays]:
            self.load_essay(essays[0]["id"])
            
    def _on_selector_changed(self, index):
        essay_id = self.essay_selector.currentData()
        if essay_id and essay_id != self.current_essay_id:
            def load_next():
                self.load_essay(essay_id)
            self.save_essay_state(callback_after=load_next)

    def _create_new_essay(self):
        def spawn_new():
            self.current_essay_id = str(uuid.uuid4())
            self.title_input.clear()
            if hasattr(self, 'web_view'):
                self.web_view.page().runJavaScript("if(window.quill) { window.quill.setContents([]); }")
            self.refresh_essay_list(auto_load=False)
        self.save_essay_state(callback_after=spawn_new)

    def _init_editor(self):
        if getattr(sys, 'frozen', False):
            root_dir = sys._MEIPASS
        else:
            root_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..'))
        
        js_path = os.path.join(root_dir, "assets", "quill", "quill.js")
        css_path = os.path.join(root_dir, "assets", "quill", "quill.snow.css")
        dummy_base_url = QUrl.fromLocalFile(os.path.join(root_dir, "dummy_origin.html"))

        html_template = f"""
        <!DOCTYPE html>
        <html>
        <head>
            <meta charset="utf-8">
            <link href="{QUrl.fromLocalFile(css_path).url()}" rel="stylesheet">
            <script src="{QUrl.fromLocalFile(js_path).url()}"></script>
            <style>
                body, html {{ 
                    margin: 0; padding: 0; height: 100vh; overflow: hidden; 
                    display: flex; flex-direction: column; background: #1e1e1e; 
                }}
                
                .ql-toolbar.ql-snow {{ 
                    flex-shrink: 0; 
                    background: #2d2d2d; border: none !important; border-bottom: 1px solid #111 !important; 
                    padding: 8px !important; display: flex; justify-content: center; z-index: 10; 
                    box-shadow: 0px 2px 10px rgba(0,0,0,0.3);
                }}
                
                .ql-snow .ql-picker.ql-font {{ width: 165px !important; text-align: left; }}
                .ql-snow .ql-picker.ql-font .ql-picker-label {{ padding-right: 20px !important; }}
                .ql-snow .ql-picker.ql-size {{ width: 70px !important; text-align: left; }}
                
                #scroller {{ 
                    flex-grow: 1; 
                    overflow-y: auto; 
                    background: transparent; 
                    display: flex; justify-content: center; align-items: flex-start;
                    padding: 40px 0; 
                }}
                
                /* THE FIX: We move the background and shadow to our own wrapper 
                   so it perfectly encapsulates all text, no matter how long it gets.
                */
                #editor-container {{ 
                    border: none !important; 
                    width: 8.5in;
                    min-height: 11in;
                    height: auto !important; /* Force it to grow with content */
                    background-color: #252525;
                    box-shadow: 0px 5px 15px rgba(0,0,0,0.6);
                }}
                
                /* Override Quill's strict height limits */
                .ql-container.ql-snow {{ 
                    border: none !important; 
                    height: auto !important; 
                    background: transparent !important; 
                }}
                
                .ql-editor {{ 
                    height: auto !important;
                    min-height: 11in; 
                    padding: 1in !important; 
                    background: transparent !important; /* Let the container's background show through */
                    color: #e0e0e0; 
                    font-family: 'Times New Roman', serif; 
                    font-size: 16px; 
                    line-height: 1.8; 
                    overflow-y: visible !important;
                }}
                
                .ql-snow .ql-stroke {{ stroke: #e0e0e0; }}
                .ql-snow .ql-fill, .ql-snow .ql-stroke.ql-fill {{ fill: #e0e0e0; }}
                .ql-snow .ql-picker {{ color: #e0e0e0; }}
                .ql-snow .ql-picker-options {{ background-color: #2d2d2d; border-color: #444; }}
                
                .ql-editor.ql-blank::before {{ color: #888; font-style: italic; left: 1in; }}
                .ql-snow .ql-picker.ql-size .ql-picker-label::before, .ql-snow .ql-picker.ql-size .ql-picker-item::before {{ content: attr(data-value); }}
                .ql-snow .ql-picker.ql-size .ql-picker-label:not([data-value])::before {{ content: '16px'; }}
                .ql-snow .ql-picker.ql-font .ql-picker-label::before, .ql-snow .ql-picker.ql-font .ql-picker-item::before {{ content: attr(data-value); text-transform: capitalize; }}
                .ql-snow .ql-picker.ql-font .ql-picker-label:not([data-value])::before {{ content: 'Times New Roman'; }}
                
                @media print {{
                    body, html, #scroller {{ background: white !important; padding: 0 !important; margin: 0 !important; overflow: visible !important; height: auto !important; display: block; }}
                    #editor-container {{ width: 100% !important; min-height: auto !important; box-shadow: none !important; border: none !important; margin: 0 !important; background: white !important; color: black !important; display: block; filter: none; }}
                    .ql-toolbar {{ display: none !important; }}
                    .ql-editor {{ padding: 0 !important; color: black !important; overflow: visible !important; }}
                }}
            </style>
        </head>
        <body>
            <div id="scroller">
                <div id="editor-container"></div>
            </div>
            <script>
                function applyTheme(bg_main, bg_panel, bg_input, text_main, border, accent) {{
                    document.body.style.backgroundColor = bg_main;
                    
                    // Apply the paper color to the expanding wrapper, not Quill
                    var editorContainer = document.getElementById('editor-container');
                    if (editorContainer) {{
                        editorContainer.style.backgroundColor = bg_input;
                    }}
                    
                    var qlEditor = document.querySelector('.ql-editor');
                    if (qlEditor) {{
                        qlEditor.style.color = text_main;
                    }}
                    
                    var toolbar = document.querySelector('.ql-toolbar');
                    if (toolbar) {{
                        toolbar.style.backgroundColor = bg_panel;
                        toolbar.style.borderBottom = '1px solid ' + border;
                    }}
                    var pickers = document.querySelectorAll('.ql-picker');
                    pickers.forEach(p => p.style.color = text_main);
                }}
                
                window.onload = function() {{
                    if (typeof Quill === 'undefined') return;
                    const Size = Quill.import('attributors/style/size');
                    Size.whitelist = ['10px', '11px', '12px', '14px', '16px', '18px', '24px', '32px'];
                    Quill.register(Size, true);
                    const Font = Quill.import('attributors/style/font');
                    Font.whitelist = ['times-new-roman', 'arial', 'georgia', 'courier-new', 'verdana'];
                    Quill.register(Font, true);
                    const toolbarOptions = [
                        [{{ 'font': Font.whitelist }}, {{ 'size': Size.whitelist }}],
                        ['bold', 'italic', 'underline', 'strike'],
                        [{{ 'list': 'ordered'}}, {{ 'list': 'bullet' }}],
                        [{{ 'align': [] }}],
                        ['blockquote', 'code-block', 'link'],
                        ['clean']
                    ];
                    
                    window.quill = new Quill('#editor-container', {{
                        theme: 'snow',
                        placeholder: 'Start drafting your essay...',
                        modules: {{ toolbar: toolbarOptions }}
                    }});
                    
                    var toolbar = document.querySelector('.ql-toolbar');
                    if (toolbar) {{
                        document.body.insertBefore(toolbar, document.getElementById('scroller'));
                    }}
                }};
            </script>
        </body>
        </html>
        """
        self.web_view.setHtml(html_template, baseUrl=dummy_base_url)

    def load_essay(self, essay_id):
        data = self.project_manager.get_essay(essay_id)
        if data:
            self.current_essay_id = data["id"]
            self.title_input.setText(data["title"])
            self.refresh_essay_list(auto_load=False)
            safe_content = json.dumps(data.get("content", ""))
            js_code = f"""
            (function checkAndPaste() {{
                if (window.quill) {{
                    window.quill.root.innerHTML = {safe_content};
                }} else {{
                    setTimeout(checkAndPaste, 100);
                }}
            }})();
            """
            if hasattr(self, 'web_view'):
                self.web_view.page().runJavaScript(js_code)

    def save_essay_state(self, callback_after=None):
        id_to_save = self.current_essay_id
        title_to_save = self.title_input.text().strip() or "Untitled Essay"

        def on_js_result(content):
            if content and content != "<p><br></p>": 
                self.project_manager.upsert_essay(id_to_save, title_to_save, content)
            if callback_after:
                callback_after()
            
        if hasattr(self, 'web_view') and self.web_view.page():
            self.web_view.page().runJavaScript("window.quill ? window.quill.root.innerHTML : ''", on_js_result)
        else:
            if callback_after: callback_after()

    def export_to_pdf(self):
        self.save_essay_state()
        default_name = f"{self.title_input.text() or 'Essay'}.pdf"
        path, _ = QFileDialog.getSaveFileName(self, "Export Essay to PDF", default_name, "PDF Files (*.pdf)")
        
        if path:
            layout = QPageLayout(
                QPageSize(QPageSize.PageSizeId.Letter),
                QPageLayout.Orientation.Portrait,
                QMarginsF(1.0, 1.0, 1.0, 1.0),
                QPageLayout.Unit.Inch
            )
            self.web_view.page().printToPdf(path, layout)

    def update_theme(self, theme):
        self.current_theme = theme
        
        self.top_bar.setStyleSheet(f"background: {theme['bg_panel']}; border-bottom: 1px solid {theme['border']};")
        self.title_input.setStyleSheet(f"font-size: 14px; font-weight: bold; padding: 4px; background: {theme['bg_panel']}; color: {theme['text_main']}; border: none;")
        
        combo_style = f"background: {theme['bg_input']}; color: {theme['text_main']}; border: 1px solid {theme['border']}; padding: 2px; border-radius: 4px;"
        self.zoom_selector.setStyleSheet(combo_style)
        self.essay_selector.setStyleSheet(combo_style)
        
        btn_style = f"background: {theme['bg_input']}; color: {theme['text_main']}; border: 1px solid {theme['border']}; padding: 4px 8px; border-radius: 4px;"
        for btn in self.top_bar.findChildren(QPushButton):
            btn.setStyleSheet(btn_style)
            
        if hasattr(self, 'loading_label'):
            self.loading_label.setStyleSheet(f"font-size: 16px; font-weight: bold; background: {theme['bg_main']}; color: {theme['text_main']};")
            
        if hasattr(self, 'web_view') and self.web_view.page():
            js = f"if(typeof applyTheme === 'function') applyTheme('{theme['bg_main']}', '{theme['bg_panel']}', '{theme['bg_input']}', '{theme['text_main']}', '{theme['border']}', '{theme['accent']}');"
            self.web_view.page().runJavaScript(js)