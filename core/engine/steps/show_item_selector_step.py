"""
core/engine/steps/show_item_selector_step.py

Pauses the runner and shows an interactive item-selection dialog in the GUI.
The user reviews a list of items (JSON array from state), checks the ones they
want, and clicks the action button. The step then resumes and returns the
selected items as a JSON array.

Uses the same QMutex/QWaitCondition pause pattern as USER_INPUT so the runner
thread blocks cleanly while the GUI awaits user confirmation.
"""
from __future__ import annotations

import json

from core.engine.steps.base_step import BaseStep
from core.engine.execution_result import SystemEvent
from core.plugins.plugin_step_protocol import StepContext


class ShowItemSelectorStep(BaseStep):
    step_type = "SHOW_ITEM_SELECTOR"
    label = "Show Item Selector"
    category = "Interaction"
    description = (
        "Pause the workflow and show a checkable list dialog. "
        "The user selects items and confirms; selected items are returned as the step result."
    )
    input_schema = {
        "items": {"type": "array", "label": "Items (JSON array from state)", "required": True},
        "title": {"type": "string", "label": "Dialog Title"},
        "action_label": {"type": "string", "label": "Action Button Label (e.g. 'Save Claims')"},
        "item_display_key": {"type": "string", "label": "Key to display per item (e.g. 'claim')"},
        "item_subtitle_key": {"type": "string", "label": "Secondary display key (e.g. 'type')"},
    }

    def execute(self, context: StepContext, inputs: dict):
        mutex = context._pause_mutex
        wait_cond = context._wait_condition
        response_holder = context._user_response_holder

        if mutex is None or wait_cond is None or response_holder is None:
            return self.build_result("Error: SHOW_ITEM_SELECTOR requires runner pause primitives.")

        items = inputs.get("items") or []
        if isinstance(items, str):
            try:
                items = json.loads(items)
            except Exception:
                items = [{"text": items}]

        step = getattr(context, "_step_proxy", None)
        step_id = getattr(step, "step_id", "item_selector")

        system_events = [
            SystemEvent(
                event_type="user_input_requested",
                payload={
                    "step_id": step_id,
                    "schema": {
                        "_input_type": "item_selector",
                        "_items": items,
                        "_title": inputs.get("title", "Select Items"),
                        "_action_label": inputs.get("action_label", "Confirm"),
                        "_display_key": inputs.get("item_display_key", ""),
                        "_subtitle_key": inputs.get("item_subtitle_key", ""),
                    },
                },
            )
        ]

        mutex.lock()
        wait_cond.wait(mutex)
        mutex.unlock()

        response = response_holder[0]
        response_holder[0] = None

        selected = response if isinstance(response, (list, dict)) else []
        return self.build_result(json.dumps(selected), system_events=system_events)
