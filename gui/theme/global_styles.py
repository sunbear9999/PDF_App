# gui/theme/global_styles.py

def get_global_stylesheet(theme: dict) -> str:
    """Returns the master stylesheet for the application based on the active theme dict."""
    return f"""
        QMainWindow {{
            background-color: {theme.get('bg_main', '#1e1e1e')};
        }}
        QDockWidget {{
            font-weight: bold;
            color: {theme.get('text_main', '#ffffff')};
        }}
        QDockWidget::title {{
            background: {theme.get('bg_panel', '#2b2b2b')};
            padding-left: 10px;
            padding-top: 4px;
        }}
        QScrollBar:vertical {{
            border: none;
            background: {theme.get('bg_main', '#1e1e1e')};
            width: 10px;
            margin: 0px 0px 0px 0px;
        }}
        QScrollBar::handle:vertical {{
            background: {theme.get('border', '#444444')};
            min-height: 20px;
            border-radius: 5px;
        }}
        QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{
            height: 0px;
        }}
        QMenuBar {{
            background-color: {theme.get('bg_panel', '#2b2b2b')};
            color: {theme.get('text_main', '#ffffff')};
        }}
        QMenuBar::item:selected {{
            background-color: {theme.get('accent', '#0078d7')};
        }}
        QMenu {{
            background-color: {theme.get('bg_panel', '#2b2b2b')};
            color: {theme.get('text_main', '#ffffff')};
            border: 1px solid {theme.get('border', '#444444')};
        }}
        QMenu::item:selected {{
            background-color: {theme.get('accent', '#0078d7')};
        }}
        QPushButton {{
            background-color: {theme.get('bg_panel', '#2b2b2b')};
            color: {theme.get('text_main', '#ffffff')};
            border: 1px solid {theme.get('border', '#444444')};
            border-radius: 4px;
            padding: 5px;
        }}
        QPushButton:hover {{
            background-color: {theme.get('border', '#444444')};
        }}
        QLineEdit, QTextEdit, QComboBox {{
            background-color: {theme.get('bg_panel', '#2b2b2b')};
            color: {theme.get('text_main', '#ffffff')};
            border: 1px solid {theme.get('border', '#444444')};
            border-radius: 4px;
            padding: 4px;
        }}
    """