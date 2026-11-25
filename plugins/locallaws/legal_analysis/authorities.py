"""Cited legal authorities: cases ("Herrera v. Wyoming"), code citations ("25 U.S.C. § 1911"),
constitutional provisions and named Acts.

As an entity extractor it follows the project's annotation rules: a named Act is a LAW entity;
a case name or a code/constitution citation is a citation, not an entity, so its span only
stops the names inside it ("Herrera", "Wyoming") from being read as people or places
(``suppress_only``). The Legal analysis stage lists every cited authority with its reporter,
year, signal and treatment. A case's key is its two parties, so the same case cited twice
groups together.
"""
from __future__ import annotations

import re
from typing import Any, List

from core.models.intelligence_models import EntityMention

from . import patterns as p

CITE_WINDOW = 120
ARTICLES = {"the", "a", "an", "this", "that"}            # characters after a case name searched for its reporter and year
_YEAR = re.compile(r"\(([^()]{0,60}?)\b(1[7-9]\d\d|20\d\d)\)")
_NOT_PARTY = re.compile(r"^(?:See|Cf|Compare|But|Accord|In|And|The|E\.?g)\b\.?\s*")


class LegalAuthorityExtractor:
    id = "locallaws.legal_authority"
    version = "1.0"
    layer = 2
    entity_types = ["LAW"]

    def extract(self, text: str, context: Any = None) -> List[EntityMention]:
        found: List[EntityMention] = []
        taken: List[tuple] = []

        def add(start, end, surface, key, **meta):
            if any(start < b and a < end for a, b in taken):
                return
            taken.append((start, end))
            found.append(EntityMention(surface, "LAW", char_start=start, char_end=end, normalized_key=key,
                                       extractor_id=self.id, confidence=0.95,
                                       metadata={"authoritative": True, "authority_kind": meta.pop("kind"), **meta}))

        for match in p.CASE_NAME.finditer(text):
            first = _NOT_PARTY.sub("", match.group(1)).strip()
            if not first or not first[:1].isupper():
                continue
            start = match.end(1) - len(first)          # after a leading signal ("See", "Cf.")
            second = match.group(2).strip()
            surface = text[start:match.end(2)]
            window = text[match.end(2):match.end(2) + CITE_WINDOW]
            reporter = p.REPORTER.search(window)
            year = _YEAR.search(window)
            add(start, match.end(2), surface, case_key(first, second), kind="case", suppress_only=True,
                reporter=" ".join(reporter.groups()) if reporter and reporter.start() < 8 else "",
                year=year.group(2) if year else "")
        for match in p.STATUTE.finditer(text):
            title, code, section = match.groups()
            add(match.start(), match.end(), match.group(0), f"{title} {re.sub(r'[^A-Za-z]', '', code).lower()} {section}".lower(),
                kind="statute", suppress_only=True)
        for match in p.CONSTITUTION.finditer(text):
            add(match.start(), match.end(), match.group(0), re.sub(r"\s+", " ", match.group(0).lower()), kind="constitution",
                suppress_only=True)
        for match in p.NAMED_ACT.finditer(text):
            name = match.group(1).strip()
            words = name.split()
            while words and words[0].lower() in ARTICLES:
                words = words[1:]
            if len(words) >= 2:                       # "The Act", "The Treaty" are not names
                name = " ".join(words)
                start = match.start(1) + match.group(1).index(name)
                add(start, start + len(name), name, name.lower(), kind="act")
        return found


def case_key(first: str, second: str) -> str:
    fold = lambda s: re.sub(r"[^a-z0-9 ]", "", s.lower()).strip()
    return f"{fold(first)} v {fold(second)}"
