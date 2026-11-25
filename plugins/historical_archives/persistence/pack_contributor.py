"""Portable .ppack contribution for saved historical searches."""
from __future__ import annotations

from core.models.pack_models import PackItem

from ..models import ArchiveQuery


class HistoricalSearchPackContributor:
    contributor_id = "historical_archives"
    display_name = "Historical Archives Saved Searches"

    def __init__(self, repository) -> None:
        self._repository = repository

    def get_exportable_items(self):
        try:
            searches = self._repository.list_searches()
        except RuntimeError:
            searches = []
        return [
            PackItem(
                item_id=item["id"],
                display_name=item["name"],
                category="plugin_data",
                metadata={"result_count": item["result_count"]},
            )
            for item in searches
        ]

    def export_items(self, item_ids):
        selected = set(item_ids)
        try:
            searches = self._repository.list_searches()
        except RuntimeError:
            searches = []
        return {
            "version": 1,
            "searches": [
                {
                    "id": item["id"],
                    "name": item["name"],
                    "query": item["query"].to_dict(),
                }
                for item in searches
                if item["id"] in selected
            ],
        }

    def preview_items(self, data):
        return [
            PackItem(
                item_id=str(item.get("id", "")),
                display_name=str(item.get("name", "Historical search")),
                category="plugin_data",
            )
            for item in data.get("searches", [])
            if isinstance(item, dict) and item.get("id")
        ]

    def import_items(self, data, item_ids):
        selected = set(item_ids)
        try:
            for item in data.get("searches", []):
                if not isinstance(item, dict) or item.get("id") not in selected:
                    continue
                query = ArchiveQuery.from_dict(item.get("query", {}))
                self._repository.save_search(str(item.get("name", "")), query)
            return True
        except (RuntimeError, TypeError, ValueError):
            return False
