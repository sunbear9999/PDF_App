"""Producer contract for intelligence stages.

A producer turns one source's prerequisites into published artifacts for one
stage. The scheduler owns queueing, accounting and status; producers own the
work. Producers never touch widgets or call generative providers, check
``context.cancelled()`` between batches, and publish replacements before
retiring old outputs so an interrupted run never leaves a source emptier.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Protocol, runtime_checkable

from core.models.producer_models import ProducerIdentity, deterministic


class ProducerOutcome:
    COMPLETE = "complete"     # ran; zero outputs is a valid result (e.g. no dates)
    BLOCKED = "blocked"       # a prerequisite (transcript, model, parse) is missing
    CANCELLED = "cancelled"


class ProducerBlocked(RuntimeError):
    """Raised when a stage cannot run yet; recorded as ``blocked``, not failed.

    ``reason`` is a stable code the GUI can act on (e.g. ``needs_ocr``) and
    ``details`` carries what it needs to offer the fix.
    """

    def __init__(self, message: str = "", *, reason: str = "", details=None):
        super().__init__(message)
        self.reason = reason
        self.details = dict(details or {})


class ProducerCancelled(RuntimeError):
    """Raised when cancellation is observed; recorded as ``cancelled``."""


@dataclass
class ProducerContext:
    source: Dict[str, Any]
    source_id: str
    stage: str
    category: str
    producer_version: str = "1.0"
    settings: Dict[str, Any] = field(default_factory=dict)
    cancel_check: Callable[[], bool] = lambda: False

    @property
    def path(self) -> str:
        return self.source.get("path") or self.source_id

    def cancelled(self) -> bool:
        try:
            return bool(self.cancel_check())
        except Exception:
            return False

    def raise_if_cancelled(self) -> None:
        if self.cancelled():
            raise ProducerCancelled("Cancelled")


@dataclass
class ProducerResult:
    output_ids: List[str] = field(default_factory=list)
    identity: Optional[ProducerIdentity] = None
    diagnostics: Dict[str, Any] = field(default_factory=dict)


@runtime_checkable
class IntelligenceProducer(Protocol):
    producer_id: str

    def identity(self, context: ProducerContext) -> ProducerIdentity: ...

    def run(self, context: ProducerContext) -> ProducerResult: ...


class CallableProducer:
    """Adapter for plugin producers given as ``callback(context) -> ids``."""

    def __init__(self, producer_id: str, callback: Callable[[ProducerContext], Any]):
        self.producer_id = producer_id
        self._callback = callback

    def identity(self, context: ProducerContext) -> ProducerIdentity:
        return deterministic(self.producer_id, context.producer_version)

    def run(self, context: ProducerContext) -> ProducerResult:
        output = self._callback(context)
        if isinstance(output, ProducerResult):
            return output
        return ProducerResult(output_ids=[str(item) for item in output or [] if item])


def as_producer(producer_id: str, candidate: Any) -> IntelligenceProducer:
    if isinstance(candidate, IntelligenceProducer):
        return candidate
    if callable(candidate):
        return CallableProducer(producer_id, candidate)
    raise TypeError(
        f"Producer '{producer_id}' must implement run()/identity() or be callable."
    )
