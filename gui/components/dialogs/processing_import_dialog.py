"""Per-import intelligence preset, stage overrides and processing preview.

Stage names, descriptions and preset membership come from the stage registry
and ProcessingPlanService — the same plan the scheduler executes — so this
dialog cannot drift from what actually runs. The preview lists, per source
type in the batch, the stages that will run and why (preset, your override,
or required by another stage).
"""
from __future__ import annotations

import os
from html import escape

from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QCheckBox, QColorDialog, QComboBox, QDialog, QDialogButtonBox, QFormLayout,
    QGroupBox, QHBoxLayout, QLabel, QLineEdit, QPushButton, QVBoxLayout,
)
from core.events.domains.metadata_events import TagEvent, TagIntent, TagPayload
from core.events.event_bus import EventBus
from gui.components.checkable_choice_list import CheckableChoiceList

_CATEGORY_LABELS = {
    "document": "Documents", "image": "Images", "audio": "Audio",
    "video": "Video", "data": "Data files (CSV/XLSX)",
}


class ProcessingImportDialog(QDialog):
    def __init__(self, default_preset="balanced", parent=None, app_context=None, paths=None):
        super().__init__(parent)
        self._ctx = app_context or getattr(parent, "_ctx", None) or getattr(parent, "app_context", None)
        self._plans = getattr(self._ctx, "processing_plan_service", None)
        self._categories = _categories_for(self._ctx, paths or [])
        self.setWindowTitle("Process added sources")
        self.setMinimumWidth(560)
        root = QVBoxLayout(self)
        info = QLabel(
            "Choose what Papyrus may run after import. Everything here runs "
            "locally (deterministic rules or optional small models); generative "
            "AI is never started automatically."
        )
        info.setWordWrap(True)
        root.addWidget(info)
        root.addLayout(self._build_form(default_preset))
        root.addWidget(self._build_overrides())
        root.addWidget(self._build_tag_options())
        self.preview = QLabel()
        self.preview.setWordWrap(True)
        self.preview.setAccessibleName("Processing plan preview")
        root.addWidget(self.preview)
        self.remember = QCheckBox("Remember this preset as the project default")
        root.addWidget(self.remember)
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Cancel | QDialogButtonBox.StandardButton.Ok
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        root.addWidget(buttons)
        self.preset.currentIndexChanged.connect(self._sync_checks)
        self.transcription.currentIndexChanged.connect(self._refresh_preview)
        self.override_group.toggled.connect(self._refresh_preview)
        self._sync_checks()
        self._apply_theme(parent)

    def _build_tag_options(self) -> QGroupBox:
        group = QGroupBox("Add with tags")
        group.setCheckable(True)
        group.setChecked(False)
        layout = QVBoxLayout(group)
        label = QLabel("Every source in this import will receive all selected tags.")
        label.setWordWrap(True)
        layout.addWidget(label)
        self.tag_choices = CheckableChoiceList(group)
        project_manager = getattr(self._ctx, "project_manager", None)
        tags = project_manager.get_all_tags() if project_manager else []
        self._set_tag_choices(tags)
        self.tag_choices.setMinimumHeight(110)
        layout.addWidget(self.tag_choices)
        self._tag_empty_label = QLabel()
        layout.addWidget(self._tag_empty_label)
        self._update_empty_tag_label(tags)
        create_row = QHBoxLayout()
        self.new_tag_name = QLineEdit()
        self.new_tag_name.setPlaceholderText("Create a new tag")
        self.new_tag_color = "#4CAF50"
        self.new_tag_color_button = QPushButton("Color")
        self.new_tag_color_button.clicked.connect(self._pick_new_tag_color)
        self._style_new_tag_color()
        add_button = QPushButton("Create & Select")
        add_button.clicked.connect(self._create_import_tag)
        create_row.addWidget(self.new_tag_name, 1)
        create_row.addWidget(self.new_tag_color_button)
        create_row.addWidget(add_button)
        layout.addLayout(create_row)
        self._tag_bus = EventBus.get_instance()
        self._tag_bus.tag_data_updated.connect(self._handle_tag_data)
        self.tag_group = group
        return group

    def _set_tag_choices(self, tags, selected=None):
        self.tag_choices.set_choices([
            {"id": tag.get("id"), "label": tag.get("name", ""), "available": True}
            for tag in tags if tag.get("id") is not None
        ], selected)

    def _pick_new_tag_color(self):
        chosen = QColorDialog.getColor(QColor(self.new_tag_color), self, "Select Tag Color")
        if chosen.isValid():
            self.new_tag_color = chosen.name()
            self._style_new_tag_color()

    def _style_new_tag_color(self):
        self.new_tag_color_button.setStyleSheet(
            f"background-color: {self.new_tag_color}; color: white;"
        )

    def _create_import_tag(self):
        name = self.new_tag_name.text().strip()
        if not name:
            return
        self._pending_tag_name = name
        self._tag_bus.tag_action_requested.emit(
            TagIntent.CREATE,
            TagPayload(name=name, color=self.new_tag_color),
        )
        self.new_tag_name.clear()

    def _handle_tag_data(self, event, payload):
        if event != TagEvent.ALL_TAGS:
            return
        tags = list(payload.get("tags", []))
        selected = set(self.tag_choices.selected())
        pending = getattr(self, "_pending_tag_name", None)
        selected.update(
            tag.get("id") for tag in tags
            if pending and tag.get("name", "").casefold() == pending.casefold()
        )
        self._pending_tag_name = None
        self._set_tag_choices(tags, selected)
        self._update_empty_tag_label(tags)

    def _update_empty_tag_label(self, tags):
        self._tag_empty_label.setText(
            "" if tags else "No tags yet — create one below and it will be selected."
        )

    def _build_form(self, default_preset) -> QFormLayout:
        form = QFormLayout()
        self.preset = QComboBox()
        for label, value in (
            ("Minimal", "minimal"), ("Balanced", "balanced"),
            ("Deep", "deep"), ("Manual", "manual"),
        ):
            self.preset.addItem(label, value)
        self.preset.setCurrentIndex(max(0, self.preset.findData(default_preset)))
        form.addRow("Preset", self.preset)
        self.transcription = QComboBox()
        self.transcription.addItem("Ask before transcription", "ask")
        self.transcription.addItem("Transcribe locally when available", "always")
        self.transcription.addItem("Do not transcribe", "never")
        form.addRow("Audio / video", self.transcription)
        return form

    def _build_overrides(self) -> QGroupBox:
        group = QGroupBox("Stage overrides")
        group.setCheckable(True)
        group.setChecked(False)
        layout = QVBoxLayout(group)
        self.checks = {}
        registry = getattr(self._ctx, "intelligence_stage_registry", None)
        specs = [spec for spec in (registry.all() if registry else [])
                 if any(c in spec.source_categories for c in self._categories)]
        if not specs:
            layout.addWidget(QLabel("Stage list unavailable (no stage registry)."))
        for spec in specs:
            text = " — ".join(filter(None, [spec.display_label, spec.description]))
            check = QCheckBox(f"{text}  ·  {spec.cost_hint}" if spec.cost_hint else text)
            check.setProperty("stage_id", spec.id)
            check.setToolTip(spec.description)
            check.toggled.connect(self._refresh_preview)
            layout.addWidget(check)
            self.checks[spec.id] = check
        self.override_group = group
        return group

    def _apply_theme(self, parent) -> None:
        ctx = getattr(parent, "_ctx", None) or getattr(parent, "app_context", None)
        manager = (
            getattr(ctx, "theme_manager", None)
            or getattr(parent, "theme_manager", None)
        )
        theme = manager.get_theme() if manager else {
            "bg_main": "#1e1e1e", "bg_panel": "#2b2b2b",
            "bg_input": "#252525", "text_main": "#ffffff",
            "text_muted": "#aaaaaa", "border": "#444444",
            "accent": "#b366ff",
        }
        self.setStyleSheet(f"""
            QDialog {{
                background-color: {theme['bg_main']};
                color: {theme['text_main']};
            }}
            QLabel, QCheckBox, QGroupBox {{
                color: {theme['text_main']};
            }}
            QGroupBox {{
                background-color: {theme['bg_panel']};
                border: 1px solid {theme['border']};
                border-radius: 6px;
                margin-top: 10px;
                padding: 10px 8px 8px 8px;
                font-weight: bold;
            }}
            QGroupBox::title {{
                subcontrol-origin: margin;
                left: 10px;
                padding: 0 4px;
            }}
            QComboBox {{
                background-color: {theme['bg_input']};
                color: {theme['text_main']};
                border: 1px solid {theme['border']};
                border-radius: 4px;
                padding: 5px 8px;
                min-height: 20px;
            }}
            QComboBox QAbstractItemView {{
                background-color: {theme['bg_panel']};
                color: {theme['text_main']};
                selection-background-color: {theme['accent']};
                border: 1px solid {theme['border']};
            }}
            QCheckBox::indicator {{
                width: 16px;
                height: 16px;
            }}
            QPushButton {{
                background-color: {theme['bg_panel']};
                color: {theme['text_main']};
                border: 1px solid {theme['border']};
                border-radius: 4px;
                padding: 6px 14px;
            }}
            QPushButton:hover {{
                background-color: {theme['bg_input']};
            }}
        """)
        ok_button = self.findChild(
            QDialogButtonBox
        ).button(QDialogButtonBox.StandardButton.Ok)
        if ok_button:
            ok_button.setStyleSheet(
                f"background-color: {theme['accent']}; color: white; "
                "border: none; border-radius: 4px; padding: 6px 14px; "
                "font-weight: bold;"
            )

    def _preset_stages(self, preset):
        """Stages the preset selects for any source type in this batch."""
        if not self._plans:
            return set()
        selection = {"preset": preset, "transcription_policy": self.transcription.currentData()}
        return {
            stage for category in self._categories
            for stage in self._plans.preview(selection, category).runnable
        }

    def _sync_checks(self):
        enabled = self._preset_stages(self.preset.currentData())
        for stage_id, check in self.checks.items():
            check.blockSignals(True)
            check.setChecked(stage_id in enabled)
            check.blockSignals(False)
        self._refresh_preview()

    def _refresh_preview(self, *_):
        if not self._plans:
            self.preview.setText("")
            return
        parts = []
        for category in self._categories:
            plan = self._plans.preview(self.selection(), category)
            stages = [f"{escape(i.label)} <i>({escape(i.reason)})</i>"
                      for i in plan.items if i.selected]
            name = _CATEGORY_LABELS.get(category, category.title())
            parts.append(f"<b>{escape(name)}:</b> " + (", ".join(stages) or "nothing"))
        self.preview.setText("Will run — " + "<br>".join(parts))

    def selection(self):
        preset = self.preset.currentData()
        defaults = self._preset_stages(preset)
        overrides = {}
        if self.override_group.isChecked():
            for stage_id, check in self.checks.items():
                value = check.isChecked()
                if value != (stage_id in defaults):
                    overrides[stage_id] = value
        return {
            "preset": preset,
            "stage_overrides": overrides,
            "transcription_policy": self.transcription.currentData(),
            "generative_policy": "on_demand",
            "remember_for_project": self.remember.isChecked(),
            "tag_ids": self.tag_choices.selected() if self.tag_group.isChecked() else [],
        }

    def closeEvent(self, event):
        try:
            self._tag_bus.tag_data_updated.disconnect(self._handle_tag_data)
        except (AttributeError, RuntimeError, TypeError):
            pass
        super().closeEvent(event)


def _categories_for(ctx, paths):
    """Source categories in the batch (documents when unknown)."""
    registry = getattr(ctx, "source_format_registry", None)
    categories = []
    for path in paths:
        source_type = registry.detect_source_type(path) if registry else ""
        spec = registry.get(source_type) if registry and source_type else None
        category = getattr(spec, "category", None) or (
            "data" if os.path.splitext(path)[1].lower() in {".csv", ".tsv", ".xlsx"} else "document"
        )
        if category not in categories:
            categories.append(category)
    return categories or ["document"]
