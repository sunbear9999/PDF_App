"""Conservative temporal context policy shared by extraction and legacy views."""
from dataclasses import dataclass
import re

from core.services.extraction.legal_citation_patterns import LEGAL_REPORTER_RE, STATUTE_RE

_REFERENCE_SECTION = re.compile(
    r"^(?:references?|bibliography|works? cited|literature cited|table of authorities)$", re.I)
_IDENTIFIER = re.compile(
    r"(?:\b(?:No\.|Case|Docket|ISBN|ISSN|doi|arXiv)\s*[:#]?\s*[-\w./–]+|"
    r"§{1,2}\s*\d+|\b(?:p\.|pp\.|page|pages|volume|section)\s+\d+|"
    r"\b\d{3}[-–]\d{3}[-–]\d{4}\b|\b\d{1,3}(?:,\d{3})+\b|"
    # Pin cites ("supra, at 1701 - 1702") and numbered instruments ("Ord. 2004-09").
    r"(?:,|\b(?:supra|infra|id\.|ibid\.?))\s*,?\s*at\s+\d+(?:\s*[-–]\s*\d+)?|"
    r"\b(?:ord|res|reg|pub\.\s?l|h\.\s?r|s\.\s?b|h\.\s?b|no|ch|art)\.\s*\d{2,4}[-–]\d+)", re.I)
_QUANTITY = re.compile(
    r"\b\d{4}\s*(?:%|kg\b|km\b|mg\b|ms\b|voters?\b|samples?\b|"
    r"participants?\b|patients?\b|people\b|tokens?\b|dollars?\b|items?\b)", re.I)
_PIN_YEAR = re.compile(r"\b\d+(?:,\s*\d+)+\s*\(\d{4}\)|\(\d{4}\)\s+\d+")
_TEMPORAL_CUE = re.compile(
    r"\b(?:year|winter|spring|summer|autumn|fall|dated|occurred|happened|"
    r"began|ended|born|died|founded|enacted|election|war|result|survey|census|"
    r"study|held|decided|ruling|season|anniversary)\b", re.I)
# Syntax that makes a bare year a date even without a temporal noun nearby
# ("in the 1647 will", "a source of 1548", "between 1765 and 1768", "(d. 1762)").
_YEAR = r"(?:1[0-9]{3}|20[0-2][0-9])"
_PREP_BEFORE = re.compile(
    r"(?:\b(?:in|of|from|until|till|through|since|by|before|after|around|about|circa|ca\.|c\.|"
    r"between|during)\s+(?:the\s+)?(?:(?:early|late|mid|middle)[\s-]+)?|\b(?:d|b|r|fl)\.\s*)$", re.I)
_RANGE_BEFORE = re.compile(_YEAR + r"[’']?s?\s*(?:[-–—/]|\s(?:and|to|or|until|through)\s)\s*$", re.I)
_RANGE_AFTER = re.compile(r"^\s*(?:[-–—]|\s(?:and|to|until|through)\s)\s*(?:" + _YEAR + r"|\d{2})\b", re.I)
# "a total of 1500", "an area of 1200": a quantity, not a date.
_QUANTITY_OF = re.compile(
    r"\b(?:total|number|sum|area|population|sample|count|amount|average|mean|median|length|height|"
    r"depth|weight|cost|price|value|score|range|minimum|maximum|size)\s+of\s+$", re.I)
# A year that signs a line after a name or place: "—cabildo of Ixil, 1767".
_SIGNATURE = re.compile(r"[^\W\d_][.)]?\s*,\s*$")
_LINE_END = re.compile(r"^\s*[.)\]]?\s*(?:\n|$)")
# "the 1610 Tekax uprising", "the 1868 Treaty": a year modifying a proper name.
_THE_BEFORE = re.compile(r"\bthe\s+$", re.I)
_NAME_AFTER = re.compile(r"^\s+[A-Z][^\W\d_]")


def temporal_syntax(text: str, start: int, end: int) -> bool:
    before, after = text[max(0, start - 40):start], text[end:end + 30]
    if _PREP_BEFORE.search(before):
        return not _QUANTITY_OF.search(before)
    return bool(_RANGE_BEFORE.search(before) or _RANGE_AFTER.search(after)
                or (_SIGNATURE.search(before) and _LINE_END.match(after))
                or (_THE_BEFORE.search(before) and _NAME_AFTER.match(after)))


_INSTRUMENT_NOUN = r"(?:Act|Code|Treaty|Regulation|Constitution|Agreement|Convention|Charter|Statute|Accord|Compact)s?"
_INSTRUMENT = re.compile(r"\b" + _INSTRUMENT_NOUN + r"\s+of\s*$", re.I)
# "the 1868 Treaty", "1934 Securities Exchange Act": the year names the instrument.
_INSTRUMENT_AFTER = re.compile(r"^\s+(?:[A-Z][\w’'-]*\s+){0,3}" + _INSTRUMENT_NOUN + r"\b")
# "(Ord. 2004-09, 10-4-2004)": a date inside an instrument's citation parenthetical.
_INSTRUMENT_PAREN = re.compile(r"\((?:ord|res|pub\.\s?l|stat|no|ch|art|h\.\s?r|s\.\s?b)\.[^()]{0,60}$", re.I)
_PUBLICATION = re.compile(
    r"\b(?:published|publication date|received|accepted|revised)\b[^.;\n!?]{0,45}$", re.I)
_COPYRIGHT = re.compile(r"©|\b(?:copyright|all rights reserved)\b", re.I)


@dataclass(frozen=True)
class DateContext:
    role: str
    eligible: bool
    reason: str = ""
    non_date: bool = False

    def metadata(self) -> dict:
        return {"context_role": self.role, "timeline_eligible": self.eligible,
                "timeline_exclusion_reason": self.reason}


def _overlaps(pattern, text, start, end):
    return any(m.start() < end and start < m.end() for m in pattern.finditer(text))


def classify_date_context(text, start, end, *, tier="bare_year", block_type="paragraph",
                          section_path=(), citations=()) -> DateContext:
    """Classify the matched occurrence, not every year in its surrounding quote."""
    if _overlaps(_IDENTIFIER, text, start, end) or _overlaps(_QUANTITY, text, start, end):
        return DateContext("non_date", False, "identifier or quantity", True)
    legal = _legal_context(text, start, end)
    if legal:
        return legal
    if block_type == "bibliography" or any(_REFERENCE_SECTION.fullmatch(p.strip()) for p in section_path):
        return DateContext("citation", False, "reference-list year")
    for citation in citations:
        loc = citation.location
        if loc and loc.char_start is not None and loc.char_end is not None:
            if loc.char_start < end and start < loc.char_end:
                return DateContext("citation", False, "in-text reference year")
    prefix = text[max(0, start - 75):start]
    line = text[text.rfind("\n", 0, start) + 1:text.find("\n", end) if "\n" in text[end:] else len(text)]
    if _COPYRIGHT.search(line):
        return DateContext("copyright", False, "copyright or rights notice")
    if block_type in {"header", "footer"}:
        return DateContext("header/footer", False, "page header or footer")
    if _INSTRUMENT_PAREN.search(prefix):
        return DateContext("citation", False, "date in an instrument citation")
    if _INSTRUMENT.search(prefix) or _INSTRUMENT_AFTER.match(text[end:end + 60]):
        return DateContext("named_instrument", False, "year in an instrument name")
    if _PUBLICATION.search(prefix):
        return DateContext("publication", False, "publication metadata")
    if (tier == "bare_year" and not _TEMPORAL_CUE.search(text[max(0, start - 55):end + 55])
            and not temporal_syntax(text, start, end)):
        return DateContext("unknown", False, "year-shaped number lacks temporal context", True)
    return DateContext("event", True)


def _legal_context(text, start, end):
    for pattern in (STATUTE_RE, LEGAL_REPORTER_RE):
        for match in pattern.finditer(text):
            if match.start() < end and start < match.end():
                if "year" in match.groupdict() and match["year"]:
                    left, right = match.span("year")
                    if left < end and start < right:
                        return DateContext("citation", False, "legal reference year")
                return DateContext("non_date", False, "authority volume, page or section", True)
    if _overlaps(_PIN_YEAR, text, start, end):
        return DateContext("citation", False, "year attached to a legal pin citation")
    return None


def legacy_timeline_eligible(row, citation_extractor) -> bool:
    """Old artifacts lack context fields: inspect their exact matched occurrence."""
    from core.models.extraction_models import DocumentBlock

    meta = row.get("metadata") or {}
    if "timeline_eligible" in meta:
        return bool(meta["timeline_eligible"])
    text = row.get("exact_text") or ""
    phrase = meta.get("exact_text") or meta.get("normalized_date") or row.get("normalized_text") or ""
    spans = [(m.start(), m.end()) for m in re.finditer(re.escape(str(phrase)), text)] if phrase else []
    if not spans:
        return True  # No recorded span: preserve existing unresolved observations.
    citations = citation_extractor.extract([DocumentBlock("legacy", "legacy", None,
                                             "paragraph", text, text)], "legacy")
    return any(classify_date_context(text, start, end,
               tier="prep_year" if re.match(r"(?i)(in|since|by|after|before)\b", str(phrase))
               else "bare_year" if re.fullmatch(r"\d{4}", str(phrase)) else "date",
               section_path=row.get("section_path") or (),
               citations=citations).eligible for start, end in spans)
