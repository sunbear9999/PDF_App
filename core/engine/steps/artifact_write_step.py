"""ARTIFACT_WRITE — persist derived knowledge artifacts + edges from a workflow.

The single rules-compliant seam for an analysis mode to write knowledge
artifacts (deterministic or LLM-derived: abstractive summaries, relation edges,
claim threads) into the ``intelligence_artifacts`` / ``intelligence_artifact_edges``
store. Writes go through ArtifactService (never raw DB), are provenance-stamped
(``created_by``, ``confidence``), and are idempotent via ``stable_id`` plus the
``intelligence_analysis_runs`` cache (mode + params_hash + doc_hash).

Inputs (all resolved from workflow state):
  - ``artifacts``: JSON list of {ref, artifact_type, storage_ref_id, source_id,
    exact_text, metadata, confidence, parent_ref|parent_artifact_id, locator,
    section_path}. ``ref`` is a temp id edges can point at.
  - ``edges``: JSON list of {source_ref|source, target_ref|target, edge_type,
    confidence, metadata}.
  - ``mode``, ``params_hash``, ``doc_hash``: optional cache key; on a hit the
    step short-circuits and re-emits the cached summary.
  - ``created_by``: default "model".
"""
from __future__ import annotations

import json
from typing import Any, Dict, List

from core.engine.steps.base_step import BaseStep
from core.plugins.plugin_step_protocol import StepContext


class ArtifactWriteStep(BaseStep):
    step_type = "ARTIFACT_WRITE"
    label = "Write Knowledge Artifacts"
    category = "Intelligence"
    description = "Persist derived artifacts and edges to the knowledge base (idempotent, provenance-stamped)."
    input_schema = {
        "artifacts": {"type": "json", "label": "Artifacts"},
        "edges": {"type": "json", "label": "Edges"},
        "mode": {"type": "string", "label": "Mode (cache key)"},
        "params_hash": {"type": "string", "label": "Params hash"},
        "doc_hash": {"type": "string", "label": "Doc hash"},
        "created_by": {"type": "string", "label": "Created by"},
    }
    output_schema = {"written_artifacts": "integer", "written_edges": "integer"}

    def execute(self, context: StepContext, inputs: dict):
        service = getattr(context, "artifact_service", None)
        if not service:
            return self.build_result(json.dumps({"error": "artifact service unavailable"}))
        db = getattr(service, "db", None)
        created_by = str(inputs.get("created_by") or "model")

        mode = str(inputs.get("mode") or "")
        params_hash = str(inputs.get("params_hash") or "")
        doc_hash = str(inputs.get("doc_hash") or "")
        if db and mode and doc_hash:
            cached = db.get_analysis_run(mode, params_hash, doc_hash)
            if cached is not None:
                return self.build_result(json.dumps(cached.get("output") or {}))

        artifacts = _json_list(inputs.get("artifacts"))
        edges = _json_list(inputs.get("edges"))

        ref_to_id: Dict[str, str] = {}
        written_artifacts = 0
        for art in artifacts:
            if not isinstance(art, dict):
                continue
            parent = art.get("parent_artifact_id") or ref_to_id.get(str(art.get("parent_ref") or ""))
            artifact_id = service.create_artifact(
                source_id=art.get("source_id"),
                artifact_type=str(art.get("artifact_type") or "note"),
                storage_ref_type=str(art.get("storage_ref_type") or "analysis"),
                storage_ref_id=str(art.get("storage_ref_id") or art.get("ref") or ""),
                exact_text=str(art.get("exact_text") or art.get("text") or ""),
                source_path=art.get("source_path"),
                source_type=str(art.get("source_type") or ""),
                parent_artifact_id=parent,
                locator=art.get("locator") or {},
                section_path=art.get("section_path") or [],
                metadata=art.get("metadata") or {},
                confidence=_float(art.get("confidence")),
                created_by=created_by,
                extractor_name=str(art.get("extractor_name") or mode or "analysis_mode"),
                model_role=art.get("model_role"),
                model_name=art.get("model_name"),
            )
            if artifact_id:
                written_artifacts += 1
                ref = str(art.get("ref") or "")
                if ref:
                    ref_to_id[ref] = artifact_id

        written_edges = 0
        for edge in edges:
            if not isinstance(edge, dict):
                continue
            src = edge.get("source") or ref_to_id.get(str(edge.get("source_ref") or ""))
            tgt = edge.get("target") or ref_to_id.get(str(edge.get("target_ref") or ""))
            if not src or not tgt:
                continue
            edge_id = service.create_edge(
                src, tgt, str(edge.get("edge_type") or "relates_to"),
                confidence=_float(edge.get("confidence")),
                created_by=created_by,
                metadata=edge.get("metadata") or {},
            )
            if edge_id:
                written_edges += 1

        summary = {"written_artifacts": written_artifacts, "written_edges": written_edges}
        if db and mode and doc_hash:
            try:
                db.save_analysis_run(mode, params_hash, doc_hash, summary,
                                     source_id=(artifacts[0].get("source_id") if artifacts else None))
            except Exception:
                pass
        return self.build_result(json.dumps(summary), state_updates={"artifact_write_summary": summary})


def _json_list(value: Any) -> List[Any]:
    if isinstance(value, list):
        return value
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
            return parsed if isinstance(parsed, list) else ([parsed] if parsed else [])
        except Exception:
            return []
    if isinstance(value, dict):
        return [value]
    return []


def _float(value):
    try:
        return float(value) if value is not None else None
    except (TypeError, ValueError):
        return None
