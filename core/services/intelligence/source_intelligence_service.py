"""Public facade for source-level intelligence controls."""
from __future__ import annotations
import re
from contextlib import nullcontext
from typing import Any, Dict, Iterable, Optional
from PySide6.QtCore import QCoreApplication

from core.models.intelligence_models import ProcessingStage, StageStatus
from core.services._shared.qt_tasks import SerialTaskQueue
from core.services.intelligence.source_intelligence_events import (
    highlight_deleted, ocr_completed, project_closing, source_removed,
)

class SourceIntelligenceService:
    def __init__(
        self,
        *,
        project_manager=None,
        artifact_service=None,
        scheduler=None,
        policy_service=None,
        hybrid_retrieval_service=None,
        processing_plan_service=None,
        llm_manager=None,
        bus=None,
        parent=None,
    ):
        self.project_manager = project_manager
        self.artifact_service = artifact_service
        self.scheduler = scheduler
        self.policy_service = policy_service
        self.hybrid_retrieval_service = hybrid_retrieval_service
        self.processing_plan_service = processing_plan_service
        self.llm_manager = llm_manager
        self.bus = bus
        self._background = SerialTaskQueue() if QCoreApplication.instance() else None
        self._background_generation = 0
        if self.bus:
            self.bus.source_added.connect(self._on_source_added)
            self.bus.highlight_created.connect(self._on_highlight_changed)
            self.bus.highlight_updated.connect(self._on_highlight_changed)
            self.bus.video_status_updated.connect(self._on_video_status)
            self.bus.ocr_status_updated.connect(lambda e, p: ocr_completed(self, e, p))
            self.bus.project_clearing_started.connect(lambda *_: project_closing(self))
            self.bus.source_removed.connect(lambda _e, p: source_removed(self, p))
            self.bus.highlight_deleted.connect(lambda _e, p: highlight_deleted(self, p))

    @property
    def db(self):
        return getattr(self.project_manager, "db_intelligence", None)

    manages_project_locks = True

    def get_source_status(self, source_id: str) -> Dict[str, Any]:
        source = self._source_by_id(source_id)
        if not source:
            return {"source_id": source_id, "stages": {}}
        canonical_source_id = source.get("id") or source_id
        artifacts = self.artifact_service.get_artifacts(canonical_source_id, {"limit": 2000})
        db = getattr(self.project_manager, "db_intelligence", None)
        stage_rows = db.get_stage_statuses(canonical_source_id) if db else {}
        stages = {stage.value: StageStatus.NOT_RUN.value for stage in ProcessingStage}
        for artifact in artifacts:
            stage = (artifact.get("metadata") or {}).get("stage")
            if stage and stage in stages:
                status = artifact.get("status") or StageStatus.COMPLETE.value
                if stages[stage] != StageStatus.STALE.value:
                    stages[stage] = status
        for stage, row in stage_rows.items():
            if stage in stages:
                stages[stage] = row.get("status") or stages[stage]
        for stage in self.policy_service.stages_for_source(source) if self.policy_service else []:
            stages.setdefault(stage, StageStatus.NOT_RUN.value)
        return {
            "source_id": canonical_source_id,
            "source": source,
            "processing_mode": (
                self.policy_service.processing_mode_for_source(source)
                if self.policy_service else ""
            ),
            "stages": stages,
            "stage_details": stage_rows,
            "artifact_count": len(artifacts),
        }

    def run_stage(self, source_id: str, stage: str, *, force: bool = False):
        if not self.scheduler:
            return None
        jobs = self.scheduler.enqueue_source(
            source_id, [stage], force_stages=[stage] if force else []
        )
        return jobs[-1] if jobs else None

    def run_missing(self, source_id: str):
        return self.scheduler.enqueue_source(source_id) if self.scheduler else []

    def run_missing_background(self, source_id: str) -> None:
        self._submit_background(lambda: self.run_missing(source_id))

    def process_plan(self, source_id, **kwargs):
        if not self.processing_plan_service:
            raise ValueError("Processing plan service is unavailable.")
        return self.processing_plan_service.process_source(source_id, **kwargs)

    def rerun_stale(self, source_id: str):
        status = self.get_source_status(source_id)
        stale = [stage for stage, value in status.get("stages", {}).items() if value == StageStatus.STALE.value]
        return [self.run_stage(source_id, stage) for stage in stale]

    def rerun_stale_background(self, source_id: str) -> None:
        self._submit_background(lambda: self.rerun_stale(source_id))

    def delete_derived(self, source_id: str, stage: Optional[str] = None) -> int:
        return self.artifact_service.delete_derived(source_id, stage=stage) if self.artifact_service else 0

    def delete_derived_background(self, source_id: str, stage: Optional[str] = None) -> None:
        self._submit_background(lambda: self.delete_derived(source_id, stage))

    def purge_source(self, source_id: str) -> int:
        """Permanently remove every intelligence and vector record for a source."""
        if not source_id or not self.db:
            return 0
        if self.scheduler:
            self.scheduler.cancel_source_jobs(source_id)
        write_lock = getattr(self.db, "_write_lock", None)
        # Chroma can take tens of seconds for a large source. It must never run
        # while holding the project's one SQLite lock: ordinary UI reads would
        # otherwise block behind unrelated vector I/O.
        if self.llm_manager:
            self.llm_manager.remove_source_from_index(source_id)
        with write_lock if write_lock is not None else nullcontext():
            count = self.db.purge_source_intelligence(source_id)
        if self.bus and hasattr(self.bus, "intelligence_state_changed"):
            self.bus.intelligence_state_changed.emit(
                "source_intelligence_updated",
                {"source_id": source_id, "purged": count},
            )
        return count

    def purge_orphaned_sources(self) -> int:
        """Purge intelligence left by sources removed before lifecycle cleanup existed."""
        if not self.db:
            return 0
        live = {
            source.get("id") for source in self.project_manager.list_sources()
            if source.get("id")
        }
        return sum(
            self.purge_source(source_id)
            for source_id in self.db.list_intelligence_source_ids()
            if source_id not in live
        )

    def archive_derived(self, source_id: str, stage: Optional[str] = None) -> int:
        return self.artifact_service.archive_derived(source_id, stage=stage) if self.artifact_service else 0

    def archive_derived_background(self, source_id: str, stage: Optional[str] = None) -> None:
        self._submit_background(lambda: self.archive_derived(source_id, stage))

    def set_source_processing_mode(self, source_id: str, mode: str) -> None:
        if self.policy_service:
            self.policy_service.set_source_processing_mode(source_id, mode)

    def get_artifacts(self, source_id: Optional[str] = None, filters: Optional[Dict[str, Any]] = None):
        return self.artifact_service.get_artifacts(source_id, filters) if self.artifact_service else []

    def get_related_artifacts(self, artifact_id: str):
        return self.artifact_service.get_related_artifacts(artifact_id) if self.artifact_service else {}

    def ensure_analysis_artifacts(
        self,
        source_ids: Iterable[str],
        stages: Optional[Iterable[str]] = None,
        cancel_check=None,
    ) -> Dict[str, Any]:
        """Create reusable local inputs for an analysis workflow.

        This is intentionally synchronous: it is called by SOURCE_STAGE while
        MasterActionRunner is already executing in a background thread.  Each
        stage uses the shared scheduler and its freshness checks, so subsequent
        analysis modes reuse summaries and graph seeds rather than paying the
        preparation cost again.
        """
        requested = list(stages if stages is not None else (
            ProcessingStage.PARSED_BLOCKS.value,
            ProcessingStage.SECTIONS.value,
            ProcessingStage.ABBREVIATIONS.value,
            ProcessingStage.ENTITIES.value,
            ProcessingStage.ENTITY_LINKING.value,
            ProcessingStage.CITATIONS.value,
            ProcessingStage.CROSS_REFERENCES.value,
            ProcessingStage.DATES.value,
            ProcessingStage.CLAIMS.value,
            ProcessingStage.SUMMARIES.value,
            ProcessingStage.GRAPH_SEEDS.value,
        ))
        results = []
        for source_id in [str(value) for value in source_ids or [] if value]:
            source = self._source_by_id(source_id)
            if not source:
                results.append({"source_id": source_id, "error": "Source not found"})
                continue
            canonical_id = str(source.get("id") or source_id)
            db = getattr(self.project_manager, "db_intelligence", None)
            if db and hasattr(db, "backfill_payload_hashes"):
                db.backfill_payload_hashes(canonical_id)
            for stage in self._analysis_stages(source, requested):
                if cancel_check and cancel_check():
                    return {"status": "cancelled", "results": results}
                current = bool(
                    self.scheduler
                    and hasattr(self.scheduler, "is_stage_current")
                    and self.scheduler.is_stage_current(canonical_id, stage)
                )
                if current:
                    results.append({
                        "source_id": canonical_id, "stage": stage,
                        "status": "reused",
                    })
                    continue
                try:
                    result = self.scheduler.run_stage_inline(
                        canonical_id, stage, cancel_check=cancel_check
                    ) if self.scheduler else {
                        "source_id": canonical_id, "stage": stage,
                        "status": "unavailable",
                    }
                except Exception as exc:
                    result = {
                        "source_id": canonical_id, "stage": stage,
                        "status": "failed", "error": str(exc),
                    }
                results.append(result)
        failures = [row for row in results if row.get("status") in {"failed", "unavailable"}]
        reused = [row for row in results if row.get("status") == "reused"]
        prepared = [row for row in results if row.get("status") == "complete"]
        return {
            "status": "complete" if not failures else "partial",
            "results": results,
            "failures": failures,
            "prepared_count": len(prepared),
            "reused_count": len(reused),
            "failed_count": len(failures),
        }

    def _analysis_stages(self, source, requested):
        return self.resolve_analysis_stages(source, requested)

    def resolve_analysis_stages(self, source, requested):
        """Use the registered category dependencies for synchronous workflow preparation too."""
        registry=getattr(self.scheduler,'stage_registry',None)
        policy=self.policy_service or getattr(self.scheduler,'policy_service',None)
        if registry is None or policy is None:
            return requested
        category=policy.category_for_type(source.get('source_type') or 'unknown')
        selected=[stage for stage in requested if (spec:=registry.get(stage)) and category in spec.source_categories]
        return registry.expand_dependencies(selected,category)

    def build_citation_map(self, source_id: str) -> Dict[str, Any]:
        """Project source → bibliography → in-text occurrences for any caller."""
        source = self._source_by_id(source_id)
        from core.services.intelligence.citation_graph_service import CitationGraphService

        return CitationGraphService(
            self.project_manager, self.artifact_service
        ).build(source or {})

    def get_discovery_groups(
        self,
        source_id: str,
        *,
        selected_type: str = "",
        project_scope: bool = False,
        sort_mode: str = "mentions",
        cancel_check=None,
    ):
        """Return canonical entity/citation cards for any Discovery surface."""
        from core.extraction.deterministic import (
            ExtractedEntityGroup, ExtractedMention,
        )

        db = getattr(self.project_manager, "db_intelligence", None)
        source = self._source_by_id(source_id)
        if not db or not source:
            return []
        canonical_id = str(source.get("id") or source_id)
        sources = {
            str(item.get("id")): item
            for item in self.project_manager.list_sources(include_removed=False)
            if item.get("id")
        }
        source_paths = {
            key: item.get("path") or "" for key, item in sources.items()
        }
        location_types = {"LOCATION", "GPE", "LOC", "FAC"}
        grouped_entities: Dict[Any, Dict[str, list]] = {}
        for entity in db.list_artifacts(
            artifact_type="entity", status="complete", limit=100000
        ):
            if cancel_check and cancel_check():
                return []
            meta = entity.get("metadata") or {}
            entity_type = meta.get("entity_type") or ""
            display_type = "LOCATION" if entity_type in location_types else entity_type
            if selected_type == "CITATION" or (
                selected_type and display_type != selected_type
            ):
                continue
            mentions = db.list_entity_mentions_for_canonical(
                entity.get("artifact_id")
            )
            if not mentions:
                mentions = db.list_entity_mentions(
                    entity.get("normalized_key")
                    or entity.get("normalized_text") or "",
                    entity_type,
                )
            if not project_scope:
                mentions = [
                    mention for mention in mentions
                    if mention.get("source_id") == canonical_id
                ]
            if not mentions:
                continue
            key = (
                display_type,
                entity.get("normalized_key")
                or entity.get("normalized_text") or "",
            )
            aggregate = grouped_entities.setdefault(
                key, {"entities": [], "mentions": []}
            )
            aggregate["entities"].append(entity)
            aggregate["mentions"].extend(mentions)

        groups = []
        for (display_type, _), aggregate in grouped_entities.items():
            if cancel_check and cancel_check():
                return []
            canonical = aggregate["entities"][0]
            mentions = {
                mention.get("artifact_id"): mention
                for mention in aggregate["mentions"]
                if mention.get("artifact_id")
            }.values()
            converted = []
            for mention in mentions:
                locator = mention.get("locator") or {}
                evidence_id = (
                    (mention.get("metadata") or {}).get("evidence_artifact_id")
                )
                evidence = db.get_artifact(evidence_id) if evidence_id else None
                converted_mention = ExtractedMention(
                    text=mention.get("exact_text") or "",
                    context=(
                        (evidence or {}).get("exact_text")
                        or mention.get("exact_text") or ""
                    ),
                    page_num=int(locator.get("page") or 0),
                )
                converted_mention.source_path = (
                    mention.get("source_path")
                    or source_paths.get(str(mention.get("source_id")), "")
                )
                converted_mention.source_id = mention.get("source_id")
                converted_mention.show_source_name = project_scope
                converted.append(converted_mention)
            source_ids = {
                getattr(mention, "source_id", None)
                for mention in converted
                if getattr(mention, "source_id", None)
            }
            meta = canonical.get("metadata") or {}
            groups.append(ExtractedEntityGroup(
                canonical_text=(
                    canonical.get("exact_text")
                    or canonical.get("normalized_text") or ""
                ),
                entity_type=display_type,
                mentions=converted,
                extra_properties={
                    **meta,
                    "artifact_id": canonical.get("artifact_id"),
                    "artifact_ids": [
                        item.get("artifact_id") for item in aggregate["entities"]
                    ],
                    "review_state": canonical.get("review_state"),
                    "mention_count": len(converted),
                    "source_count": len(source_ids),
                    "provenance": {
                        "created_by": canonical.get("created_by"),
                        "extractor": canonical.get("extractor_name"),
                    },
                },
            ))

        if selected_type in ("", "CITATION"):
            groups.extend(self._citation_discovery_groups(
                db, canonical_id, source_paths, project_scope,
                ExtractedEntityGroup, ExtractedMention,
                cancel_check=cancel_check,
            ))
        count_key = "source_count" if sort_mode == "sources" else "mention_count"
        groups.sort(key=lambda group: (
            -int(group.extra_properties.get(count_key) or 0),
            -int(
                group.extra_properties.get("mention_count")
                or len(group.mentions)
            ),
            group.canonical_text.casefold(),
        ))
        return groups

    @staticmethod
    def _citation_discovery_groups(
        db, source_id, source_paths, project_scope,
        group_type, mention_type, cancel_check=None,
    ):
        entries = db.list_artifacts(
            artifact_type="citation_entry", status="complete", limit=100000
        )
        if not project_scope:
            entries = [
                entry for entry in entries
                if entry.get("source_id") == source_id
            ]
        matches_by_entry = {}
        for citation_id, entry_id in db.citation_assignments(
            None if project_scope else source_id
        ).items():
            if entry_id:
                matches_by_entry.setdefault(entry_id, []).append(citation_id)
        aggregates = {}
        for entry in entries:
            if cancel_check and cancel_check():
                return []
            meta = entry.get("metadata") or {}
            doi = str(meta.get("doi") or "").strip().casefold()
            title = re.sub(
                r"\W+", " ",
                str(meta.get("title") or entry.get("exact_text") or ""),
            ).strip().casefold()
            authors = meta.get("authors") or []
            author = str(authors[0] if authors else "").casefold()
            authority = str(meta.get("authority") or "").strip().casefold()
            key = (
                doi or authority
                or f"{author}|{meta.get('year') or ''}|{title[:160]}"
            )
            aggregates.setdefault(key, []).append(entry)

        groups = []
        for work_entries in aggregates.values():
            if cancel_check and cancel_check():
                return []
            primary = work_entries[0]
            primary_meta = primary.get("metadata") or {}
            bibliography_mentions = []
            in_text_mentions = []
            source_ids = set()
            seen_citations = set()
            for entry in work_entries:
                entry_source_id = str(entry.get("source_id") or "")
                source_ids.add(entry_source_id)
                entry_path = (
                    entry.get("source_path")
                    or source_paths.get(entry_source_id, "")
                )
                bibliography = mention_type(
                    text="Bibliography entry",
                    context=entry.get("exact_text") or "",
                    page_num=int((entry.get("locator") or {}).get("page") or 0),
                )
                bibliography.source_path = entry_path
                bibliography.source_id = entry_source_id
                bibliography.show_source_name = project_scope
                bibliography_mentions.append(bibliography)
                for cite_id in matches_by_entry.get(entry.get("artifact_id"), []):
                    if cite_id in seen_citations:
                        continue
                    cite = db.get_artifact(cite_id)
                    if not cite:
                        continue
                    seen_citations.add(cite_id)
                    cite_meta = cite.get("metadata") or {}
                    mention = mention_type(
                        text=(
                            cite_meta.get("raw_text")
                            or cite.get("exact_text") or ""
                        ),
                        context=(
                            cite.get("exact_text")
                            or cite_meta.get("raw_text") or ""
                        ),
                        page_num=int(
                            (cite.get("locator") or {}).get("page") or 0
                        ),
                    )
                    mention.source_path = (
                        cite.get("source_path")
                        or source_paths.get(str(cite.get("source_id") or ""), "")
                    )
                    mention.source_id = cite.get("source_id")
                    mention.show_source_name = project_scope
                    in_text_mentions.append(mention)
            groups.append(group_type(
                canonical_text=str(
                    primary_meta.get("title")
                    or primary.get("exact_text")
                    or "Bibliography entry"
                ),
                entity_type="entity.source",
                mentions=in_text_mentions,
                extra_properties={
                    **primary_meta,
                    "artifact_id": primary.get("artifact_id"),
                    "artifact_ids": [
                        entry.get("artifact_id") for entry in work_entries
                    ],
                    "bibliography_raw": primary.get("exact_text") or "",
                    "bib_page_num": int(
                        (primary.get("locator") or {}).get("page") or 0
                    ),
                    "bibliography_mentions": bibliography_mentions,
                    "mention_count": len(in_text_mentions),
                    "source_count": len(
                        {value for value in source_ids if value}
                    ),
                },
            ))
        return groups

    def build_retrieval_plan(self, query: str, **kwargs):
        retrieval = getattr(self.scheduler, "hybrid_retrieval_service", None) if self.scheduler else None
        retrieval = retrieval or getattr(self, "hybrid_retrieval_service", None)
        if retrieval and hasattr(retrieval, "build_plan"):
            return retrieval.build_plan(query, **kwargs)
        from core.models.intelligence_models import RetrievalPlan
        return RetrievalPlan(query=query, **kwargs)

    def retrieve(self, plan):
        retrieval = getattr(self.scheduler, "hybrid_retrieval_service", None) if self.scheduler else None
        retrieval = retrieval or getattr(self, "hybrid_retrieval_service", None)
        return retrieval.retrieve(plan) if retrieval else []

    def cancel_source_jobs(self, source_id: str) -> int:
        return self.scheduler.cancel_source_jobs(source_id) if self.scheduler else 0

    def pause_source(self, source_id: str) -> int:
        return self.scheduler.pause_source(source_id) if self.scheduler else 0

    def resume_source(self, source_id: str):
        return self.scheduler.resume_source(source_id) if self.scheduler else []

    def start_backfill(self, source_ids, stages=None) -> Dict[str, Any]:
        db = getattr(self.project_manager, "db_intelligence", None)
        queued = {}
        for source_id in source_ids or []:
            source = self._source_by_id(source_id)
            selected = list(
                stages or (
                    self.policy_service.stages_for_source(source)
                    if source and self.policy_service else []
                )
            )
            if db:
                db.set_backfill_state(
                    source_id, selected_stages=selected,
                    completed_stages=[], status="queued",
                )
            queued[source_id] = self.scheduler.enqueue_source(
                source_id, stages=selected
            ) if self.scheduler else []
        return queued

    def start_backfill_background(self, source_ids, stages=None) -> None:
        """Plan a bulk backfill away from the Qt event thread."""
        ids = list(source_ids or [])
        self._submit_background(lambda: self.start_backfill(ids, stages))

    def list_backfill(self):
        db = getattr(self.project_manager, "db_intelligence", None)
        return db.list_backfill_state() if db else []

    def run_after_hashing(self, path: str, then) -> None:
        """Hash a (large) source file off the Qt thread, then run ``then`` on it.

        Enqueueing fingerprints every stage with the file's sha256; for a
        184 MB scan that took ~0.5 s on the Qt thread and froze the UI. With
        the cache warm, ``then`` only reads it.
        """
        if not path or not self.artifact_service:
            then()
            return

        def work() -> None:
            try:
                self.artifact_service.source_input_hash(path)
            finally:
                then()

        self._submit_background(work)

    def _submit_background(self, operation) -> None:
        if self._background is None:
            operation()
        else:
            generation = self._background_generation
            self._background.submit(
                lambda: operation() if generation == self._background_generation else None
            )

    def _on_source_added(self, event, payload) -> None:
        from core.services.intelligence.source_intelligence_events import source_added
        source_added(self, payload)

    def _on_highlight_changed(self, event, payload) -> None:
        from core.services.intelligence.source_intelligence_events import highlight_changed
        highlight_changed(self, payload)

    def _on_video_status(self, event, payload) -> None:
        from core.services.intelligence.source_intelligence_events import video_status
        video_status(self, event, payload)

    def _source_by_id(self, source_id: str) -> Optional[Dict[str, Any]]:
        if not self.project_manager:
            return None
        for source in self.project_manager.list_sources(include_removed=True):
            if source.get("id") == source_id or source.get("path") == source_id:
                return source
        return None
