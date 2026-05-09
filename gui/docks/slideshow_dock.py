# gui/docks/slideshow_dock.py
import os
import sys
from PySide6.QtWidgets import QWidget, QVBoxLayout, QLabel, QStackedWidget
from PySide6.QtWebEngineWidgets import QWebEngineView
from PySide6.QtWebEngineCore import QWebEngineSettings, QWebEngineProfile
from PySide6.QtCore import QUrl, Qt

class SlideshowTab(QWidget):
    def __init__(self, project_manager, main_window):
        super().__init__()
        self.project_manager = project_manager
        self.main_window = main_window
        
        self.layout = QVBoxLayout(self)
        self.layout.setContentsMargins(0, 0, 0, 0)
        self.layout.setSpacing(0)

        self.stack = QStackedWidget()
        self.loading_label = QLabel("Loading OpenDeck Studio...")
        self.loading_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.stack.addWidget(self.loading_label)

        # Profile setup - Critical for OpenDeck's LocalStorage saves
        profile = QWebEngineProfile.defaultProfile()
        
        self.web_view = QWebEngineView(profile)
        settings = self.web_view.settings()
        settings.setAttribute(QWebEngineSettings.WebAttribute.LocalContentCanAccessRemoteUrls, True)
        settings.setAttribute(QWebEngineSettings.WebAttribute.LocalContentCanAccessFileUrls, True)
        settings.setAttribute(QWebEngineSettings.WebAttribute.LocalStorageEnabled, True)
        
        self.stack.addWidget(self.web_view)
        self.layout.addWidget(self.stack)

        self.web_view.loadFinished.connect(self._on_load_finished)
        self._load_opendeck()

    def _load_opendeck(self):
        # Resolve path to opendeck index.html based on execution environment
        if getattr(sys, 'frozen', False):
            root_dir = sys._MEIPASS
        else:
            root_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..'))
            
        index_path = os.path.join(root_dir, "assets", "opendeck", "index.html")
        
        if os.path.exists(index_path):
            self.web_view.load(QUrl.fromLocalFile(index_path))
        else:
            self.loading_label.setText(f"Error: Could not find OpenDeck at:\n{index_path}\nMake sure the folder is placed in assets/opendeck/")

    def _on_load_finished(self, ok):
        if ok:
            # 1. Inject JS to permanently skip the landing page
            bypass_js = """
                // Tell OpenDeck's local storage to always remember we want the dashboard
                localStorage.setItem('openDeckAppState', 'dashboard');
                
                // If the landing view happens to be visible (first boot), hide it and force-start
                var landing = document.getElementById('landingView');
                if (landing && landing.style.display !== 'none') {
                    if (typeof startAppFromLanding === 'function') {
                        startAppFromLanding();
                    }
                }
            """
            self.web_view.page().runJavaScript(bypass_js)
            self.stack.setCurrentWidget(self.web_view)
            if hasattr(self, 'current_theme') and self.current_theme:
                self.update_theme(self.current_theme)

    def update_theme(self, theme):
        self.current_theme = theme
        
        if hasattr(self, 'loading_label'):
            self.loading_label.setStyleSheet(f"font-size: 16px; font-weight: bold; background: {theme['bg_main']}; color: {theme['text_main']};")
            
        if hasattr(self, 'web_view') and self.web_view.page():
            # Inject CSS to force OpenDeck's UI to respect the Papyrus theme
            # We explicitly target UI panels while avoiding styling the actual slides (.od-deck-shell)
            css_injection = f"""
            var style = document.getElementById('papyrus-theme-override');
            if (!style) {{
                style = document.createElement('style');
                style.id = 'papyrus-theme-override';
                document.head.appendChild(style);
            }}
            style.innerHTML = `
                /* Core Backgrounds */
                body, #landingView, #dashboardView, .sidebar-left, .sidebar-right, .preview-pane {{
                    background-color: {theme['bg_main']} !important;
                    background-image: none !important;
                }}
                /* Headers & Panels */
                .header, .inspector-pane__header, .viewport-guard__panel, .modal-content {{
                    background-color: {theme['bg_panel']} !important;
                    border-bottom-color: {theme['border']} !important;
                    border-color: {theme['border']} !important;
                }}
                /* Inputs & Cards */
                .project-card, .template-card, .inspector-card, input.prop-input, 
                select.prop-input, textarea.prop-input, .speaker-notes-input, .bg-slate-900 {{
                    background-color: {theme['bg_input']} !important;
                    border-color: {theme['border']} !important;
                    color: {theme['text_main']} !important;
                }}
                /* UI Text - Exclude slides */
                h1:not(.od-deck-shell h1), h2:not(.od-deck-shell h2), 
                p:not(.od-deck-shell p), span:not(.od-deck-shell span), .prop-label {{
                    color: {theme['text_main']} !important;
                }}
                /* Subtext */
                .text-slate-400, .text-slate-500, .prop-header {{
                    color: {theme.get('text_dim', '#888')} !important;
                }}
            `;
            // Attempt to update OpenDeck's internal accent variable if it supports it
            document.documentElement.style.setProperty('--accent-color', '{theme.get('accent', '#3B82F6')}');
            """
            self.web_view.page().runJavaScript(css_injection)