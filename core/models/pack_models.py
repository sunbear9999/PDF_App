"""
core/models/pack_models.py

Data models for the .ppack (Papyrus Pack) import/export format.
A .ppack file is a ZIP archive containing a manifest.json and category subdirectories.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List

CATEGORY_ORDER = [
    "themes",
    "blueprints",
    "prompts",
    "steps",
    "analysis_templates",
    "shortcuts",
    "layouts",
    "plugins",
    "plugin_configs",
    "plugin_data",
]

CATEGORY_DISPLAY_NAMES = {
    "themes": "Themes",
    "blueprints": "Blueprints",
    "prompts": "Prompts",
    "steps": "Workflow Steps",
    "analysis_templates": "Analysis Templates",
    "shortcuts": "Keyboard Shortcuts",
    "layouts": "Layouts",
    "plugins": "Plugins",
    "plugin_configs": "Plugin Configurations",
    "plugin_data": "Plugin Data",
}

PPACK_VERSION = "1.0"


@dataclass
class PackItem:
    """Represents a single exportable/importable item within a category."""
    item_id: str
    display_name: str
    category: str
    metadata: Dict = field(default_factory=dict)


@dataclass
class PackManifest:
    """Top-level manifest written to manifest.json inside the .ppack ZIP."""
    pack_id: str
    name: str
    description: str
    app_version: str
    created_at: str
    ppack_version: str = PPACK_VERSION
    categories: Dict[str, List[str]] = field(default_factory=dict)
    # {item_id: {"prompts": [...], "steps": [...]}} — filled by dependency analyzer
    dependencies: Dict[str, Dict[str, List[str]]] = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "pack_id": self.pack_id,
            "name": self.name,
            "description": self.description,
            "app_version": self.app_version,
            "created_at": self.created_at,
            "ppack_version": self.ppack_version,
            "categories": self.categories,
            "dependencies": self.dependencies,
        }

    @staticmethod
    def from_dict(d: dict) -> "PackManifest":
        return PackManifest(
            pack_id=d.get("pack_id", ""),
            name=d.get("name", "Unnamed Pack"),
            description=d.get("description", ""),
            app_version=d.get("app_version", ""),
            created_at=d.get("created_at", ""),
            ppack_version=d.get("ppack_version", PPACK_VERSION),
            categories=d.get("categories", {}),
            dependencies=d.get("dependencies", {}),
        )
