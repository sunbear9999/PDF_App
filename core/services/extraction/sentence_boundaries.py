"""Shared conservative sentence boundaries for source spans and language features.

Abbreviations, initials, decimals and legal references are not sentence stops.
The returned strings remain verbatim slices of the input (no offset drift).
"""
import re

# A footnote marker may sit between the stop and the next sentence
# ("under the ICRA.7 In Shenandoah…", "manner.¹⁻³ Postmortem…"); it only counts
# after a letter/closing mark and before a capitalised word, never in "3.5 In".
_BOUNDARY = re.compile(
    r"[.!?](?:[\"'’”\])]+)?\s+"
    r"|(?<=[A-Za-z\"'’”)\]])[.!?](?:[\"'’”\])]+)?(?:\d{1,3}|[¹²³⁴⁵⁶⁷⁸⁹⁰⁻,]+)\s+(?=[A-Z“\"(])"
)
_ABBREVIATION = re.compile(
    r"\b(?:Mr|Mrs|Ms|Dr|Prof|Sr|Jr|vs|v|Fig|Figs|Eq|Eqs|No|Nos|"
    r"Vol|pp|p|ed|eds|Inc|Co|Corp|Dept|Cir|Ct|Supp|Rep|Stat|Art|Sec|"
    r"Id|Ibid|Sch|Bd|Cmty|Univ|Cong|Rec|Assn|Ltd|"
    r"Jan|Feb|Mar|Apr|Jun|Jul|Aug|Sep|Sept|Oct|Nov|Dec|al|cf|e\.g|i\.e)\.$",
    re.I,
)
# Legal/academic citation abbreviations ("n. 3", "Ann. Rep.", "Cert. denied",
# "Wash. 2d", "Sects. 3 and 4.3"). Case-sensitive, so "they fell ill. Then"
# and "I am. So" still end sentences.
_CASED_ABBREVIATION = re.compile(
    r"\b(?:n|nn|para|paras|seq|"
    r"Ann|App|Cert|Pub|Reg|Regs|Rptr|Dist|Govt|Natl|Intl|Comm|Envtl|Fed|Ch|Cl|"
    r"Sect|Sects|Rev|Am|Soc|Sci|Med|Assoc|Admin|Educ|Indus|Mgmt|"
    r"Cal|Wash|Ariz|Okla|Mont|Wyo|Colo|Minn|Wis|Tenn|Ill|Ind|Mass|Conn|Pa|Fla|Ga|"
    r"Ala|Ark|Kan|Neb|Nev|Tex|Va|Md|Del|Ky|La|Miss|Mo|Mich|Vt|Haw)\.$"
)
_INITIAL = re.compile(r"(?:\b[A-Z]\.|\b(?:[A-Za-z]\.){2,})$")
_ELLIPSIS = re.compile(r"\.{2,}|\.(?:\s+\.){2,}")
# Both abbreviation patterns are anchored at the end of the text before a
# candidate stop, so only its tail can match. Searching the whole prefix made
# splitting quadratic in block length (minutes on 60k-character PDF blocks).
# One extra leading character keeps ``\b`` decisions identical.
_TAIL = 64


def split_sentences(text: str) -> list[str]:
    """Split at genuine punctuation boundaries, retaining abbreviation runs."""
    parts = []
    start = 0
    ellipses = [(match.start(), match.end()) for match in _ELLIPSIS.finditer(text or "")]
    for match in _BOUNDARY.finditer(text or ""):
        if any(lo <= match.start() < hi for lo, hi in ellipses):
            continue
        prefix = text[max(0, match.start() + 1 - _TAIL):match.start() + 1]
        if match.group()[0] == "." and (
            _ABBREVIATION.search(prefix) or _CASED_ABBREVIATION.search(prefix)
            or _INITIAL.search(prefix)
        ):
            continue
        parts.append(text[start:match.start() + 1].strip())
        # Include closing quotes/brackets in the sentence they belong to.
        stop = match.end() - len(match.group()) + len(match.group().rstrip())
        parts[-1] = text[start:stop].strip()
        start = match.end()
    if text[start:].strip():
        parts.append(text[start:].strip())
    return [part for part in parts if part]
