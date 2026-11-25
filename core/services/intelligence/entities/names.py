"""Conservative within-document surname resolution; explicit grouping wins."""
import re
from collections import defaultdict
from core.services.intelligence.entity_extractor import normalize_key

from core.models.extraction_patterns import HONORIFICS


def resolve(mentions, registry=None, constraints=()):
    protected = {value for rule in constraints for value in (rule['source_key'], rule.get('other_key')) if value}
    packs = registry.get_packs_for_type('person_honorific') if registry else []
    patterns = [p for pack in packs for p in pack.compiled] or [re.compile(HONORIFICS)]
    people, surnames, firsts = [], defaultdict(set), set()
    for mention in mentions:
        if mention.entity_type != 'PERSON':
            continue
        old = mention.normalized_key
        name = mention.surface_text
        for pattern in patterns:
            name = pattern.sub('', name)
        key = normalize_key(name, 'PERSON')
        if old not in protected and key not in protected:
            mention.normalized_key = key
        parts = key.split()
        if len(parts) >= 2:
            surnames[parts[-1]].add(key)
            firsts.add(parts[0])
        people.append(mention)
    for mention in people:
        key = mention.normalized_key
        if len(key.split()) != 1 or key in firsts or key in protected:
            continue
        candidates = surnames.get(key, set())
        if len(candidates) == 1 and not candidates & protected:
            mention.normalized_key = next(iter(candidates))
            mention.metadata.update(resolution_method='unique_document_surname', matched_name_key=mention.normalized_key)
        elif len(candidates) > 1:
            mention.metadata['name_candidates'] = sorted(candidates)
