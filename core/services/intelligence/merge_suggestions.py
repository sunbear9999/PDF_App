"""Possible duplicates of one entity, ranked, each with the reason it was suggested.

Suggestions never merge anything: they fill the "Suggested merges" list of the
grouping review, where the user decides. Only entities of the same type can be
merged, so only those are suggested; pairs marked "Keep separate" are left out.
"""
from __future__ import annotations

import json
import re
import unicodedata
from typing import Any, Dict, List

from rapidfuzz import fuzz

from core.services.intelligence.entity_review import key

MAX_POOL = 400
MAX_SUGGESTIONS = 15
MIN_TOKEN = 3
SPELLING_RATIO = 85
ARTICLES = frozenset({"the", "a", "an", "la", "el", "le", "los", "las", "de"})
SCORES = {"same": 1.0, "contains": 0.8, "initials": 0.75}


def _fold(text: str) -> str:
    text = "".join(c for c in unicodedata.normalize("NFKD", text or "").casefold() if not unicodedata.combining(c))
    words = re.findall(r"[^\W_]+", text)
    while len(words) > 1 and words[0] in ARTICLES:
        words = words[1:]
    return " ".join(words)


def _initials(text: str) -> str:
    return "".join(word[0] for word in _fold(text).split() if word not in ARTICLES)


def _names(row) -> List[str]:
    meta = row.get("metadata") or {}
    return [n for n in dict.fromkeys([row.get("exact_text") or "", *(meta.get("aliases") or [])]) if n]


def _words(text: str) -> List[str]:
    """Space-separated words, folded; hyphenated words stay whole ("non-Pech", "Maya-language")."""
    folded = "".join(c for c in unicodedata.normalize("NFKD", text or "").casefold() if not unicodedata.combining(c))
    words = [w.strip(".,;:()[]'\"’") for w in folded.split()]
    words = [w for w in words if w]
    while len(words) > 1 and words[0] in ARTICLES:
        words = words[1:]
    return words


def _contains(first: str, second: str) -> bool:
    """One name is the other plus words ("Spain"/"New Spain", "Lockhart"/"James Lockhart"); the shorter
    covers at least half the longer, and a list ("Cacalchen, Ebtun") never contains a name."""
    (short, s_raw), (long_, l_raw) = sorted(((_words(first), first), (_words(second), second)), key=lambda x: len(x[0]))
    if not short or len(short) == len(long_) or len(" ".join(short)) < MIN_TOKEN or re.search(r"[,;]", l_raw):
        return False
    if len(short) * 2 < len(long_):
        return False
    return any(long_[i:i + len(short)] == short for i in range(len(long_) - len(short) + 1))


WORD_RATIO = 75


def _wordwise(a: str, b: str) -> bool:
    """Spelling variants differ inside words, not by whole words or numbers: "Juan Canche"/"Juan
    Cante" yes; "Chapter Eleven"/"Chapter Twelve" or two dates no."""
    if re.findall(r"\d+", a) != re.findall(r"\d+", b):
        return False
    first, second = a.split(), b.split()
    return len(first) == len(second) and all(x == y or fuzz.ratio(x, y) >= WORD_RATIO for x, y in zip(first, second))


def compare(first: str, second: str):
    """(score, reason) for two names, or None when they do not look alike."""
    a, b = _fold(first), _fold(second)
    if not a or not b:
        return None
    if a == b:
        return SCORES["same"], f"Same name: “{first}” and “{second}”"
    if _contains(first, second):
        return SCORES["contains"], f"One name contains the other: “{first}” / “{second}”"
    for acronym, full in ((first, second), (second, first)):
        if acronym.isupper() and 2 <= len(acronym) <= 6 and _initials(full) == acronym.casefold() and len(full.split()) > 1:
            return SCORES["initials"], f"“{acronym}” are the initials of “{full}”"
    ratio = fuzz.ratio(a, b)
    if ratio >= SPELLING_RATIO and _wordwise(a, b):
        return ratio / 100 * SCORES["contains"], f"Similar spelling: “{first}” / “{second}” ({ratio:.0f}%)"
    return None


def suggest(service, entity_id: str, limit: int = MAX_SUGGESTIONS) -> List[Dict[str, Any]]:
    db = service.db
    row = db.get_artifact(entity_id)
    if not row or row.get("artifact_type") != "entity":
        return []
    kind = (row.get("metadata") or {}).get("entity_type")
    names = _names(row)
    best: Dict[str, Dict[str, Any]] = {}
    for other in _pool(db, row, kind, names):
        for mine in names:
            for theirs in _names(other):
                found = compare(mine, theirs)
                if found and found[0] > best.get(other["artifact_id"], {}).get("score", 0):
                    best[other["artifact_id"]] = {"row": other, "score": found[0], "reason": found[1]}
    blocked = _kept_apart(db, kind, key(row))
    from core.services.intelligence.entity_hierarchy import decided_pairs
    related = decided_pairs(db)
    ranked = [item for item in best.values() if key(item["row"]) not in blocked
              and tuple(sorted((entity_id, item["row"]["artifact_id"]))) not in related]
    ranked.sort(key=lambda item: (-item["score"], -int((item["row"].get("metadata") or {}).get("mention_count") or 0)))
    return ranked[:limit]


def _pool(db, row, kind, names):
    """Same-type entities sharing a word with the name, or whose initials could match it."""
    tokens = {t for name in names for t in _fold(name).split() if len(t) >= MIN_TOKEN and t not in ARTICLES}
    letters = {name[0].casefold() for name in names if name.isupper() and len(name) <= 6}
    likes = [f"%{t}%" for t in sorted(tokens)][:12]
    clauses = ["exact_text LIKE ?"] * len(likes) + ["lower(substr(exact_text,1,1))=?"] * len(letters)
    if not clauses:
        return []
    rows = db._conn.execute(
        "SELECT artifact_id FROM intelligence_artifacts WHERE artifact_type='entity' AND status='complete' "
        "AND review_state IN ('unreviewed','verified','corrected') AND artifact_id!=? "
        "AND json_extract(metadata_json,'$.entity_type')=? AND (" + " OR ".join(clauses) + ") LIMIT ?",
        (row["artifact_id"], kind, *likes, *sorted(letters), MAX_POOL)).fetchall()
    return db.list_artifacts_by_ids([r[0] for r in rows])


def _kept_apart(db, kind, source_key):
    rows = db._conn.execute(
        "SELECT source_key, other_key FROM intelligence_entity_rules WHERE rule_type='never_merge' AND entity_type=? "
        "AND (source_key=? OR other_key=?)", (kind, source_key, source_key)).fetchall()
    return {other if mine == source_key else mine for mine, other in rows}


# -- project-wide queue for Review issues -------------------------------------------------
MAX_BLOCK = 200          # a word shared by more names than this ("san", "maya") does not pair names
_CACHE: Dict[tuple, list] = {}


def _all_pairs(db) -> list:
    """Every suggested pair in the project, best first; cached per knowledge revision."""
    from core.db.intelligence_artifact_queries import revision_token
    from core.models.entity_categories import MEASURE_TYPES
    token = (id(db), revision_token(db))
    if token in _CACHE:
        return _CACHE[token]
    rows = db._conn.execute(
        "SELECT artifact_id FROM intelligence_artifacts WHERE artifact_type='entity' AND status='complete' "
        "AND review_state IN ('unreviewed','verified','corrected') AND COALESCE(json_extract(metadata_json,'$.entity_type'),'') "
        "NOT IN (SELECT value FROM json_each(?))", (json.dumps(sorted(MEASURE_TYPES)),)).fetchall()
    entities = db.list_artifacts_by_ids([r[0] for r in rows])
    by_type: Dict[str, list] = {}
    for row in entities:
        by_type.setdefault((row.get("metadata") or {}).get("entity_type"), []).append(row)
    pairs = []
    for kind, group in by_type.items():
        pairs.extend(_type_pairs(db, kind, group))
    pairs.sort(key=lambda p: (-p["score"], -p["mentions"]))
    _CACHE.clear()
    _CACHE[token] = pairs
    return pairs


def _type_pairs(db, kind, group):
    index: Dict[str, list] = {}
    for row in group:
        keys = {t for n in _names(row) for t in _fold(n).split() if len(t) >= MIN_TOKEN and t not in ARTICLES}
        keys |= {"#" + _initials(n) for n in _names(row) if len(n.split()) > 1}
        keys |= {"#" + n.casefold() for n in _names(row) if n.isupper() and 2 <= len(n) <= 6}
        for k in keys:
            index.setdefault(k, []).append(row)
    seen, found = set(), []
    for members in index.values():
        if len(members) > MAX_BLOCK:
            continue
        for i, first in enumerate(members):
            for second in members[i + 1:]:
                pair = tuple(sorted((first["artifact_id"], second["artifact_id"])))
                if pair in seen:
                    continue
                seen.add(pair)
                best = max((compare(a, b) or (0, "") for a in _names(first) for b in _names(second)), key=lambda x: x[0])
                if best[0]:
                    found.append(_pair(first, second, best))
    found = _unique_containers(found)
    blocked = {tuple(sorted(r)) for r in db._conn.execute(
        "SELECT source_key, other_key FROM intelligence_entity_rules WHERE rule_type='never_merge' AND entity_type=?", (kind,))}
    from core.services.intelligence.entity_hierarchy import decided_pairs
    related = decided_pairs(db)      # already a subgroup or a confirmed connection: not duplicates
    return [p for p in found if tuple(sorted((p["source_key"], p["target_key"]))) not in blocked
            and tuple(sorted((p["source_id"], p["target_id"]))) not in related]


def _unique_containers(pairs):
    """A short name inside several longer names ("Chan" in "Gaspar Chan", "Clara Chan") could be
    any of them: suggest a containment pair only when the shorter name has one container."""
    containers: Dict[str, int] = {}
    contained = [p for p in pairs if p["reason"].startswith("One name contains")]
    for pair in contained:
        containers[pair["shorter"]] = containers.get(pair["shorter"], 0) + 1
    return [p for p in pairs if not p["reason"].startswith("One name contains") or containers[p["shorter"]] == 1]


def _pair(first, second, best):
    count = lambda row: int((row.get("metadata") or {}).get("mention_count") or 0)
    target, source = sorted((first, second), key=count, reverse=True)       # keep the more mentioned
    return {"pair_id": "merge:" + "|".join(sorted((source["artifact_id"], target["artifact_id"]))),
            "source_id": source["artifact_id"], "target_id": target["artifact_id"], "source_key": key(source),
            "target_key": key(target), "score": best[0], "reason": best[1], "mentions": count(first) + count(second),
            "entity_type": (source.get("metadata") or {}).get("entity_type"),
            "shorter": min((first, second), key=lambda row: len(_words(row.get("exact_text") or "")))["artifact_id"]}


def merge_queue(service, excluded=(), entity_types=()):
    """The next suggested pair (that both entities still exist), and how many remain."""
    excluded, wanted = set(excluded or []), set(entity_types or [])
    pairs = [p for p in _all_pairs(service.db) if p["pair_id"] not in excluded
             and (not wanted or p["entity_type"] in wanted)]
    return (pairs[0] if pairs else None), len(pairs)
