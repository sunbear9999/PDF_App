from __future__ import annotations

from typing import Any, Dict, Sequence

from core.services.reference.citation_sync import CitationSyncResult


class ZoteroLocalReadOnlySyncAdapter:
    """Outbound-sync placeholder for Zotero's current local read-only API."""

    adapter_id = "zotero.local"
    display_name = "Zotero Local API"

    def can_write(self) -> bool:
        return False

    def sync_pdfs(
        self,
        pdf_paths: Sequence[str],
        citations: Dict[str, Dict[str, Any]],
        *,
        collection_name: str = "",
    ) -> CitationSyncResult:
        return CitationSyncResult(
            ok=False,
            message=(
                "Sync to Zotero is unavailable with the local API; "
                "metadata import/copy is available."
            ),
            synced_ids=[],
        )
