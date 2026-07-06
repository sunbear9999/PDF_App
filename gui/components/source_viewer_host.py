from __future__ import annotations

import os

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QColorDialog,
    QComboBox,
    QDoubleSpinBox,
    QHBoxLayout,
    QPushButton,
    QSlider,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from core.events.domains.document_events import DocumentIntent, DocumentPayload, SourceEvent, SourceIntent, SourcePayload
from core.events.event_bus import EventBus


class SourceViewerHost(QWidget):
    """
    Registry-driven stacked host for every source viewer widget.

    Builds one widget per SourceViewerSpec registered on
    app_context.plugin_extension_registry (see gui/components/source_viewer_builtins.py
    for the built-in pdf/video/audio/image/text/data registrations) and
    dispatches to the right one by source_type — no hardcoded per-format
    branching. self.pdf_viewer / self.video_player are kept as back-compat
    aliases since other files reference them directly.
    """

    def __init__(self, app_context=None, parent=None):
        super().__init__(parent)
        self._ctx = app_context
        self.bus = EventBus.get_instance()
        self._viewers = {}
        self._current_source_type = "pdf"
        self._settings_service = getattr(app_context, "source_viewer_settings_service", None)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        # ---- Toolbar with display settings ----
        toolbar_layout = QHBoxLayout()
        toolbar_layout.setContentsMargins(8, 4, 8, 4)
        toolbar_layout.setSpacing(8)

        # Zoom buttons
        zoom_out_btn = QPushButton("−")
        zoom_out_btn.setMaximumWidth(32)
        zoom_out_btn.clicked.connect(self.zoom_out)
        toolbar_layout.addWidget(zoom_out_btn)

        zoom_reset_btn = QPushButton("Reset")
        zoom_reset_btn.setMaximumWidth(60)
        zoom_reset_btn.clicked.connect(self.zoom_reset)
        toolbar_layout.addWidget(zoom_reset_btn)

        zoom_in_btn = QPushButton("+")
        zoom_in_btn.setMaximumWidth(32)
        zoom_in_btn.clicked.connect(self.zoom_in)
        toolbar_layout.addWidget(zoom_in_btn)

        toolbar_layout.addSpacing(16)

        # Reading Mode button (word-by-word highlighting)
        self._reading_mode_btn = QPushButton("📖 Read")
        self._reading_mode_btn.setMaximumWidth(70)
        self._reading_mode_btn.setCheckable(True)
        self._reading_mode_btn.setToolTip("Word-by-word reading mode")
        self._reading_mode_btn.clicked.connect(self._on_reading_mode_btn_clicked)
        toolbar_layout.addWidget(self._reading_mode_btn)

        # Book view mode dropdown (pagination)
        self._mode_combo = QComboBox()
        self._mode_combo.addItems(["Normal", "Book"])
        self._mode_combo.setMaximumWidth(100)
        self._mode_combo.currentTextChanged.connect(self._on_reading_mode_changed)
        toolbar_layout.addWidget(self._mode_combo)

        toolbar_layout.addSpacing(12)

        # Dark mode toggle
        self._dark_mode_btn = QPushButton("🌙")
        self._dark_mode_btn.setMaximumWidth(40)
        self._dark_mode_btn.setCheckable(True)
        self._dark_mode_btn.setToolTip("Toggle dark mode")
        self._dark_mode_btn.clicked.connect(self._toggle_dark_mode)
        toolbar_layout.addWidget(self._dark_mode_btn)

        # Theme mode toggle
        self._theme_mode_btn = QPushButton("🎨")
        self._theme_mode_btn.setMaximumWidth(40)
        self._theme_mode_btn.setCheckable(True)
        self._theme_mode_btn.setToolTip("Toggle theme mode")
        self._theme_mode_btn.clicked.connect(self._toggle_theme_mode)
        toolbar_layout.addWidget(self._theme_mode_btn)

        toolbar_layout.addSpacing(12)

        # Font size slider
        self._font_size_slider = QSlider(Qt.Orientation.Horizontal)
        self._font_size_slider.setMinimum(8)
        self._font_size_slider.setMaximum(32)
        self._font_size_slider.setMaximumWidth(120)
        self._font_size_slider.valueChanged.connect(self._on_font_size_changed)
        toolbar_layout.addWidget(self._font_size_slider)

        # Text color picker
        self._text_color_btn = QPushButton("🎨 Text")
        self._text_color_btn.setMaximumWidth(80)
        self._text_color_btn.clicked.connect(self._pick_text_color)
        toolbar_layout.addWidget(self._text_color_btn)

        # Background color picker
        self._bg_color_btn = QPushButton("🎨 BG")
        self._bg_color_btn.setMaximumWidth(80)
        self._bg_color_btn.clicked.connect(self._pick_bg_color)
        toolbar_layout.addWidget(self._bg_color_btn)

        toolbar_layout.addStretch()
        layout.addLayout(toolbar_layout)

        # ---- Stacked viewer widgets ----
        self.stacked = QStackedWidget()
        layout.addWidget(self.stacked)

        registry = getattr(app_context, "plugin_extension_registry", None)
        if registry is not None:
            for spec in registry.get_source_viewers():
                try:
                    widget = spec.viewer_factory(app_context, self)
                except Exception as exc:
                    print(f"[SourceViewerHost] Failed to build viewer for '{spec.source_type}': {exc}")
                    continue
                self._viewers[spec.source_type] = widget
                self.stacked.addWidget(widget)

        # Back-compat aliases — many call sites reference these directly
        # (e.g. chat_streamer.py's getattr(viewer, "annot_manager", None)).
        self.pdf_viewer = self._viewers.get("pdf")
        self.video_player = self._viewers.get("video")

        self._load_settings()
        self.bus.source_opened.connect(self._on_source_opened)
        self.bus.gui_action_requested.connect(self._on_gui_action)

    @property
    def _active_viewer(self):
        return self._viewers.get(self._current_source_type) or self.pdf_viewer

    @property
    def pdf_path(self):
        return getattr(self.pdf_viewer, "pdf_path", None)

    @property
    def current_source_path(self):
        """Get the current source file path from active viewer."""
        widget = self._active_viewer
        if widget is None:
            return ""
        for attr in ("current_source_path", "video_path", "pdf_path"):
            value = getattr(widget, attr, None)
            if value:
                return value
        return ""

    @property
    def worker(self):
        return getattr(self.pdf_viewer, "worker", None)

    @property
    def scene(self):
        return getattr(self.pdf_viewer, "scene", None)

    @property
    def doc(self):
        return getattr(self.pdf_viewer, "doc", None)

    @doc.setter
    def doc(self, value):
        self.pdf_viewer.doc = value

    @property
    def annot_manager(self):
        return getattr(self.pdf_viewer, "annot_manager", None)

    def _load_settings(self) -> None:
        """Load current settings and update UI."""
        if not self._settings_service:
            return
        settings = self._settings_service.get_settings()
        self._mode_combo.setCurrentText("Book" if settings.reading_mode == "book" else "Normal")
        self._font_size_slider.setValue(settings.font_size_base)
        self._text_color_btn.setStyleSheet(f"background-color: {settings.text_color or '#000000'};")
        self._bg_color_btn.setStyleSheet(f"background-color: {settings.bg_color or '#FFFFFF'};")
        self._dark_mode_btn.setChecked(getattr(settings, 'dark_mode_enabled', False))
        self._theme_mode_btn.setChecked(getattr(settings, 'theme_mode_enabled', False))

    def _on_gui_action(self, event, payload):
        """Refresh UI when settings broadcast."""
        from core.events.domains.gui_events import GuiEvent
        if event == GuiEvent.SOURCE_VIEWER_SETTINGS_CHANGED:
            self._load_settings()

    def _on_reading_mode_btn_clicked(self) -> None:
        """Toggle word-by-word reading mode (PDF only for now)."""
        # Reading mode is currently only implemented for PDFs with visual highlighting
        # For other formats, it would require implementing word-level bbox extraction
        if self._current_source_type == "pdf" or self._current_source_type == "__book__":
            widget = self._active_viewer
            if widget and hasattr(widget, 'toggle_reading_mode'):
                widget.toggle_reading_mode(self._reading_mode_btn.isChecked())

    def _on_reading_mode_changed(self, text: str) -> None:
        """Change reading mode and update current view."""
        if not self._settings_service:
            return
        settings = self._settings_service.get_settings()
        settings.reading_mode = "book" if text == "Book" else "normal"
        self._settings_service.set_settings(settings)
        # Re-dispatch current source to apply new mode
        source_type = self._current_source_type
        text_formats = ("txt", "md", "html", "docx", "epub", "rtf", "doc", "pdf")  # Include PDF
        if source_type in text_formats and text == "Book":
            book_widget = self._viewers.get("__book__")
            if book_widget and self.current_source_path:
                # Load the source into book view (works for text AND PDF)
                book_widget._load(self.current_source_path, source_type)
                self._current_source_type = "__book__"
                self.stacked.setCurrentWidget(book_widget)
        elif source_type == "__book__" and text == "Normal":
            # Switch back to the original viewer
            # Determine which viewer based on what was being viewed before book mode
            self._current_source_type = "pdf" if hasattr(self, "_previous_source_type") and self._previous_source_type == "pdf" else "txt"
            widget = self._viewers.get(self._current_source_type) or self.pdf_viewer
            if widget:
                self.stacked.setCurrentWidget(widget)

    def _on_font_size_changed(self, value: int) -> None:
        """Update font size."""
        if not self._settings_service:
            return
        settings = self._settings_service.get_settings()
        settings.font_size_base = value
        self._settings_service.set_settings(settings)

    def _pick_text_color(self) -> None:
        """Open color picker for text color."""
        if not self._settings_service:
            return
        settings = self._settings_service.get_settings()
        current_color = QColor(settings.text_color or "#000000")
        color = QColorDialog.getColor(current_color, self, "Choose Text Color")
        if color.isValid():
            settings.text_color = color.name()
            self._settings_service.set_settings(settings)

    def _pick_bg_color(self) -> None:
        """Open color picker for background color."""
        if not self._settings_service:
            return
        settings = self._settings_service.get_settings()
        current_color = QColor(settings.bg_color or "#FFFFFF")
        color = QColorDialog.getColor(current_color, self, "Choose Background Color")
        if color.isValid():
            settings.bg_color = color.name()
            self._settings_service.set_settings(settings)

    def _toggle_dark_mode(self) -> None:
        """Toggle dark mode for all viewers."""
        if not self._settings_service:
            return
        settings = self._settings_service.get_settings()
        if self._dark_mode_btn.isChecked():
            settings.dark_mode_enabled = True
            settings.theme_mode_enabled = False
            self._theme_mode_btn.setChecked(False)
        else:
            settings.dark_mode_enabled = False
        self._settings_service.set_settings(settings)
        self._forward("toggle_dark_mode")

    def _toggle_theme_mode(self) -> None:
        """Toggle theme mode for all viewers."""
        if not self._settings_service:
            return
        settings = self._settings_service.get_settings()
        if self._theme_mode_btn.isChecked():
            settings.theme_mode_enabled = True
            settings.dark_mode_enabled = False
            self._dark_mode_btn.setChecked(False)
        else:
            settings.theme_mode_enabled = False
        self._settings_service.set_settings(settings)
        self._forward("toggle_theme_mode")

    def _on_source_opened(self, event, payload):
        if event != SourceEvent.OPENED:
            return
        source_type = payload.source_type or "pdf"

        # Track the original source type for switching back from book mode
        self._previous_source_type = source_type

        # For all text/document formats, check reading_mode setting and dispatch to BookViewWidget if "book"
        text_formats = ("txt", "md", "html", "docx", "epub", "rtf", "doc", "pdf")
        if source_type in text_formats and self._settings_service:
            settings = self._settings_service.get_settings()
            if settings.reading_mode == "book":
                # Route to BookViewWidget instead of the format-specific viewer
                book_widget = self._viewers.get("__book__")
                if book_widget:
                    self._current_source_type = "__book__"
                    self.stacked.setCurrentWidget(book_widget)
                    return

        # Standard dispatch: use registered viewer for source_type
        widget = self._viewers.get(source_type) or self.pdf_viewer
        if widget is None:
            return
        self._current_source_type = source_type if source_type in self._viewers else "pdf"
        self.stacked.setCurrentWidget(widget)

    def register_shortcuts(self):
        if hasattr(self.video_player, "register_shortcuts"):
            self.video_player.register_shortcuts()

    def _forward(self, method_name, *args, **kwargs):
        widget = self._active_viewer
        method = getattr(widget, method_name, None) if widget is not None else None
        if callable(method):
            return method(*args, **kwargs)
        return None

    def zoom_out(self):
        return self._forward("zoom_out")

    def zoom_reset(self):
        return self._forward("zoom_reset")

    def zoom_in(self):
        return self._forward("zoom_in")

    def toggle_reading_mode(self):
        return self._forward("toggle_reading_mode")

    def sharpen_focus(self):
        return self._forward("sharpen_focus")

    def rotate_view(self):
        return self._forward("rotate_view")

    def swap_document_handle(self, doc):
        return self._forward("swap_document_handle", doc)

    def jump_to_annotation(self, page_num, annot_id):
        return self._forward("jump_to_annotation", page_num, annot_id)

    def jump_to_page(self, page_num):
        return self._forward("jump_to_page", page_num)

    def reload_page(self, page_num):
        return self._forward("reload_page", page_num)

    def jump_to_source(self, doc_name, quote):
        pm = getattr(self._ctx, "project_manager", None) if self._ctx else None
        source = None
        if pm and hasattr(pm, "list_sources"):
            source = next((s for s in pm.list_sources() if doc_name in os.path.basename(s.get("path", ""))), None)
        source_type = (source or {}).get("source_type") or "pdf"
        registry = getattr(self._ctx, "plugin_extension_registry", None)
        handler = registry.get_citation_source_handler(source_type) if registry else None
        if handler:
            handler.callback(
                {
                    "doc_name": doc_name,
                    "quote": quote,
                    "source_id": (source or {}).get("id"),
                    "path": (source or {}).get("path"),
                },
                self._ctx,
            )
            return
        # Last-resort fallback for a source_type with no registered handler.
        return self._forward("jump_to_source", doc_name, quote)

    def jump_to_video(self, source_id=None, path=None, timestamp=0):
        self.bus.source_action_requested.emit(
            SourceIntent.OPEN,
            SourcePayload(
                path=path,
                source_id=source_id,
                source_type="video",
                timestamp=timestamp,
                context={"autoplay": True},
            ),
        )
