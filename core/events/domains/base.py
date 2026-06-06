# core/events/domains/base.py
from dataclasses import dataclass
from typing import Any

@dataclass
class BasePayload:
    """Allows dataclasses to be accessed like dictionaries to prevent breaking legacy code."""
    
    def get(self, key: str, default: Any = None) -> Any:
        return getattr(self, key, default)

    def __getitem__(self, key: str) -> Any:
        if hasattr(self, key):
            return getattr(self, key)
        raise KeyError(key)