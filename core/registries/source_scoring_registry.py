"""
core/registries/source_scoring_registry.py

Plugin-ready registry for source quality scoring metrics.
Each metric is a BaseScoringMetric subclass; plugins register additional ones.
"""
from __future__ import annotations

import re
import sqlite3
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Dict, Iterable, List, Optional, Type


# ---------------------------------------------------------------------------
# Data types
# ---------------------------------------------------------------------------

@dataclass
class MetricResult:
    metric_id: str
    label: str
    points: int
    max_points: int
    reasoning: str
    db_used: Optional[str] = None


# ---------------------------------------------------------------------------
# Base class
# ---------------------------------------------------------------------------

class BaseScoringMetric(ABC):
    metric_id: str = ""
    label: str = ""
    description: str = ""
    max_points: int = 0
    plugin_id: Optional[str] = None

    @abstractmethod
    def compute(
        self,
        pdf_path: str,
        doi: Optional[str],
        journal: Optional[str],
        has_meta: bool,
        has_references: bool,
        extracted_citations: List[Dict],
        db_conn: sqlite3.Connection,
    ) -> MetricResult: ...


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------

@dataclass
class ScoringMetricDefinition:
    id: str
    label: str
    description: str
    metric_cls: Type[BaseScoringMetric]
    plugin_id: Optional[str] = None


class SourceScoringRegistry:
    def __init__(self) -> None:
        self._metrics: Dict[str, ScoringMetricDefinition] = {}

    def register(self, defn: ScoringMetricDefinition) -> None:
        self._metrics[defn.id] = defn

    def get(self, metric_id: str) -> Optional[ScoringMetricDefinition]:
        return self._metrics.get(metric_id)

    def all(self) -> Iterable[ScoringMetricDefinition]:
        return self._metrics.values()

    def remove_by_plugin(self, plugin_id: str) -> None:
        self._metrics = {mid: d for mid, d in self._metrics.items()
                         if d.plugin_id != plugin_id}


# ---------------------------------------------------------------------------
# Built-in metrics
# ---------------------------------------------------------------------------

class MetadataCompletenessMetric(BaseScoringMetric):
    metric_id = "metadata"
    label = "Metadata Completeness"
    description = "Awards points when the PDF contains embedded metadata."
    max_points = 10

    def compute(self, pdf_path, doi, journal, has_meta, has_references,
                extracted_citations, db_conn) -> MetricResult:
        if has_meta:
            return MetricResult(self.metric_id, self.label, 10, 10,
                                "PDF contains embedded metadata.")
        return MetricResult(self.metric_id, self.label, 0, 10,
                            "No embedded metadata found in PDF.")


class StructuralHeuristicMetric(BaseScoringMetric):
    metric_id = "structure"
    label = "Bibliography Present"
    description = "Awards points when a References/Bibliography section is detected."
    max_points = 15

    def compute(self, pdf_path, doi, journal, has_meta, has_references,
                extracted_citations, db_conn) -> MetricResult:
        if has_references:
            return MetricResult(self.metric_id, self.label, 15, 15,
                                "References / Bibliography section detected.")
        return MetricResult(self.metric_id, self.label, 0, 15,
                            "No References or Bibliography section found.")


class DOIPresenceMetric(BaseScoringMetric):
    metric_id = "doi"
    label = "DOI Presence"
    description = "Awards points when a valid DOI is found or provided."
    max_points = 15

    def compute(self, pdf_path, doi, journal, has_meta, has_references,
                extracted_citations, db_conn) -> MetricResult:
        if doi:
            return MetricResult(self.metric_id, self.label, 15, 15,
                                f"Valid DOI found: {doi}")
        return MetricResult(self.metric_id, self.label, 0, 15,
                            "No DOI found. Provide one manually for a higher score.")


class VenueQualityMetric(BaseScoringMetric):
    metric_id = "venue"
    label = "Venue Quality"
    description = "Checks SCImago SJR quartile and DOAJ membership for the journal."
    max_points = 60

    def compute(self, pdf_path, doi, journal, has_meta, has_references,
                extracted_citations, db_conn) -> MetricResult:
        if not journal:
            return MetricResult(self.metric_id, self.label, 0, 60,
                                "No journal name available. Provide one manually.",
                                db_used=None)
        cursor = db_conn.cursor()
        jl = journal.lower()

        cursor.execute("SELECT quartile FROM sjr WHERE LOWER(title) LIKE ? LIMIT 1",
                       (f"%{jl}%",))
        row = cursor.fetchone()
        if row:
            q = row[0] if isinstance(row, (list, tuple)) else row["quartile"]
            pts = {"Q1": 60, "Q2": 45, "Q3": 30, "Q4": 15}.get(q, 15)
            return MetricResult(self.metric_id, self.label, pts, 60,
                                f"SCImago SJR quartile {q} journal: {journal}",
                                db_used="SCImago SJR")

        cursor.execute("SELECT 1 FROM doaj WHERE LOWER(title) LIKE ? LIMIT 1",
                       (f"%{jl}%",))
        if cursor.fetchone():
            return MetricResult(self.metric_id, self.label, 45, 60,
                                f"Journal verified in DOAJ (open access): {journal}",
                                db_used="DOAJ")

        return MetricResult(self.metric_id, self.label, 0, 60,
                            f"Journal not found in SCImago or DOAJ: {journal}",
                            db_used="SCImago SJR / DOAJ")


class PredatoryJournalCheck(BaseScoringMetric):
    metric_id = "predatory"
    label = "Predatory Journal Check"
    description = "Applies a 50-point penalty if the journal appears on the Stop Predatory Journals list."
    max_points = 0

    def compute(self, pdf_path, doi, journal, has_meta, has_references,
                extracted_citations, db_conn) -> MetricResult:
        if not journal:
            return MetricResult(self.metric_id, self.label, 0, 0,
                                "No journal to check against predatory list.")
        cursor = db_conn.cursor()
        cursor.execute(
            "SELECT 1 FROM predatory_journals WHERE LOWER(title) LIKE ? LIMIT 1",
            (f"%{journal.lower()}%",),
        )
        if cursor.fetchone():
            return MetricResult(self.metric_id, self.label, -50, 0,
                                f"FLAG: Journal matches Stop Predatory Journals list: {journal}",
                                db_used="Stop Predatory Journals")
        return MetricResult(self.metric_id, self.label, 0, 0,
                            "Journal not found on predatory journals list.",
                            db_used="Stop Predatory Journals")


class RetractionWatchCheck(BaseScoringMetric):
    metric_id = "retraction"
    label = "Retraction Watch"
    description = "Zeroes the score if the document's own DOI appears in the Retraction Watch database."
    max_points = 0

    def compute(self, pdf_path, doi, journal, has_meta, has_references,
                extracted_citations, db_conn) -> MetricResult:
        if not doi:
            return MetricResult(self.metric_id, self.label, 0, 0,
                                "No DOI to check against Retraction Watch.")
        cursor = db_conn.cursor()
        cursor.execute("SELECT 1 FROM retractions WHERE doi = ? LIMIT 1", (doi,))
        if cursor.fetchone():
            return MetricResult(
                self.metric_id, self.label, -999, 0,
                f"FATAL: DOI {doi} found in Retraction Watch — paper has been retracted.",
                db_used="Retraction Watch",
            )
        return MetricResult(self.metric_id, self.label, 0, 0,
                            "DOI not found in Retraction Watch.",
                            db_used="Retraction Watch")


def _clean_bib_journal(raw: str) -> str:
    """Extract just the journal name from a raw bibliography trailing field.

    The deterministic extractor stores everything after the paper title as the
    'journal' field, e.g. "Nature Biotechnology, 10(2), 123-145.".  We strip
    the volume / issue / page tail so we're left with just the journal name.
    """
    if not raw:
        return ""
    # Stop at the first comma/period followed by digits (volume, issue, page)
    # or at common abbreviations vol. / no. / pp.
    m = re.search(
        r",\s*(?:\d|\bvol\.?|\bno\.?|\bpp\.?|\bpages?\b|\beditor|\bed\.|\bIn\b)",
        raw, re.IGNORECASE,
    )
    if m:
        raw = raw[:m.start()]
    # Remove trailing punctuation and whitespace
    raw = raw.strip(" .,;:'\"")
    # If still very long (> 80 chars) this is probably not a journal name
    if len(raw) > 80:
        return ""
    return raw


def _journal_is_predatory(cursor, journal_name: str) -> bool:
    """Precise predatory-journal check that avoids false positives.

    Uses two complementary SQL strategies so we don't discard real matches
    but also don't fire on short or generic substrings:

    1. Exact match (case-insensitive).
    2. The predatory-list title is contained IN the journal name — ensures
       the predatory name is a meaningful component of what was cited.

    Short journal names (< 6 chars) are skipped entirely.
    """
    j = journal_name.strip().lower()
    if len(j) < 6:
        return False
    # Exact match
    cursor.execute(
        "SELECT 1 FROM predatory_journals WHERE LOWER(title) = ? LIMIT 1", (j,)
    )
    if cursor.fetchone():
        return True
    # Predatory title appears as a substring of the cleaned journal name
    # (handles "Academic Exchange Quarterly" inside "Academic Exchange Quarterly, 5(3)")
    cursor.execute(
        "SELECT title FROM predatory_journals WHERE ? LIKE '%' || LOWER(title) || '%' LIMIT 5",
        (j,),
    )
    for (ptitle,) in cursor.fetchall():
        # Guard: the predatory name must cover at least 60 % of the journal name
        # to avoid very short predatory names falsely matching long strings
        if ptitle and len(ptitle) / max(len(j), 1) >= 0.6:
            return True
    return False


class CitationQualityMetric(BaseScoringMetric):
    metric_id = "citation_quality"
    label = "Citation Quality"
    description = (
        "Checks cited papers against Retraction Watch (−10 each) "
        "and cited journals against the predatory-journals list (−5 each). "
        "Max penalty −30.  Journal names are cleaned before matching to "
        "remove volume/issue/page noise from bibliography entries."
    )
    max_points = 0

    def compute(self, pdf_path, doi, journal, has_meta, has_references,
                extracted_citations, db_conn) -> MetricResult:
        if not extracted_citations:
            return MetricResult(self.metric_id, self.label, 0, 0,
                                "No extracted citations to evaluate.")

        cursor = db_conn.cursor()
        retracted_count = 0
        predatory_count = 0
        reasons: List[str] = []
        checked = 0

        for cite in extracted_citations:
            cited_doi = (cite.get("doi") or "").strip().lower()
            raw_journal = cite.get("journal") or ""
            cited_journal = _clean_bib_journal(raw_journal)
            cited_key = cite.get("citation_key") or cite.get("title") or "?"
            checked += 1

            if cited_doi:
                cursor.execute(
                    "SELECT 1 FROM retractions WHERE LOWER(doi) = ? LIMIT 1",
                    (cited_doi,),
                )
                if cursor.fetchone():
                    retracted_count += 1
                    reasons.append(f"Retracted: {cited_key}")

            if cited_journal:
                if _journal_is_predatory(cursor, cited_journal):
                    predatory_count += 1
                    reasons.append(f"Predatory journal: {cited_journal}")

        penalty = min(30, retracted_count * 10 + predatory_count * 5)

        if not reasons:
            return MetricResult(
                self.metric_id, self.label, 0, 0,
                f"{checked} citations checked — none flagged.",
                db_used="Retraction Watch / Stop Predatory Journals",
            )

        reason_summary = "; ".join(reasons[:5])
        if len(reasons) > 5:
            reason_summary += f" (+{len(reasons) - 5} more)"
        return MetricResult(
            self.metric_id, self.label, -penalty, 0,
            f"Penalty −{penalty} ({checked} checked): {reason_summary}",
            db_used="Retraction Watch / Stop Predatory Journals",
        )


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------

def build_default_source_scoring_registry() -> SourceScoringRegistry:
    registry = SourceScoringRegistry()
    defaults = [
        ScoringMetricDefinition("metadata", "Metadata Completeness",
                                MetadataCompletenessMetric.description,
                                MetadataCompletenessMetric),
        ScoringMetricDefinition("structure", "Bibliography Present",
                                StructuralHeuristicMetric.description,
                                StructuralHeuristicMetric),
        ScoringMetricDefinition("doi", "DOI Presence",
                                DOIPresenceMetric.description,
                                DOIPresenceMetric),
        ScoringMetricDefinition("venue", "Venue Quality",
                                VenueQualityMetric.description,
                                VenueQualityMetric),
        ScoringMetricDefinition("predatory", "Predatory Journal Check",
                                PredatoryJournalCheck.description,
                                PredatoryJournalCheck),
        ScoringMetricDefinition("retraction", "Retraction Watch",
                                RetractionWatchCheck.description,
                                RetractionWatchCheck),
        ScoringMetricDefinition("citation_quality", "Citation Quality",
                                CitationQualityMetric.description,
                                CitationQualityMetric),
    ]
    for defn in defaults:
        registry.register(defn)
    return registry
