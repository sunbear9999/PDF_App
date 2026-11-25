"""Author/year syntax and normalized names shared by extraction and matching."""
from __future__ import annotations

import re
import unicodedata

YEAR_RE = re.compile(r"\b((?:1[5-9]\d{2}|20\d{2})[a-z]?)\b")
_PAREN = re.compile(r"\(([^()]+)\)")
_NAME_WORD = r"[A-ZÀ-Þ][\w’'\-]*\.?"
_NAME = rf"{_NAME_WORD}(?:(?:\s+(?:and|&|et\s+al\.?|van|von|de|del|da))?\s+{_NAME_WORD}){{0,6}}"
_NARRATIVE = re.compile(rf"\b({_NAME}(?:\s+et\s+al\.?)?)\s*\(((?:1[5-9]\d{{2}}|20\d{{2}})[a-z]?)\)")
_PREFIX = re.compile(r"^(?:see(?: also)?|e\.g\.|cf\.|compare|(?:as\s+)?(?:quoted|cited)\s+in)\s*,?\s*", re.I)
_SEPARATOR = re.compile(r"\s*(?:&|\band\b)\s*")
_NON_AUTHOR = re.compile(
    r"\b(?:CA\d+|CADC|Cir|Ct|Rev|Ord|Sess)\b|\b(?:[ENSW]\.D\.|D\.)"
    # a publisher parenthetical in a notes-style citation: "(Hurst And Blackett Ltd, 1939)"
    r"|\b(?:Ltd|Press|Publishers?|Publishing|Books|Verlag|Sons)\b", re.I,
)
_CALENDAR_PAREN = re.compile(
    r"^(?:Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|Jun(?:e)?|"
    r"Jul(?:y)?|Aug(?:ust)?|Sep(?:t(?:ember)?)?|Oct(?:ober)?|Nov(?:ember)?|"
    r"Dec(?:ember)?)\.?\s+\d{4}\b", re.I,
)


def normalized_name(value: str) -> str:
    text = unicodedata.normalize("NFKD", value or "").casefold()
    text = "".join(char for char in text if not unicodedata.combining(char))
    return " ".join(re.findall(r"[^\W\d_]+", text))


def author_keys(text: str) -> list[str]:
    """Keep names as units; 'and' never splits inside a surname."""
    text = _PREFIX.sub("", text.strip()).strip(" ,")
    return [part.strip(" ,") for part in _SEPARATOR.split(text) if part.strip(" ,")]


def _valid_author(text: str) -> bool:
    if _NON_AUTHOR.search(text) or re.search(r"\s+(?:[IVXLCDM]{2,}|\d+(?:st|nd|rd|th))$", text):
        return False
    clean = re.sub(r"\bet\s+al\.?", "", text).strip(" ,")
    clean = re.sub(r"\b(?:and|van|von|de|del|da)\b|&|,", " ", clean)   # "Compoc, Enomoto, and Ho"
    # Institutional authors keep lowercase particles: "Government of Canada".
    clean = re.sub(r"(?<=[A-Za-z])\s+(?:of|for|the|on)\s+(?=[A-Z])", " ", clean)
    words = clean.split()
    if words and all(len(word.strip(".")) <= 1 for word in words):
        return False   # "C(2024)" is a document number, not an author
    return bool(words) and len(words) <= 8 and all(
        word[:1].isupper() and re.fullmatch(r"[\w’'\-.]+", word) for word in words
    )


def author_year_mentions(text: str):
    """Yield exact span + author keys + year, including grouped years/suffixes."""
    occupied = []
    for match in _PAREN.finditer(text):
        observations = _parenthetical_parts(match)
        if observations:
            occupied.append((match.start(), match.end()))
            yield from observations
    for match in _NARRATIVE.finditer(text):
        if any(start <= match.start() < end for start, end in occupied):
            continue
        if _valid_author(match[1]):
            yield match.start(), match.end(), author_keys(match[1]), match[2]


def _parenthetical_parts(match):
    observations = []
    for part in re.finditer(r"[^;]+", match[1]):
        if _CALENDAR_PAREN.match(part[0].strip()):
            continue
        years = list(YEAR_RE.finditer(part[0]))
        if not years:
            continue
        author = _PREFIX.sub("", part[0][:years[0].start()].strip()).strip(" ,")
        if not _valid_author(author):
            continue
        start = match.start(1) + part.start()
        end = match.start(1) + part.end()
        # A single parenthesis remains one exact visual span. Grouped entries
        # use their own exact substring, not synthesized '(...)' text.
        if ";" not in match[1]:
            start, end = match.start(), match.end()
        observations.extend((start, end, author_keys(author), year[1]) for year in years)
    return observations
