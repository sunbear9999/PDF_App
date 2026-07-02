"""Category extension contract for pack serialization."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable


@dataclass(frozen=True)
class PackCategoryHandler:
    """All operations required for one independently packaged category."""

    collect: Callable
    write: Callable
    preview: Callable
    apply: Callable
