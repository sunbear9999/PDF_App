"""
core/engine/steps/dispatch_event_step.py

DISPATCH_EVENT — enables a background workflow step to fire any EventBus signal.
Useful for triggering workspace layouts, switching tabs, etc. from within a blueprint.
"""
from __future__ import annotations

from core.engine.steps.base_step import BaseStep
from core.engine.execution_result import SystemEvent
from core.plugins.plugin_step_protocol import StepContext
from core.utils.state_resolver import StateResolver


class DispatchEventStep(BaseStep):
    step_type = "DISPATCH_EVENT"
    label = "Dispatch Event"
    category = "Interaction"
    description = "Emit a named EventBus signal from within a workflow step."
    input_schema = {
        "signal_name": {
            "type": "string",
            "label": "EventBus signal name (e.g. 'workspace_action_requested')",
            "required": True,
        },
        "intent": {
            "type": "string",
            "label": "Intent or Event enum value (string key)",
        },
        "payload": {
            "type": "object",
            "label": "Payload dict (supports {variable} substitution)",
        },
    }

    def execute(self, context: StepContext, inputs: dict):
        signal_name = str(inputs.get("signal_name", ""))
        if not signal_name:
            return self.build_result({"error": "signal_name is required"})

        # Resolve any {variable} references in the payload dict
        pm = context.prompt_manager
        raw_payload = inputs.get("payload") or {}
        if isinstance(raw_payload, dict):
            resolved_payload = {
                k: StateResolver.resolve_val(v, context.state, pm)
                for k, v in raw_payload.items()
            }
        else:
            resolved_payload = raw_payload

        # Resolve intent string → enum value via EventBus signal contract
        intent_str = inputs.get("intent")
        resolved_intent = self._resolve_intent(signal_name, intent_str)

        # Build payload dataclass if possible
        payload_obj = self._build_payload(signal_name, resolved_payload)

        system_event = self.bus_emit_event(
            signal_name,
            resolved_intent,
            payload_obj if payload_obj is not None else resolved_payload,
        )

        return self.build_result(
            {"dispatched": signal_name, "intent": str(resolved_intent)},
            system_events=[system_event],
        )

    def _resolve_intent(self, signal_name: str, intent_str: str | None):
        """Try to resolve an intent string to the correct enum value."""
        if intent_str is None:
            return None
        try:
            from core.events.event_payloads import SIGNAL_CONTRACTS
            contract = SIGNAL_CONTRACTS.get(signal_name)
            if contract:
                intent_cls = contract[0]
                return getattr(intent_cls, intent_str, intent_str)
        except Exception:
            pass
        return intent_str

    def _build_payload(self, signal_name: str, payload_dict: dict):
        """Try to build a typed payload dataclass for the signal."""
        try:
            from core.events.event_payloads import SIGNAL_CONTRACTS
            contract = SIGNAL_CONTRACTS.get(signal_name)
            if contract and len(contract) > 1:
                payload_cls = contract[1]
                # Only pass fields that the dataclass accepts
                import dataclasses
                if dataclasses.is_dataclass(payload_cls):
                    valid_keys = {f.name for f in dataclasses.fields(payload_cls)}
                    filtered = {k: v for k, v in payload_dict.items() if k in valid_keys}
                    return payload_cls(**filtered)
        except Exception:
            pass
        return None
