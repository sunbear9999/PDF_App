"""
ArtifactEmbeddingService — embed real knowledge artifacts into the vector index.

This is what makes semantic search and the artifact store *fuse*. It embeds
sentence + section/doc-summary artifacts into the SAME ChromaDB collection the
document chunks live in (`pdf_workspace`), but keyed by each artifact's real
`intelligence_artifacts` id. Because HybridRetrievalService._vector_search reads
`metadata["artifact_id"]` and RRF fuses on that id, these vector hits now
reinforce the structured / keyword / entity signals for the same segment
instead of sitting in a disjoint id space.

Block chunks (indexed by LocalLLMManager.index_documents) stay as a coarse
breadth tier; these fine-grained artifact records add precision.

Deterministic, batched, status-tracked (`intelligence_artifact_embeddings`).
No new vector store, no new deps — reuses `llm_manager.get_batch_embeddings`.
"""
from __future__ import annotations

import os
from typing import Any, Dict, List, Optional

from core.models.intelligence_models import ModelRole
from core.services.intelligence.embedding_identity import embedding_input_hash, validate_vectors

# Canonical breadth and precision tiers. Every vector carries a real artifact
# id; there is no second undocumented document-chunk indexing path.
_EMBED_TYPES = (
    "block", "sentence", "transcript_segment", "argument_unit", "summary",
    "table_row",  # data-file rows (CSV/XLSX) are retrievable by meaning too
)
_BATCH = 64


class ArtifactEmbeddingService:
    def __init__(self, project_manager=None, llm_manager=None, model_role_service=None) -> None:
        self.project_manager = project_manager
        self.llm_manager = llm_manager
        self.model_role_service = model_role_service

    @property
    def _db(self):
        return getattr(self.project_manager, "db_intelligence", None)

    def embed_source(self, source_id: str, cancel_check=None) -> int:
        """Stream eligible artifacts in small batches; incomplete updates fail.

        ``cancel_check`` is polled between batches so closing the app or
        cancelling the job stops within one batch instead of after the book."""
        from itertools import islice
        from core.models.intelligence_models import ArtifactQuery, SourceScope
        lm, db = self.llm_manager, self._db
        collection = getattr(lm, "collection", None)
        if db is None or not lm or not getattr(lm, "ai_enabled", False) or collection is None:
            return 0
        source = self._source_by_id(source_id) or {}
        model_name = self._embedding_model_name()
        query = ArtifactQuery(source_scope=SourceScope(source_ids=[source_id]),
            artifact_types=list(_EMBED_TYPES), statuses=["complete"],
            review_states=["unreviewed", "verified", "corrected"], current_only=True)
        iterator = (row for row in db.iter_artifacts(query) if (row.get("exact_text") or "").strip())
        eligible_ids, completed, total = set(), 0, 0
        while batch := list(islice(iterator, _BATCH)):
            if cancel_check and cancel_check():
                return 0
            eligible_ids.update(row["artifact_id"] for row in batch)
            total += len(batch)
            pending = self._pending_batch(batch, model_name, collection)
            completed += len(batch) - len(pending)
            if pending:
                completed += self._embed_batch(pending, source_id, source, model_name, collection)
        stale_ids = [item["artifact_id"] for item in db.list_artifact_embeddings(source_id)
                     if item["artifact_id"] not in eligible_ids]
        if stale_ids:
            collection.delete(ids=stale_ids)
            db.delete_artifact_embeddings(stale_ids)
        return total if completed == total else 0

    def _pending_batch(self, batch, model_name, collection):
        pending, reusable = [], []
        for row in batch:
            input_hash = embedding_input_hash(model_name, row)
            row = {**row, "embedding_input_hash": input_hash}
            existing = self._db.get_artifact_embedding(row["artifact_id"])
            if (existing and existing.get("status") == "complete"
                    and existing.get("model_name") == model_name
                    and existing.get("input_hash") == input_hash):
                reusable.append(row)
            else:
                pending.append(row)
        present = _present_vector_ids(collection, [row["artifact_id"] for row in reusable])
        pending.extend(row for row in reusable if row["artifact_id"] not in present)
        return pending

    def _embed_batch(self, batch, source_id, source, model_name, collection):
        try:
            vectors = self.llm_manager.get_batch_embeddings([row["exact_text"] for row in batch])
        except Exception:
            return 0
        if not vectors or len(vectors) != len(batch):
            return 0
        dimension = validate_vectors(vectors, len(batch))
        metadata = [self._vector_metadata(row, source_id, source, model_name, dimension) for row in batch]
        try:
            collection.upsert(documents=[row["exact_text"] for row in batch], embeddings=vectors,
                              metadatas=metadata, ids=[row["artifact_id"] for row in batch])
        except Exception as exc:
            raise RuntimeError(
                f"Vector index update failed for embedding model '{model_name}' ({dimension} dimensions): {exc}. "
                "Rebuild the vector index when changing to a model with different dimensions."
            ) from exc
        for row in batch:
            self._db.upsert_artifact_embedding(row["artifact_id"], {
                "source_id": source_id, "embedding_ref_type": "chroma", "embedding_ref_id": row["artifact_id"],
                "status": "complete", "model_role": ModelRole.EMBEDDING.value,
                "model_name": model_name, "input_hash": row["embedding_input_hash"],
            })
        return len(batch)

    @staticmethod
    def _vector_metadata(row, source_id, source, model_name, dimension):
        locator, retrieval_meta = row.get("locator") or {}, row.get("metadata") or {}
        meta = {**{key: value for key, value in retrieval_meta.items() if isinstance(value, (str, int, float, bool))},
            **{f"tag_{tag}": True for tag in retrieval_meta.get("tags") or []},
            "embedding_dimension": dimension, "artifact_id": row["artifact_id"],
            "artifact_type": row.get("artifact_type") or "", "storage_ref_type": "chroma",
            "storage_ref_id": row["artifact_id"],
            "doc_name": os.path.basename(source.get("path") or "") or source_id,
            "doc_id": source.get("path") or source_id, "source_id": source_id,
            "source_type": source.get("source_type") or "",
            "page": _scalar(locator.get("page")),
            "start": _scalar(locator.get("char_start") if locator.get("char_start") is not None else locator.get("start")),
            "end": _scalar(locator.get("char_end") if locator.get("char_end") is not None else locator.get("end")),
            "section_path": " > ".join(str(part) for part in row.get("section_path") or []),
            "class_label": retrieval_meta.get("argument_role") or retrieval_meta.get("class_label") or "",
            "model_role": ModelRole.EMBEDDING.value, "model_name": model_name,
        }
        return {key: value for key, value in meta.items() if value is not None}

    def _embedding_model_name(self) -> str:
        if self.model_role_service:
            try:
                return self.model_role_service.resolve_role(ModelRole.EMBEDDING.value) or ""
            except Exception:
                pass
        return getattr(self.llm_manager, "embedding_model", "") or ""

    def _source_by_id(self, source_id: str) -> Optional[Dict[str, Any]]:
        pm = self.project_manager
        if not pm:
            return None
        for source in pm.list_sources(include_removed=True):
            if source.get("id") == source_id or source.get("path") == source_id:
                return source
        return None


def _scalar(value):
    """Chroma metadata only accepts str/int/float/bool; drop anything else."""
    if isinstance(value, (str, int, float, bool)):
        return value
    return None


def _present_vector_ids(collection, artifact_ids: List[str]) -> set[str]:
    present: set[str] = set()
    for start in range(0, len(artifact_ids), _BATCH):
        try:
            response = collection.get(
                ids=artifact_ids[start:start + _BATCH], include=[]
            )
            if isinstance(response, dict):
                present.update(str(value) for value in response.get("ids") or [])
        except Exception:
            continue
    return present
