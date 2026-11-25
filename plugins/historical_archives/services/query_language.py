"""Boolean query normalization and provider-neutral structured query assembly."""
from __future__ import annotations

import re
from dataclasses import dataclass, replace
from typing import Callable, List

from ..models import ArchiveQuery
from ..providers.search_schema import US_STATES


_TOKEN_RE = re.compile(r'"(?:\\.|[^"\\])*"|[()]|\b(?:AND|OR|NOT)\b|[^\s()]+', re.I)
_BOOLEAN_RE = re.compile(r"\b(?:AND|OR|NOT)\b|[()]")
_TYPE_ALIASES = {
    "newspaper": ("newspaper", "newspapers"),
    "book": ("book", "books"),
    "image": ("photo", "photos", "photograph", "photographs", "image", "images"),
    "map": ("map", "maps"),
    "manuscript": ("manuscript", "manuscripts"),
    "audio": ("audio", "recording", "recordings"),
    "video": ("film", "films", "video", "videos"),
}


@dataclass(frozen=True)
class QueryInterpretation:
    query: ArchiveQuery
    inferred: tuple[str, ...] = ()


def normalize_boolean_expression(value: str) -> str:
    """Normalize explicit/implicit Boolean syntax and reject malformed input."""
    raw = str(value or "").strip()
    if not raw or raw == "*":
        return "*"
    tokens = _TOKEN_RE.findall(raw)
    if not tokens:
        return "*"
    output: List[str] = []
    depth = 0
    previous = "start"
    for token in tokens:
        upper = token.upper()
        if token == "(":
            if previous in {"operand", "close"}:
                output.append("AND")
            output.append(token)
            depth += 1
            previous = "open"
        elif token == ")":
            if depth <= 0 or previous not in {"operand", "close"}:
                raise ValueError("Boolean query has an unmatched or empty parenthesis.")
            output.append(token)
            depth -= 1
            previous = "close"
        elif upper in {"AND", "OR"}:
            if previous not in {"operand", "close"}:
                raise ValueError(f"Boolean operator {upper} needs a term before it.")
            output.append(upper)
            previous = "operator"
        elif upper == "NOT":
            if previous in {"operand", "close"}:
                output.append("AND")
            output.append("NOT")
            previous = "not"
        else:
            if previous in {"operand", "close"}:
                output.append("AND")
            output.append(token)
            previous = "operand"
    if depth:
        raise ValueError("Boolean query has an unmatched parenthesis.")
    if previous in {"operator", "not", "open"}:
        raise ValueError("Boolean query ends before its final term.")
    return " ".join(output)


def render_boolean_expression(value: str, escape: Callable[[str], str]) -> str:
    """Render normalized syntax while escaping only literals, not operators."""
    normalized = normalize_boolean_expression(value)
    if normalized == "*":
        return "*"
    rendered = []
    for token in _TOKEN_RE.findall(normalized):
        if token in {"(", ")"} or token.upper() in {"AND", "OR", "NOT"}:
            rendered.append(token.upper() if token not in {"(", ")"} else token)
        elif token.startswith('"') and token.endswith('"'):
            rendered.append(f'"{escape(token[1:-1])}"')
        else:
            rendered.append(escape(token))
    return " ".join(rendered)


def build_structured_expression(
    query: ArchiveQuery,
    escape: Callable[[str], str] = lambda value: value,
) -> str:
    """Combine free Boolean input with exact/all/any/excluded controls."""
    groups = []
    if query.original_query and query.original_query != "*":
        groups.append(f"({render_boolean_expression(query.original_query, escape)})")
    if query.exact_phrase:
        groups.append(f'"{escape(query.exact_phrase)}"')
    all_terms = _words(query.all_words)
    if all_terms:
        groups.append("(" + " AND ".join(escape(term) for term in all_terms) + ")")
    any_terms = _words(query.any_words)
    if any_terms:
        groups.append("(" + " OR ".join(escape(term) for term in any_terms) + ")")
    for term in _words(query.excluded_words):
        groups.append(f"NOT {escape(term)}")
    return " AND ".join(groups) or "*"


def interpret_query(query: ArchiveQuery) -> QueryInterpretation:
    """Infer obvious type, place, and date filters from natural search text."""
    raw = (query.raw_query or query.original_query).strip()
    if not raw:
        return QueryInterpretation(replace(query, original_query="*"))
    normalized = normalize_boolean_expression(raw)
    if _BOOLEAN_RE.search(raw):
        return QueryInterpretation(
            replace(query, raw_query=raw, original_query=normalized)
        )
    remaining = raw
    inferred = []
    result_type, remaining, found = _infer_type(query.result_type, remaining)
    inferred.extend(found)
    location, remaining, found = _infer_location(query.location, remaining)
    inferred.extend(found)
    date_from, date_to, remaining, found = _infer_dates(query, remaining)
    inferred.extend(found)
    remaining = re.sub(r"\s+", " ", remaining).strip(" ,;-")
    return QueryInterpretation(
        replace(
            query,
            raw_query=raw,
            original_query=remaining or "*",
            result_type=result_type or "all",
            location=location,
            date_from=date_from,
            date_to=date_to,
        ),
        tuple(inferred),
    )


def _infer_type(current: str, text: str):
    if current not in {"", "all", "newspaper"}:
        return current, text, []
    for value, aliases in _TYPE_ALIASES.items():
        match = next(
            (
                alias
                for alias in aliases
                if re.search(rf"\b{re.escape(alias)}\b", text, re.I)
            ),
            None,
        )
        if match:
            remaining = re.sub(rf"\b{re.escape(match)}\b", " ", text, flags=re.I)
            return value, remaining, [f"Type: {value}"]
    return current, text, []


def _infer_location(current: str, text: str):
    if current:
        return current, text, []
    for place in sorted(US_STATES, key=len, reverse=True):
        if not re.search(rf"\b{re.escape(place)}\b", text, re.I):
            continue
        remaining = re.sub(
            rf"\b(?:from|in|near|of)\s+{re.escape(place)}\b",
            " ",
            text,
            flags=re.I,
        )
        remaining = re.sub(rf"\b{re.escape(place)}\b", " ", remaining, flags=re.I)
        return place, remaining, [f"Place: {place}"]
    return current, text, []


def _infer_dates(query: ArchiveQuery, text: str):
    if query.date_from or query.date_to:
        return query.date_from, query.date_to, text, []
    match = re.search(
        r"\b(?:between|from)\s+(\d{4})\s+(?:and|to)\s+(\d{4})\b",
        text,
        re.I,
    )
    if not match:
        return "", "", text, []
    date_from, date_to = match.groups()
    remaining = text[:match.start()] + " " + text[match.end():]
    return date_from, date_to, remaining, [f"Dates: {date_from}–{date_to}"]


def _words(value: str) -> List[str]:
    return re.findall(r'"[^"]+"|[^\s,;]+', str(value or "").strip())
