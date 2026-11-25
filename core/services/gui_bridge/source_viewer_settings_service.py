"""
SourceViewerSettingsService — manages global display preferences for text viewers.
Persisted in AppConfig (user-level), broadcasts changes via EventBus.
"""
from __future__ import annotations

import json
from typing import Optional

from core.events.event_bus import EventBus
from core.models.display_settings import SourceViewerDisplaySettings


class SourceViewerSettingsService:
    """
    Central hub for source viewer display settings (fonts, colors, reading mode, pagination).
    Loads from AppConfig, persists back on change, and broadcasts via EventBus so all
    open viewers can re-render immediately.
    """

    def __init__(self, app_config=None) -> None:
        self._config = app_config  # AppConfigService or dict-like
        self._current: Optional[SourceViewerDisplaySettings] = None
        self._bus = EventBus.get_instance()

    def load_settings(self) -> SourceViewerDisplaySettings:
        """Load settings from AppConfig, cache, and return."""
        if self._current is not None:
            return self._current

        if self._config is None:
            self._current = SourceViewerDisplaySettings()
            return self._current

        # Try to load from AppConfig (key: "source_viewer.display_settings")
        try:
            stored_json = self._config.get("source_viewer.display_settings", "{}")
            if stored_json and stored_json != "{}":
                data = json.loads(stored_json)
                self._current = SourceViewerDisplaySettings(**data)
            else:
                self._current = SourceViewerDisplaySettings()
        except (json.JSONDecodeError, TypeError):
            self._current = SourceViewerDisplaySettings()

        return self._current

    def get_settings(self) -> SourceViewerDisplaySettings:
        """Return current settings (load if not yet cached)."""
        if self._current is None:
            self.load_settings()
        return self._current

    def set_settings(self, settings: SourceViewerDisplaySettings) -> None:
        """
        Update settings, persist to AppConfig, and broadcast change.

        Args:
            settings: new SourceViewerDisplaySettings instance
        """
        self._current = settings

        # Persist to AppConfig
        if self._config is not None:
            # Convert dataclass to dict (exclude plugin_id=="" defaults)
            data = {
                k: v
                for k, v in settings.__dict__.items()
                if k != "plugin_id" or v != ""
            }
            self._config.set("source_viewer.display_settings", json.dumps(data))

        # Broadcast change so all viewers can update
        from core.events.domains.gui_events import GuiEvent, GuiPayload

        self._bus.gui_action_requested.emit(
            GuiEvent.SOURCE_VIEWER_SETTINGS_CHANGED,
            GuiPayload(settings=settings),
        )

    def reset_to_default(self) -> None:
        """Reset to factory defaults and persist."""
        self.set_settings(SourceViewerDisplaySettings())

    def apply_theme_colors(
        self, text_color: str, bg_color: str, override_user_custom: bool = False
    ) -> None:
        """
        Update colors based on current theme. If user has customized colors,
        only update if override_user_custom=True (theme switch forces update).

        Args:
            text_color: hex color from theme
            bg_color: hex color from theme
            override_user_custom: if True, override user's custom colors; else preserve
        """
        settings = self.get_settings()
        # Only update if not yet customized, or if forcing override (theme change)
        if override_user_custom or (not settings.text_color and not settings.bg_color):
            settings.text_color = text_color
            settings.bg_color = bg_color
            self.set_settings(settings)
