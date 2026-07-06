"""
Core display settings for source viewers — text colors, font size, reading mode preference,
pagination settings. Persisted in AppConfig (user-level, across all projects), not ProjectManager.
"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class SourceViewerDisplaySettings:
    """Global reading & display preferences for all text-based source viewers."""

    # Reading mode: "normal" (single view, fit to window) or "book" (paginated, page-flip nav)
    reading_mode: str = "normal"

    # Font and layout
    font_family: str = "Segoe UI, Arial, sans-serif"  # fallback chain
    font_size_base: int = 12  # px; zoom multiplies this
    line_height: float = 1.5  # relative to font size
    letter_spacing: float = 0.0  # px
    paragraph_spacing: float = 1.0  # line-height multiplier

    # Colors — user customization overrides theme defaults
    text_color: str = ""  # empty = use theme; hex #RRGGBB otherwise
    bg_color: str = ""  # empty = use theme; hex #RRGGBB otherwise

    # Display modes
    dark_mode_enabled: bool = False  # Invert colors for dark background
    theme_mode_enabled: bool = False  # Use app theme colors

    # Book mode pagination (lines_per_page × line_height ≈ visual page height)
    lines_per_page: int = 28  # typical 12px font, 1.5 line height ≈ 8.5" page
    page_width_chars: int = 80  # for word-wrap; 0 = no limit (full window width)

    # Animations
    page_flip_animation_ms: int = 300  # smooth page transition duration

    # Persistence / ownership
    plugin_id: str = ""  # empty = built-in; set if registered by a plugin
