"""Models for importing externally acquired files through Papyrus."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


@dataclass(frozen=True)
class SourceImportRequest:
    local_path: str
    source_type: str = ""
    title: str = ""
    metadata: Dict[str, Any] = field(default_factory=dict)
    provenance: Dict[str, Any] = field(default_factory=dict)
    citation: Dict[str, Any] = field(default_factory=dict)
    ocr_blocks: List[Dict[str, Any]] = field(default_factory=list)
    preferred_filename: str = ""


@dataclass(frozen=True)
class SourceImportResult:
    source_id: str
    path: str
    source_type: str
    checksum: str
    reused: bool = False
    citation_created: bool = False
    ocr_attached: bool = False
