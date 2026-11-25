"""Everything that holds significant memory registers here (rules.md §6)."""
from __future__ import annotations

import threading
from typing import Dict, List, Optional

from core.models.memory_models import MemoryConsumer


class MemoryConsumerRegistry:
    def __init__(self) -> None:
        self._items: Dict[str, MemoryConsumer] = {}
        self._lock = threading.Lock()

    def register(self, consumer: MemoryConsumer) -> None:
        with self._lock:
            self._items[consumer.id] = consumer

    def unregister(self, consumer_id: str) -> None:
        with self._lock:
            self._items.pop(consumer_id, None)

    def remove_by_plugin(self, plugin_id: str) -> int:
        with self._lock:
            doomed = [key for key, item in self._items.items() if item.plugin_id == plugin_id]
            for key in doomed:
                del self._items[key]
        return len(doomed)

    def get(self, consumer_id: str) -> Optional[MemoryConsumer]:
        with self._lock:
            return self._items.get(consumer_id)

    def all(self) -> List[MemoryConsumer]:
        with self._lock:
            return list(self._items.values())
