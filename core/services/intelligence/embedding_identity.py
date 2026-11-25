"""Reusable vector identity includes both content and retrieval metadata."""
import hashlib
import json
import math


def embedding_input_hash(model_name, row):
    payload = {"model": str(model_name), "text": row.get("exact_text") or "",
               "metadata": row.get("metadata") or {}, "locator": row.get("locator") or {},
               "section_path": row.get("section_path") or []}
    return hashlib.sha256(json.dumps(payload, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def validate_vectors(vectors, expected_count):
    if not vectors or len(vectors) != expected_count:
        raise ValueError("Embedding response omitted vectors; rerun Embeddings after checking the selected model.")
    dimensions = {len(vector) for vector in vectors if vector is not None}
    if len(dimensions) != 1 or 0 in dimensions or len(dimensions) == 0:
        raise ValueError("Embedding model returned inconsistent or empty vector dimensions.")
    for vector in vectors:
        if vector is None or any(not isinstance(value, (int, float)) or not math.isfinite(value) for value in vector):
            raise ValueError("Embedding model returned a missing or non-finite vector.")
    return next(iter(dimensions))
