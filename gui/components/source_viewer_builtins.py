"""
gui/components/source_viewer_builtins.py

Registers SourceViewerSpec factories for the built-in PDF/video/audio
viewers. This lives in gui/, not core/papyrus_core.py, specifically because
it imports PDFViewer/VideoPlayer — core must never import gui (rules.md 1.3).

Called once from MainWindow.__init__, right before SourceViewerHost is
constructed, so the registry is populated before SourceViewerHost queries it.
"""
from __future__ import annotations

from core.plugins.extension_registry import SourceViewerSpec
from gui.components.pdf_viewer import PDFViewer
from gui.components.video_player import VideoPlayer


def register_builtin_source_viewers(app_context) -> None:
    registry = app_context.plugin_extension_registry
    if registry is None:
        return

    if registry.get_source_viewer("pdf") is None:
        registry.add_source_viewer(SourceViewerSpec(
            source_type="pdf",
            display_name="PDF",
            viewer_factory=lambda ctx, parent: PDFViewer(app_context=ctx, parent=parent),
        ))
    if registry.get_source_viewer("video") is None:
        registry.add_source_viewer(SourceViewerSpec(
            source_type="video",
            display_name="Video",
            viewer_factory=lambda ctx, parent: VideoPlayer(app_context=ctx, parent=parent),
        ))

    _register_reflowable_text_viewers(registry)
    _register_book_view_widget(registry)
    _register_data_preview_viewers(registry)
    _register_audio_viewer(registry)
    _register_image_viewer(registry)


def _register_reflowable_text_viewers(registry) -> None:
    """DOCX/EPUB/TXT/MD/HTML/RTF/DOC all share one flowed-text viewer (Phase 3)."""
    try:
        from gui.components.reflowable_text_viewer import ReflowableTextViewer
    except ImportError:
        return
    for source_type in ("docx", "epub", "txt", "md", "html", "rtf", "doc"):
        if registry.get_source_viewer(source_type) is None:
            registry.add_source_viewer(SourceViewerSpec(
                source_type=source_type,
                display_name=source_type.upper(),
                # Bind source_type as a default arg so each of the 7 registered
                # instances only reacts to its own format's source_opened
                # events, not a late-bound reference to the loop variable.
                viewer_factory=lambda ctx, parent, st=source_type: ReflowableTextViewer(
                    app_context=ctx, parent=parent, source_type=st
                ),
            ))


def _register_book_view_widget(registry) -> None:
    """Book view with pagination + page navigation (Phase 8)."""
    try:
        from gui.components.book_view_widget import BookViewWidget
    except ImportError:
        return
    if registry.get_source_viewer("__book__") is None:
        registry.add_source_viewer(SourceViewerSpec(
            source_type="__book__",
            display_name="Book View",
            viewer_factory=lambda ctx, parent: BookViewWidget(app_context=ctx, parent=parent),
        ))


def _register_data_preview_viewers(registry) -> None:
    """CSV/TSV/XLSX share one read-only preview viewer with an "Open in Data
    Dock" action (Phase 4)."""
    try:
        from gui.components.csv_preview_viewer import CsvPreviewViewer
    except ImportError:
        return
    for source_type in ("csv", "tsv", "xlsx"):
        if registry.get_source_viewer(source_type) is None:
            registry.add_source_viewer(SourceViewerSpec(
                source_type=source_type,
                display_name=source_type.upper(),
                viewer_factory=lambda ctx, parent, st=source_type: CsvPreviewViewer(
                    app_context=ctx, parent=parent, source_type=st
                ),
            ))


def _register_audio_viewer(registry) -> None:
    """Audio reuses VideoPlayer's transcript/playback machinery (Phase 5)."""
    try:
        from gui.components.audio_player import AudioPlayer
    except ImportError:
        return
    if registry.get_source_viewer("audio") is None:
        registry.add_source_viewer(SourceViewerSpec(
            source_type="audio",
            display_name="Audio",
            viewer_factory=lambda ctx, parent: AudioPlayer(app_context=ctx, parent=parent),
        ))


def _register_image_viewer(registry) -> None:
    """OCR-backed image viewer (Phase 6)."""
    try:
        from gui.components.image_viewer import ImageViewer
    except ImportError:
        return
    if registry.get_source_viewer("image") is None:
        registry.add_source_viewer(SourceViewerSpec(
            source_type="image",
            display_name="Image",
            viewer_factory=lambda ctx, parent: ImageViewer(app_context=ctx, parent=parent),
        ))
