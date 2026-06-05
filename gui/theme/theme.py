import json
from PySide6.QtCore import QObject, Signal, QSettings, Qt
from PySide6.QtWidgets import (QApplication, QDialog, QVBoxLayout, QHBoxLayout, 
                             QLabel, QPushButton, QColorDialog, QScrollArea, 
                             QWidget, QFormLayout)
from PySide6.QtGui import QColor

class CustomThemeDialog(QDialog):
    def __init__(self, current_custom_theme, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Custom Theme Editor")
        self.setMinimumSize(450, 650)
        
        # Working copy of colors
        self.colors = dict(current_custom_theme)
        self.layout = QVBoxLayout(self)
        
        # Scroll area for the color pickers
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll_widget = QWidget()
        self.form = QFormLayout(scroll_widget)
        
        self.buttons = {}
        
        # Friendly names for the theme keys
        labels = {
            "bg_main": "Main Background",
            "bg_panel": "Panel Background",
            "bg_input": "Input Field Background",
            "text_main": "Main Text",
            "text_muted": "Muted Text",
            "border": "Borders",
            "accent": "Accent Color",
            "accent_hover": "Accent Hover",
            "canvas": "Workspace Canvas",
            "success": "Success Color",
            "warning": "Warning Color",
            "error": "Error Color",
            "ai_bubble": "AI Note Background",
            "ai_bubble_border": "AI Note Border",
            "ai_bubble_hover": "AI Note Hover",
            "user_bubble": "User Note Background",
            "user_bubble_border": "User Note Border",
            "user_bubble_hover": "User Note Hover"
        }
        
        for key, name in labels.items():
            color = self.colors.get(key, "#000000")
            btn = QPushButton(color.upper())
            btn.setFixedSize(100, 30)
            
            # Calculate contrasting text color for readability
            text_color = self._get_contrasting_text(color)
            
            btn.setStyleSheet(f"background-color: {color}; color: {text_color}; border: 1px solid #aaaaaa; font-weight: bold;")
            btn.clicked.connect(lambda checked, k=key, b=btn: self.pick_color(k, b))
            self.buttons[key] = btn
            self.form.addRow(name, btn)
            
        scroll.setWidget(scroll_widget)
        self.layout.addWidget(scroll)
        
        # Save / Cancel Buttons
        btn_layout = QHBoxLayout()
        
        reset_btn = QPushButton("Reset to Defaults")
        reset_btn.clicked.connect(self.reset_to_defaults)
        
        save_btn = QPushButton("Save Theme")
        save_btn.clicked.connect(self.accept)
        
        cancel_btn = QPushButton("Cancel")
        cancel_btn.clicked.connect(self.reject)
        
        btn_layout.addWidget(reset_btn)
        btn_layout.addStretch()
        btn_layout.addWidget(cancel_btn)
        btn_layout.addWidget(save_btn)
        self.layout.addLayout(btn_layout)
        
        # Style the dialog itself using current theme
        theme = ThemeManager().get_theme()
        self.setStyleSheet(f"background-color: {theme['bg_main']}; color: {theme['text_main']}; font-weight: bold;")
        scroll_widget.setStyleSheet(f"background-color: {theme['bg_panel']};")
        save_btn.setStyleSheet(f"background-color: {theme['accent']}; color: #ffffff; padding: 6px 15px; border-radius: 4px; border: none;")
        cancel_btn.setStyleSheet(f"background-color: {theme['bg_input']}; color: {theme['text_main']}; padding: 6px 15px; border-radius: 4px; border: 1px solid {theme['border']};")
        reset_btn.setStyleSheet(f"background-color: {theme['warning']}; color: #000000; padding: 6px 15px; border-radius: 4px; border: none;")

    def _get_contrasting_text(self, hex_color):
        c = QColor(hex_color)
        brightness = (c.red() * 299 + c.green() * 587 + c.blue() * 114) / 1000
        return "#000000" if brightness > 128 else "#ffffff"

    def pick_color(self, key, btn):
        initial_color = QColor(self.colors[key])
        color = QColorDialog.getColor(initial_color, self, f"Select Color for {key}")
        if color.isValid():
            hex_color = color.name()
            self.colors[key] = hex_color
            btn.setText(hex_color.upper())
            text_color = self._get_contrasting_text(hex_color)
            btn.setStyleSheet(f"background-color: {hex_color}; color: {text_color}; border: 1px solid #aaaaaa; font-weight: bold;")

    def reset_to_defaults(self):
        # Fallback to the dark theme baseline
        default_colors = ThemeManager().themes["Dark (Default)"]
        self.colors = dict(default_colors)
        for key, btn in self.buttons.items():
            hex_color = self.colors[key]
            btn.setText(hex_color.upper())
            text_color = self._get_contrasting_text(hex_color)
            btn.setStyleSheet(f"background-color: {hex_color}; color: {text_color}; border: 1px solid #aaaaaa; font-weight: bold;")

    def get_colors(self):
        return self.colors


class _ThemeManager(QObject):
    """
    Internal ThemeManager class. 
    Inherits from QObject to allow Signals.
    """
    theme_changed = Signal(dict)

    def __init__(self):
        super().__init__()
        self._init_themes()

    def _init_themes(self):
        self.themes = {
            "Dark (Default)": {
                "bg_main": "#1e1e1e", "bg_panel": "#2b2b2b", "bg_input": "#333333",
                "text_main": "#ffffff", "text_muted": "#aaaaaa", "border": "#555555",
                "accent": "#0078D7", "accent_hover": "#0055ff", "canvas": "#1a1a1a",
                "success": "#00cc66", "warning": "#ffaa00", "error": "#ff4444",
                "ai_bubble": "#2d2238", "ai_bubble_border": "#b57edc", "ai_bubble_hover": "#38274a",
                "user_bubble": "#2b2b2b", "user_bubble_border": "#444444", "user_bubble_hover": "#333333"
            },
            "Light": {
                "bg_main": "#f0f0f0", "bg_panel": "#ffffff", "bg_input": "#e0e0e0",
                "text_main": "#000000", "text_muted": "#555555", "border": "#cccccc",
                "accent": "#005a9e", "accent_hover": "#0078D7", "canvas": "#e8e8e8",
                "success": "#28a745", "warning": "#d97706", "error": "#dc3545",
                "ai_bubble": "#f3e8ff", "ai_bubble_border": "#d8b4fe", "ai_bubble_hover": "#e9d5ff",
                "user_bubble": "#ffffff", "user_bubble_border": "#cccccc", "user_bubble_hover": "#f8f9fa"
            },
            "Ocean": {
                "bg_main": "#0f172a", "bg_panel": "#1e293b", "bg_input": "#334155",
                "text_main": "#f8fafc", "text_muted": "#94a3b8", "border": "#475569",
                "accent": "#3b82f6", "accent_hover": "#2563eb", "canvas": "#020617",
                "success": "#10b981", "warning": "#f59e0b", "error": "#ef4444",
                "ai_bubble": "#2e1065", "ai_bubble_border": "#8b5cf6", "ai_bubble_hover": "#4c1d95",
                "user_bubble": "#1e293b", "user_bubble_border": "#475569", "user_bubble_hover": "#334155"
            },
            "Solarized": {
                "bg_main": "#002b36", "bg_panel": "#073642", "bg_input": "#002b36",
                "text_main": "#839496", "text_muted": "#586e75", "border": "#586e75",
                "accent": "#268bd2", "accent_hover": "#2aa198", "canvas": "#001b21",
                "success": "#859900", "warning": "#b58900", "error": "#dc322f",
                "ai_bubble": "#1a0b2e", "ai_bubble_border": "#6c71c4", "ai_bubble_hover": "#2a1b3e",
                "user_bubble": "#073642", "user_bubble_border": "#586e75", "user_bubble_hover": "#002b36"
            },
            "Bubblegum": {
                "bg_main": "#ffbe6f", "bg_panel": "#99c1f1", "bg_input": "#dc8add",
                "text_main": "#e66100", "text_muted": "#aaaaaa", "border": "#2ec27e",
                "accent": "#0078d7", "accent_hover": "#0055ff", "canvas": "#8ff0a4",
                "success": "#00cc66", "warning": "#ffaa00", "error": "#ff4444",
                "ai_bubble": "#2d2238", "ai_bubble_border": "#b57edc", "ai_bubble_hover": "#38274a",
                "user_bubble": "#ffa348", "user_bubble_border": "#c64600", "user_bubble_hover": "#333333",
                "user_bubble": "#073642", "user_bubble_border": "#586e75", "user_bubble_hover": "#002b36"
            }
        }
        
        # Load user's Custom theme securely from QSettings JSON
        settings = QSettings("PDFMultitool", "Workspace")
        saved_custom_json = settings.value("custom_theme_json", None)
        if saved_custom_json:
            try:
                self.themes["Custom"] = json.loads(saved_custom_json)
            except json.JSONDecodeError:
                self.themes["Custom"] = dict(self.themes["Dark (Default)"])
        else:
            self.themes["Custom"] = dict(self.themes["Dark (Default)"])
            
        self.current_theme_name = "Dark (Default)"
        self.theme = self.themes[self.current_theme_name]

    def set_theme(self, name):
        if name in self.themes:
            self.current_theme_name = name
            self.theme = self.themes[name]
            app = QApplication.instance()
            if app:
                self.apply_global_style(app)
            self.theme_changed.emit(self.theme)

    def get_theme(self):
        return self.theme

    def edit_custom_theme(self, parent_widget=None):
        dialog = CustomThemeDialog(self.themes["Custom"], parent_widget)
        if dialog.exec():
            new_colors = dialog.get_colors()
            self.themes["Custom"] = new_colors
            
            # Save the new theme to QSettings so it survives restarts
            settings = QSettings("PDFMultitool", "Workspace")
            settings.setValue("custom_theme_json", json.dumps(new_colors))
            
            # Auto-switch to Custom theme if they just edited it
            self.set_theme("Custom")

    def apply_global_style(self, app):
        t = self.theme
        style = f"""
            QMainWindow {{ background-color: {t['bg_main']}; color: {t['text_main']}; }}
            QWidget {{ color: {t['text_main']}; font-family: Arial; }}
            QPushButton {{ background-color: {t['bg_input']}; border-radius: 4px; padding: 6px 12px; font-weight: bold; border: 1px solid {t['border']}; color: {t['text_main']}; }}
            QPushButton:hover {{ background-color: {t['bg_panel']}; }}
            QPushButton:checked {{ background-color: {t['accent']}; border: 1px solid {t['accent_hover']}; color: #ffffff; }}
            QComboBox {{ background-color: {t['bg_input']}; border: 1px solid {t['border']}; padding: 4px; border-radius: 4px; color: {t['text_main']}; }}
            QComboBox QAbstractItemView {{ background-color: {t['bg_panel']}; color: {t['text_main']}; selection-background-color: {t['accent']}; border: 1px solid {t['border']}; }}
            QMenu {{ background-color: {t['bg_panel']}; color: {t['text_main']}; border: 1px solid {t['border']}; }}
            QMenu::item:selected {{ background-color: {t['accent']}; color: #ffffff; }}
            QMessageBox {{ background-color: {t['bg_main']}; color: {t['text_main']}; }}
            QMessageBox QLabel {{ color: {t['text_main']}; }}
            QMessageBox QPushButton {{ background-color: {t['bg_panel']}; color: {t['text_main']}; padding: 5px 15px; border-radius: 4px; border: 1px solid {t['border']}; }}
            QMessageBox QPushButton:hover {{ background-color: {t['bg_input']}; }}
            QInputDialog {{ background-color: {t['bg_main']}; color: {t['text_main']}; }}
            QInputDialog QLineEdit {{ background-color: {t['bg_input']}; color: {t['text_main']}; border: 1px solid {t['border']}; padding: 4px; }}
            QTextEdit, QLineEdit {{ background-color: {t['bg_input']}; color: {t['text_main']}; border: 1px solid {t['border']}; }}
            QListWidget {{ background-color: {t['bg_input']}; color: {t['text_main']}; border: 1px solid {t['border']}; }}
            QScrollArea {{ border: none; background-color: transparent; }}
            QStackedWidget {{ background-color: {t['bg_main']}; border-left: 1px solid {t['border']}; }}
            QTabBar::tab {{ background: {t['bg_panel']}; padding: 8px 20px; border: 1px solid {t['border']}; color: {t['text_main']}; }}
            QTabBar::tab:selected {{ background: {t['bg_input']}; font-weight: bold; border-bottom: 2px solid {t['accent']}; }}
            QTabWidget::pane {{ border: 1px solid {t['border']}; background: {t['bg_main']}; }}
        """
        app.setStyleSheet(style)


# -------------------------------------------------------------
# STRICT SINGLETON PATTERN - SAFE FOR PySide6
# -------------------------------------------------------------
# Instead of overriding __new__, which breaks C++ object pointers 
# in PyQt, we use a module-level global and a factory function.
_global_theme_manager = None

def ThemeManager():
    """
    Factory function returning a true Singleton. 
    Any file calling `ThemeManager()` gets the exact same safe instance.
    """
    global _global_theme_manager
    if _global_theme_manager is None:
        _global_theme_manager = _ThemeManager()
    return _global_theme_manager