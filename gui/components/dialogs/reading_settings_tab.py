"""Reading controls reuse the source-viewer settings service and event."""
from dataclasses import replace
from PySide6.QtWidgets import QWidget, QVBoxLayout, QCheckBox, QLabel
from core.events.domains.gui_events import GuiEvent


class ReadingSettingsTab(QWidget):
    def __init__(self, app_context, parent=None):
        super().__init__(parent)
        self.ctx = app_context
        self.service = getattr(app_context, 'source_viewer_settings_service', None)
        layout = QVBoxLayout(self)
        self.links = QCheckBox('Show smart links and hover previews', self)
        self.links.setEnabled(self.service is not None)
        layout.addWidget(self.links)
        self.entities = QCheckBox('Show entity links (hover a name; Alt+] / Alt+[ step through its mentions)', self)
        self.entities.setEnabled(self.service is not None)
        layout.addWidget(self.entities)
        text = QLabel('Hover over a linked note, citation or contents entry to read its preview. '
                      'Click to jump; Alt+Left returns to your reading position. '
                      'Run Sentences & sections and Citations to build these links. '
                      'Uncertain matches remain available for review in Intelligence.', self)
        text.setWordWrap(True)
        layout.addWidget(text)
        layout.addStretch()
        self.links.toggled.connect(self._set_links)
        self.entities.toggled.connect(self._set_entities)
        self._refresh()
        bus = getattr(app_context, 'bus', None)
        if bus:
            bus.gui_action_requested.connect(self._changed)
        registry = getattr(getattr(app_context, 'help_service', None), 'ui_target_registry', None)
        if registry:
            identity = f'settings.reading.{id(self)}'
            registry.register_widget(identity, self.links, 'core.reading.smart_links')
            self.destroyed.connect(lambda *_, r=registry, key=identity: r.unregister(key))

    def _refresh(self):
        self.links.blockSignals(True)
        self.links.setChecked(self.service.get_settings().smart_links_enabled if self.service else True)
        self.links.blockSignals(False)
        self.entities.blockSignals(True)
        self.entities.setChecked(self.service.get_settings().entity_links_enabled if self.service else False)
        self.entities.blockSignals(False)

    def _set_links(self, enabled):
        if self.service:
            self.service.set_settings(replace(self.service.get_settings(), smart_links_enabled=enabled))

    def _set_entities(self, enabled):
        if self.service:
            self.service.set_settings(replace(self.service.get_settings(), entity_links_enabled=enabled))

    def _changed(self, event, _payload):
        if event == GuiEvent.SOURCE_VIEWER_SETTINGS_CHANGED:
            self._refresh()
