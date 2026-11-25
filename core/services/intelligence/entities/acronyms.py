"""Give an acronym the key of the long form it is defined beside: "Indian Civil Rights Act (ICRA)"."""
from __future__ import annotations

import re
from typing import Any, Dict, List

from core.models.intelligence_models import EntityMention
from core.services.intelligence.entity_extractor import normalize_key

_DEFINITION_RE = re.compile(
    r"\b([A-Z][A-Za-z0-9&'’.\-]+(?:\s+[A-Z][A-Za-z0-9&'’.\-]+){1,8})"
    r"\s+\(([A-Z][A-Z0-9&.\-]{1,12})\)"
)


def apply_acronym_aliases(mentions: List[EntityMention], records: List[Dict[str, Any]]) -> None:
    by_evidence: Dict[str, List[EntityMention]] = {}
    for mention in mentions:
        by_evidence.setdefault(str(mention.metadata.get("evidence_artifact_id") or ""), []).append(mention)
    for row in records:
        group = by_evidence.get(str(row.get("artifact_id") or ""), [])
        if not group:
            continue
        for match in _DEFINITION_RE.finditer(row.get("exact_text") or ""):
            _alias(group, match.group(1), match.group(2))


def _alias(group: List[EntityMention], long_form: str, acronym: str) -> None:
    long_key = normalize_key(long_form)
    for acronym_mention in [m for m in group if m.surface_text.casefold() == acronym.casefold()]:
        compatible = [
            mention for mention in group
            if mention.entity_type == acronym_mention.entity_type
            and (normalize_key(mention.surface_text) == long_key
                 or long_key.endswith(normalize_key(mention.surface_text)))
        ]
        if compatible:
            acronym_mention.normalized_key = compatible[0].normalized_key
            acronym_mention.metadata["resolved_alias_of"] = long_form
