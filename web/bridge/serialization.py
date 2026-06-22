from __future__ import annotations

import dataclasses
import enum
import json
from pathlib import Path
from typing import Any


def json_safe(value: Any, *, depth: int = 0) -> Any:
    """Copy an EventBus payload into a JSON-only, path-safe representation."""
    if depth > 12:
        return None
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, enum.Enum):
        return value.name.lower()
    if isinstance(value, Path):
        return value.name
    if dataclasses.is_dataclass(value):
        return json_safe(dataclasses.asdict(value), depth=depth + 1)
    if isinstance(value, dict):
        return {str(k): json_safe(v, depth=depth + 1) for k, v in value.items() if str(k) not in {"doc", "runner", "blueprint"}}
    if isinstance(value, (list, tuple, set)):
        return [json_safe(v, depth=depth + 1) for v in value]
    if hasattr(value, "to_dict"):
        try:
            return json_safe(value.to_dict(), depth=depth + 1)
        except Exception:
            pass
    if hasattr(value, "__dict__"):
        return json_safe({k: v for k, v in vars(value).items() if not k.startswith("_")}, depth=depth + 1)
    try:
        json.dumps(value)
        return value
    except Exception:
        return str(value)
