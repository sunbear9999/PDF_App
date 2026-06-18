"""
core/help/content_loader.py

Loads built-in help topics and tutorials from the content/ JSON files.
Follows the same pattern as core/analysis_templates.json loading.
Missing or malformed files are logged and skipped — the app always starts.
"""
from __future__ import annotations

import json
import logging
import os
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from core.help.help_registry import HelpRegistry
    from core.help.tutorial_registry import TutorialRegistry

log = logging.getLogger(__name__)

_CONTENT_DIR = os.path.join(os.path.dirname(__file__), "content")


def load_builtin_topics(registry: "HelpRegistry") -> None:
    """Load help_topics.json from the content directory into registry."""
    path = os.path.join(_CONTENT_DIR, "help_topics.json")
    _load_topics_from_file(path, registry, owner="")


def load_builtin_tutorials(registry: "TutorialRegistry") -> None:
    """Load tutorials.json from the content directory into registry."""
    path = os.path.join(_CONTENT_DIR, "tutorials.json")
    _load_tutorials_from_file(path, registry, owner="")


def load_topics_from_file(path: str, registry: "HelpRegistry", owner: str = "") -> int:
    """Load help topics from an arbitrary JSON file (for pack/plugin contributions). Returns count loaded."""
    return _load_topics_from_file(path, registry, owner)


def load_tutorials_from_file(path: str, registry: "TutorialRegistry", owner: str = "") -> int:
    """Load tutorials from an arbitrary JSON file (for pack/plugin contributions). Returns count loaded."""
    return _load_tutorials_from_file(path, registry, owner)


# ------------------------------------------------------------------
# Internal helpers
# ------------------------------------------------------------------

def _load_topics_from_file(path: str, registry: "HelpRegistry", owner: str) -> int:
    from core.help.models import HelpTopic
    if not os.path.exists(path):
        log.debug("HelpLoader: topics file not found: %s", path)
        return 0
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        count = 0
        for item in data:
            try:
                if owner:
                    item = dict(item)
                    item.setdefault("plugin_id", owner)
                    item.setdefault("namespace", "plugin")
                topic = HelpTopic(**item)
                registry.register(topic)
                count += 1
            except Exception:
                log.exception("HelpLoader: skipping malformed topic entry: %r", item.get("id", "?"))
        log.debug("HelpLoader: loaded %d topics from %s", count, path)
        return count
    except Exception:
        log.exception("HelpLoader: failed to load topics from %s", path)
        return 0


def _load_tutorials_from_file(path: str, registry: "TutorialRegistry", owner: str) -> int:
    from core.help.models import TutorialDefinition, TutorialStep
    if not os.path.exists(path):
        log.debug("HelpLoader: tutorials file not found: %s", path)
        return 0
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        count = 0
        for item in data:
            try:
                item = dict(item)
                steps_raw = item.pop("steps", [])
                steps = [TutorialStep(**s) for s in steps_raw]
                if owner:
                    item.setdefault("plugin_id", owner)
                    item.setdefault("namespace", "plugin")
                tutorial = TutorialDefinition(**item, steps=steps)
                registry.register(tutorial)
                count += 1
            except Exception:
                log.exception("HelpLoader: skipping malformed tutorial entry: %r", item.get("id", "?"))
        log.debug("HelpLoader: loaded %d tutorials from %s", count, path)
        return count
    except Exception:
        log.exception("HelpLoader: failed to load tutorials from %s", path)
        return 0
