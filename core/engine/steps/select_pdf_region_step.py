from __future__ import annotations

import uuid

from PySide6.QtCore import QMutex, QWaitCondition

from core.engine.steps.base_step import BaseStep
from core.events.domains.data_dock_events import DataDockIntent, DataDockPayload
from core.events.event_bus import EventBus
from core.plugins.plugin_step_protocol import StepContext


class SelectPdfRegionStep(BaseStep):
    step_type = "SELECT_PDF_REGION"
    label = "Select PDF Region"
    category = "Interaction"
    description = "Pause for the user to draw a rectangle in the open PDF viewer."
    input_schema = {
        "timeout_ms": {"type": "integer", "label": "Timeout (ms)", "default": 300000},
    }
    output_schema = {"region": {"type": "object", "label": "PDF Region"}}

    def execute(self, context: StepContext, inputs: dict):
        bus = EventBus.get_instance()
        request_id = f"workflow_pdf_region_{uuid.uuid4()}"
        mutex, condition, holder = QMutex(), QWaitCondition(), [None]

        def receive(received_id, region):
            if received_id != request_id:
                return
            mutex.lock()
            holder[0] = region
            condition.wakeAll()
            mutex.unlock()

        bus.pdf_data_selection_ready.connect(receive)
        fired = False
        try:
            bus.data_dock_action_requested.emit(
                DataDockIntent.SELECT_PDF_REGION, DataDockPayload(request_id=request_id),
            )
            remaining = int(inputs.get("timeout_ms") or 300000)
            while remaining > 0 and not fired and not context.is_aborted():
                interval = min(500, remaining)
                mutex.lock()
                fired = condition.wait(mutex, interval)
                mutex.unlock()
                remaining -= interval
        finally:
            try:
                bus.pdf_data_selection_ready.disconnect(receive)
            except (RuntimeError, TypeError):
                pass
        if not fired or holder[0] is None:
            bus.data_dock_action_requested.emit(
                DataDockIntent.CANCEL_PDF_REGION_SELECTION, DataDockPayload(request_id=request_id),
            )
            return self.build_result({"cancelled": True})
        return self.build_result(holder[0].to_dict())
