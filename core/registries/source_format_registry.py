"""
core/registries/source_format_registry.py

Central dispatch table for every ingestible source format (PDF, video, audio,
image, and document/data formats like DOCX, EPUB, CSV). Pure Python — no Qt,
no GUI imports.

Replaces the old hardcoded extension checks in ProjectManager.detect_source_type()
and the scattered `if source_type == "video"` / `if ext == ".pdf"` branches
across the ingestion, viewer, and explorer layers.

Usage (built-in registration in papyrus_core.py):
    registry = SourceFormatRegistry()
    registry.register(SourceFormatSpec(
        source_type="docx", label="Word Document", category="document",
        extensions=[".docx"], parser_provider_id="docx_parser",
    ))

Plugin usage (in plugin on_load):
    api.source_formats.register(SourceFormatSpec(...))
"""
from __future__ import annotations

import mimetypes
import os
from dataclasses import dataclass, field
from typing import Callable, List, Optional


@dataclass
class SourceFormatSpec:
    """
    All identity/dispatch metadata for one ingestible source format.

    Deliberately holds no Qt/GUI callables — viewer widgets are registered
    separately as SourceViewerSpec (core/plugins/extension_registry.py) since
    core must never import gui. parser_provider_id / data_loader_id are keys
    into DocumentParserRegistry / DataProviderRegistry respectively, resolved
    lazily so registration order doesn't matter.
    """
    source_type: str                 # "pdf", "docx", "epub", "csv", "audio", "image", ...
    label: str
    category: str                    # "document" | "data" | "video" | "audio" | "image"
    extensions: List[str] = field(default_factory=list)   # lowercase, leading dot
    mime_types: List[str] = field(default_factory=list)
    parser_provider_id: Optional[str] = None    # -> DocumentParserRegistry, category="document"|"image"
    data_loader_id: Optional[str] = None        # -> DataProviderRegistry loader, category="data"
    is_available: Callable[[], bool] = field(default=lambda: True)
    unavailable_reason: str = ""
    icon: str = "📄"
    priority: int = 50               # lower checked first when extensions overlap
    plugin_id: str = ""


class SourceFormatRegistry:
    """Registry of SourceFormatSpec objects, keyed by source_type."""

    def __init__(self) -> None:
        self._specs: dict[str, SourceFormatSpec] = {}

    def register(self, spec: SourceFormatSpec) -> None:
        self._specs[spec.source_type] = spec

    def get(self, source_type: str) -> Optional[SourceFormatSpec]:
        return self._specs.get(source_type)

    def all(self) -> List[SourceFormatSpec]:
        return sorted(self._specs.values(), key=lambda s: s.priority)

    def detect_source_type(self, path: str) -> str:
        """
        Resolve a source_type for a file path. Extension match first (by
        priority), then a mimetypes-based fallback. Returns "unknown" if
        nothing claims it — never silently defaults to "pdf".
        """
        ext = os.path.splitext(path or "")[1].lower()
        if ext:
            for spec in self.all():
                if ext in spec.extensions:
                    return spec.source_type
        mime, _ = mimetypes.guess_type(path or "")
        if mime:
            for spec in self.all():
                if mime in spec.mime_types:
                    return spec.source_type
            major = mime.split("/", 1)[0]
            for spec in self.all():
                if any(m.startswith(f"{major}/") for m in spec.mime_types):
                    return spec.source_type
        return "unknown"

    def is_available(self, source_type: str) -> bool:
        spec = self.get(source_type)
        if spec is None:
            return False
        try:
            return bool(spec.is_available())
        except Exception:
            return False

    def remove_by_plugin(self, plugin_id: str) -> None:
        self._specs = {
            key: spec for key, spec in self._specs.items()
            if spec.plugin_id != plugin_id
        }
