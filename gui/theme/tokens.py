"""
Theme key contract for Papyrus.

Every key listed here is guaranteed to exist in every theme dict emitted by
ThemeManager.  Use ``UnifiedThemedMixin._t(key)`` to access them safely —
it falls back to FALLBACK_THEME when a key is missing.

When adding a new key, update:
  1. This file (documentation)
  2. FALLBACK_THEME in gui/components/base/core.py
  3. Every theme dict in gui/theme/theme.py (_ThemeManager._init_themes)
"""

THEME_KEYS: dict[str, str] = {
    # Backgrounds
    "bg_main":    "Main application background (window, canvas surround)",
    "bg_panel":   "Cards, dock headers, toolbar backgrounds",
    "bg_input":   "Text inputs, code blocks, table cells",

    # Text
    "text_main":  "Primary readable text",
    "text_muted": "Secondary text, placeholders, disabled labels",

    # Structure
    "border":     "All dividers, widget outlines",

    # Interactive
    "accent":       "Primary interactive highlight, selected states, primary buttons",
    "accent_hover": "Hover/active state for accent elements",

    # Semantic
    "success": "Positive outcomes, completion states",
    "warning": "Caution states, non-critical alerts",
    "error":   "Error states, destructive actions",

    # Workspace
    "canvas": "Workspace / graph canvas background",

    # AI conversation bubbles
    "ai_bubble":        "AI message bubble background",
    "ai_bubble_border": "AI bubble border",
    "ai_bubble_hover":  "AI bubble hover state",

    # User conversation bubbles
    "user_bubble":        "User message bubble background",
    "user_bubble_border": "User bubble border",
    "user_bubble_hover":  "User bubble hover state",
}
