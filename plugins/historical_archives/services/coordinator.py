"""Parallel, failure-isolated federated archive search."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import replace
from typing import Dict, Iterable, List

from ..models import ArchiveQuery, ArchiveResult, SearchBatch
from .query_planner import QueryPlanner
from .ranking import LocalRanker, ResultDeduplicator


class FederatedSearchCoordinator:
    def __init__(
        self,
        providers: Iterable,
        client,
        *,
        max_workers: int = 3,
        max_variants_per_provider: int = 4,
    ) -> None:
        self.providers = {provider.provider_id: provider for provider in providers}
        self._client = client
        self._planner = QueryPlanner()
        self._ranker = LocalRanker()
        self._dedupe = ResultDeduplicator()
        self._max_workers = max(1, min(int(max_workers), 4))
        self._max_variants = max(1, min(int(max_variants_per_provider), 8))

    def search(self, query: ArchiveQuery, provider_ids: List[str], task_context=None):
        variants = query.variants or self._planner.expand(query)
        selected = [
            self.providers[item] for item in provider_ids if item in self.providers
        ]
        if query.original_query.lower().startswith("https://") and "iiif" in self.providers:
            selected = [provider for provider in selected if provider.provider_id == "iiif"]
        errors: Dict[str, str] = {}
        all_results: List[ArchiveResult] = []
        if not selected:
            return SearchBatch([], {"search": "Select at least one provider."}, variants)
        with ThreadPoolExecutor(max_workers=min(self._max_workers, len(selected))) as pool:
            futures = {
                pool.submit(self._search_provider, provider, query, variants, task_context): provider
                for provider in selected
            }
            completed = 0
            for future in as_completed(futures):
                provider = futures[future]
                if task_context:
                    task_context.raise_if_cancelled()
                try:
                    results = future.result()
                    all_results.extend(results)
                    if task_context:
                        for result in results:
                            task_context.publish({
                                "type": "result",
                                "result": result.to_dict(),
                            })
                except Exception as exc:
                    errors[provider.provider_id] = str(exc)
                    if task_context:
                        task_context.publish({
                            "type": "provider_error",
                            "provider": provider.label,
                            "error": str(exc),
                        })
                completed += 1
                if task_context:
                    task_context.report(
                        completed, len(selected), f"Searched {provider.label}"
                    )
        ranked = self._ranker.rank(self._dedupe.mark(all_results), query)
        return SearchBatch(ranked, errors, variants)

    def _search_provider(self, provider, query, variants, task_context):
        if provider.provider_id == "iiif":
            return provider.search(query, self._client, task_context)
        found = {}
        active = [item for item in variants if item.enabled][: self._max_variants]
        per_variant = max(3, query.max_results // max(1, len(active)))
        for variant in active:
            if task_context:
                task_context.raise_if_cancelled()
            variant_query = replace(
                query,
                original_query=variant.text,
                max_results=per_variant,
            )
            for result in provider.search(variant_query, self._client, task_context):
                result.matched_variant = variant.text
                found.setdefault(result.provider_key, result)
            if len(found) >= query.max_results:
                break
        return list(found.values())[: query.max_results]
