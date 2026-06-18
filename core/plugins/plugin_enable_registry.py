"""
core/plugins/plugin_enable_registry.py

Persists which plugins are disabled across app restarts.
Disabled plugin IDs are stored in ~/.papyrus/disabled_plugins.json.
"""
from __future__ import annotations

import json
import os
import threading
from typing import Set


class PluginEnableRegistry:
    """Singleton that tracks which plugins have been disabled by the user."""

    _instance: "PluginEnableRegistry | None" = None
    _lock = threading.Lock()

    def __new__(cls):
        with cls._lock:
            if cls._instance is None:
                inst = super().__new__(cls)
                inst._disabled: Set[str] = set()
                inst._path: str = ""
                cls._instance = inst
        return cls._instance

    @classmethod
    def get_instance(cls) -> "PluginEnableRegistry":
        return cls()

    def init(self, user_data_dir: str) -> None:
        self._path = os.path.join(user_data_dir, "disabled_plugins.json")
        self._load()

    def _load(self) -> None:
        if not self._path or not os.path.isfile(self._path):
            return
        try:
            with open(self._path, "r", encoding="utf-8") as fh:
                data = json.load(fh)
            if isinstance(data, list):
                self._disabled = set(str(x) for x in data)
        except Exception:
            pass

    def _save(self) -> None:
        if not self._path:
            return
        try:
            os.makedirs(os.path.dirname(self._path), exist_ok=True)
            with open(self._path, "w", encoding="utf-8") as fh:
                json.dump(sorted(self._disabled), fh, indent=2)
        except Exception:
            pass

    def is_disabled(self, plugin_id: str) -> bool:
        return plugin_id in self._disabled

    def disabled_ids(self) -> Set[str]:
        return set(self._disabled)

    def disable(self, plugin_id: str) -> None:
        self._disabled.add(plugin_id)
        self._save()

    def enable(self, plugin_id: str) -> None:
        self._disabled.discard(plugin_id)
        self._save()
