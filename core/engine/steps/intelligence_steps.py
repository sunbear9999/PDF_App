"""Workflow steps for artifact-aware intelligence services."""
from __future__ import annotations

import json
from dataclasses import asdict, is_dataclass
from typing import Any, List

from core.engine.steps.base_step import BaseStep
from core.models.intelligence_models import RetrievalPlan, RetrievalResult
from core.plugins.plugin_step_protocol import StepContext


class BuildRetrievalPlanStep(BaseStep):
    step_type = "BUILD_RETRIEVAL_PLAN"
    label = "Build Retrieval Plan"
    category = "Intelligence"
    description = "Create an artifact-aware retrieval plan for a query."
    input_schema = {
        "query": {"type": "string", "label": "Query", "required": True},
        "allowed_source_ids": {"type": "array", "label": "Allowed Source IDs"},
        "artifact_types": {"type": "array", "label": "Artifact Types"},
        "max_results": {"type": "integer", "label": "Max Results"},
        "use_embeddings": {"type": "boolean", "label": "Use Vector Fallback"},
    }

    def execute(self, context: StepContext, inputs: dict):
        service = context.hybrid_retrieval_service
        query = str(inputs.get("query") or "")
        allowed = _list(inputs.get("allowed_source_ids"))
        artifact_types = _list(inputs.get("artifact_types"))
        max_results = int(inputs.get("max_results") or 10)
        use_embeddings = bool(inputs.get("use_embeddings", True))
        if service and hasattr(service, "build_plan"):
            plan = service.build_plan(
                query,
                allowed_source_ids=allowed,
                artifact_types=artifact_types or None,
                max_results=max_results,
                use_embeddings=use_embeddings,
            )
        else:
            plan = RetrievalPlan(
                query=query,
                allowed_source_ids=allowed,
                artifact_types=artifact_types,
                use_embeddings=use_embeddings,
                max_results=max_results,
            )
        return self.build_result(json.dumps(asdict(plan), ensure_ascii=False))


class ArtifactRetrieveStep(BaseStep):
    step_type = "ARTIFACT_RETRIEVE"
    label = "Artifact Retrieve"
    category = "Intelligence"
    description = "Retrieve structured artifacts and vector fallback results."
    input_schema = {
        "plan": {"type": "json", "label": "Retrieval Plan"},
        "query": {"type": "string", "label": "Query"},
        "allowed_source_ids": {"type": "array", "label": "Allowed Source IDs"},
        "max_results": {"type": "integer", "label": "Max Results"},
    }

    def execute(self, context: StepContext, inputs: dict):
        service = context.hybrid_retrieval_service
        if not service:
            return self.build_result("[]")
        plan = _plan(inputs.get("plan"))
        if plan is None:
            query = str(inputs.get("query") or "")
            if hasattr(service, "build_plan"):
                plan = service.build_plan(
                    query,
                    allowed_source_ids=_list(inputs.get("allowed_source_ids")),
                    max_results=int(inputs.get("max_results") or 10),
                )
            else:
                plan = RetrievalPlan(
                    query=query,
                    allowed_source_ids=_list(inputs.get("allowed_source_ids")),
                    max_results=int(inputs.get("max_results") or 10),
                )
        results = service.retrieve(plan)
        return self.build_result(json.dumps([_result_dict(item) for item in results], ensure_ascii=False))


class PackArtifactContextStep(BaseStep):
    step_type = "PACK_ARTIFACT_CONTEXT"
    label = "Pack Artifact Context"
    category = "Intelligence"
    description = "Pack retrieval results into compact citation-ready context."
    input_schema = {
        "query": {"type": "string", "label": "Query", "required": True},
        "results": {"type": "json", "label": "Retrieval Results", "required": True},
    }

    def execute(self, context: StepContext, inputs: dict):
        packer = context.context_packing_service
        if not packer:
            return self.build_result("")
        results = [_retrieval_result(item) for item in _json_list(inputs.get("results"))]
        bundle = packer.pack(str(inputs.get("query") or ""), results)
        return self.build_result(bundle.text, state_updates={"artifact_context_bundle": asdict(bundle)})


class SourceStageStep(BaseStep):
    step_type = "SOURCE_STAGE"
    label = "Source Stage"
    category = "Intelligence"
    description = "Request source intelligence processing through the scheduler."
    input_schema = {
        "source_id": {"type": "string", "label": "Source ID", "required": True},
        "stage": {"type": "string", "label": "Stage"},
        "action": {"type": "string", "label": "Action"},
    }

    def execute(self, context: StepContext, inputs: dict):
        service = context.source_intelligence_service
        source_id = str(inputs.get("source_id") or "")
        action = str(inputs.get("action") or "run_stage")
        stage = str(inputs.get("stage") or "")
        if not service:
            return self.build_result({"error": "Source intelligence service is unavailable."})
        if action == "run_missing":
            jobs = service.run_missing(source_id)
        elif action == "rerun_stale":
            jobs = service.rerun_stale(source_id)
        elif action == "delete_derived":
            jobs = service.delete_derived(source_id, stage or None)
        else:
            jobs = service.run_stage(source_id, stage)
        return self.build_result(str(jobs))


def _list(value: Any) -> List[str]:
    if value is None:
        return []
    if isinstance(value, list):
        return [str(item) for item in value if str(item).strip()]
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
            if isinstance(parsed, list):
                return _list(parsed)
        except Exception:
            pass
        return [part.strip() for part in value.split(",") if part.strip()]
    return [str(value)]


def _json_list(value: Any) -> List[Any]:
    if isinstance(value, list):
        return value
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
            return parsed if isinstance(parsed, list) else []
        except Exception:
            return []
    return []


def _plan(value: Any) -> RetrievalPlan | None:
    if isinstance(value, RetrievalPlan):
        return value
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except Exception:
            return None
    if isinstance(value, dict):
        fields = RetrievalPlan.__dataclass_fields__
        return RetrievalPlan(**{key: value.get(key) for key in fields if key in value})
    return None


def _retrieval_result(value: Any) -> RetrievalResult:
    if isinstance(value, RetrievalResult):
        return value
    if isinstance(value, dict):
        fields = RetrievalResult.__dataclass_fields__
        return RetrievalResult(**{key: value.get(key) for key in fields if key in value})
    return RetrievalResult(artifact_id="", artifact_type="", exact_text=str(value or ""))


def _result_dict(value: Any) -> dict:
    if is_dataclass(value):
        return asdict(value)
    if isinstance(value, dict):
        return value
    return {"text": str(value)}
