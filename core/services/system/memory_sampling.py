"""System and process memory readings (psutil), cheap enough to run every tick."""
from __future__ import annotations

from typing import Dict

import psutil

# Ollama's server holds its models; they are reported as model consumers from
# /api/ps, so the process is excluded from "background workers" to avoid
# counting the same memory twice.
_MODEL_SERVER_NAMES = ("ollama",)
_SELF = psutil.Process()


def sample_system() -> Dict[str, int]:
    memory = psutil.virtual_memory()
    swap = psutil.swap_memory()
    app_rss, children_rss = _process_tree_rss()
    return {
        "total": int(memory.total), "available": int(memory.available),
        "used": int(memory.total - memory.available),
        "swap_total": int(swap.total), "swap_used": int(swap.used),
        "app_rss": app_rss, "children_rss": children_rss,
    }


def _process_tree_rss() -> tuple[int, int]:
    me = _SELF
    try:
        app = int(me.memory_info().rss)
    except psutil.Error:
        return 0, 0
    children = 0
    for child in me.children(recursive=True):
        try:
            if any(name in child.name().lower() for name in _MODEL_SERVER_NAMES):
                continue
            children += int(child.memory_info().rss)
        except psutil.Error:
            continue
    return app, children
