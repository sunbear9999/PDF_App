"""
core/services/document/source_evaluation_service.py

Event-driven orchestrator for the source evaluation pipeline.

Flow
----
1. document_added  →  queue the path as pending
2. discovery_state_changed(EXTRACTION_COMPLETE) for a pending path
        →  Phase 1: read from pm.get_citation() first (same data citation dock uses),
                    fall back to PDF scan only if still missing
        →  if doi+journal found → Phase 2 (scoring, off-thread)
        →  if missing metadata AND user explicitly requested → emit METADATA_REQUESTED;
           GUI shows dialog  (background auto-evaluations never prompt)
3. source_eval_state_changed(METADATA_PROVIDED)  →  Phase 2 with user data
4. source_eval_action_requested(RUN_EVALUATION)  →  Phase 1 immediately (manual trigger)
5. citation_action_requested(UPDATE_ENTRY)  →  if doc already scored, re-score with
   fresh doi/journal from the updated citation record (automatic sync with citation dock)

Score + ledger are persisted to the source entity's properties in GraphDB so
workspace nodes can display a badge without re-querying.
"""
from __future__ import annotations

import re
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional, Set

from PySide6.QtCore import QObject, QThread, Signal

import fitz

from core.events.event_bus import EventBus
from core.events.domains.document_events import DocumentEvent
from core.events.domains.discovery_events import DiscoveryEvent
from core.events.domains.evaluation_events import (
    SourceEvalEvent,
    SourceEvalEventPayload,
    SourceEvalIntent,
    SourceEvalPayload,
)
from core.events.domains.citation_events import CitationIntent
from core.services.document.source_evaluator_service import OfflineSourceEvaluator
from core.registries.source_scoring_registry import build_default_source_scoring_registry


_DOI_RE = re.compile(r"\b10\.\d{4,9}/[-._;()/:A-Z0-9]+\b", re.IGNORECASE)
_STANDALONE_NUMBER_LINE = re.compile(r"(?m)^(\d{1,3})\.\n(?=\S)")
_NUMERIC_ENTRY_START = re.compile(r"^\d{1,3}\. ")
_PAGE_HEADER_RE = re.compile(r"\b\d+\s+of\s+\d+\b")


def _journal_from_raw_bib(raw: str) -> str:
    """Extract journal name from a raw bibliography string.

    For the typical academic format "Authors. Title. Journal Year, vol, pages."
    the journal appears immediately before the year.  We find the last ". "
    boundary before the year and take the text that follows.

    This is best-effort — abbreviated journal names with dots (e.g.
    "Lancet Reg. Health Eur.") may return only the last component.
    """
    if not raw:
        return ""
    try:
        clean = re.sub(r"\[[A-Za-z ]+\]", "", raw).strip()
        year_m = re.search(r"\b(?:18|19|20)\d{2}\b", clean)
        if not year_m:
            return ""
        before_year = clean[: year_m.start()].rstrip(", ")
        last_period = before_year.rfind(". ")
        if last_period >= 0:
            candidate = before_year[last_period + 2 :].strip(". ,;:")
        else:
            candidate = before_year.strip(". ,;:")
        return candidate[:100] if 2 <= len(candidate) <= 100 else ""
    except Exception:
        return ""


def _collapse_numeric_continuations(bib_text: str) -> str:
    """Join wrapped continuation lines into their parent numbered reference entry.

    For numbered bibliographies (MDPI, Vancouver, IEEE …) the shared
    `_split_bibliography_entries` splitter can mistake an author name on a
    continuation line (e.g. "Bajracharya, R.; et al.") for the start of a new
    entry because it matches the author-name pattern.  Collapsing all lines
    between numbered starts onto a single line eliminates the ambiguity.

    Page headers (e.g. "Nutrients 2025, 17, 859  15 of 20") are stripped so
    they don't become spurious entries when they appear between page breaks
    inside the references section.

    Only called when numeric style is detected; author-year bibliographies use
    the splitter's own blank-line / author-pattern logic unchanged.
    """
    result: List[str] = []
    for line in bib_text.splitlines():
        stripped = line.rstrip()
        if not stripped:
            result.append("")
        elif _NUMERIC_ENTRY_START.match(stripped):
            result.append(stripped)
        elif _PAGE_HEADER_RE.search(stripped) and not _NUMERIC_ENTRY_START.match(stripped):
            # Skip running page headers ("Journal 2025, 17, 859  15 of 20")
            pass
        elif result and result[-1]:
            result[-1] += " " + stripped
        else:
            result.append(stripped)
    return "\n".join(result)


def _is_numeric_bibliography(bib_text: str) -> bool:
    matches = sum(
        1 for line in bib_text.splitlines()[:30]
        if _NUMERIC_ENTRY_START.match(line.strip())
    )
    return matches >= 2


def _extract_bibliography_from_pdf(path: str) -> List[Dict]:
    """Parse bibliography entries from a PDF using DocumentIngestionTask's parsing methods.

    Uses the same splitting and parsing logic that runs during document ingestion
    so there is no duplicated extraction code.  Two pre-processing steps improve
    robustness for numbered-reference PDFs:

    1. Merge "N.\\n" (number on its own line) with the following line so the
       shared splitter sees the complete "N. Authors…" pattern.
    2. For numeric bibliographies, collapse multi-line entries onto single lines
       so continuation lines (e.g. "Bajracharya, R.; et al.") are not mistaken
       for the start of a new entry.
    """
    try:
        from core.services.document.document_ingestion_service import DocumentIngestionTask

        doc = fitz.open(path)
        try:
            pages_text = [doc.load_page(i).get_text() for i in range(len(doc))]
        finally:
            doc.close()
        raw_text = "\f".join(pages_text)

        # Step 1: merge standalone "N.\n" lines with the text that follows them.
        full_text = _STANDALONE_NUMBER_LINE.sub(r"\1. ", raw_text)

        task = DocumentIngestionTask(path=path, source_id=None, llm_manager=None, bus=None)
        spans = task._bibliography_spans(full_text)
        if not spans:
            return []
        bib_text = task._slice_spans(full_text, spans)

        # Step 2: for numbered lists, collapse wrapped lines so the shared
        # splitter does not split one entry on a continuation author name.
        if _is_numeric_bibliography(bib_text):
            bib_text = _collapse_numeric_continuations(bib_text)

        bibliography = task._extract_bibliography_entries(bib_text)

        result = []
        for entry in bibliography.values():
            journal = _journal_from_raw_bib(entry.raw)
            result.append({
                "citation_key": entry.key,
                "title": entry.title or "",
                "authors": ", ".join(entry.authors),
                "year": entry.year or "",
                "doi": entry.doi or "",
                "journal": journal,
            })
        return result
    except Exception:
        return []


# ---------------------------------------------------------------------------
# Background workers
# ---------------------------------------------------------------------------

class _MetadataWorker(QThread):
    """Phase 1: resolve DOI, journal, has_meta, has_refs for a PDF.

    Priority order (highest → lowest):
      1. Explicit overrides passed by the caller (e.g. user-supplied from Re-score)
      2. Stored citation record — same DB the citation dock populates via pm.get_citation()
      3. Live extraction via CitationManager (pymupdf + CrossRef) — same code citation dock uses
      4. pypdf fallback in OfflineSourceEvaluator for has_meta / has_refs structure flags

    has_meta and has_refs are always determined by the pypdf structural scan since
    they reflect PDF internal structure, not bibliographic metadata.
    """
    finished: Signal = Signal(str, object, object, bool, bool)
    failed: Signal = Signal(str, str)

    def __init__(
        self,
        pdf_path: str,
        evaluator: OfflineSourceEvaluator,
        citation_manager,
        project_manager,
        doi_override: Optional[str] = None,
        journal_override: Optional[str] = None,
    ) -> None:
        super().__init__()
        self._path = pdf_path
        self._evaluator = evaluator
        self._cm = citation_manager
        self._pm = project_manager
        self._doi_override = doi_override
        self._journal_override = journal_override

    def run(self) -> None:
        try:
            doi: Optional[str] = self._doi_override or None
            journal: Optional[str] = self._journal_override or None

            # --- Step 1: stored citation record (fast, no network) ---
            if not doi or not journal:
                try:
                    stored = self._pm.get_citation(self._path) or {}
                    doi = doi or stored.get("doi") or None
                    journal = journal or stored.get("journal") or None
                    # Some providers store journal under field aliases
                    if not journal:
                        fields = stored.get("fields", {})
                        journal = (
                            fields.get("journal")
                            or fields.get("container-title")
                            or fields.get("publicationTitle")
                            or None
                        )
                    # Strip empty strings to None
                    doi = doi if doi else None
                    journal = journal if journal else None
                except Exception:
                    pass

            # --- Step 2: CitationManager — the same extraction citation dock uses ---
            if not doi or not journal:
                try:
                    if self._cm is not None:
                        extracted = self._cm.extract_metadata(self._path)
                        doi = doi or extracted.get("doi") or None
                        journal = journal or extracted.get("journal") or None
                        doi = doi if doi else None
                        journal = journal if journal else None
                        # Persist so the citation dock sees this data next time
                        if extracted.get("doi") or extracted.get("title"):
                            try:
                                self._pm.upsert_citation(extracted)
                            except Exception:
                                pass
                except Exception:
                    pass

            # --- Step 3: structural flags via fitz (same library as citation dock) ---
            try:
                has_meta, has_refs = self._evaluator.extract_structural_flags(Path(self._path))
            except Exception:
                has_meta = False
                has_refs = False

            self.finished.emit(self._path, doi, journal, has_meta, has_refs)
        except Exception as exc:
            self.failed.emit(self._path, str(exc))


@dataclass
class _ScoringJob:
    path: str
    doi: Optional[str]
    journal: Optional[str]
    has_meta: bool
    has_refs: bool
    citations: List[Dict]


class _ScoringWorker(QThread):
    """Phase 2: run all registered scoring metrics off the main thread."""
    finished: Signal = Signal(str, int, list, bool, bool)
    failed: Signal = Signal(str, str)

    def __init__(self, job: _ScoringJob, evaluator: OfflineSourceEvaluator) -> None:
        super().__init__()
        self._job = job
        self._evaluator = evaluator

    def run(self) -> None:
        j = self._job
        try:
            citations = j.citations
            has_refs = j.has_refs
            if not citations:
                citations = _extract_bibliography_from_pdf(j.path)
                # If we found bibliography entries, the references section
                # exists — override has_refs so StructuralHeuristicMetric
                # doesn't contradict the CitationQualityMetric.
                if citations:
                    has_refs = True
            result = self._evaluator.run_scoring(
                Path(j.path), j.doi, j.journal,
                j.has_meta, has_refs,
                extracted_citations=citations,
            )
            self.finished.emit(
                j.path, result.score, result.ledger_dicts,
                result.is_retracted, result.needs_manual_review,
            )
        except Exception as exc:
            self.failed.emit(j.path, str(exc))


# ---------------------------------------------------------------------------
# Orchestrator service
# ---------------------------------------------------------------------------

class SourceEvaluationService(QObject):
    """
    Headless service — lives in PapyrusCore, accessible via AppContext.
    Communicates exclusively through the EventBus.
    """

    def __init__(self, project_manager, metrics_db_path: str, citation_manager=None) -> None:
        super().__init__()
        self._pm = project_manager
        self._cm = citation_manager   # same CitationManager the citation dock uses
        self._registry = build_default_source_scoring_registry()
        self._evaluator = OfflineSourceEvaluator(metrics_db_path, self._registry)
        self._bus: EventBus = EventBus.get_instance()

        self._pending: Set[str] = set()
        self._meta_workers: Dict[str, _MetadataWorker] = {}
        self._score_workers: Dict[str, _ScoringWorker] = {}
        self._pending_metadata: Dict[str, dict] = {}

        self._bus.document_added.connect(self._on_document_added)
        self._bus.discovery_state_changed.connect(self._on_discovery_state_changed)
        self._bus.source_eval_action_requested.connect(self._on_eval_action_requested)
        self._bus.source_eval_state_changed.connect(self._on_eval_state_changed)
        self._bus.citation_action_requested.connect(self._on_citation_action)

    # ------------------------------------------------------------------
    # Event handlers
    # ------------------------------------------------------------------

    def _on_document_added(self, event, payload) -> None:
        if event != DocumentEvent.DOCUMENT_ADDED:
            return
        path = getattr(payload, "path", None)
        if path:
            self._pending.add(path)

    def _on_discovery_state_changed(self, event, payload) -> None:
        if event != DiscoveryEvent.EXTRACTION_COMPLETE:
            return
        path = getattr(payload, "source_path", None)
        if path and path in self._pending:
            self._pending.discard(path)
            self._start_phase1(path, user_requested=False)

    def _on_eval_action_requested(self, intent, payload) -> None:
        if intent != SourceEvalIntent.RUN_EVALUATION:
            return
        path = getattr(payload, "pdf_path", None)
        if not path:
            return
        self._pending.discard(path)
        doi_override = getattr(payload, "doi", None) or None
        journal_override = getattr(payload, "journal", None) or None
        self._start_phase1(
            path,
            user_requested=True,
            doi_override=doi_override,
            journal_override=journal_override,
        )

    def _on_eval_state_changed(self, event, payload) -> None:
        if event != SourceEvalEvent.METADATA_PROVIDED:
            return
        cid = getattr(payload, "correlation_id", None)
        state = self._pending_metadata.pop(cid, None)
        if not state:
            return
        doi = getattr(payload, "doi", None) or state.get("doi")
        journal = getattr(payload, "journal", None) or state.get("journal")
        self._start_phase2(
            state["path"], doi, journal,
            state["has_meta"], state["has_refs"],
        )

    def _on_citation_action(self, intent, payload) -> None:
        """Re-score when the user updates citation data in the citation dock."""
        if intent != CitationIntent.UPDATE_ENTRY:
            return
        data = getattr(payload, "data", None) or {}
        if not isinstance(data, dict):
            return
        path = data.get("doc_id") or data.get("path")
        if not path:
            return
        # Only re-score docs that have already been evaluated
        if self.get_score_for_path(path) is None:
            return
        # pm.upsert_citation(data) has already been called by CitationAppService
        # (it connects before us since it's instantiated first in PapyrusCore)
        self._start_phase1(path, user_requested=False)

    # ------------------------------------------------------------------
    # Pipeline
    # ------------------------------------------------------------------

    def _start_phase1(
        self,
        path: str,
        user_requested: bool = False,
        doi_override: Optional[str] = None,
        journal_override: Optional[str] = None,
    ) -> None:
        self._bus.source_eval_state_changed.emit(
            SourceEvalEvent.EVALUATION_STARTED,
            SourceEvalEventPayload(pdf_path=path),
        )

        # The worker itself handles all three extraction paths in priority order:
        # stored citation record → CitationManager (pymupdf + CrossRef) → pypdf fallback
        worker = _MetadataWorker(
            path, self._evaluator, self._cm, self._pm,
            doi_override=doi_override, journal_override=journal_override,
        )
        worker.finished.connect(
            lambda p, doi, journal, hm, hr: self._on_metadata_ready(
                p, doi, journal, hm, hr, user_requested=user_requested
            )
        )
        worker.failed.connect(self._on_phase1_error)
        self._meta_workers[path] = worker
        worker.start()

    def _on_metadata_ready(
        self, path: str, doi, journal, has_meta: bool, has_refs: bool,
        user_requested: bool = False,
    ) -> None:
        self._meta_workers.pop(path, None)
        missing = []
        if not doi:
            missing.append("doi")
        if not journal:
            missing.append("journal")

        if missing and user_requested:
            # Only interrupt the user with a dialog when they explicitly asked
            cid = str(uuid.uuid4())
            self._pending_metadata[cid] = {
                "path": path, "doi": doi, "journal": journal,
                "has_meta": has_meta, "has_refs": has_refs,
            }
            self._bus.source_eval_state_changed.emit(
                SourceEvalEvent.METADATA_REQUESTED,
                SourceEvalEventPayload(
                    pdf_path=path,
                    correlation_id=cid,
                    missing_fields=missing,
                    doi=doi,
                    journal=journal,
                ),
            )
        else:
            # Background auto-evaluation: score with whatever metadata we have
            self._start_phase2(path, doi, journal, has_meta, has_refs)

    def _on_phase1_error(self, path: str, error: str) -> None:
        self._meta_workers.pop(path, None)
        self._bus.source_eval_state_changed.emit(
            SourceEvalEvent.EVALUATION_COMPLETE,
            SourceEvalEventPayload(pdf_path=path, error=error),
        )

    def _start_phase2(
        self, path: str, doi, journal, has_meta: bool, has_refs: bool
    ) -> None:
        citations = self._load_citations_for_path(path)
        job = _ScoringJob(
            path=path, doi=doi, journal=journal,
            has_meta=has_meta, has_refs=has_refs,
            citations=citations,
        )
        worker = _ScoringWorker(job, self._evaluator)
        worker.finished.connect(self._on_scoring_done)
        worker.failed.connect(self._on_phase2_error)
        self._score_workers[path] = worker
        worker.start()

    def _on_scoring_done(
        self, path: str, score: int, ledger: list,
        is_retracted: bool, needs_review: bool,
    ) -> None:
        self._score_workers.pop(path, None)
        self._persist_score(path, score, ledger)
        self._bus.source_eval_state_changed.emit(
            SourceEvalEvent.EVALUATION_COMPLETE,
            SourceEvalEventPayload(
                pdf_path=path,
                score=score,
                ledger=ledger,
                is_retracted=is_retracted,
                needs_manual_review=needs_review,
            ),
        )

    def _on_phase2_error(self, path: str, error: str) -> None:
        self._score_workers.pop(path, None)
        self._bus.source_eval_state_changed.emit(
            SourceEvalEvent.EVALUATION_COMPLETE,
            SourceEvalEventPayload(pdf_path=path, error=error),
        )

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _load_citations_for_path(self, path: str) -> List[Dict]:
        """Return the properties dicts of all citation entities referenced by this source.

        Deduplicates by citation_key so multiple extraction runs don't inflate
        the count.
        """
        try:
            db = getattr(self._pm, "db_graph", None)
            if not db:
                return []
            source = db.get_source_entity_by_path(path)
            if not source:
                return []
            relations = db.get_relations_for_entity(
                source.id, as_source=True, as_target=False
            )
            seen_keys: Set[str] = set()
            citations = []
            for rel in relations:
                if getattr(rel, "relation_type", "") != "relation.references":
                    continue
                cited = db.get_entity(rel.target_id)
                if not cited:
                    continue
                ckey = cited.properties.get("citation_key")
                if not ckey or ckey in seen_keys:
                    continue
                seen_keys.add(ckey)
                citations.append(dict(cited.properties))
            return citations
        except Exception:
            return []

    def _persist_score(self, path: str, score: int, ledger: list) -> None:
        try:
            db = getattr(self._pm, "db_graph", None)
            if not db:
                return
            entity = db.get_source_entity_by_path(path)
            if not entity:
                return
            entity.properties["source_score"] = score
            entity.properties["score_ledger"] = ledger
            entity.properties["score_computed_at"] = datetime.now(timezone.utc).isoformat()
            db.upsert_entity(entity)
        except Exception:
            pass

    # ------------------------------------------------------------------
    # Public helpers
    # ------------------------------------------------------------------

    def get_score_for_path(self, path: str) -> Optional[int]:
        try:
            db = getattr(self._pm, "db_graph", None)
            if not db:
                return None
            entity = db.get_source_entity_by_path(path)
            return entity.properties.get("source_score") if entity else None
        except Exception:
            return None

    def get_ledger_for_path(self, path: str) -> Optional[List[Dict]]:
        try:
            db = getattr(self._pm, "db_graph", None)
            if not db:
                return None
            entity = db.get_source_entity_by_path(path)
            return entity.properties.get("score_ledger") if entity else None
        except Exception:
            return None

    def register_metric(self, defn) -> None:
        self._registry.register(defn)

    def remove_plugin_metrics(self, plugin_id: str) -> None:
        self._registry.remove_by_plugin(plugin_id)
