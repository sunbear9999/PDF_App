"""
GUI events — display settings changes, viewer state, theme updates.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum, auto
from typing import TYPE_CHECKING, Optional

from .base import BasePayload

if TYPE_CHECKING:
    from core.models.display_settings import SourceViewerDisplaySettings


class GuiEvent(Enum):
    """GUI-level events."""

    SOURCE_VIEWER_SETTINGS_CHANGED = auto()
    THEME_CHANGED = auto()
    DOCK_FOCUSED = auto()


class GuiIntent(Enum):
    """User intents that trigger GUI changes."""

    UPDATE_DISPLAY_SETTINGS = auto()
    OPEN_SETTINGS_DIALOG = auto()


@dataclass
class GuiPayload(BasePayload):
    """GUI event payload."""

    settings: Optional[SourceViewerDisplaySettings] = None
    theme_name: Optional[str] = None
    dock_name: Optional[str] = None
    error: Optional[str] = None
