"""
core/plugins/plugin_config.py

Thread-safe, JSON-backed per-plugin key-value store.
Values are persisted to ~/.papyrus/plugin_configs/<plugin_id>.json.
"""
from __future__ import annotations

import json
import os
import threading
from typing import Any, Dict


class PluginConfig:
    def __init__(self, plugin_id: str, base_dir: str) -> None:
        self._plugin_id = plugin_id
        config_dir = os.path.join(base_dir, "plugin_configs")
        os.makedirs(config_dir, exist_ok=True)
        self._path = os.path.join(config_dir, f"{plugin_id}.json")
        self._lock = threading.Lock()
        self._data: Dict[str, Any] = self._load()

    def _load(self) -> Dict[str, Any]:
        if not os.path.exists(self._path):
            return {}
        try:
            with open(self._path, "r", encoding="utf-8") as f:
                return json.load(f)
        except (json.JSONDecodeError, OSError):
            return {}

    def _save(self) -> None:
        try:
            with open(self._path, "w", encoding="utf-8") as f:
                json.dump(self._data, f, indent=2, ensure_ascii=False)
        except OSError as exc:
            print(f"[PluginConfig] Failed to save config for '{self._plugin_id}': {exc}")

    def get(self, key: str, default: Any = None) -> Any:
        with self._lock:
            return self._data.get(key, default)

    def set(self, key: str, value: Any) -> None:
        with self._lock:
            self._data[key] = value
            self._save()

    def get_all(self) -> Dict[str, Any]:
        with self._lock:
            return dict(self._data)

    def reset(self) -> None:
        with self._lock:
            self._data = {}
            self._save()
