"""Bounded NER windows with context, complete input coverage and raw offsets."""
import logging
from itertools import zip_longest

MAX_WINDOW_CHARS = 100_000
CONTEXT_CHARS = 256


def windows(text):
    """Yield (start, end, ownership start, ownership end) without copying text.

    Adjacent windows overlap for names crossing a boundary. A mention belongs
    to the window containing its start, so overlap never duplicates mentions.
    """
    if len(text) <= MAX_WINDOW_CHARS:
        yield 0, len(text), 0, len(text)
        return
    core_size = MAX_WINDOW_CHARS - 2 * CONTEXT_CHARS
    owner_start = 0
    while owner_start < len(text):
        owner_end = min(len(text), owner_start + core_size)
        if owner_end < len(text):
            # Prefer a word boundary, while keeping every character owned.
            for pos in range(owner_end, max(owner_start, owner_end-CONTEXT_CHARS), -1):
                if text[pos-1].isspace():
                    owner_end = pos
                    break
        yield max(0, owner_start-CONTEXT_CHARS), min(len(text), owner_end+CONTEXT_CHARS), owner_start, owner_end
        owner_start = owner_end


def entities_batch(nlp, values, batch_size):
    plan = [(i, *span) for i, text in enumerate(values) for span in windows(text)]
    result = [[] for _ in values]
    try:
        documents = nlp.pipe((values[i][start:end] for i, start, end, _, _ in plan),
                             batch_size=batch_size)
        for item, doc in zip_longest(plan, documents):
            if item is None or doc is None:
                raise ValueError("NER returned a different number of windows than requested")
            _append(result, item, doc)
    except Exception:
        # Some providers cannot batch an otherwise valid input. Retry bounded
        # windows individually, but never turn a failed window into no entities.
        logging.getLogger(__name__).warning("Batched NER failed; retrying individual windows", exc_info=True)
        result = [[] for _ in values]
        for item in plan:
            i, start, end, _, _ = item
            try:
                _append(result, item, nlp(values[i][start:end]))
            except Exception as exc:
                raise RuntimeError(f"NER failed for input {i}, characters {start}:{end}") from exc
    return result


def _append(result, item, doc):
    index, start, _end, owner_start, owner_end = item
    for ent in doc.ents:
        absolute_start, absolute_end = start + ent.start_char, start + ent.end_char
        if owner_start <= absolute_start < owner_end:
            result[index].append((ent.text, ent.label_, absolute_start, absolute_end))
