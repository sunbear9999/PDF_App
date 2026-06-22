"""
core/engine/workflow_step_api.py

Minimal, sandboxed API handle injected into PYTHON_SCRIPT step execution environments.

This object — available as ``workflow_api`` inside user script strings — intentionally
exposes only what a data-transform script needs: read the current state and queue a
system event.  It does NOT expose PapyrusAPI, database connections, or Qt objects.
"""
from __future__ import annotations

from typing import Any, Callable, Dict


class WorkflowStepAPI:
    """Sandbox handle passed to PYTHON_SCRIPT steps as ``workflow_api``.

    Example usage inside a PYTHON_SCRIPT step's script string::

        keywords = workflow_api.get_state("rag_results", [])
        filtered = [k for k in keywords if len(k) > 3]
        workflow_api.emit_event("debug_log", {"msg": f"kept {len(filtered)} keywords"})
        result = filtered
    """

    def __init__(self, state_snapshot: Dict[str, Any], emit_fn: Callable[[str, Dict], None]) -> None:
        self._state = state_snapshot
        self._emit_fn = emit_fn

    # ------------------------------------------------------------------
    # State access (read-only view of the runner state at step start)
    # ------------------------------------------------------------------

    def get_state(self, key: str, default: Any = None) -> Any:
        """Return a value from the current workflow state."""
        return self._state.get(key, default)

    def get_all_state(self) -> Dict[str, Any]:
        """Return a shallow copy of the full state dict."""
        return dict(self._state)

    # ------------------------------------------------------------------
    # Structured side-effects
    # ------------------------------------------------------------------

    def emit_event(self, event_type: str, payload: Dict[str, Any]) -> None:
        """Queue a system event to be processed by the runner after this step.

        Use this instead of directly importing EventBus.  Events are processed
        in order after state is updated, ensuring thread-safe sequencing.
        """
        self._emit_fn(event_type, payload)

    def log(self, message: str) -> None:
        """Convenience: emit a debug_log system event."""
        self._emit_fn("debug_log", {"msg": message})
