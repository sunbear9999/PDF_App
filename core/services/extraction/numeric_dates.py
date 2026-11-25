"""Numeric dates such as "4-2-1647" or "11/28/1765": day and month order decided per document.

A date whose first number is above 12 is day-first ("28-11-1765"); one whose second number is
above 12 is month-first ("11-28-1765"). Those unambiguous dates vote on the document's
convention. A date with both numbers 12 or below follows that convention when it is clear
(at least ``MIN_VOTES`` votes and ``MIN_SHARE`` of them agree), recorded as inferred with the
basis. Without a clear convention the date keeps its year only and stays ambiguous for review,
never a guessed day. Two-digit years are left as they are.
"""
from __future__ import annotations

import re
from datetime import date
from typing import Iterable, Optional, Tuple

MIN_VOTES = 3
MIN_SHARE = 0.8
_NUMERIC = re.compile(r"^(\d{1,2})([-/.])(\d{1,2})\2(\d{4})$")


def _parts(raw: str) -> Optional[Tuple[int, int, int]]:
    match = _NUMERIC.match((raw or "").strip())
    return (int(match.group(1)), int(match.group(3)), int(match.group(4))) if match else None


def _order(first: int, second: int) -> Optional[str]:
    if first > 12 >= second:
        return "day_first"
    if second > 12 >= first:
        return "month_first"
    return None


def _iso(year: int, month: int, day: int) -> Optional[str]:
    try:
        return date(year, month, day).isoformat()
    except ValueError:
        return None


def convention(raws: Iterable[str]) -> Tuple[Optional[str], int]:
    """The document's clear day/month order and how many unambiguous dates support it."""
    votes = {"day_first": 0, "month_first": 0}
    for raw in raws:
        parts = _parts(raw)
        order = _order(parts[0], parts[1]) if parts else None
        if order:
            votes[order] += 1
    winner = max(votes, key=votes.get)
    total = sum(votes.values())
    if votes[winner] >= MIN_VOTES and votes[winner] >= MIN_SHARE * total:
        return winner, votes[winner]
    return None, votes[winner]


def resolve(dates) -> None:
    """Normalise every numeric date of one document in place (``ExtractedDate`` objects)."""
    numeric = [d for d in dates if _parts(d.exact_text)]
    if not numeric:
        return
    order, support = convention(d.exact_text for d in numeric)
    for item in numeric:
        first, second, year = _parts(item.exact_text)
        own = _order(first, second)
        chosen = own or order
        iso = _iso(year, *( (second, first) if chosen == "day_first" else (first, second))) if chosen else None
        if iso:
            item.normalized_date, item.certainty = iso, "confirmed" if own else "inferred"
            item.metadata = {**item.metadata, "precision": "day", "date_order": chosen,
                             "date_order_basis": "the numbers" if own else
                             f"{support} unambiguous dates in this document are {chosen.replace('_', '-')}"}
        elif own is None and first <= 12 and second <= 12:
            item.normalized_date, item.certainty = str(year), "ambiguous"
            item.metadata = {**item.metadata, "precision": "year", "date_order": "unknown",
                             "date_order_basis": "day and month could be either way; the document has no clear convention"}
