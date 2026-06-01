from PySide6.QtGui import QColor


def style_dialog_with_theme(dialog, theme, extra_styles: str = ""):
    stylesheet = f"""
        QDialog {{ background-color: {theme['bg_main']}; color: {theme['text_main']}; }}
        QLabel {{ color: {theme['text_main']}; font-weight: bold; }}
        QListWidget, QTabWidget::pane {{ background-color: {theme['bg_panel']}; color: {theme['text_main']}; border: 1px solid {theme['border']}; }}
        QListWidget::item:selected, QTabBar::tab:selected {{ background-color: {theme['accent']}; color: #ffffff; }}
        QPushButton {{ background-color: {theme['accent']}; color: #ffffff; border: none; padding: 6px 12px; border-radius: 4px; font-weight: bold; }}
        QMenu {{ background-color: {theme['bg_panel']}; color: {theme['text_main']}; border: 1px solid {theme['border']}; }}
    """
    if extra_styles:
        stylesheet += extra_styles
    dialog.setStyleSheet(stylesheet)
