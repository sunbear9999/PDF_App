"""
core/help/models.py

Pure dataclasses for the help and tutorial subsystem.
No Qt, no Papyrus imports — safe to import anywhere.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum, auto
from typing import Optional


@dataclass
class HelpTopic:
    """A single searchable help article."""
    id: str                               # Namespaced: "core.analysis.run", "plugin.zotero.sync"
    title: str
    summary: str                          # One-line description shown in search results
    body: str                             # Markdown body for the full topic view
    category: str                         # Grouping label, e.g. "Analysis", "Workspace"
    keywords: list[str] = field(default_factory=list)
    related_topics: list[str] = field(default_factory=list)   # Other HelpTopic IDs
    tutorial_ids: list[str] = field(default_factory=list)     # Related TutorialDefinition IDs
    feature_id: str = ""                  # Maps to UITargetRegistry key for F1/What's This?
    version: str = "1.0"
    namespace: str = "core"              # "core" | "plugin" | "pack"
    plugin_id: str = ""                  # Owner plugin for cleanup; empty for built-ins


@dataclass
class TutorialStep:
    """One step in a declarative tutorial."""
    id: str
    target_id: str                        # Stable UITargetRegistry key (never a visible label)
    text: str                             # Instruction shown on the tutorial card
    before_actions: list[dict] = field(default_factory=list)  # Approved action dicts, never callables
    advance_condition: str = "next_button"   # "next_button" | "widget_clicked" | "signal"
    interaction_mode: str = "passive"        # "passive" | "click_only"
    optional: bool = False


@dataclass
class TutorialDefinition:
    """A complete, declaratively-defined interactive tutorial."""
    id: str                               # Namespaced: "core.tutorial.getting_started"
    title: str
    steps: list[TutorialStep] = field(default_factory=list)
    category: str = "General"
    prerequisites: list[str] = field(default_factory=list)   # Other TutorialDefinition IDs
    related_help_topics: list[str] = field(default_factory=list)
    version: str = "1.0"
    estimated_minutes: int = 2
    namespace: str = "core"
    plugin_id: str = ""                  # Owner plugin for cleanup; empty for built-ins


class TutorialState(Enum):
    """States of the tutorial execution state machine."""
    IDLE = auto()
    PREPARING = auto()
    WAITING_FOR_TARGET = auto()
    DISPLAYING_STEP = auto()
    WAITING_FOR_INTERACTION = auto()
    ADVANCING = auto()
    COMPLETED = auto()
    CANCELLED = auto()
    FAILED = auto()
