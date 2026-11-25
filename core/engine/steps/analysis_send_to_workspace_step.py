"""
core/engine/steps/analysis_send_to_workspace_step.py

Migrated from MasterActionRunner._run_analysis_send_to_workspace().
"""
from __future__ import annotations

from core.engine.steps.base_step import BaseStep
from core.engine.execution_result import SystemEvent
from core.plugins.plugin_step_protocol import StepContext


class AnalysisSendToWorkspaceStep(BaseStep):
    step_type = "ANALYSIS_SEND_TO_WORKSPACE"
    label = "Send Analysis to Workspace"
    category = "Analysis"
    description = "Push a completed analysis result into the workspace graph."
    input_schema = {
        "result": {"type": "object", "label": "Analysis Result"},
        "workspace_id": {"type": "integer", "label": "Workspace ID"},
    }

    def execute(self, context: StepContext, inputs: dict):
        from core.engine.execution_result import SystemEvent
        from core.events.domains.analysis_events import AnalysisEvent, AnalysisPayload

        result = inputs.get("result") or context.state.get("analysis_result") or {}
        workspace_id = int(
            inputs.get("workspace_id")
            or context.state.get("analysis_workspace_id")
            or 1
        )
        graph = {
            "entities": list(result.get("entities") or []),
            "relations": list(result.get("relations") or []),
            "template_title": result.get("template_title") or result.get("title"),
            "mode_id": result.get("mode_id"),
            "focus": result.get("focus"),
        }
        summary = {
            "entity_count": len(graph["entities"]),
            "relation_count": len(graph["relations"]),
            "workspace_id": workspace_id,
            "queued_incrementally": True,
        }

        system_events = [
            self.bus_emit_event(
                "analysis_result_changed",
                AnalysisEvent.SENT_TO_WORKSPACE,
                AnalysisPayload(
                    doc_path=result.get("doc_path"),
                    template_id=result.get("template_id"),
                    run_id=result.get("run_id"),
                    workspace_id=workspace_id,
                    template={},
                    result=summary if isinstance(summary, dict) else {"value": summary},
                ),
            ),
            SystemEvent(
                event_type="bus_emit",
                payload={
                    "signal": "workspace_action_requested",
                    "intent": "IMPORT_GRAPH",
                    "data": {"graph": graph, "workspace_id": workspace_id},
                },
            ),
        ]

        return self.build_result(summary, system_events=system_events)
