"""Conservative document genre evidence and registered narrative role cues."""
import re
from collections import Counter

from core.services.extraction.block_roles import content_text, is_furniture

from core.models.extraction_patterns import GENRE_CUES, NARRATIVE_ROLES


def annotate_genre(blocks, registry=None):
    # A bounded, distributed sample prevents front matter from deciding the
    # genre and keeps the classifier cheap on large books.
    prose = [content_text(b) for b in blocks if not is_furniture(b)
             and b.structure.get('region') == 'body' and len(content_text(b).split()) >= 20]
    step = max(1, len(prose)//48)
    sample = '\n'.join(text[:1000] for text in prose[::step][:48])
    scores = Counter()
    for genre, pattern in GENRE_CUES.items():
        packs = registry.get_packs_for_type('genre_' + genre) if registry else []
        patterns = [p for pack in packs for p in pack.compiled] or [re.compile(pattern, re.I)]
        scores[genre] = sum(len(p.findall(sample)) for p in patterns)
    ranked = scores.most_common()
    winner, count = ranked[0]
    clear = count >= 3 and count >= 2 * max(1, ranked[1][1])
    result = {'genre': winner if clear else 'general', 'genre_scores': dict(scores),
              'genre_reason': 'dominant lexical evidence' if clear else 'insufficient or mixed evidence'}
    for block in blocks:
        block.structure.update(result)
        if clear and winner == 'humanities':
            block.structure['argument_cues'] = {
                role: [pattern.pattern for pack in registry.get_packs_for_type('narrative_' + role) for pattern in pack.compiled]
                if registry else [pattern] for role, pattern in NARRATIVE_ROLES.items()}
