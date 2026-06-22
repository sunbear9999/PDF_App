"""
core/engine/execution_result.py

Structured return contract for every workflow step execution.

All CustomExecutionStep.execute() implementations should return an ExecutionResult.
The MasterActionRunner coerces plain dicts / scalars via _coerce_result() so that
legacy plugin steps continue to work without modification during the migration period.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List


@dataclass
class SystemEvent:
    """A side-effect the runner should process after a step completes.

    event_type values recognised by MasterActionRunner._process_system_events():
        "prompt_trace"        — append PromptTraceCall to self.prompt_trace
        "bus_emit"            — emit a signal on EventBus; payload: {signal, intent, data}
        "save_chat_message"   — persist output to project DB
        "user_input_requested"— emit runner.user_input_requested signal
    """
    event_type: str
    payload: Dict[str, Any] = field(default_factory=dict)


@dataclass
class ExecutionResult:
    """Structured return from every step execution.

    Attributes
    ----------
    raw_value:
        The primary output stored at ``state[step.output_key]``.  May be any
        JSON-serialisable type — str, dict, list, int, bool, or None.
    state_updates:
        Additional key→value pairs merged into the runner state *after*
        raw_value is assigned.  Use for steps that produce multiple named
        outputs (e.g. ANALYSIS_CONTRACT sets several analysis_* keys).
    ui_payloads:
        Pre-built list of payload dicts passed directly to BlueprintUIRouter.
        When non-empty the router routes these without calling build_ui_payloads().
        Each dict must have at minimum a ``"type"`` key understood by BaseTab.receive_ai_payload().
    system_events:
        Ordered list of side-effects fired by the runner after state is updated.
        Steps should not fire bus signals directly — use system_events instead
        so the runner controls sequencing and thread safety.
    """
    raw_value: Any = None
    state_updates: Dict[str, Any] = field(default_factory=dict)
    ui_payloads: List[Dict[str, Any]] = field(default_factory=list)
    system_events: List[SystemEvent] = field(default_factory=list)
