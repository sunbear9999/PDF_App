"""Resolve an artifact to an honest, typed source navigation target.

Every "Jump to source" surface should call :meth:`SourceAnchorResolver.resolve`
instead of passing a basename and quote to a viewer's text search. The
resolver identifies the source by ID (never by filename), places the artifact
inside its recorded block using producer offsets, repairs a stale offset only
by a unique in-block text match, and computes PDF highlight rectangles from
the same block assembly the parser used. The result's ``precision`` says what
was actually achieved; nothing silently falls back to another occurrence.

Headless: no Qt. Resolution may parse a PDF page, so GUI callers run it on a
worker thread.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, replace
from typing import Any, Dict, List, Optional

from core.models.intelligence_models import ArtifactRecord
from core.models.source_anchor_models import (
    AnchorGranularity, AnchorSpan, NavigationPrecision, SourceNavigationTarget,
    rect_tuple, rect_tuples, revision_id_for, unresolved,
)
from core.models.producer_models import identity_of
from core.services.intelligence.anchor_text_locator import (
    find_occurrences, locate_in_block, map_normalized_range, text_key, weakest,
)

_TIME_SOURCE_TYPES = {"video", "audio"}
# Smallest recorded units first, so a quote resolves to a sentence when it can.
_QUOTE_UNITS = ("sentence", "transcript_segment", "block", "table_row")
_FILE_CHECKED_TYPES = {"pdf"}


@dataclass
class _SpanRequest:
    block_id: Optional[str]
    page: Optional[int]
    char_start: Optional[int]
    char_end: Optional[int]
    expected: Optional[str]


class SourceAnchorResolver:
    def __init__(
        self,
        project_manager: Any = None,
        artifact_service: Any = None,
        document_extraction_service: Any = None,
        pdf_geometry: Any = None,
    ) -> None:
        self._pm = project_manager
        self._artifacts = artifact_service
        self._des = document_extraction_service
        self._geometry = pdf_geometry

    # ------------------------------------------------------------------ API

    def resolve(self, artifact_id: str) -> SourceNavigationTarget:
        db = getattr(self._artifacts,"db",None)
        try:
            current = db.resolve_current_artifact_id(artifact_id) if db and hasattr(db,"resolve_current_artifact_id") else artifact_id
        except ValueError as exc:
            return unresolved(artifact_id,str(exc))
        artifact = self._get_artifact(current)
        if not artifact:
            return unresolved(artifact_id,"The artifact no longer exists.")
        target = self._resolve_record(artifact)
        if current != artifact_id:
            target = replace(target,artifact_id=artifact_id,reason="Followed confirmed source correspondence. " + target.reason)
        return target

    def _resolve_record(self, artifact) -> SourceNavigationTarget:
        artifact_id = artifact["artifact_id"]
        source = self._source(artifact.get("source_id"))
        base = {
            "source_id": (source or {}).get("id") or artifact.get("source_id"),
            "source_path": (source or {}).get("path") or artifact.get("source_path"),
            "source_type": (source or {}).get("source_type") or artifact.get("source_type"),
        }
        missing = self._missing_reason(source, base)
        if missing:
            return SourceNavigationTarget(
                artifact_id=artifact_id, precision=NavigationPrecision.MISSING_SOURCE,
                reason=missing, **base,
            )
        if any((artifact.get("metadata") or {}).get(key) for key in ('reference_note', 'manual_event')):
            return self._saved_note_target(artifact, base)
        locator = artifact.get("locator") or {}
        if base["source_type"] in _TIME_SOURCE_TYPES and locator.get("start") is not None:
            return self._time_target(artifact, locator, base)
        if artifact.get("artifact_type") == "annotation" and locator.get("rects"):
            return self._region_target(artifact, locator, base)
        if locator.get("row_index") is not None:
            return self._row_target(artifact, locator, base)
        requests = self._span_requests(artifact, locator)
        if not requests:
            return unresolved(artifact_id, "No source location was recorded for this item.", **base)
        return self._check_revision(artifact, self._text_target(artifact, requests, base))

    def resolve_artifact_quote(self, artifact_id, quote, *, allow_replacement=False):
        """Resolve inside the named artifact only; never search another passage."""
        target = self.resolve(artifact_id)
        db = getattr(self._artifacts, "db", None)
        row = db.get_artifact(db.resolve_current_artifact_id(artifact_id)) if db else None
        if not row or target.precision not in {NavigationPrecision.EXACT, NavigationPrecision.REPAIRED}:
            return target
        text = str(row.get("exact_text") or "")
        if allow_replacement and not find_occurrences(text, quote):
            return target
        if not quote or len(find_occurrences(text, quote)) != 1:
            return unresolved(artifact_id, "The saved quote is not unique inside its artifact.")
        if target.source_type in _TIME_SOURCE_TYPES:
            return target  # Segment precision is explicit; no invented word timing.
        if quote == text and (len(target.spans) != 1 or target.spans[0].char_start is None):
            return target
        return self._quote_inside_target(row, quote, target)

    def _quote_inside_target(self, row, quote, target):
        if len(target.spans) != 1:
            return unresolved(row["artifact_id"], "This subquote needs a verified range inside one recorded span.")
        span = target.spans[0]
        block = self._block(target.source_id, span.block_id) if span.block_id else None
        if block is None or span.char_start is None or span.char_end is None:
            return unresolved(row["artifact_id"], "This subquote needs a verified character range.")
        hits = find_occurrences(block.exact_text[span.char_start:span.char_end], quote)
        if len(hits) != 1:
            return unresolved(row["artifact_id"], "The quote is ambiguous inside its recorded span.")
        start, end = hits[0]
        locator = {"block_id": span.block_id, "page": span.page_index,
                   "char_start": span.char_start + start, "char_end": span.char_start + end}
        probe = {**row, "artifact_type": "quote", "exact_text": quote, "locator": locator}
        scoped = self._text_target(probe, self._span_requests(probe, locator), {
            "source_id": target.source_id, "source_path": target.source_path, "source_type": target.source_type})
        return replace(scoped, precision=weakest([scoped.precision, target.precision]),
                       reason="Verified inside the referenced artifact's recorded span. " + target.reason)

    def _saved_note_target(self, artifact, base):
        metadata = artifact["metadata"]
        stored = metadata.get("saved_anchor") or {}
        pinned = metadata.get("saved_source_hash")
        if pinned and self._artifacts.source_input_hash(base["source_path"]) != pinned:
            return unresolved(artifact["artifact_id"], "The source changed after this linked record was saved. Review its location.", **base)
        try:
            spans = tuple(AnchorSpan(**{**span, "rects": rect_tuples(span.get("rects"))}) for span in stored["spans"])
            return SourceNavigationTarget(artifact_id=artifact["artifact_id"],
                precision=NavigationPrecision(stored["precision"]), granularity=AnchorGranularity(stored["granularity"]),
                spans=spans, coordinate_basis=stored.get('coordinate_basis', 'block_raw_text'),
                reason="Saved source span. " + stored.get("reason", ""), **base)
        except (KeyError, ValueError, TypeError):
            return unresolved(artifact["artifact_id"], "This linked note's saved anchor requires repair.", **base)

    def _check_revision(self, artifact, target: SourceNavigationTarget) -> SourceNavigationTarget:
        """Downgrade 'exact' when the file changed after extraction."""
        identity = identity_of(artifact)
        pinned = identity.source_revision if identity else ""
        hasher = getattr(self._artifacts, "source_input_hash", None)
        if not pinned or not callable(hasher) or target.precision != NavigationPrecision.EXACT:
            return target
        current = revision_id_for(hasher(target.source_path) or "")
        if not current or current == pinned:
            return target
        return replace(target, precision=NavigationPrecision.REPAIRED, reason=(
            "The source file changed after this item was extracted; its location "
            "was re-verified against the current text. Rerun processing to refresh it."
        ))

    def resolve_quote(self, hint: Dict[str, Any], quote: str) -> SourceNavigationTarget:
        """Place a free-text quote (chat, notes, search) in one recorded passage.

        The source is chosen by ID/path, else by exact file name; when names
        are ambiguous the quote's location decides. Only a unique match is
        navigable — callers fall back to text search and say so otherwise.
        """
        quote = _clean_quote(quote)
        if not quote:
            return unresolved("", "No quoted text to locate.")
        hits = []
        for source in self._candidate_sources(hint):
            hits.extend(self._passages_containing(source, quote))
        if len(hits) != 1:
            reason = ("The quoted text was not found in recorded passages." if not hits
                      else f"The quoted text appears in {len(hits)} recorded passages.")
            return unresolved("", reason)
        return self._quote_target(hits[0], quote)

    def _candidate_sources(self, hint: Dict[str, Any]) -> List[Dict[str, Any]]:
        sources = [s for s in (self._pm.list_sources() if self._pm else [])]
        if hint.get("source_id"):
            return [s for s in sources if s.get("id") == hint["source_id"]]
        if hint.get("path"):
            return [s for s in sources if s.get("path") == hint["path"]]
        name = str(hint.get("doc_name") or "").strip().casefold()
        exact = [s for s in sources if os.path.basename(s.get("path") or "").casefold() == name]
        return exact or [s for s in sources
                         if name and name in os.path.basename(s.get("path") or "").casefold()]

    def _passages_containing(self, source: Dict[str, Any], quote: str) -> List[Dict[str, Any]]:
        db = getattr(self._artifacts, "db", None)
        if not db:
            return []
        needle = text_key(quote)
        for kind in _QUOTE_UNITS:
            rows = [row for row in db.list_artifacts(source_id=source.get("id"), artifact_type=kind,
                                                     status="complete", limit=100000)
                    if needle in text_key(row.get("exact_text"))]
            if rows:
                return rows
        return []

    def _quote_target(self, row: Dict[str, Any], quote: str) -> SourceNavigationTarget:
        locator = row.get("locator") or {}
        if locator.get("start") is not None and not locator.get("block_id"):
            return self.resolve(row["artifact_id"])
        probe = {**row, "artifact_type": "quote", "exact_text": quote,
                 "locator": {**locator, "char_end": None}}
        target = self._text_target(probe, self._span_requests(probe, probe["locator"]), {
            "source_id": row.get("source_id"), "source_path": row.get("source_path"),
            "source_type": row.get("source_type"),
        })
        precision = (NavigationPrecision.REPAIRED
                     if target.precision == NavigationPrecision.EXACT else target.precision)
        return replace(target, artifact_id=row["artifact_id"], precision=precision, reason=(
            "Located by matching the quoted text to one recorded passage."
            if precision == NavigationPrecision.REPAIRED else target.reason))

    # ------------------------------------------------------------- sources

    def _get_artifact(self, artifact_id: str) -> Optional[Dict[str, Any]]:
        db = getattr(self._artifacts, "db", None)
        return db.get_artifact(artifact_id) if db and artifact_id else None

    def _source(self, source_id: Optional[str]) -> Optional[Dict[str, Any]]:
        lookup = getattr(self._pm, 'get_source_record', None)
        if lookup:
            return lookup(source_id)
        if not self._pm or not source_id:
            return None
        for source in self._pm.list_sources(include_removed=True):
            if source.get("id") == source_id:
                return source
        return None

    @staticmethod
    def _missing_reason(source: Optional[Dict[str, Any]], base: Dict[str, Any]) -> str:
        if not source:
            return "The source this item came from is not in the project."
        if (source.get("state") or {}).get("is_removed"):
            return "The source this item came from was removed from the project."
        path = base.get("source_path") or ""
        if base.get("source_type") in _FILE_CHECKED_TYPES and not os.path.exists(path):
            return f"The source file is missing: {path}"
        return ""

    # ----------------------------------------------------------- non-text

    @staticmethod
    def _time_target(artifact, locator, base) -> SourceNavigationTarget:
        span = AnchorSpan(
            time_start=_float(locator.get("start")),
            time_end=_float(locator.get("end")),
            quote=str(artifact.get("exact_text") or ""),
        )
        return SourceNavigationTarget(
            artifact_id=artifact["artifact_id"], precision=NavigationPrecision.EXACT,
            granularity=AnchorGranularity.TIME_RANGE, spans=(span,),
            reason="Transcript segment time range (segment-level, not word-level).",
            **base,
        )

    @staticmethod
    def _region_target(artifact, locator, base) -> SourceNavigationTarget:
        span = AnchorSpan(
            page_index=_int(locator.get("page")),
            block_id=locator.get("block_id"),
            quote=str(artifact.get("exact_text") or ""),
            rects=rect_tuples(locator.get("rects")),
        )
        precision = NavigationPrecision.EXACT if span.rects else NavigationPrecision.COARSE
        return SourceNavigationTarget(
            artifact_id=artifact["artifact_id"], precision=precision,
            granularity=AnchorGranularity.REGION, spans=(span,),
            reason="Your highlight's recorded region.", **base,
        )

    @staticmethod
    def _row_target(artifact, locator, base) -> SourceNavigationTarget:
        row = int(locator["row_index"])
        return SourceNavigationTarget(
            artifact_id=artifact["artifact_id"], precision=NavigationPrecision.COARSE,
            granularity=AnchorGranularity.ROW,
            spans=(AnchorSpan(quote=str(artifact.get("exact_text") or "")),),
            reason=f"Opens the data file at row {row + 1}; the viewer cannot select a row yet.",
            **base,
        )

    # --------------------------------------------------------- span inputs

    def _span_requests(self, artifact, locator) -> List[_SpanRequest]:
        kind = artifact.get("artifact_type")
        if kind == "entity_mention":
            mapped = self._entity_mention_request(artifact, locator)
            if mapped:
                return mapped
        expected = _expected_text(artifact)
        spans = locator.get("source_spans") or []
        if len(spans) > 1:
            # Reconstructed multi-block sentence: each component is verified
            # on its own, since the joined text matches no single block.
            return [
                _SpanRequest(s.get("block_id"), _int(s.get("page")), _int(s.get("char_start")),
                             _int(s.get("char_end")), None)
                for s in spans if s.get("block_id")
            ]
        if not locator.get("block_id") and locator.get("page") is None:
            return []
        return [_SpanRequest(
            locator.get("block_id"), _int(locator.get("page")),
            _int(locator.get("char_start")), _int(locator.get("char_end")), expected,
        )]

    def _entity_mention_request(self, artifact, locator) -> Optional[List[_SpanRequest]]:
        """Entity offsets are relative to the normalized sentence text; map
        them through the sentence's raw block range."""
        sentence_id = locator.get("sentence_id")
        db = getattr(self._artifacts, "db", None)
        if not sentence_id or not db:
            return None
        sentence = db.get_artifact(ArtifactRecord.stable_id(
            artifact.get("source_id") or "", "sentence", "extraction_sentences", sentence_id,
        ))
        from core.services.intelligence.mention_coordinates import mention_spans
        spans = mention_spans(sentence or {}, locator, lambda identity: self._block(artifact.get("source_id"), identity))
        if spans:
            return [_SpanRequest(s["block_id"], s["page"], s["char_start"], s["char_end"], None)
                    for s in spans]
        # Legacy rows without reconstructable spans remain coarse, never
        # silently navigate to another occurrence of a repeated name.
        sent_loc = (sentence or {}).get("locator") or {}
        return [_SpanRequest(sent_loc.get("block_id"), _int(sent_loc.get("page")), None, None, None)] if sent_loc else None

    # -------------------------------------------------------------- text

    def _text_target(self, artifact, requests, base) -> SourceNavigationTarget:
        blocks = self._blocks_by_id(base["source_id"], base["source_path"])
        spans: List[AnchorSpan] = []
        precisions: List[NavigationPrecision] = []
        for request in requests:
            span, precision = self._locate(request, blocks.get(request.block_id or ""))
            spans.append(span)
            precisions.append(precision)
        if base["source_type"] == "pdf" or base["source_type"] is None:
            spans, precisions = self._with_pdf_geometry(base["source_path"], spans, precisions, blocks)
        precision = weakest(precisions)
        granular = any(span.char_start is not None for span in spans)
        return SourceNavigationTarget(
            artifact_id=artifact["artifact_id"], precision=precision,
            granularity=_granularity(granular, spans), spans=tuple(spans),
            reason=_reason(precision), **base,
        )

    @staticmethod
    def _locate(request: _SpanRequest, block) -> tuple:
        if block is None:
            span = AnchorSpan(page_index=request.page, block_id=request.block_id,
                              quote=request.expected or "")
            return span, NavigationPrecision.COARSE
        located = locate_in_block(block.exact_text, request.char_start,
                                  request.char_end, request.expected)
        quote = (
            block.exact_text[located.char_start:located.char_end]
            if located.char_start is not None else (request.expected or "")
        )
        page = getattr(block, "page_num", None)
        span = AnchorSpan(
            page_index=page if page is not None else request.page,
            block_id=block.block_id, char_start=located.char_start,
            char_end=located.char_end, quote=quote,
        )
        return span, located.precision

    def _with_pdf_geometry(self, path, spans, precisions, blocks) -> tuple:
        if not self._geometry or not path:
            return spans, precisions
        out_spans, out_precisions = [], []
        for span, precision in zip(spans, precisions):
            block = blocks.get(span.block_id or "")
            rects, precision = self._geometry.rects_for_span(path, span, block, precision)
            out_spans.append(_replace_rects(span, rects))
            out_precisions.append(precision)
        return out_spans, out_precisions

    # ------------------------------------------------------------- blocks

    def _blocks_by_id(self, source_id, path) -> Dict[str, Any]:
        if not self._des:
            return {}
        blocks = self._des.get_blocks(source_id) or (self._des.get_blocks(path) if path else [])
        return {block.block_id: block for block in blocks or []}

    def _block(self, source_id, block_id):
        if not block_id:
            return None
        source = self._source(source_id) or {}
        return self._blocks_by_id(source_id, source.get("path")).get(block_id)


def _clean_quote(quote: str) -> str:
    """Strip wrapping quote marks and truncation ellipses from a quoted span."""
    text = str(quote or "").strip().strip("\"'“”‘’").strip()
    for ellipsis in ("...", "…"):
        text = text.removesuffix(ellipsis).removeprefix(ellipsis).strip()
    return text


def _expected_text(artifact: Dict[str, Any]) -> Optional[str]:
    metadata = artifact.get("metadata") or {}
    kind = artifact.get("artifact_type")
    if kind == "date_mention":
        return metadata.get("exact_text") or None
    if kind == "in_text_citation":
        return metadata.get("raw_text") or None
    return artifact.get("exact_text") or None


def _replace_rects(span: AnchorSpan, rects) -> AnchorSpan:
    return replace(span, rects=tuple(r for r in (rect_tuple(x) for x in rects or []) if r))


def _granularity(granular: bool, spans) -> AnchorGranularity:
    if granular:
        return AnchorGranularity.CHAR_RANGE
    if any(span.block_id for span in spans):
        return AnchorGranularity.BLOCK
    return AnchorGranularity.PAGE


_REASONS = {
    NavigationPrecision.EXACT: "Exact recorded source span.",
    NavigationPrecision.REPAIRED: (
        "Recorded offsets were missing or stale; the exact text was found inside "
        "its recorded block."
    ),
    NavigationPrecision.COARSE: (
        "Only the block or page is known; the exact text could not be placed "
        "without guessing between occurrences."
    ),
}


def _reason(precision: NavigationPrecision) -> str:
    return _REASONS.get(precision, "")


def _int(value: Any) -> Optional[int]:
    try:
        return int(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _float(value: Any) -> Optional[float]:
    try:
        return float(value) if value is not None else None
    except (TypeError, ValueError):
        return None
