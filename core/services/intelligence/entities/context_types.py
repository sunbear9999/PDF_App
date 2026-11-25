"""Weighted context evidence can correct a repeated NER mistake."""
import re
from collections import Counter, defaultdict

from core.services.intelligence.entity_extractor import normalize_key

# Packs are named by type and direction; plugins can add language-specific cues.
from core.models.extraction_patterns import CUES


def apply(mentions, texts, registry=None):
    groups = defaultdict(list)
    for mention in mentions:
        groups[normalize_key(mention.surface_text)].append(mention)
    for group in groups.values():
        votes = Counter({kind: min(2, count) for kind, count in Counter(m.entity_type for m in group).items()})
        local = {}
        reasons = defaultdict(list)
        for mention in group:
            local[id(mention)] = set()
            text = texts.get(mention.sentence_id, "")
            for cue, default in CUES.items():
                kind, direction = cue.split("_")
                packs = registry.get_packs_for_type("entity_cue_" + cue) if registry else []
                patterns = [p for pack in packs for p in pack.compiled] or [re.compile(default)]
                context = text[max(0, (mention.char_start or 0)-70):mention.char_start] if direction == "before" else text[mention.char_end:][:70]
                if any(pattern.search(context) for pattern in patterns):
                    local[id(mention)].add(kind)
                    votes[kind] += 4
                    reasons[kind].append(cue)
        ranked = votes.most_common()
        if not ranked:
            continue
        winner, score = ranked[0]
        margin = (score - ranked[1][1]) / sum(votes.values()) if len(ranked) > 1 else 1.0
        for mention in group:
            # One spelling can name both a language and its speakers. Only
            # explicit local evidence disambiguates when cue types disagree.
            active = [kind for kind, cues in reasons.items() if cues]
            if len(active) > 1:
                own = local[id(mention)]
                if len(own) == 1:
                    kind = next(iter(own))
                    mention.entity_type = kind
                    mention.normalized_key = normalize_key(mention.surface_text, kind)
                    mention.metadata["type_reasons"] = reasons[kind]
                else:
                    mention.metadata["type_ambiguous"] = True
                continue
            if margin < 0.2:
                mention.metadata["type_ambiguous"] = True
            elif reasons[winner] and mention.entity_type != winner:
                mention.metadata.update(retyped_from=mention.entity_type, type_reasons=sorted(set(reasons[winner])),
                                        type_confidence=score/sum(votes.values()))
                mention.entity_type = winner
                mention.normalized_key = normalize_key(mention.surface_text, winner)
