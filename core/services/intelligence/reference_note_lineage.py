"""Resolve note authorship from recorded trace calls, never the active model."""
from core.models.producer_models import ProducerIdentity, MethodKind, human


def resolve_lineage(pm, origin, body):
    origin = dict(origin or {})
    if origin.get("kind", "user") not in {"user", "assistant"}:
        raise ValueError("Note origin must be user or assistant.")
    trace_id = origin.get("trace_id")
    trace = pm.get_prompt_trace(trace_id) if trace_id and hasattr(pm, "get_prompt_trace") else None
    calls = (trace or {}).get("calls") or []
    step = origin.get("step_id")
    candidates = [call for call in calls if not step or call.get("step_id") == step]
    models = {call.get("model") for call in candidates if call.get("model")}
    model = next(iter(models)) if len(models) == 1 else ""
    roles = {call.get("model_role") for call in candidates if call.get("model") == model and call.get("model_role")}
    role = next(iter(roles)) if len(roles) == 1 else None
    edited = origin.get("kind") == "assistant" and body != origin.get("suggested_text", body)
    producer = human()
    if origin.get("kind") == "assistant" and not edited:
        producer = ProducerIdentity("research_assistant_note", method_kind=MethodKind.GENERATIVE.value,
            model_id=model, fallback_reason="" if model else "Originating model not recorded unambiguously; current model was not substituted.")
    metadata = {"authored_by": "model" if producer.method_kind == "generative" else "user",
                "edited_assistant_suggestion": edited}
    if origin.get("kind") == "assistant":
        metadata["assistant_lineage"] = {"trace_id": trace_id, "step_id": step,
            "model_id": model or None, "model_identity_status": "recorded" if model else "unrecorded",
            "model_role": role,
            "suggested_text": origin.get("suggested_text", body)}
    return producer, metadata
