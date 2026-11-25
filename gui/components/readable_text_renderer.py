"""
ReadableTextRenderer — factory for styled QTextDocuments used by both
ReflowableTextViewer (single view) and BookViewWidget (per-page rendering).
Abstracts text styling from viewer implementation.
"""
from __future__ import annotations

from PySide6.QtGui import QColor, QFont, QTextDocument
from PySide6.QtWidgets import QApplication

from core.models.display_settings import SourceViewerDisplaySettings


class ReadableTextRenderer:
    """Renders plain text or formatted HTML into a styled QTextDocument."""

    @staticmethod
    def render_text(
        text: str,
        settings: SourceViewerDisplaySettings,
        heading_level: int = 0,
        theme_text_color: str = "#000000",
        theme_bg_color: str = "#FFFFFF",
    ) -> QTextDocument:
        """
        Create a styled QTextDocument from text.

        Args:
            text: the text content
            settings: display preferences (font size, colors, line height, etc.)
            heading_level: 0 = body, 1-6 = heading sizes (larger = bigger text)
            theme_text_color: default text color from theme (if not customized)
            theme_bg_color: default bg color from theme (if not customized)

        Returns:
            QTextDocument with styling applied
        """
        text_color, bg_color = ReadableTextRenderer.colors(settings, theme_text_color, theme_bg_color)

        # Calculate font size (heading_level 0 = base, each level up adds 2-4px)
        base_size = settings.font_size_base
        if heading_level > 0:
            # h1: +6, h2: +5, h3: +4, h4: +3, h5: +2, h6: +1
            size_boost = max(1, 7 - heading_level)
            font_size = base_size + size_boost
        else:
            font_size = base_size

        # Build CSS stylesheet for the document
        # Use CSS for precise control over fonts, spacing, colors
        css = f"""
            body {{
                color: {text_color};
                background-color: {bg_color};
                font-family: {settings.font_family};
                font-size: {font_size}px;
                line-height: {settings.line_height};
                margin: 1em;
                padding: 0;
            }}
            p {{
                margin-top: {settings.paragraph_spacing * settings.line_height * font_size}px;
                margin-bottom: {settings.paragraph_spacing * settings.line_height * font_size}px;
                margin-left: 0;
                margin-right: 0;
            }}
            h1, h2, h3, h4, h5, h6 {{
                color: {text_color};
                font-weight: bold;
                margin-top: {settings.paragraph_spacing * 1.5 * font_size}px;
                margin-bottom: {settings.paragraph_spacing * font_size}px;
            }}
            h1 {{ font-size: {font_size + 6}px; }}
            h2 {{ font-size: {font_size + 5}px; }}
            h3 {{ font-size: {font_size + 4}px; }}
            h4 {{ font-size: {font_size + 3}px; }}
            h5 {{ font-size: {font_size + 2}px; }}
            h6 {{ font-size: {font_size + 1}px; }}
            a {{ color: {ReadableTextRenderer._link_color(text_color)}; }}
            blockquote {{
                color: {ReadableTextRenderer._mute_color(text_color)};
                border-left: 3px solid {ReadableTextRenderer._mute_color(text_color)};
                margin-left: 0;
                padding-left: 1em;
            }}
            code {{
                background-color: {ReadableTextRenderer._invert_color(bg_color)};
                color: {text_color};
                padding: 2px 4px;
                border-radius: 3px;
            }}
        """

        doc = QTextDocument()
        doc.setDefaultStyleSheet(css)

        # Set document background color
        bg = QColor(bg_color)
        if bg.isValid():
            doc.setDefaultFont(QFont(settings.font_family, font_size))

        # Wrap text in basic HTML structure
        html = f"<html><body>{text}</body></html>"
        doc.setHtml(html)

        return doc

    @staticmethod
    def colors(settings, theme_text_color="#000000", theme_bg_color="#FFFFFF"):
        # Handle theme and dark mode
        if getattr(settings, 'theme_mode_enabled', False):
            # Use app theme colors
            from gui.theme.theme import ThemeManager
            try:
                theme = ThemeManager().get_theme()
                theme_text_color = theme.get('text', '#000000')
                theme_bg_color = theme.get('canvas', '#FFFFFF')
            except Exception:
                pass

        if getattr(settings, 'dark_mode_enabled', False):
            # Invert colors for dark mode
            text_color = ReadableTextRenderer._invert_color(theme_text_color)
            bg_color = ReadableTextRenderer._invert_color(theme_bg_color)
        else:
            # Resolve colors: use settings if customized, else theme defaults
            text_color = settings.text_color or theme_text_color
            bg_color = settings.bg_color or theme_bg_color

        return text_color, bg_color

    @staticmethod
    def _link_color(text_color: str) -> str:
        """Derive a link color that contrasts with text color."""
        try:
            color = QColor(text_color)
            # If text is dark, use bright blue; if light, use dark blue
            if color.lightness() < 128:
                return "#0066FF"  # bright blue
            else:
                return "#004099"  # dark blue
        except Exception:
            return "#0066FF"

    @staticmethod
    def _mute_color(text_color: str) -> str:
        """Reduce saturation/brightness for secondary text (quotes, etc.)."""
        try:
            color = QColor(text_color)
            # Reduce lightness by ~30%
            h, s, l, a = color.getHsl()
            l = max(0, l - 30)
            color.setHsl(h, s, l, a)
            return color.name()
        except Exception:
            return "#808080"

    @staticmethod
    def _invert_color(bg_color: str) -> str:
        """Get a slightly inverted color for code blocks."""
        try:
            color = QColor(bg_color)
            h, s, l, a = color.getHsl()
            l = 255 - l  # invert lightness
            l = max(0, min(255, l))  # clamp
            color.setHsl(h, s, l, a)
            return color.name()
        except Exception:
            return "#E0E0E0"
