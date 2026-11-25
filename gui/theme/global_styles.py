# gui/theme/global_styles.py

def get_global_stylesheet(theme: dict) -> str:
    """Return the inherited application theme for core and plugin widgets.

    Standard Qt controls are deliberately styled here instead of in individual
    plugins. A plugin can still override any selector with a local stylesheet
    or implement ``update_theme(theme)`` for bespoke rendering.
    """
    return f"""
        QMainWindow {{
            background-color: {theme.get('bg_main', '#1e1e1e')};
        }}
        QWidget[papyrus_plugin_root="true"] {{
            background-color: {theme.get('bg_panel', '#2b2b2b')};
            color: {theme.get('text_main', '#ffffff')};
        }}
        QLabel, QCheckBox, QRadioButton {{
            color: {theme.get('text_main', '#ffffff')};
        }}
        QLabel[muted="true"] {{
            color: {theme.get('text_muted', '#aaaaaa')};
        }}
        QLabel[state="error"] {{
            color: {theme.get('error', '#ff4444')};
        }}
        QLabel[state="success"] {{
            color: {theme.get('success', '#00cc66')};
        }}
        QLabel[state="warning"] {{
            color: {theme.get('warning', '#ffaa00')};
        }}
        QLabel[state="running"] {{
            color: {theme.get('accent', '#0078d7')};
        }}
        QLabel[state="inferred"] {{
            color: {theme.get('accent', '#0078d7')};
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
        QPushButton, QToolButton {{
            background-color: {theme.get('bg_panel', '#2b2b2b')};
            color: {theme.get('text_main', '#ffffff')};
            border: 1px solid {theme.get('border', '#444444')};
            border-radius: 4px;
            padding: 5px;
        }}
        QPushButton:hover:!disabled, QToolButton:hover:!disabled,
        QPushButton:checked, QToolButton:checked {{
            background-color: {theme.get('accent', '#0078d7')};
            color: #ffffff;
            border-color: {theme.get('accent', '#0078d7')};
        }}
        QPushButton:disabled, QToolButton:disabled {{
            color: {theme.get('text_muted', '#aaaaaa')};
        }}
        QLineEdit, QTextEdit, QTextBrowser, QPlainTextEdit, QComboBox,
        QSpinBox, QDoubleSpinBox, QDateEdit, QDateTimeEdit, QTimeEdit,
        QListView, QListWidget, QTreeView, QTreeWidget, QTableView,
        QTableWidget {{
            background-color: {theme.get('bg_input', '#333333')};
            alternate-background-color: {theme.get('bg_panel', '#2b2b2b')};
            color: {theme.get('text_main', '#ffffff')};
            border: 1px solid {theme.get('border', '#444444')};
            border-radius: 4px;
            padding: 4px;
            selection-background-color: {theme.get('accent', '#0078d7')};
            selection-color: #ffffff;
        }}
        QLineEdit:focus, QTextEdit:focus, QTextBrowser:focus,
        QPlainTextEdit:focus, QComboBox:focus, QSpinBox:focus,
        QDoubleSpinBox:focus, QDateEdit:focus, QDateTimeEdit:focus,
        QTimeEdit:focus, QListView:focus, QListWidget:focus,
        QTreeView:focus, QTreeWidget:focus, QTableView:focus,
        QTableWidget:focus {{
            border-color: {theme.get('accent', '#0078d7')};
        }}
        QComboBox QAbstractItemView {{
            background-color: {theme.get('bg_input', '#333333')};
            color: {theme.get('text_main', '#ffffff')};
            border: 1px solid {theme.get('border', '#444444')};
            selection-background-color: {theme.get('accent', '#0078d7')};
            selection-color: #ffffff;
            outline: none;
        }}
        QHeaderView::section {{
            background-color: {theme.get('bg_panel', '#2b2b2b')};
            color: {theme.get('text_main', '#ffffff')};
            border: 0;
            border-right: 1px solid {theme.get('border', '#444444')};
            border-bottom: 1px solid {theme.get('border', '#444444')};
            padding: 5px;
        }}
        QGroupBox {{
            color: {theme.get('text_main', '#ffffff')};
            border: 1px solid {theme.get('border', '#444444')};
            border-radius: 4px;
            margin-top: 8px;
        }}
        QGroupBox::title {{
            subcontrol-origin: margin;
            left: 8px;
            padding: 0 4px;
        }}
        QTabWidget::pane {{
            border: 1px solid {theme.get('border', '#444444')};
            background-color: {theme.get('bg_main', '#1e1e1e')};
        }}
        QTabBar::tab {{
            background-color: {theme.get('bg_panel', '#2b2b2b')};
            color: {theme.get('text_main', '#ffffff')};
            border: 1px solid {theme.get('border', '#444444')};
            padding: 6px 10px;
        }}
        QTabBar::tab:selected {{
            background-color: {theme.get('bg_input', '#333333')};
            border-bottom: 2px solid {theme.get('accent', '#0078d7')};
        }}
        QScrollArea {{
            border: 0;
            background-color: {theme.get('bg_panel', '#2b2b2b')};
        }}
        QScrollArea > QWidget > QWidget {{
            background-color: {theme.get('bg_panel', '#2b2b2b')};
            color: {theme.get('text_main', '#ffffff')};
        }}
        QSplitter::handle {{
            background-color: {theme.get('border', '#444444')};
        }}
        QToolTip {{
            background-color: {theme.get('bg_panel', '#2b2b2b')};
            color: {theme.get('text_main', '#ffffff')};
            border: 1px solid {theme.get('border', '#444444')};
        }}
    """
