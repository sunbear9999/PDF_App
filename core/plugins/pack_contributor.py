"""
core/plugins/pack_contributor.py

Protocol and registry for plugin-contributed importable/exportable data.
Plugins register a PackContributor via api.register_pack_contributor(contributor).
"""
from __future__ import annotations

from typing import Dict, List, Protocol, runtime_checkable

from core.models.pack_models import PackItem


@runtime_checkable
class PackContributor(Protocol):
    """
    Protocol that plugins implement to expose custom data to the pack system.

    contributor_id must be globally unique (recommend using plugin_id).
    The display_name is shown in the Import/Export UI under "Plugin Data".
    """
    contributor_id: str
    display_name: str

    def get_exportable_items(self) -> List[PackItem]:
        """Return all items this plugin can export. Called to populate the export tree."""
        ...

    def export_items(self, item_ids: List[str]) -> dict:
        """Serialize the selected items into a JSON-safe dict for writing into the .ppack."""
        ...

    def preview_items(self, data: dict) -> List[PackItem]:
        """Parse raw data from a .ppack ZIP and return PackItem descriptors for the preview tree."""
        ...

    def import_items(self, data: dict, item_ids: List[str]) -> bool:
        """Apply imported data for the given item_ids. Return True on success."""
        ...


class PackContributorRegistry:
    """
    Simple registry mapping plugin_id → list[PackContributor].
    Cleaned up automatically when a plugin is unloaded.
    """

    def __init__(self) -> None:
        self._by_plugin: Dict[str, List[PackContributor]] = {}

    def register(self, contributor: PackContributor, plugin_id: str) -> None:
        self._by_plugin.setdefault(plugin_id, []).append(contributor)

    def remove_plugin(self, plugin_id: str) -> None:
        self._by_plugin.pop(plugin_id, None)

    def get_all(self) -> List[PackContributor]:
        result: List[PackContributor] = []
        for contributors in self._by_plugin.values():
            result.extend(contributors)
        return result
