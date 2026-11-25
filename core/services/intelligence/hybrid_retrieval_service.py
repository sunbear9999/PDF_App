"""Artifact-first retrieval with vector RAG fallback."""
from __future__ import annotations

from contextvars import ContextVar
from dataclasses import asdict
from typing import Any, Dict, List, Optional

from core.models.intelligence_models import RetrievalPlan, RetrievalResult, SourceScope
from core.services.intelligence.artifact_eligibility import (
    is_current_artifact,
)


from core.services.intelligence import retrieval_ranking as _ranking
from core.services.intelligence import retrieval_hydration as _hydration
from core.services.intelligence import retrieval_vectors as _vectors
from core.services.intelligence.retrieval_ranking import (
    STRUCTURED_TYPES, INTENT_TYPE_HINTS, _dedupe_results, _infer_intent, _type_boost,
)


class HybridRetrievalService:
    def __init__(self, project_manager=None, llm_manager=None, artifact_service=None,
                 cross_encoder=None, language_feature_service=None,
                 structured_query_service=None):
        self.project_manager = project_manager
        self.llm_manager = llm_manager
        self.artifact_service = artifact_service
        self.cross_encoder = cross_encoder
        self.language_feature_service = language_feature_service
        self.structured_query_service = structured_query_service
        self._source_titles = ContextVar("intelligence_retrieval_source_titles", default=None)
        self._provider_revision = 0
        self._providers: Dict[str, Any] = {}
        self._intent_providers: Dict[str, Any] = {}

    def register_provider(self, provider_id: str, callback) -> None:
        if not provider_id or not callable(callback):
            raise ValueError("Retrieval provider requires a stable id and callable.")
        self._providers[provider_id] = callback
        self._provider_revision += 1

    @property
    def db(self):
        return getattr(self.project_manager, "db_intelligence", None)

    def search_citations(self, query, allowed_docs):
        """Rank citation-bearing passages through the shared eligibility/scope gate."""
        plan = self.build_plan(query, allowed_docs=allowed_docs, has_citation=True,
                               graph_expansion=0, max_results=5)
        return [asdict(item) for item in self.retrieve(plan)]

    def unregister_provider(self, provider_id: str) -> None:
        self._providers.pop(provider_id, None)
        self._provider_revision += 1

    def register_intent_provider(self, provider_id: str, callback) -> None:
        if not provider_id or not callable(callback):
            raise ValueError("Intent provider requires a stable id and callable.")
        self._intent_providers[provider_id] = callback

    def unregister_intent_provider(self, provider_id: str) -> None:
        self._intent_providers.pop(provider_id, None)

    def build_plan(
        self,
        query: str,
        *,
        allowed_source_ids: Optional[List[str]] = None,
        allowed_docs: Optional[List[str]] = None,
        source_scope: Optional[SourceScope] = None,
        max_results: int = 10,
        artifact_types: Optional[List[str]] = None,
        use_embeddings: bool = True,
        use_reranker: bool = True,
        reranker_policy: str = "auto",
        graph_expansion: int = 1,
        section_paths: Optional[List[List[str]]] = None,
        argument_roles: Optional[List[str]] = None,
        artifact_families: Optional[List[str]] = None,
        coverage_strategy: str = "relevance",
        per_section_limit: int = 0,
        min_sections: int = 0,
        tag_filters: Optional[List[str]] = None,
        tag_logic: str = "AND",
        has_citation: Optional[bool] = None,
        has_table: Optional[bool] = None,
        block_types: Optional[List[str]] = None) -> RetrievalPlan:
        intent = self._infer_intent(query)
        preferred = list(artifact_types or INTENT_TYPE_HINTS.get(intent, []))
        scope = source_scope or self.resolve_source_scope(
            source_ids=allowed_source_ids, source_refs=allowed_docs
        )
        from core.services.intelligence.retrieval_scope import apply_document_tags
        tag_filters, scope = apply_document_tags(self.project_manager, scope, list(tag_filters or []), tag_logic)
        return RetrievalPlan(
            query=query,
            allowed_source_ids=list(scope.source_ids),
            source_scope=scope,
            artifact_types=list(artifact_types or preferred or []),
            preferred_artifact_types=preferred,
            query_intent=intent,
            use_embeddings=use_embeddings,
            use_reranker=use_reranker,
            reranker_policy=reranker_policy,
            graph_expansion=max(0, int(graph_expansion)),
            section_paths=list(section_paths or []),
            argument_roles=list(argument_roles or []),
            artifact_families=list(artifact_families or []),
            coverage_strategy=str(coverage_strategy or "relevance"),
            per_section_limit=max(0, int(per_section_limit or 0)),
            min_sections=max(0, int(min_sections or 0)),
            max_results=max_results,
            tag_filters=list(tag_filters or []), tag_logic=tag_logic, has_citation=has_citation,
            has_table=has_table, block_types=list(block_types or []),
        )

    def resolve_source_scope(
        self,
        *,
        source_ids: Optional[List[str]] = None,
        source_refs: Optional[List[str]] = None,
    ) -> SourceScope:
        """Resolve source IDs, paths, and display names once at the boundary."""
        requested = {str(value) for value in [*(source_ids or []), *(source_refs or [])] if value}
        if not requested:
            return SourceScope()
        ids: List[str] = []
        paths: List[str] = []
        names: List[str] = []
        matched: Dict[str, int] = {value: 0 for value in requested}
        pm = self.project_manager
        sources = pm.list_sources() if pm and hasattr(pm, "list_sources") else []
        for source in sources:
            sid = str(source.get("id") or "")
            path = str(source.get("path") or "")
            title = str(source.get("title") or "")
            basename = __import__("os").path.basename(path)
            hits = requested.intersection({sid, path, title, basename})
            if hits:
                for value in hits:
                    matched[value] += 1
                if sid and sid not in ids:
                    ids.append(sid)
                if path and path not in paths:
                    paths.append(path)
                for name in (title, basename):
                    if name and name not in names:
                        names.append(name)
        # A lightweight adapter may know IDs without enumerating sources.
        if not sources:
            ids.extend(str(value) for value in source_ids or [] if value)
            for value in source_ids or []:
                matched[str(value)] = 1
        unresolved = sorted(value for value, count in matched.items() if count != 1)
        return SourceScope(source_ids=list(dict.fromkeys(ids)), source_paths=paths,
                           display_names=names, unresolved_refs=unresolved)

    def retrieve(self, plan: RetrievalPlan, use_cache: bool = True) -> List[RetrievalResult]:
        """Read source labels once per call; concurrent callers have isolated maps."""
        pm = self.project_manager
        sources = pm.list_sources() if pm and hasattr(pm, "list_sources") else []
        titles = {source.get("id"): source.get("title") or source.get("path") or "" for source in sources}
        token = self._source_titles.set(titles)
        try:
            return self._retrieve(plan, use_cache)
        finally:
            self._source_titles.reset(token)

    def _retrieve(self, plan: RetrievalPlan, use_cache: bool) -> List[RetrievalResult]:
        if self._unresolved_scope(plan):
            return []
        self._sync_annotations(plan)
        cache_key = self._cache_key(plan)
        if use_cache and cache_key:
            cached = self._read_cache(*cache_key)
            if cached is not None:
                return self._canonical_results(plan, cached)[: plan.max_results]

        signal_lists: List[List[RetrievalResult]] = []
        if plan.use_structured:
            signal_lists.append(self._structured_search(plan))
        if plan.use_keyword:
            signal_lists.append(self._keyword_search(plan))
        anchored = self._entity_anchored_search(plan)
        if anchored:
            signal_lists.append(anchored)
        if plan.use_embeddings:
            signal_lists.append(self._vector_search(plan, remaining=max(plan.max_results, plan.rerank_top_k)))
        for provider_id, provider in list(self._providers.items()):
            try:
                provided = [it for it in provider(plan) if isinstance(it, RetrievalResult)]
                if provided:
                    signal_lists.append(provided)
            except Exception as exc:
                print(f"[HybridRetrievalService] provider '{provider_id}' failed: {exc}")

        # Stage C — reciprocal-rank fusion, then sentence-type preference, then
        # rerank (cross-encoder if available, else deterministic lexical).
        fused = self._canonical_results(plan, self._rrf_fuse(signal_lists))
        fused = self._apply_sentence_type_preference(plan, fused)
        if plan.graph_expansion > 0:
            fused = self._expand_graph_neighbors(plan, fused)
        fused = self._apply_review_preference(fused)
        fused = self._rerank(plan, fused)
        results = _dedupe_results(sorted(fused, key=lambda item: item.score, reverse=True))
        results = self._apply_coverage(plan, self._canonical_results(plan, results))

        if use_cache and cache_key:
            self._write_cache(*cache_key, plan, results)
        return results[: plan.max_results]

    def _sync_annotations(self, plan):
        # Annotation synchronization precedes the signature. Idempotent writes
        # make this a no-op on warm retrieval instead of invalidating its cache.
        if self.artifact_service:
            try:
                source_ids = self._source_ids(plan)
                if source_ids:
                    for source_id in source_ids:
                        self.artifact_service.sync_all_annotation_artifacts(source_id)
                else:
                    self.artifact_service.sync_all_annotation_artifacts()
            except Exception:
                pass

    def _structured_search(self, plan: RetrievalPlan) -> List[RetrievalResult]:
        """Stream typed candidates; score before retaining a bounded candidate pool."""
        if not self.artifact_service or not self._has_typed_filters(plan):
            return []
        import heapq
        from core.db.intelligence_artifact_queries import plan_query
        query = plan_query(plan, self._source_ids(plan))
        terms = {term.lower() for term in plan.query.split() if len(term) > 2}
        pool, per_section = [], {}
        capacity = max(plan.max_results * 8, 100)
        for row in self.artifact_service.iter_artifacts(query):
            if not is_current_artifact(self._db, row, review_states=plan.review_states, include_stale=plan.include_stale):
                continue
            text = row.get("exact_text") or row.get("normalized_text") or ""
            score = _type_boost(row.get("artifact_type") or "", plan)
            score += sum(term in text.lower() for term in terms) / max(1, len(terms))
            candidate = (score, row["artifact_id"], row)
            heapq.heappush(pool, candidate)
            if len(pool) > capacity:
                heapq.heappop(pool)
            if plan.coverage_strategy == "section_diverse":
                key = (row.get("source_id"), tuple(row.get("section_path") or []))
                previous = per_section.get(key)
                if (previous is None and len(per_section) < capacity) or (previous is not None and candidate[:2] > previous[:2]):
                    per_section[key] = candidate
        selected = {item[1]: item for item in pool}
        selected.update({item[1]: item for item in per_section.values()})
        return [self._row_to_result(row, score, "structured", "Typed artifact match")
                for score, _, row in sorted(selected.values(), key=lambda item: item[:2], reverse=True)]

    @staticmethod
    def _has_typed_filters(plan):
        return bool(plan.argument_roles or plan.section_paths or plan.artifact_families
                    or plan.tag_filters or plan.has_citation is not None
                    or plan.has_table is not None or plan.block_types)

    @property
    def _db(self):
        return getattr(self.project_manager, "db_intelligence", None)

    def _keyword_search(self, plan: RetrievalPlan) -> List[RetrievalResult]:
        """BM25/keyword signal over artifact text (FTS5), all types.

        Catches exact terms and proper nouns that embeddings blur.
        """
        db = self._db
        if not db:
            return []
        from core.db.intelligence_artifact_queries import plan_query
        rows = db.search_artifacts(
            plan.query,
            artifact_types=plan.artifact_types or None,
            source_ids=self._source_ids(plan) or None,
            limit=max(plan.max_results * 2, 10),
            include_stale=plan.include_stale,
            review_states=plan.review_states,
            artifact_query=plan_query(plan, self._source_ids(plan)),
        )
        results = []
        for rank, row in enumerate(rows):
            results.append(self._row_to_result(row, 1.0 / (rank + 1), "keyword", "BM25 keyword match"))
        return results

    def _entity_anchored_search(self, plan: RetrievalPlan) -> List[RetrievalResult]:
        """Resolve query entities to canonical entities, then pull the sentences
        they are mentioned in across the whole project (cross-document reach)."""
        db = self._db
        if not db:
            return []
        keys = self._query_anchor_keys(plan.query)
        if not keys:
            return []
        entities = db.list_entities_by_keys(list(keys), limit=25)
        if not entities:
            return []
        from core.db.intelligence_artifact_queries import plan_query
        eligible = [entity for entity in entities if is_current_artifact(db, entity)]
        rows = db.entity_contexts([entity["artifact_id"] for entity in eligible],
                                 plan_query(plan, self._source_ids(plan)), max(plan.max_results * 4, 30))
        return [self._row_to_result(row, 1.0 / (rank + 1), "entity", "Entity mention in canonical source context")
                for rank, row in enumerate(rows)]

    def _query_anchor_keys(self, query: str) -> set:
        """Normalized keys for entities/proper nouns in the query itself."""
        from core.services.intelligence.entity_extractor import normalize_key
        keys = set()
        lang = self.language_feature_service
        if lang is not None:
            try:
                for surface, _label, _s, _e in lang.get_entities(query or ""):
                    key = normalize_key(surface)
                    if key:
                        keys.add(key)
            except Exception:
                pass
        if not keys:
            # Fallback: capitalized multi-word spans / significant tokens.
            import re
            for match in re.findall(r"\b[A-Z][a-zA-Z]+(?:\s+[A-Z][a-zA-Z]+)*", query or ""):
                key = normalize_key(match)
                if len(key) > 2:
                    keys.add(key)
        return keys

    def _rrf_fuse(self, signal_lists: List[List[RetrievalResult]]) -> List[RetrievalResult]:
        return _ranking._rrf_fuse(signal_lists)

    def _apply_sentence_type_preference(self, plan: RetrievalPlan, results: List[RetrievalResult]) -> List[RetrievalResult]:
        return _ranking._apply_sentence_type_preference(plan, results)

    def _rerank(self, plan: RetrievalPlan, results: List[RetrievalResult]) -> List[RetrievalResult]:
        return _ranking._rerank(plan, results, self.cross_encoder)

    @staticmethod
    def _apply_coverage(
            plan: RetrievalPlan, results: List[RetrievalResult]
        ) -> List[RetrievalResult]:
        return _ranking._apply_coverage(plan, results)

    # -- cache -------------------------------------------------------------

    def _cache_key(self, plan: RetrievalPlan):
        db = self._db
        if not db:
            return None
        import hashlib
        import json
        scorer = {}
        if self.cross_encoder and hasattr(self.cross_encoder, "diagnostics"):
            try:
                scorer = self.cross_encoder.diagnostics()
            except Exception:
                scorer = {}
        blob = json.dumps(
            {"plan": asdict(plan), "reranker": scorer, "embedding_model": self._embedding_model_name(), "providers": [sorted(self._providers), self._provider_revision]},
            sort_keys=True, ensure_ascii=False, default=str,
        )
        query_hash = hashlib.sha256(blob.encode("utf-8", "replace")).hexdigest()
        families = list(plan.artifact_families or [])
        if not families:
            families = ["structure", "argument", "summary", "annotation", "relation", "embedding"]
        if "embedding" not in families:
            families.append("embedding")
        state_hash = (
            db.knowledge_signature(
                source_ids=self._source_ids(plan), artifact_families=families
            )
            if hasattr(db, "knowledge_signature") else db.state_signature()
        )
        return query_hash, state_hash

    def _read_cache(self, query_hash: str, state_hash: str):
        db = self._db
        cached = db.get_retrieval_cache(query_hash, state_hash) if db else None
        if not cached:
            return None
        out = []
        for d in cached.get("fused", []):
            try:
                out.append(RetrievalResult(**{k: v for k, v in d.items()
                                              if k in RetrievalResult.__dataclass_fields__}))
            except Exception:
                continue
        return out

    def _write_cache(self, query_hash: str, state_hash: str, plan: RetrievalPlan, results: List[RetrievalResult]) -> None:
        db = self._db
        if not db:
            return
        try:
            db.save_retrieval_cache(query_hash, state_hash,
                                    {"query": plan.query, "intent": plan.query_intent},
                                    [asdict(r) for r in results])
        except Exception:
            pass

    def _row_to_result(self, row: Dict[str, Any], score: float, signal: str, reason: str) -> RetrievalResult:
        return _hydration._row_to_result(row, score, signal, reason, self._source_title)

    def _embedding_model_name(self):
        return _vectors._embedding_model_name(self.llm_manager)

    def _vector_search(self, plan: RetrievalPlan, remaining: int) -> List[RetrievalResult]:
        return _vectors._vector_search(self.llm_manager, self._db, self._source_ids(plan), self._vector_doc_filters(plan), plan, remaining)

    def _vector_results(self, plan, raw):
        return _vectors._vector_results(self.llm_manager, self._db, plan, raw)

    def _expand_graph_neighbors(
            self, plan: RetrievalPlan, results: List[RetrievalResult]
        ) -> List[RetrievalResult]:
        return _hydration._expand_graph_neighbors(self._db, self._source_ids(plan), self._row_to_result, plan, results)

    def _apply_review_preference(self, results: List[RetrievalResult]) -> List[RetrievalResult]:
        return _ranking._apply_review_preference(results)

    def _result_metadata(self, row: Dict[str, Any]) -> Dict[str, Any]:
        return _hydration._result_metadata(row)

    def _source_ids(self, plan: RetrievalPlan) -> List[str]:
        scope = plan.source_scope
        if isinstance(scope, dict):
            return list(scope.get("source_ids") or plan.allowed_source_ids or [])
        return list(scope.source_ids or plan.allowed_source_ids or [])

    @staticmethod
    def _unresolved_scope(plan: RetrievalPlan) -> bool:
        scope = plan.source_scope
        if isinstance(scope, dict):
            return bool(scope.get("unresolved_refs"))
        return bool(scope.unresolved_refs)

    def _canonical_results(
            self, plan: RetrievalPlan, results: List[RetrievalResult]
        ) -> List[RetrievalResult]:
        return _hydration._canonical_results(self._db, self._source_ids(plan), self._row_to_result, plan, results)

    def _canonical_metadata(self, plan, item, metadata):
        return _hydration._canonical_metadata(self._db, self._source_ids(plan), plan, item, metadata)

    def _vector_doc_filters(self, plan: RetrievalPlan) -> List[str]:
        scope = plan.source_scope
        if isinstance(scope, dict):
            values = [*(scope.get("source_paths") or []), *(scope.get("display_names") or [])]
        else:
            values = [*(scope.source_paths or []), *(scope.display_names or [])]
        return list(dict.fromkeys(value for value in values if value))

    def _infer_intent(self, query: str) -> str:
        for _provider_id, provider in list(self._intent_providers.items()):
            try:
                intent = provider(query)
                if isinstance(intent, str) and intent:
                    return intent
            except Exception:
                continue
        return _infer_intent(query)

    def _source_title(self, source_id: Optional[str], source_path: Optional[str]) -> str:
        cached = self._source_titles.get()
        if cached is not None:
            return cached.get(source_id) or source_path or ""
        pm = self.project_manager
        if pm and source_id:
            for source in pm.list_sources(include_removed=True):
                if source.get("id") == source_id:
                    return source.get("title") or source.get("path") or ""
        return source_path or ""
