"""Finalize completed mentions before persistence, without amplifying noise."""
from core.services.intelligence.entity_refinement import _DocumentContext, _author_initial_context


def finalize(mentions, texts):
    context = _DocumentContext(mentions, texts)
    best = {}
    for mention in mentions:
        surface = mention.surface_text
        if (surface.isupper() and len(surface) <= 5 and surface in context.initials
                and surface not in context.acronyms and _author_initial_context(mention, context)):
            continue
        key = (mention.sentence_id, mention.char_start, mention.char_end)
        old = best.get(key)
        priority = lambda m: (bool(m.metadata.get('type_evidence') or m.metadata.get('type_reasons')), m.confidence)
        if old is None or priority(mention) > priority(old):
            best[key] = mention
    return list(best.values())
