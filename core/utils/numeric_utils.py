from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Optional


_MINUS_TRANSLATION = str.maketrans({"\u2212": "-", "\u2013": "-", "\u2014": "-"})
_TOKEN_RE = re.compile(
    r"(?<![\w.])"
    r"(?P<open>\()?\s*"
    r"(?P<currency>[$€£¥])?\s*"
    r"(?P<number>[+-]?(?:(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d*)?|\.\d+))"
    r"\s*(?P<percent>%)?\s*(?P<close>\))?"
    r"(?!\w)"
)


@dataclass(frozen=True)
class ParsedNumber:
    value: float
    token: str
    is_percent: bool = False


def parse_number(value: Any) -> Optional[ParsedNumber]:
    """Extract the most useful numeric token from a spreadsheet cell.

    A whole-cell match wins. Otherwise the rightmost valid token is used, which
    handles common label/value cells such as ``Revenue 2024: -12.5%.``.
    Percentages retain their displayed magnitude (12.5%, not 0.125), matching
    Data Dock's existing calculation behavior.
    """
    text = str(value if value is not None else "").strip().translate(_MINUS_TRANSLATION)
    if not text:
        return None

    matches = list(_TOKEN_RE.finditer(text))
    if not matches:
        return None

    whole = [m for m in matches if not text[:m.start()].strip() and not text[m.end():].strip(" \t\r\n.,;:!?*")]
    match = whole[-1] if whole else matches[-1]
    raw_number = match.group("number").replace(",", "")
    try:
        number = float(raw_number)
    except (TypeError, ValueError):
        return None
    if match.group("open") and match.group("close"):
        number = -abs(number)
    return ParsedNumber(number, match.group(0).strip(), bool(match.group("percent")))


def coerce_number(value: Any) -> Optional[float]:
    parsed = parse_number(value)
    return parsed.value if parsed is not None else None


def is_number(value: Any) -> bool:
    return parse_number(value) is not None
