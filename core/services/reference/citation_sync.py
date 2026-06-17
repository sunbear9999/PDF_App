from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Protocol, Sequence


@dataclass
class CitationSyncResult:
    ok: bool
    message: str
    synced_ids: list[str]


class CitationOutboundSyncAdapter(Protocol):
    """Boundary for plugins that can push project citation/PDF data outward."""

    adapter_id: str
    display_name: str

    def can_write(self) -> bool: ...

    def sync_pdfs(
        self,
        pdf_paths: Sequence[str],
        citations: Dict[str, Dict[str, Any]],
        *,
        collection_name: str = "",
    ) -> CitationSyncResult: ...
