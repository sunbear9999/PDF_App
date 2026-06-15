from PySide6.QtGui import QColor


def style_dialog_with_theme(dialog, theme, extra_styles: str = ""):
    stylesheet = f"""
        QDialog {{ background-color: {theme['bg_main']}; color: {theme['text_main']}; }}
        QLabel {{ color: {theme['text_main']}; font-weight: bold; }}
        QGroupBox {{ color: {theme['text_main']}; border: 1px solid {theme['border']}; border-radius: 6px; margin-top: 8px; padding: 8px; }}
        QListWidget, QTabWidget::pane, QTextEdit, QPlainTextEdit, QLineEdit, QComboBox, QSpinBox, QDoubleSpinBox {{
            background-color: {theme['bg_panel']};
            color: {theme['text_main']};
            border: 1px solid {theme['border']};
            border-radius: 4px;
            padding: 5px 7px;
            selection-background-color: {theme['accent']};
            selection-color: #ffffff;
        }}
        QComboBox::drop-down {{ border-left: 1px solid {theme['border']}; width: 22px; }}
        QComboBox QAbstractItemView {{
            background-color: {theme['bg_panel']};
            color: {theme['text_main']};
            border: 1px solid {theme['border']};
            selection-background-color: {theme['accent']};
            selection-color: #ffffff;
        }}
        QListWidget::item:selected, QTabBar::tab:selected {{ background-color: {theme['accent']}; color: #ffffff; }}
        QPushButton {{ background-color: {theme['accent']}; color: #ffffff; border: none; padding: 6px 12px; border-radius: 4px; font-weight: bold; }}
        QPushButton:hover {{ background-color: {theme['accent_hover']}; }}
        QPushButton:disabled {{ background-color: {theme['bg_input']}; color: {theme['text_muted']}; border: 1px solid {theme['border']}; }}
        QMenu {{ background-color: {theme['bg_panel']}; color: {theme['text_main']}; border: 1px solid {theme['border']}; }}
    """
    if extra_styles:
        stylesheet += extra_styles
    dialog.setStyleSheet(stylesheet)
