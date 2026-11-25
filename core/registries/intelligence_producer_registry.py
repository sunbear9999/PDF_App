"""Handler-id → producer lookup for intelligence stages (built-in and plugin)."""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from core.models.producer_contract import IntelligenceProducer, as_producer


class IntelligenceProducerRegistry:
    def __init__(self) -> None:
        self._producers: Dict[str, IntelligenceProducer] = {}
        self._owners: Dict[str, Optional[str]] = {}

    def register(self, handler_id: str, producer: Any, plugin_id: Optional[str] = None) -> str:
        if not handler_id:
            raise ValueError("A producer needs a handler_id.")
        self._producers[handler_id] = as_producer(handler_id, producer)
        self._owners[handler_id] = plugin_id
        return handler_id

    def unregister(self, handler_id: str) -> None:
        self._producers.pop(handler_id, None)
        self._owners.pop(handler_id, None)

    def get(self, handler_id: str) -> Optional[IntelligenceProducer]:
        return self._producers.get(handler_id)

    def has(self, handler_id: str) -> bool:
        return handler_id in self._producers

    def handler_ids(self) -> List[str]:
        return list(self._producers)

    def owned_by(self, plugin_id: str) -> List[str]:
        return [key for key, owner in self._owners.items() if owner == plugin_id]

    def remove_by_plugin(self, plugin_id: str) -> List[str]:
        removed = self.owned_by(plugin_id)
        for handler_id in removed:
            self.unregister(handler_id)
        return removed
