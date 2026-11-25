"""Shared legal selectors for detection, matching and temporal exclusions.

Explicit reporter names avoid treating arbitrary capitalized phrases as cases.
PDF line breaks/spaces inside abbreviations do not change authority identity.
"""
import re

REPORTER = (
    r"(?:U\.\s*S\.|S\.\s*Ct\.|L\.\s*Ed\.\s*(?:2d)?|"
    r"F\.\s*(?:Supp\.\s*(?:2d|3d)?|App(?:'|’)?x|2d|3d|4th)?|"
    r"(?:P|A|So|N\.\s*E|N\.\s*W|S\.\s*E|S\.\s*W)\.\s*(?:2d|3d)?|"
    r"Cong\.\s*Rec\.|Fed\.\s*Reg\.)"
)
# Pin cites may follow the first page ("436 U.S. 49, 65, 67") but never eat
# the volume of a parallel citation ("…, 67, 98 S.Ct. 1670"); CourtListener
# prints spaces before commas ("450 U.S. 544 , 547").
_PIN = rf"(?:\s*,\s*\d{{1,5}}(?!\d)(?:[-–]\d{{1,5}})?(?!\s+(?:{REPORTER}|Stat\.)))*"
LEGAL_REPORTER_RE = re.compile(
    rf"\b(?P<volume>\d{{1,4}})\s+(?P<reporter>{REPORTER})\s+"
    rf"(?P<page>\d{{1,5}}){_PIN}"
    r"(?:\s*\((?:[A-Za-z.\d\s]+\s+)?(?P<year>\d{4})\))?"
)
LEGAL_SHORT_REPORTER_RE = re.compile(
    rf"\b(?P<volume>\d{{1,4}})\s+(?P<reporter>{REPORTER})\s+at\s+"
    r"(?P<pin>\d{1,5})(?:[-–]\d{1,5})?"
)
STATUTE_RE = re.compile(
    r"\b(?P<volume>\d{1,3})\s+"
    r"(?P<reporter>U\.?\s*S\.?\s*C\.?|C\.?\s*F\.?\s*R\.?|Stat\.)\s*"   # "15 Stat. 650": Statutes at Large
    r"(?:§{1,2}\s*)?(?P<page>\d+(?:\.\d+)*)[\w().\-–]*"
    # subsections may wrap to the next line: "§ 3563\n(b)(6), (13), (15)"
    r"(?:\s*(?:\([a-zA-Z0-9]{1,4}\))+(?:,\s*(?:\([a-zA-Z0-9]{1,4}\))+)*)?" + _PIN
)
# Treatises/books cited by title with pages and an edition/year parenthetical:
# "Federal Indian Law 380-81 (6th ed. 2011)", "Parading Through History 88-90
# (1995)", "Cohen's Handbook of Federal Indian Law § 1.07, at 97".
# Two or more title words; never led by a signal or sentence word ("See",
# "In the United States 12 (1999)").
_SIGNAL = r"(?:See|Cf|Compare|But|Also|In|On|At|For|The|This|That|And|Of|By|As|With|Id|Ibid|Accord)\b"
# A title starts a citation clause: after ", " (author), a sentence or a signal.
_TITLE_START = r"(?:(?<=, )|(?<=[.;:]\s)|(?<=\bSee )|(?<=\bsee )|(?<=also )|(?<=Cf\. )|(?<=\()|^|(?<=\n))"
_TITLE = (_TITLE_START + r"(?!" + _SIGNAL + r")[A-Z][\w’'.\-]*"
          r"(?:\s+(?:of|on|the|and|for|in|to|from|a|an)){0,2}\s+[A-Z][\w’'.\-]*"
          r"(?:\s+(?:of|on|the|and|for|in|to|from|a|an|[A-Z][\w’'.\-]*))*")
SECONDARY_RE = re.compile(
    rf"{_TITLE}\s+(?:\d{{1,4}}(?:[-–]\d{{1,4}})?(?:,\s*\d{{1,4}})*(?:,\s*p\.\s*\d+)?\s*"
    r"\((?:[^()]{0,25}?\s)?(?:ed\.\s*)?(?:1[5-9]|20)\d{2}\)"
    r"|§\s*\d+(?:\.\d+)*,\s*at\s*\d+(?:[-–]\d+)?)"
)
# "Wyoming Statutes section 15-1-107", "Wyo. Stat. §§ 15-1-105 and 16-4-404(b)",
# "Cal. Penal Code § 187".
STATE_STATUTE_RE = re.compile(
    r"\b(?P<volume>(?:[A-Z][a-z]+\.?\s+){1,2})(?P<reporter>(?:Revised\s+)?Statutes|Rev\.\s*Stat\.|Stat\.|"
    r"(?:[A-Z][a-z]+\s+)?Code(?:\s+Ann\.)?)\s+(?:sections?|§§?)\s*"
    r"(?P<page>\d+(?:[-–.]\d+)*[a-z]?(?:\([a-z0-9]+\))*)"
    r"(?:(?:\s*,\s*|\s+and\s+)\d+(?:[-–.]\d+)*(?:\([a-z0-9]+\))*)*"
)
# "S. Rep. No. 103-340, at 1 (1994)", "H.R. Rep. No. 95-1386"
LEGISLATIVE_RE = re.compile(
    r"\b(?:S|H\.\s*R)\.\s*(?:Rep|Doc|Con\.\s*Res)\.\s*No\.\s*\d+[-–]\d+(?:,\s*at\s*\d+)?(?:\s*\((?P<year>\d{4})\))?"
)
# "Brief for Respondent 48", "Reply Brief for Petitioner 5, n. 3",
# "Brief for Crow Tribe of Indians as Amicus Curiae 5"
BRIEF_RE = re.compile(
    r"\b(?:Reply\s+)?Brief\s+for\s+(?:the\s+)?[A-Z][\w’'.]*(?:\s+(?:of|the|and|[A-Z][\w’'.]*))*?"
    r"(?:\s+as\s+Amic(?:us|i)\s+Curiae)?\s+\d{1,4}(?:,\s*n\.\s*\d+)?"
)


def authority_key(text: str) -> str:
    """Volume/reporter/base page or section; pin cites are not identity."""
    for pattern in (STATUTE_RE, LEGAL_REPORTER_RE, STATE_STATUTE_RE):
        match = pattern.search(text or "")
        if match:
            reporter = re.sub(r"[^a-z0-9]", "", match["reporter"].casefold())
            volume = re.sub(r"[^a-z0-9]", "", match["volume"].casefold())
            return f"{volume}|{reporter}|{match['page']}"
    return ""
