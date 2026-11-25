"""Stage fingerprints: what must change for a completed stage to re-run.

A stage re-runs only when its own inputs change: the source file, the
*declared* relevant settings (``StageSpec.settings_keys`` plus that stage's
override), the content/review state of its dependencies' outputs, its
producer version, or the identity of the model that would run. Run IDs and
unrelated project settings are excluded, so unchanged reruns are no-ops.
"""
from __future__ import annotations

import hashlib
import json
from typing import Any, Dict, Optional

from core.models.intelligence_models import ModelRole, ProcessingStage
from core.models.producer_models import ProducerIdentity


def stage_fingerprint(scheduler: Any, source: Dict[str, Any], stage: str,
                      identity: Optional[ProducerIdentity], *, compute_hash: bool = True) -> Dict[str, str]:
    source_id = source.get("id") or source.get("source_id") or ""
    artifacts = scheduler.artifact_service
    spec = scheduler.stage_registry.get(stage) if scheduler.stage_registry else None
    return {
        "input_hash": artifacts.source_input_hash(source.get("path"), compute=compute_hash) if artifacts else "",
        "settings_hash": _hash(_relevant_settings(scheduler, source_id, stage, spec)),
        "dependency_hash": _hash([_dependency_outputs(scheduler, source, stage), _project_inputs(scheduler, spec)]
                                 if getattr(spec, "project_inputs", None) else _dependency_outputs(scheduler, source, stage)),
        # Stored as text: plugin specs may give versions as numbers.
        "producer_name": str((identity.producer_id if identity else "")
                             or getattr(spec, "handler_id", stage) or stage),
        "producer_version": str(getattr(spec, "producer_version", "1.0") or "1.0"),
        "model_role": ModelRole.EMBEDDING.value if stage == ProcessingStage.EMBEDDINGS.value else "",
        "model_name": _model_key(identity),
    }


def _model_key(identity: Optional[ProducerIdentity]) -> str:
    if identity is None:
        return ""
    key = identity.model_name
    if identity.model_version:
        key += f"@{identity.model_version}"
    if identity.model_digest:
        key += f"#{identity.model_digest[:16]}"
    if identity.configuration_digest:
        key += f"+config:{identity.configuration_digest[:16]}"
    return key


def _relevant_settings(scheduler, source_id: str, stage: str, spec) -> Dict[str, Any]:
    policy = scheduler.policy_service
    project = policy.get_project_settings() if policy else {}
    source_settings = policy.get_source_settings(source_id) if policy else {}
    relevant: Dict[str, Any] = {
        "stage": stage,
        "settings": {key: project.get(key) for key in getattr(spec, "settings_keys", []) or []},
        "override": (source_settings.get("stage_overrides") or {}).get(stage),
    }
    if stage == ProcessingStage.TRANSCRIPT.value:
        relevant["transcription_policy"] = source_settings.get("transcription_policy")
    return relevant


def _dependency_outputs(scheduler, source: Dict[str, Any], stage: str):
    db = getattr(scheduler.project_manager, "db_intelligence", None)
    registry = scheduler.stage_registry
    if not db or not registry:
        return []
    source_id = source.get("id") or source.get("source_id") or ""
    category = scheduler._category_for_source(source)
    outputs = []
    for dependency in registry.dependencies_for(stage, category):
        latest = db.latest_stage_run(source_id, dependency) or {}
        ids = latest.get("output_artifact_ids") or []
        rows = db.list_artifacts_by_ids(ids) if ids else []
        outputs.append({
            "stage": dependency,
            "input_hash": latest.get("input_hash"),
            "outputs": sorted(
                (str(row.get("artifact_id")), int(row.get("revision") or 1),
                 str(row.get("input_hash") or ""), str(row.get("review_state") or ""),
                 str(row.get("status") or ""))
                for row in rows
            ),
        })
    return outputs


def _project_inputs(scheduler, spec) -> Dict[str, str]:
    db = getattr(scheduler.project_manager, "db_intelligence", None)
    return scheduler.stage_registry.project_input_digests(spec, db) if db else {}


def _hash(value: Any) -> str:
    raw = json.dumps(value, sort_keys=True, ensure_ascii=False, default=str)
    return hashlib.sha256(raw.encode("utf-8", "replace")).hexdigest()
