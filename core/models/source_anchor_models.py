"""Typed source anchors and navigation results for artifact references.

An artifact's ``locator`` dict is producer-shaped and historically loose. These
models are the resolved, validated form every navigation surface consumes: the
GUI never infers a source from a basename or re-searches text when one of
these is available, and ``precision`` states honestly how exact it is.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional, Tuple

# Character offsets are into DocumentBlock.exact_text (raw parser text,
# before soft-hyphen/line-wrap normalization).
COORDINATE_BASIS_BLOCK_RAW = "block_raw_text"


class NavigationPrecision(str, Enum):
    EXACT = "exact"                    # producer offsets verified against source text
    REPAIRED = "repaired"              # recorded text found once inside its recorded block
    COARSE = "coarse"                  # only block/page/segment is known
    MISSING_SOURCE = "missing_source"  # source record or file is gone
    UNRESOLVED = "unresolved"          # no usable location was recorded


class AnchorGranularity(str, Enum):
    CHAR_RANGE = "char_range"
    BLOCK = "block"
    PAGE = "page"
    TIME_RANGE = "time_range"
    REGION = "region"
    ROW = "row"
    NONE = "none"


@dataclass(frozen=True)
class AnchorSpan:
    """One contiguous located piece of an artifact (a sentence may span blocks)."""
    page_index: Optional[int] = None   # zero-based; None for non-paginated sources
    block_id: Optional[str] = None
    char_start: Optional[int] = None
    char_end: Optional[int] = None
    quote: str = ""
    rects: Tuple[Tuple[float, float, float, float], ...] = ()  # PDF points, unrotated page space
    time_start: Optional[float] = None
    time_end: Optional[float] = None


@dataclass(frozen=True)
class SourceNavigationTarget:
    artifact_id: str
    precision: NavigationPrecision
    granularity: AnchorGranularity = AnchorGranularity.NONE
    source_id: Optional[str] = None
    source_path: Optional[str] = None
    source_type: Optional[str] = None
    spans: Tuple[AnchorSpan, ...] = ()
    coordinate_basis: str = COORDINATE_BASIS_BLOCK_RAW
    reason: str = ""

    @property
    def navigable(self) -> bool:
        return self.precision not in {
            NavigationPrecision.MISSING_SOURCE, NavigationPrecision.UNRESOLVED,
        }

    def to_dict(self) -> Dict[str, Any]:
        data = asdict(self)
        data["precision"] = self.precision.value
        data["granularity"] = self.granularity.value
        data["spans"] = [
            {**span, "rects": [list(rect) for rect in span["rects"]]}
            for span in data["spans"]
        ]
        data["navigable"] = self.navigable
        return data


def revision_id_for(content_hash: str) -> str:
    """Stable id of one source file content (``srcrev:<hash prefix>``)."""
    return f"srcrev:{(content_hash or '')[:16]}" if content_hash else ""


def block_span_locator(location: Any) -> Dict[str, Any]:
    """Artifact locator for an extractor ``SourceLocation`` that keeps its
    block-local raw offsets, so navigation can verify the exact span."""
    locator: Dict[str, Any] = {
        "page": getattr(location, "page_num", None),
        "block_id": getattr(location, "block_id", None),
    }
    start = getattr(location, "char_start", None)
    end = getattr(location, "char_end", None)
    if start is not None and end is not None:
        locator.update({
            "char_start": start,
            "char_end": end,
            "coordinate_basis": COORDINATE_BASIS_BLOCK_RAW,
        })
    return locator


def unresolved(artifact_id: str, reason: str, **kwargs: Any) -> SourceNavigationTarget:
    return SourceNavigationTarget(
        artifact_id=artifact_id,
        precision=NavigationPrecision.UNRESOLVED,
        reason=reason,
        **kwargs,
    )


def rect_tuple(value: Any) -> Optional[Tuple[float, float, float, float]]:
    try:
        if value is not None and len(value) >= 4:
            return tuple(float(v) for v in list(value)[:4])  # type: ignore[return-value]
    except (TypeError, ValueError):
        pass
    return None


def rect_tuples(values: Optional[List[Any]]) -> Tuple[Tuple[float, float, float, float], ...]:
    rects = (rect_tuple(item) for item in values or [])
    return tuple(rect for rect in rects if rect is not None)
