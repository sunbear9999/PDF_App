"""
core/engine/steps/user_input_step.py

Migrated from MasterActionRunner._run_user_input().
The QMutex and QWaitCondition remain on the runner and are forwarded to the step
via StepContext._pause_mutex, _wait_condition, and _user_response_holder.
"""
from __future__ import annotations

import json

from core.engine.steps.base_step import BaseStep
from core.engine.execution_result import SystemEvent
from core.plugins.plugin_step_protocol import StepContext


class UserInputStep(BaseStep):
    step_type = "USER_INPUT"
    label = "User Input"
    category = "Interaction"
    description = "Pause the workflow and collect a user response before continuing."
    input_schema = {}

    def execute(self, context: StepContext, inputs: dict):
        mutex = context._pause_mutex
        wait_cond = context._wait_condition
        response_holder = context._user_response_holder  # [value] single-element list

        if mutex is None or wait_cond is None or response_holder is None:
            return self.build_result("Error: USER_INPUT requires runner pause primitives in StepContext.")

        step = getattr(context, "_step_proxy", None)
        step_id = getattr(step, "step_id", "user_input")
        schema = getattr(step, "expected_inputs", inputs) if step else inputs

        # Signal the runner to emit user_input_requested (forwarded as system event)
        system_events = [
            SystemEvent(
                event_type="user_input_requested",
                payload={"step_id": step_id, "schema": schema},
            )
        ]

        # Block the QThread until submit_user_input() wakes us
        mutex.lock()
        wait_cond.wait(mutex)
        mutex.unlock()

        response = response_holder[0]
        response_holder[0] = None

        raw_value = json.dumps(response) if isinstance(response, dict) else str(response or "")
        return self.build_result(raw_value, system_events=system_events)
