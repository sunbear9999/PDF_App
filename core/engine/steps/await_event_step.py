"""
core/engine/steps/await_event_step.py

AWAIT_EVENT — pauses the workflow runner thread until a named EventBus signal fires
or a timeout elapses.  Uses QMutex + QWaitCondition for thread-safe blocking.
"""
from __future__ import annotations

from PySide6.QtCore import QMutex, QWaitCondition

from core.engine.steps.base_step import BaseStep
from core.plugins.plugin_step_protocol import StepContext


class AwaitEventStep(BaseStep):
    step_type = "AWAIT_EVENT"
    label = "Await Event"
    category = "Interaction"
    description = "Pause execution until a named EventBus signal fires (or timeout)."
    input_schema = {
        "signal_name": {
            "type": "string",
            "label": "EventBus signal name (e.g. 'highlight_created')",
            "required": True,
        },
        "timeout_ms": {
            "type": "integer",
            "label": "Timeout (ms, default 30000)",
        },
    }

    def execute(self, context: StepContext, inputs: dict):
        from core.events.event_bus import EventBus

        signal_name = str(inputs.get("signal_name", ""))
        timeout_ms = int(inputs.get("timeout_ms") or 30_000)

        if not signal_name:
            return self.build_result({"error": "signal_name is required", "timed_out": True})

        bus = EventBus.get_instance()
        signal = getattr(bus, signal_name, None)
        if signal is None:
            return self.build_result({"error": f"Unknown signal: {signal_name}", "timed_out": True})

        mutex = QMutex()
        wc = QWaitCondition()
        result_holder: list = [None]

        def _slot(event_or_intent=None, payload=None):
            mutex.lock()
            result_holder[0] = {
                "event": str(event_or_intent) if event_or_intent is not None else None,
                "payload": str(payload) if payload is not None else None,
            }
            wc.wakeAll()
            mutex.unlock()

        # Connect one-shot; disconnect in finally
        try:
            signal.connect(_slot)
            mutex.lock()
            fired = wc.wait(mutex, timeout_ms)
            mutex.unlock()
        finally:
            try:
                signal.disconnect(_slot)
            except Exception:
                pass

        if not fired or result_holder[0] is None:
            return self.build_result({"timed_out": True, "signal": signal_name})

        return self.build_result({**result_holder[0], "timed_out": False, "signal": signal_name})
