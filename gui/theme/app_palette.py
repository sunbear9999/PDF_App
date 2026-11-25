"""Application palette and base widget rules derived from the active theme.

The global stylesheet only colours some widget classes. Everything else (dialogs,
tree and table views, headers, group boxes, tool buttons, tooltips, custom-painted
widgets that read ``palette()``) fell back to Qt's light default palette, so dark
themes showed white panels with white text. Setting the palette fixes every widget
at once, including plugin widgets; the extra rules cover classes whose native style
ignores parts of the palette.
"""
from PySide6.QtGui import QColor, QPalette

DARK_LUMINANCE = 0.45
LINK_LIGHTEN = 0.35   # small accent-coloured text needs more contrast on dark backgrounds


def _color(theme, key, fallback):
    return QColor(theme.get(key) or fallback)


def is_dark(theme) -> bool:
    return _color(theme, "bg_main", "#1e1e1e").lightnessF() < DARK_LUMINANCE


def link_color(theme) -> str:
    accent = _color(theme, "accent", "#0078D7")
    if not is_dark(theme):
        return accent.name()
    white = QColor("#ffffff")
    mixed = QColor(*(round(a + (w - a) * LINK_LIGHTEN) for a, w in
                     ((accent.red(), white.red()), (accent.green(), white.green()), (accent.blue(), white.blue()))))
    return mixed.name()


def build_palette(theme) -> QPalette:
    main, panel, field = (_color(theme, "bg_main", "#1e1e1e"), _color(theme, "bg_panel", "#2b2b2b"),
                          _color(theme, "bg_input", "#333333"))
    text, muted = _color(theme, "text_main", "#ffffff"), _color(theme, "text_muted", "#aaaaaa")
    accent = _color(theme, "accent", "#0078D7")
    palette = QPalette()
    roles = {
        QPalette.ColorRole.Window: main, QPalette.ColorRole.WindowText: text,
        QPalette.ColorRole.Base: field, QPalette.ColorRole.AlternateBase: panel,
        QPalette.ColorRole.Text: text, QPalette.ColorRole.Button: field, QPalette.ColorRole.ButtonText: text,
        QPalette.ColorRole.ToolTipBase: panel, QPalette.ColorRole.ToolTipText: text,
        QPalette.ColorRole.PlaceholderText: muted, QPalette.ColorRole.Highlight: accent,
        QPalette.ColorRole.HighlightedText: QColor("#ffffff"), QPalette.ColorRole.Link: QColor(link_color(theme)),
        QPalette.ColorRole.LinkVisited: QColor(link_color(theme)), QPalette.ColorRole.BrightText: QColor("#ffffff"),
        QPalette.ColorRole.Light: panel.lighter(130), QPalette.ColorRole.Midlight: panel.lighter(115),
        QPalette.ColorRole.Mid: _color(theme, "border", "#555555"), QPalette.ColorRole.Dark: main.darker(130),
        QPalette.ColorRole.Shadow: QColor("#000000"),
    }
    for role, color in roles.items():
        palette.setColor(role, color)
    for role in (QPalette.ColorRole.WindowText, QPalette.ColorRole.Text, QPalette.ColorRole.ButtonText):
        palette.setColor(QPalette.ColorGroup.Disabled, role, muted)
    return palette


def base_rules(theme) -> str:
    g = lambda key, fallback: theme.get(key) or fallback
    main, panel, field = g("bg_main", "#1e1e1e"), g("bg_panel", "#2b2b2b"), g("bg_input", "#333333")
    text, border, accent = g("text_main", "#ffffff"), g("border", "#555555"), g("accent", "#0078D7")
    muted = g("text_muted", "#aaaaaa")
    return f"""
        QDialog {{ background-color: {main}; color: {text}; }}
        QAbstractItemView {{
            background-color: {field}; alternate-background-color: {panel}; color: {text};
            border: 1px solid {border}; selection-background-color: {accent}; selection-color: #ffffff;
        }}
        QHeaderView, QHeaderView::section {{
            background-color: {panel}; color: {text}; border: none; border-right: 1px solid {border};
            border-bottom: 1px solid {border}; padding: 3px 6px;
        }}
        QTableCornerButton::section {{ background-color: {panel}; border: none; }}
        QGroupBox {{
            background-color: transparent; border: 1px solid {border}; border-radius: 4px;
            margin-top: 10px; padding-top: 4px; color: {text};
        }}
        QGroupBox::title {{ subcontrol-origin: margin; left: 8px; padding: 0 3px; color: {text}; }}
        QToolButton {{
            background-color: {field}; color: {text}; border: 1px solid {border};
            border-radius: 4px; padding: 3px 8px;
        }}
        QToolButton:hover {{ background-color: {panel}; }}
        QToolButton:checked {{ background-color: {accent}; color: #ffffff; }}
        QToolBar {{ background: transparent; border: none; spacing: 4px; }}
        QToolTip {{ background-color: {panel}; color: {text}; border: 1px solid {border}; padding: 4px; }}
        QSplitter::handle {{ background-color: {border}; }}
        QSplitter::handle:hover {{ background-color: {accent}; }}
        QPlainTextEdit, QSpinBox, QDoubleSpinBox, QDateEdit {{
            background-color: {field}; color: {text}; border: 1px solid {border};
        }}
        QLabel:disabled, QCheckBox:disabled {{ color: {muted}; }}
        QPushButton#iconButton, QToolButton#iconButton {{
            padding: 2px 4px; font-weight: normal; font-size: 15px;
        }}
        QToolBar QToolButton {{ font-size: 15px; padding: 2px 5px; }}
        QLabel#decisionExplanation {{
            background-color: {panel}; border-left: 3px solid {accent}; padding: 6px 8px;
        }}
    """
