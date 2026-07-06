"""Tests for SourceViewerSettingsService."""
from unittest.mock import MagicMock

from core.models.display_settings import SourceViewerDisplaySettings
from core.services.gui_bridge.source_viewer_settings_service import SourceViewerSettingsService


def test_loads_default_settings_when_no_config():
    service = SourceViewerSettingsService(app_config=None)
    settings = service.get_settings()

    assert settings is not None
    assert settings.font_size_base == 12
    assert settings.reading_mode == "normal"


def test_persists_settings_to_config():
    config = MagicMock()
    service = SourceViewerSettingsService(app_config=config)

    new_settings = SourceViewerDisplaySettings(font_size_base=14, reading_mode="book")
    service.set_settings(new_settings)

    # Verify set was called on config
    assert config.set.called


def test_retrieves_cached_settings():
    service = SourceViewerSettingsService(app_config=None)
    settings1 = service.get_settings()
    settings2 = service.get_settings()

    # Should return the same cached instance
    assert settings1 is settings2


def test_reset_to_default():
    service = SourceViewerSettingsService(app_config=None)

    # Modify settings
    modified = SourceViewerDisplaySettings(font_size_base=20)
    service.set_settings(modified)
    assert service.get_settings().font_size_base == 20

    # Reset
    service.reset_to_default()
    settings = service.get_settings()
    assert settings.font_size_base == 12  # back to default


def test_apply_theme_colors_when_not_customized():
    service = SourceViewerSettingsService(app_config=None)
    service.apply_theme_colors("#111111", "#EEEEEE", override_user_custom=False)

    settings = service.get_settings()
    assert settings.text_color == "#111111"
    assert settings.bg_color == "#EEEEEE"
