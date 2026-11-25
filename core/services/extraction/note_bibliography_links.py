"""Link footnotes/endnotes to the bibliography entries they cite.

Notes-and-bibliography styles (Chicago, Turabian, many law reviews) cite a
source in a note — in full the first time ("Stanley G. Payne, *Fascism
Comparison and Definition* (…)"), then in short form ("Paxton, “Five
Stages”, 6", "Ibid.") — and list it again in the bibliography ("Payne,
Stanley G. *Fascism Comparison and Definition*. …"). A note names its source
by words, so the link is a scored word match:

* **coverage** — the share of the note's words (surname and title words;
  no numbers, URLs or access dates) found in the entry's opening, each word
  weighted by how rare it is across the bibliography (IDF), so "Trump" in
  a list of Trump posts counts for little and the post's own words decide;
* **author** — a bonus when the note's author surname leads the entry.

The best entry wins only with a clear margin over the runner-up; a tie stays
unlinked rather than guessed. "Ibid." takes the previous note's source.
"""
from __future__ import annotations

import math
import re
import unicodedata
from typing import Dict, List, Optional, Set

from core.models.extraction_models import BibliographyEntry
from core.services.extraction.author_year_citations import normalized_name

_STOP = {
    "the", "a", "an", "of", "and", "or", "in", "on", "to", "for", "from", "by", "with", "at", "as",
    "is", "are", "was", "be", "it", "its", "this", "that", "not", "but", "how", "what", "why", "who",
    "accessed", "available", "ed", "eds", "trans", "vol", "no", "pp", "p", "see", "also", "cf",
    "january", "february", "march", "april", "may", "june", "july", "august", "september",
    "october", "november", "december", "http", "https", "www", "com", "org", "doi", "et", "al",
}
_IBID = re.compile(r"^\W*(?:ibid|id)\.?\W*(?:,|$|\s)", re.I)
_URL = re.compile(r"https?://\S+|www\.\S+|\b10\.\d{4,}/\S+")
_LABEL = re.compile(r"^\s*(?:[⁰¹²³⁴⁵⁶⁷⁸⁹]+|\d{1,3}[.)]?)\s*")
_ENTRY_HEAD = 260           # an entry's author and title sit in its opening
_NOTE_HEAD = 220
_MIN_SCORE = 0.6
_MIN_MARGIN = 0.15
_AUTHOR_BONUS = 0.25


def link_notes(notes: List[BibliographyEntry], entries: List[BibliographyEntry]) -> Dict[str, str]:
    """Compatibility: a single unambiguous note source, when there is one."""
    return {key: targets[0] for key, targets in link_note_targets(notes, entries).items() if len(targets) == 1}


def link_note_targets(notes, entries):
    """Resolve all explicit author/year citations; short-form fallback abstains."""
    if not notes or not entries:
        return {}
    heads = {entry.entry_id: (_words(entry.raw_text[:_ENTRY_HEAD]),
                             _lead_names(_LABEL.sub("", entry.raw_text)) |
                             {normalized_name(entry.first_author_surname)})
             for entry in entries}
    frequency: Dict[str, int] = {}
    for words, _lead in heads.values():
        for word in words:
            frequency[word] = frequency.get(word, 0) + 1
    weight = {word: math.log(1 + len(heads) / count) for word, count in frequency.items()}
    links = {}
    previous = []
    previous_scope = None
    for note in sorted(notes, key=_note_order):
        scope = note.normalized_keys.get("scope")
        if scope != previous_scope:
            previous = []
        previous_scope = scope
        text = _LABEL.sub("", note.raw_text or "")
        if _IBID.match(text):
            targets = list(previous) if len(previous) == 1 else []
        else:
            targets = []
        explicit = _author_year_targets(text, entries)
        targets = list(dict.fromkeys(targets + explicit))
        if not targets and not _IBID.match(text):
            target = _best(text, heads, weight)
            targets = [target] if target else []
        if targets:
            links[note.entry_id] = targets
        previous = targets
    return links


def _best(note_text: str, heads, weight: Dict[str, float]) -> Optional[str]:
    note_words = _words(note_text[:_NOTE_HEAD])
    words = {w for w in note_words if w in weight}
    total = sum(weight[w] for w in words)
    if not total:
        return None
    surname = _note_surname(note_text)
    scored = []
    for entry_id, (entry_words, lead) in heads.items():
        shared = words & entry_words
        author_match = bool(surname and surname in lead)
        # A single generic word such as "record" must not become a perfect
        # match merely because all the note's other words were out of vocab.
        # Short author forms still resolve when the author is unambiguous;
        # title-only forms need several words and coverage of the actual note.
        if not author_match and (len(shared) < 3 or len(shared) / max(len(note_words), 1) < 0.5):
            scored.append((0.0, entry_id))
            continue
        score = sum(weight[w] for w in shared) / total
        if author_match:
            score += _AUTHOR_BONUS
        scored.append((score, entry_id))
    scored.sort(reverse=True)
    best = scored[0]
    runner = scored[1][0] if len(scored) > 1 else 0.0
    return best[1] if best[0] >= _MIN_SCORE and best[0] - runner >= _MIN_MARGIN else None


def _words(text: str) -> Set[str]:
    text = _URL.sub(" ", text or "")
    return {_stem(w) for w in re.findall(r"[^\W\d_]{2,}", text.casefold()) if w not in _STOP}


def _stem(word: str) -> str:
    """Plural-insensitive: "Oath of Loyalty" names "Oaths of Loyalty"."""
    return word[:-1] if len(word) > 3 and word.endswith("s") and not word.endswith("ss") else word


def _lead_names(entry_text: str) -> Set[str]:
    """Words before the entry's first period or comma-pair: its author(s)."""
    head = re.split(r"[.“\"]", entry_text or "", maxsplit=1)[0]
    return {w for w in re.findall(r"[^\W\d_]{2,}", head.casefold())}


def _note_surname(note_text: str) -> str:
    """"Stanley G. Payne, Fascism…" → payne; "Paxton, “Five Stages”" → paxton."""
    head = re.sub(r"\([^)]*\)", " ", note_text.split(",", 1)[0])
    if re.search(r"[“\"]", head):
        return ""           # a title-first note ("“Editorial Law”, Avalon Project")
    names = re.findall(r"[^\W\d_]{2,}", head)
    if not names or len(names) > 5:
        return ""
    return names[-1].casefold()


def _note_order(note: BibliographyEntry):
    loc = note.location
    ordinal = re.search(r"_b(\d+)$", loc.block_id or "") if loc else None
    return (loc.page_num if loc and loc.page_num is not None else -1,
            int(ordinal[1]) if ordinal else 0,
            loc.char_start if loc and loc.char_start is not None else 0)


def _author_year_targets(text, entries):
    found = []
    # Preserve years (normalized_name deliberately drops digits).
    folded = " ".join(unicodedata.normalize("NFKD", text).casefold().split())
    folded = "".join(c for c in folded if not unicodedata.combining(c))
    matches = {}
    for entry in entries:
        surname = normalized_name(entry.first_author_surname)
        year = str(entry.year or "")
        if not surname or not year:
            continue
        pattern = r"(?<!\w)" + re.escape(surname) + r"(?:\s+et\s+al\.?)?[,\s(]+" + re.escape(year) + r"(?![\w])"
        if re.search(pattern, folded):
            matches.setdefault((surname, year), []).append(entry.entry_id)
    for candidates in matches.values():
        if len(candidates) == 1:
            found.extend(candidates)
    return found
