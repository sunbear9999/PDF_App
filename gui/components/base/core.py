from PySide6.QtWidgets import QWidget
from PySide6.QtCore import Qt

# Switched to standard Solarized Dark palette
SOLARIZED_THEME = {
    "bg_main": "#002b36",      # Base03 (Main App)
    "bg_panel": "#073642",     # Base02 (Cards, Docks, Toolbars)
    "bg_input": "#00212b",     # Slightly darker for depth in inputs
    "border": "#586e75",       # Base01
    "accent": "#268bd2",       # Blue
    "success": "#859900",      # Green
    "warning": "#b58900",      # Yellow
    "error": "#dc322f",        # Red
    "text_main": "#93a1a1",    # Base1
    "text_muted": "#586e75",   # Base01
}

class ThemedMixin:
    """A mixin that provides unified, modern styling utilities."""
    
    def apply_base_theme(self, theme: dict = None):
        self.theme = {**SOLARIZED_THEME, **(theme or {})}
        self.update_theme(self.theme)

    def update_theme(self, theme: dict):
        self.theme = theme
        # Child classes will override this
        
    def get_input_style(self) -> str:
        # Softer radius, deeper background, better padding
        return (
            f"background-color: {self.theme['bg_input']}; "
            f"color: {self.theme['text_main']}; "
            f"border: 1px solid {self.theme['border']}; "
            f"border-radius: 6px; padding: 8px 10px; "
            f"font-size: 13px;"
        )

    def get_button_style(self, is_primary=False) -> str:
        # Modern, pill-like buttons with bold text
        bg = self.theme['accent'] if is_primary else self.theme['bg_panel']
        text = "#fdf6e3" if is_primary else self.theme['text_main']
        border = "none" if is_primary else f"1px solid {self.theme['border']}"
        return (
            f"background-color: {bg}; color: {text}; "
            f"border: {border}; border-radius: 6px; "
            f"padding: 8px 16px; font-weight: 600; "
            f"font-size: 13px;"
        )