"""
core/services/document/source_evaluator_service.py

Offline PDF source evaluator.  All PDF I/O uses fitz (pymupdf) — no pypdf.
DOI and journal are resolved upstream (citation_manager / pm.get_citation);
this file only handles structural flags (has_meta, has_refs) and scoring.
"""
from __future__ import annotations

import re
import sqlite3
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional

import fitz

from core.registries.source_scoring_registry import (
    BaseScoringMetric,
    MetricResult,
    SourceScoringRegistry,
    build_default_source_scoring_registry,
)


@dataclass
class SourceEvaluation:
    filepath: Path
    doi: Optional[str] = None
    journal: Optional[str] = None
    score: int = 0
    is_retracted: bool = False
    needs_manual_review: bool = False
    ledger: List[MetricResult] = field(default_factory=list)

    @property
    def ledger_dicts(self) -> List[Dict]:
        """Serialisable list of metric results for storage in GraphDB."""
        return [
            {
                "metric_id": r.metric_id,
                "label": r.label,
                "points": r.points,
                "max_points": r.max_points,
                "reasoning": r.reasoning,
                "db_used": r.db_used,
            }
            for r in self.ledger
        ]


_DOI_RE = re.compile(r"10\.\d{4,9}/[-._;()/:a-zA-Z0-9]+", re.IGNORECASE)


class OfflineSourceEvaluator:
    def __init__(
        self,
        db_path: str | Path,
        registry: Optional[SourceScoringRegistry] = None,
    ) -> None:
        self._db_path = Path(db_path)
        self._registry = registry or build_default_source_scoring_registry()

    def _open_db(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self._db_path)
        conn.row_factory = sqlite3.Row
        return conn

    # ------------------------------------------------------------------
    # Metadata extraction (I/O only — no scoring)
    # ------------------------------------------------------------------

    def extract_structural_flags(self, pdf_path: Path) -> tuple[bool, bool]:
        """Return (has_meta, has_references) using fitz (no pypdf)."""
        try:
            doc = fitz.open(str(pdf_path))
            meta = doc.metadata or {}
            has_meta = bool(
                meta.get("title") or meta.get("author")
                or meta.get("creator") or meta.get("producer")
            )
            num_pages = len(doc)
            # Search the last half of the document for the bibliography heading.
            # "Last 5 pages" was too narrow — a 20-page paper with a References
            # section starting on page 15 (1-based) = page 14 (0-indexed) was
            # missed because range(15,20) excluded page 14.
            check_from = max(0, num_pages // 2)
            check_text = " ".join(
                doc.load_page(i).get_text()
                for i in range(check_from, num_pages)
            ).lower()
            has_refs = bool(
                re.search(r"\b(references|bibliography|works\s+cited)\b", check_text)
            )
            doc.close()
            return has_meta, has_refs
        except Exception:
            return False, False

    # ------------------------------------------------------------------
    # Scoring
    # ------------------------------------------------------------------

    def run_scoring(
        self,
        pdf_path: Path,
        doi: Optional[str],
        journal: Optional[str],
        has_meta: bool,
        has_references: bool,
        extracted_citations: Optional[List[Dict]] = None,
    ) -> SourceEvaluation:
        """Run every registered metric and return a SourceEvaluation."""
        result = SourceEvaluation(filepath=pdf_path, doi=doi, journal=journal)
        citations = extracted_citations or []

        conn = self._open_db()
        try:
            for defn in self._registry.all():
                try:
                    metric: BaseScoringMetric = defn.metric_cls()
                    mr = metric.compute(
                        str(pdf_path), doi, journal,
                        has_meta, has_references, citations, conn,
                    )
                    result.ledger.append(mr)
                except Exception as exc:
                    result.ledger.append(MetricResult(
                        metric_id=defn.id,
                        label=defn.label,
                        points=0,
                        max_points=getattr(defn.metric_cls, "max_points", 0),
                        reasoning=f"Metric error: {exc}",
                    ))
        finally:
            conn.close()

        # Tally score — retraction result uses sentinel −999 to zero everything
        raw = sum(r.points for r in result.ledger)
        if any(r.points == -999 for r in result.ledger):
            result.is_retracted = True
            result.score = 0
            # Replace sentinel with display-friendly value
            for r in result.ledger:
                if r.points == -999:
                    r.points = -result.score  # will be 0; keep label intact
        else:
            result.score = max(0, min(100, raw))

        if any("predatory" in (r.reasoning or "").lower() for r in result.ledger if r.points < 0):
            result.needs_manual_review = True

        return result

    # ------------------------------------------------------------------
    # Convenience: extract + score in one call
    # ------------------------------------------------------------------

    def evaluate(
        self,
        pdf_path: Path,
        doi_override: Optional[str] = None,
        journal_override: Optional[str] = None,
        extracted_citations: Optional[List[Dict]] = None,
    ) -> SourceEvaluation:
        doi, journal, has_meta, has_refs = self.extract_metadata(pdf_path)
        doi = doi_override or doi
        journal = journal_override or journal
        return self.run_scoring(
            pdf_path, doi, journal, has_meta, has_refs,
            extracted_citations=extracted_citations,
        )
