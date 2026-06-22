"""
core/engine/steps/base_step.py

Abstract base that all built-in step classes inherit from.

Provides shared helpers so individual steps stay concise:
  - build_result()  — convenience constructor for ExecutionResult
  - bus_emit_event() — creates a "bus_emit" SystemEvent
  - prompt_trace_event() — creates a "prompt_trace" SystemEvent
"""
from __future__ import annotations

from abc import abstractmethod
from typing import Any, Dict, List, Optional

from core.engine.execution_result import ExecutionResult, SystemEvent
from core.plugins.plugin_step_protocol import CustomExecutionStep, StepContext


class BaseStep(CustomExecutionStep):
    """Shared helpers for all built-in workflow step classes."""

    # ----------------------------------------------------------------
    # Helpers for building ExecutionResult
    # ----------------------------------------------------------------

    def build_result(
        self,
        raw_value: Any = None,
        *,
        state_updates: Optional[Dict[str, Any]] = None,
        ui_payloads: Optional[List[Dict[str, Any]]] = None,
        system_events: Optional[List[SystemEvent]] = None,
    ) -> ExecutionResult:
        return ExecutionResult(
            raw_value=raw_value,
            state_updates=state_updates or {},
            ui_payloads=ui_payloads or [],
            system_events=system_events or [],
        )

    def bus_emit_event(
        self,
        signal: str,
        intent_or_event,
        payload_obj,
    ) -> SystemEvent:
        """Create a SystemEvent that makes the runner emit a bus signal."""
        return SystemEvent(
            event_type="bus_emit",
            payload={"signal": signal, "intent": intent_or_event, "payload": payload_obj},
        )

    def prompt_trace_event(self, trace_call_dict: Dict[str, Any]) -> SystemEvent:
        """Create a SystemEvent that appends a PromptTraceCall to the runner's trace."""
        return SystemEvent(event_type="prompt_trace", payload=trace_call_dict)

    @abstractmethod
    def execute(self, context: StepContext, inputs: dict) -> ExecutionResult:
        ...
