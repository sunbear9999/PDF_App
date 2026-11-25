"""Read a court opinion sentence by sentence: its parts, its reasoning and how it treats authority.

Findings (one per sentence and kind):
- ``opinion_part``  where the majority, a concurrence, a dissent or a per curiam opinion begins, with
  its author and who joins; every later finding records the part it sits in.
- ``issue`` / ``rule`` / ``application`` / ``holding``  the steps of the court's reasoning (IRAC).
- ``disposition``   affirmed, reversed, vacated, remanded.
- ``authority``     every cited case, code section or constitutional provision, with its reporter and
  year, the signal before it ("See", "Cf.") and how the court treats it when said nearby.
A document is read only when it looks like an opinion (``MIN_LEGAL_SIGNALS``), so an essay that
"concludes that" is not given holdings.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from . import patterns as p
from .authorities import LegalAuthorityExtractor

CAPTION_SENTENCES = 12

LABELS = {"opinion_part": "Opinion", "issue": "Question presented", "rule": "Legal rule / standard",
          "application": "Application to the facts", "holding": "Holding", "disposition": "Disposition",
          "authority": "Cited authority"}
PART_LABELS = {"majority": "Opinion of the Court", "concurrence": "Concurrence", "dissent": "Dissent",
               "per_curiam": "Per curiam", "panel": "Panel opinion"}


@dataclass
class Finding:
    kind: str
    row: Dict[str, Any]
    cue: str = ""
    metadata: Dict[str, Any] = field(default_factory=dict)


def looks_legal(rows: List[Dict[str, Any]]) -> bool:
    signals = 0
    for row in rows:
        text = row.get("exact_text") or ""
        signals += len(p.LEGAL_MARKERS.findall(text)) + len(p.REPORTER.findall(text)) + len(p.STATUTE.findall(text))
        if signals >= p.MIN_LEGAL_SIGNALS:
            return True
    return False


def analyze(rows: List[Dict[str, Any]]) -> List[Finding]:
    if not looks_legal(rows):
        return []
    findings: List[Finding] = []
    part: Dict[str, Any] = {"opinion": "majority", "author": ""}
    authorities = LegalAuthorityExtractor()
    # The caption names the case itself; it is not an authority the court treats.
    own = {next((m.normalized_key for row in rows[:CAPTION_SENTENCES] for m in authorities.extract(row.get("exact_text") or "")
                 if m.metadata.get("authority_kind") == "case"), None)}
    for row in rows:
        text = (row.get("exact_text") or "").strip()
        if not text:
            continue
        started = _opinion_start(text)
        if started:
            part = {"opinion": started["kind"], "author": started.get("author", "")}
            findings.append(Finding("opinion_part", row, started["cue"], {**started, "opinion": started["kind"]}))
        context = {"opinion": part["opinion"], "author": part["author"]}
        for role, pattern in p.REASONING.items():
            match = pattern.search(text)
            if match:
                findings.append(Finding(role, row, match.group(0), dict(context)))
        disposition = p.DISPOSITION.search(text)
        if disposition:
            word = next((g for g in disposition.groups() if g), "ordered")
            findings.append(Finding("disposition", row, disposition.group(0), {**context, "outcome": word.lower()}))
        findings.extend(f for f in _authorities(row, text, context, authorities) if f.metadata["authority_key"] not in own)
    return findings


def _opinion_start(text: str) -> Optional[Dict[str, Any]]:
    match = p.OPINION_OF_COURT.search(text)
    if match:
        return {"kind": "majority", "author": match.group(1), "cue": match.group(0)}
    match = p.SEPARATE_OPINION.search(text[:400])
    if match:
        kind = "dissent" if "dissent" in match.group(3) and "concurring" not in match.group(3) else "concurrence"
        joined = [j.group(1) or "The Chief Justice" for j in p.JOINER.finditer(match.group(2) or "")]
        return {"kind": kind, "author": match.group(1), "joined_by": joined, "detail": match.group(3), "cue": match.group(0)}
    match = p.PANEL_OPINION.search(text)
    if match:
        kind = {"dissenting": "dissent", "concurring": "concurrence"}.get(match.group(2) or "", "panel")
        return {"kind": kind, "author": match.group(1), "cue": match.group(0)}
    if p.PER_CURIAM.search(text):
        return {"kind": "per_curiam", "author": "", "cue": "Per curiam"}
    return None


def _authorities(row, text, context, authorities) -> List[Finding]:
    """Each cited authority, with the signal before it and a treatment word said near it."""
    out = []
    cues = [(name, m) for name, pattern in p.TREATMENT.items() for m in pattern.finditer(text)]
    for mention in authorities.extract(text):
        kind = mention.metadata.get("authority_kind")
        if kind == "act":
            continue                           # named Acts are LAW entities, listed in People & Places
        treatment = ""
        if cues:
            near = [(m.start() - mention.char_end if m.start() >= mention.char_end else mention.char_start - m.end(), name)
                    for name, m in cues]
            distance, name = min(near, key=lambda item: abs(item[0]))
            treatment = name if abs(distance) <= p.TREATMENT_DISTANCE else ""
        signal = p.SIGNAL.search(text[:mention.char_start])
        out.append(Finding("authority", row, mention.surface_text, {
            **context, "authority": mention.surface_text, "authority_key": mention.normalized_key,
            "authority_kind": kind, "reporter": mention.metadata.get("reporter", ""),
            "year": mention.metadata.get("year", ""), "treatment": treatment,
            "signal": signal.group(1) if signal else ""}))
    return out


def display_text(finding: Finding) -> str:
    meta = finding.metadata
    if finding.kind == "opinion_part":
        who = meta.get("author") or ""
        joined = f" (joined by {', '.join(meta['joined_by'])})" if meta.get("joined_by") else ""
        return f"{PART_LABELS.get(meta['kind'], meta['kind'])} — {who}{joined}".strip(" —")
    if finding.kind == "authority":
        cite = " ".join(v for v in (meta.get("reporter"), f"({meta['year']})" if meta.get("year") else "") if v)
        extra = " · ".join(v for v in (meta.get("signal"), meta.get("treatment")) if v)
        return f"{meta['authority']}" + (f", {cite}" if cite else "") + (f" — {extra}" if extra else "")
    return (finding.row.get("exact_text") or "").strip()
