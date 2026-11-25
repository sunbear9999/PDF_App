"""One type per name, decided from how the document uses it.

NER labels each sentence on its own and makes the same mistake over and over:
a group noun used as a subject ("the Maya") comes out PERSON, a town name
PERSON. A raw majority of those labels gets *worse* as a document gets
longer, because the mistake repeats. This step therefore decides from usage
*rates* across every occurrence of the name, which do not grow with length:

* groups are used attributively ("Maya women"), bare after "the" and in a
  plural form ("Mayas"), never with person cues;
* places follow locative prepositions ("in Mérida");
* people take honorifics and person verbs ("don Pedro", "Landa wrote").

Without decisive usage the NER majority stands, but heavy attributive use
argues against PERSON. Votes from optional evidence sources (gazetteer,
GLiNER, plugins; see ``evidence_sources``) enter as per-name support rates:
they lower the bar the text must clear, never replace it. Mentions whose type a rule set from explicit evidence
(an acronym definition, a title) keep it. Every decision records its reasons.
"""
from __future__ import annotations

import re
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from core.models.extraction_patterns import USAGE_CUES
from core.models.intelligence_models import EntityMention
from core.services.intelligence.entities.evidence_sources import ASSESSED_KEY, VOTES_KEY
from core.services.intelligence.entities.surfaces import folded
from core.services.intelligence.entity_extractor import normalize_key

RESOLVED_TYPES = frozenset({"PERSON", "ORG", "LOCATION", "NORP", "PRODUCT", "WORK", "EVENT", "FAC", "LANGUAGE", "UNKNOWN"})
MIN_OCCURRENCES = 3          # fewer: keep the sentence-level labels
GROUP_SCORE = 0.6            # attributive + bare-"the" (+ plural bonus)
PLURAL_BONUS = 0.3
GROUP_MAX_LOCATIVE = 0.25
GROUP_MIN_BARE = 0.1
PLACE_LOCATIVE = 0.18
PERSON_CUE = 0.1             # person cues decide
PERSON_BLOCK = 0.05          # a little person evidence blocks group/place decisions
ATTRIBUTIVE_NOT_PERSON = 0.4
SETTLED_SHARE = 0.6
DOMINANT_SHARE = 0.9
MIN_PLURAL_STEM = 3
MIN_PLURAL_REPEATS = 2
PLACE_SUPPORT = 1.0          # gazetteer-weighted support: half the mentions carry a weight-2 place vote
PLACE_LOCATIVE_SUPPORTED = 0.05
# Arbitration of sentence labels: NER shares count half, evidence support
# (gazetteer, GLiNER: up to ~2 per source) counts half its value, so a
# consistent second model outvotes a repeated spaCy mistake ("PD" -> place).
NER_SHARE_WEIGHT = 0.5
SUPPORT_WEIGHT = 0.5
LANGUAGE_SUPPORT = 0.8        # mean GLiNER weight (2 x probability) for a language reading
RARE_MODEL_SUPPORT = 1.2     # a name used once or twice: a confident second model may retype it
# Between the core name types a second model is no better than the sentence labels
# (gold: "Blackfeet Reservation" place -> ORG, "Europeans" group -> ORG), so there it
# only breaks ties; it moves names freely to technical types (organism, disease, model).
CORE_TYPES = frozenset({"PERSON", "ORG", "LOCATION", "NORP"})
RARE_CORE_SUPPORT = 1.6
# Labels NER systematically gives group nouns; group usage may replace them.
GROUP_OVERRIDES = frozenset({"PERSON", "PRODUCT", "WORK", "EVENT", "UNKNOWN", "NORP", "LANGUAGE"})
_NEXT_WORD = re.compile(r"\s+([^\W\d_][\w’'-]*)")
# Same word shape as propagation: "O'Brien" is one word, "Maya’s" is "Maya" + "s".
_WORD = re.compile(r"[^\W_]+(?:(?:[’']|-)(?![sS]\b)[^\W_]+)*")
_THE_BEFORE = re.compile(r"(?i)\bthe\s+$")
# "Taylor acknowledged": a past-tense predicate, not an attributive noun.
_PREDICATE = re.compile(r"[a-z]{2,}ed$")


@dataclass
class Usage:
    occurrences: int = 0
    rates: Dict[str, float] = field(default_factory=dict)
    plural: bool = False


def resolve_types(mentions: List[EntityMention], texts: Dict[str, str], registry: Any = None) -> None:
    groups: Dict[str, List[EntityMention]] = defaultdict(list)
    for mention in mentions:
        if mention.entity_type in RESOLVED_TYPES:
            groups[_key(mention.surface_text)].append(mention)
    spelling = {key: _spelling(group) for key, group in groups.items()}
    usage = usage_profiles(set(spelling.values()), texts, usage_cues(registry))
    for key, group in groups.items():
        found = usage.get(spelling[key], Usage())
        cited = sum(m.metadata.get("role") == "cited_author" for m in group) / len(group)
        if cited:
            found.rates = {**found.rates, "cited_author": cited}
        decision = decide(Counter(m.entity_type for m in group), found, multi_word=" " in spelling[key],
                          support=evidence_support(group))
        if decision is not None:
            _apply(group, *decision)


def decide(ner: Counter, usage: Usage, multi_word: bool = False, support: Optional[Dict[str, float]] = None
           ) -> Optional[tuple]:
    """(type, settled, reasons), or None to keep the sentence-level labels.
    A multi-word name the model reads as a person ("Juan de la Cruz Coba") is
    not re-decided from group or place usage."""
    support = support or {}
    if not ner:
        return None
    if usage.occurrences < MIN_OCCURRENCES:
        return _rare(ner, usage, support)
    if multi_word and ner.most_common(1)[0][0] == "PERSON":
        return "PERSON", ner["PERSON"] / sum(ner.values()) >= SETTLED_SHARE, ["ner_majority"]
    r = usage.rates
    person = r.get("person_after", 0) + r.get("person_before", 0)
    if ner.most_common(1)[0][0] != "ORG":
        person += r.get("cited_author", 0)   # cited author-date: a person, unless a corporate author
    group = r.get("attributive", 0) + r.get("the_bare", 0) + (PLURAL_BONUS if usage.plural else 0)
    if person >= PERSON_CUE:
        return "PERSON", True, ["person_cues"]
    top = ner.most_common(1)[0][0]
    group_only = usage.plural or r.get("the_bare", 0) >= GROUP_MIN_BARE   # people and places lack both
    if person < PERSON_BLOCK and group_only and group >= GROUP_SCORE and r.get("locative", 0) < GROUP_MAX_LOCATIVE:
        # Places ("the United States") and organisations ("the Court") are also used
        # bare and attributively; only a plural form turns an organisation into a group.
        if top in GROUP_OVERRIDES or (top == "ORG" and usage.plural):
            # Languages are used like group nouns ("Nahuatl texts"); a model that reads
            # the name as a language more than as a group chooses between the two.
            language = top == "LANGUAGE" or support.get("LANGUAGE", 0) > max(support.get("NORP", 0), LANGUAGE_SUPPORT)
            return ("LANGUAGE" if language else "NORP"), True, ["group_usage"] + (["model_evidence"] if language and top != "LANGUAGE" else [])
    if person < PERSON_BLOCK and group < GROUP_SCORE and top not in {"ORG", "FAC"}:
        if r.get("locative", 0) >= PLACE_LOCATIVE:
            return "LOCATION", True, ["locative_usage"]
        if support.get("LOCATION", 0) >= PLACE_SUPPORT and r.get("locative", 0) >= PLACE_LOCATIVE_SUPPORTED:
            return "LOCATION", True, ["locative_usage", "place_evidence"]
    excluded = {"PERSON"} if r.get("attributive", 0) >= ATTRIBUTIVE_NOT_PERSON else set()
    return _arbitrate(ner, support, excluded)


def _arbitrate(ner: Counter, support: Dict[str, float], excluded=frozenset()) -> Optional[tuple]:
    """Usage did not decide: weigh the sentence labels against evidence support."""
    total = sum(ner.values())
    scores: Dict[str, float] = defaultdict(float)
    for kind, count in ner.items():
        scores[kind] += NER_SHARE_WEIGHT * count / total
    for kind, value in support.items():
        scores[kind] += SUPPORT_WEIGHT * value
    for kind, count in ner.items():   # core labels hold against core-type model readings
        if kind in CORE_TYPES and any(k in CORE_TYPES and k != kind for k in support):
            scores[kind] += (1 - NER_SHARE_WEIGHT) * count / total
    denominator = sum(scores.values())
    for kind in excluded:
        scores.pop(kind, None)
    if not scores:
        return None
    kind = max(sorted(scores), key=lambda k: scores[k])
    if kind == "UNKNOWN":
        return None   # usage did not decide; an untyped candidate stays untyped
    reasons = ["ner_majority"] if support.get(kind, 0) == 0 else ["ner_majority", "model_evidence"]
    if ner.get(kind, 0) == 0:
        reasons = ["model_evidence"]
    settled = scores[kind] >= SETTLED_SHARE * denominator
    return kind, settled, reasons


def _rare(ner: Counter, usage: Usage, support: Dict[str, float]) -> Optional[tuple]:
    """Too few uses for usage rates: only agreeing place evidence (a gazetteer
    place named after a locative word) may retype a name the model mislabels."""
    person = usage.rates.get("person_after", 0) + usage.rates.get("person_before", 0)
    top = ner.most_common(1)[0][0]
    if support.get("LOCATION", 0) >= PLACE_SUPPORT and not person and top not in {"ORG", "FAC"}:
        return "LOCATION", False, ["place_evidence"]
    model = {k: v for k, v in support.items() if k != "UNKNOWN"}
    if model and not person:
        kind = max(sorted(model), key=lambda k: model[k])
        needed = RARE_CORE_SUPPORT if kind in CORE_TYPES and top in CORE_TYPES else RARE_MODEL_SUPPORT
        if model[kind] >= needed and kind != top:
            return kind, False, ["model_evidence"]
    return None


def evidence_support(group: List[EntityMention]) -> Dict[str, float]:
    """Per type, summed over sources: the mean strongest vote weight across the
    mentions that source assessed (a sampled source is not diluted)."""
    weights: Dict[tuple, float] = defaultdict(float)
    assessed: Counter = Counter()
    for mention in group:
        assessed.update(mention.metadata.get(ASSESSED_KEY) or ())
        best: Dict[tuple, float] = {}
        for vote in mention.metadata.get(VOTES_KEY) or ():
            key = (vote["source"], vote["type"])
            best[key] = max(best.get(key, 0.0), float(vote.get("weight", 0)))
        for key, weight in best.items():
            weights[key] += weight
    support: Dict[str, float] = defaultdict(float)
    for (source, kind), total in weights.items():
        support[kind] += total / max(1, assessed[source])
    return dict(support)


def usage_profiles(surfaces, texts: Dict[str, str], cues: Dict[str, List[re.Pattern]]) -> Dict[str, Usage]:
    """Usage rates of each surface over every occurrence in the document's sentences.

    Each surface is profiled on its own ("Maya" also counts inside "the Maya" and
    "Yucatec Maya"), through a word index built once, so cost stays linear in text.
    """
    index: Dict[str, List[tuple]] = defaultdict(list)
    for text in texts.values():
        words = list(_WORD.finditer(text))
        for position, word in enumerate(words):
            index[word.group(0)].append((text, words, position))
    profiles = {}
    for surface in {s for s in surfaces if s}:
        counter = Counter()
        for text, start, end in _occurrences(surface, index):
            _count(counter, text, start, end, cues)
        if counter["n"]:
            profiles[surface] = Usage(counter["n"], {k: v / counter["n"] for k, v in counter.items() if k != "n"},
                                      _has_plural(surface, index))
    return profiles


def _occurrences(surface: str, index):
    tokens = _WORD.findall(surface)
    if not tokens:
        return
    for text, words, position in index.get(tokens[0], ()):
        if position + len(tokens) > len(words):
            continue
        span = words[position:position + len(tokens)]
        if [w.group(0) for w in span] == tokens and all(
                not text[a.end():b.start()].strip(" .") for a, b in zip(span, span[1:])):
            yield text, span[0].start(), span[-1].end()


def _has_plural(surface: str, index) -> bool:
    """A plural form ("Mayas") occurs, or this surface is the plural of one that does ("Nahuas")."""
    if " " in surface:
        return False
    if len(index.get(surface + "s", ())) >= MIN_PLURAL_REPEATS:
        return True
    return surface.endswith("s") and len(surface) > MIN_PLURAL_STEM and \
        len(index.get(surface[:-1], ())) >= MIN_PLURAL_REPEATS


def _count(counter: Counter, text: str, start: int, end: int, cues) -> None:
    before, after = text[max(0, start - 40):start], text[end:end + 40]
    counter["n"] += 1
    word = _NEXT_WORD.match(after)
    if word and word.group(1)[:1].islower() and not _any(cues["not_attributive"], word.group(1)) \
            and not _PREDICATE.match(word.group(1)):
        counter["attributive"] += 1
    if _THE_BEFORE.search(before) and not (word and word.group(1)[:1].isupper()):
        counter["the_bare"] += 1
    cited = _any(cues["citation_after"], after)
    for cue, rate, text_part in (("locative_before", "locative", before), ("person_before", "person_before", before),
                                 ("person_after", "person_after", after)):
        if _any(cues[cue], text_part) and not (cited and rate == "locative"):
            counter[rate] += 1


def _apply(group: List[EntityMention], kind: str, settled: bool, reasons: List[str]) -> None:
    for mention in group:
        mention.metadata["type_settled"] = settled
        if mention.metadata.get("type_evidence") is True or mention.entity_type == kind:
            continue
        mention.metadata.update(retyped_from=mention.entity_type, retyped_by="usage", type_reasons=reasons)
        mention.entity_type = kind
        mention.normalized_key = normalize_key(mention.surface_text, kind)


def usage_cues(registry: Any) -> Dict[str, List[re.Pattern]]:
    cues = {}
    for name, default in USAGE_CUES.items():
        packs = registry.get_packs_for_type("entity_usage_" + name) if registry else []
        cues[name] = [p for pack in packs for p in pack.compiled] or [re.compile(default)]
    return cues


def _any(patterns: List[re.Pattern], text: str) -> bool:
    return any(pattern.search(text) for pattern in patterns)


def _key(surface: str) -> str:
    """The canonical grouping key without type ("the Maya", "Te-kanto" -> "maya", "tekanto")."""
    return normalize_key(surface.strip()) or folded(surface.strip())


def absorb_stragglers(mentions: List[EntityMention]) -> None:
    """A name used ≥ 90 % as one type is one entity: stragglers without explicit
    evidence join it rather than forking a second entity of another type."""
    groups: Dict[str, List[EntityMention]] = defaultdict(list)
    for mention in mentions:
        if mention.entity_type in RESOLVED_TYPES:
            groups[_key(mention.surface_text)].append(mention)
    for group in groups.values():
        kind, count = Counter(m.entity_type for m in group).most_common(1)[0]
        if count == len(group) or count / len(group) < DOMINANT_SHARE:
            continue
        for mention in group:
            if mention.entity_type != kind and mention.metadata.get("type_evidence") is not True:
                mention.metadata.update(retyped_from=mention.entity_type, retyped_by="dominant_type")
                mention.entity_type = kind
                mention.normalized_key = normalize_key(mention.surface_text, kind)


def _spelling(group: List[EntityMention]) -> str:
    """The most frequent spelling, without a leading article, for usage profiling."""
    surface = Counter(m.surface_text.strip() for m in group).most_common(1)[0][0]
    return re.sub(r"(?i)^(?:the|this)\s+", "", surface)
