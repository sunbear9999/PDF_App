"""Map a sentence-relative mention to its exact raw occurrence across blocks."""
from core.services.extraction.block_roles import content_text
from core.services.extraction.sentence_segmentation_service import normalize_with_offset_map, _join_fragments


def mention_spans(sentence, mention_locator, get_block):
    locator = sentence.get('locator') or {}
    parts = locator.get('source_spans') or [locator]
    text, owners, raw = '', [], ''
    for part in parts:
        block = get_block(part.get('block_id'))
        start, end = part.get('char_start'), part.get('char_end')
        if block is None or not isinstance(start, int) or not isinstance(end, int):
            return []
        fragment = content_text(block)[start:end]
        clean, positions = normalize_with_offset_map(fragment)
        lead = len(clean)-len(clean.lstrip())
        positions = positions[lead:len(clean.rstrip())]
        clean = clean.strip()
        following = [(block.block_id, block.page_num, start+p) for p in positions]
        if text:
            joined = _join_fragments({'text': text, 'raw_text': raw}, {'text': clean})
            # Normalization may remove a terminal line-break hyphen. Demand
            # exact reconstruction; never pick the first repeated name.
            variants = [(text+' '+clean, owners+[None]+following), (text+clean, owners+following)]
            if text.endswith('-'):
                variants.append((text[:-1]+clean, owners[:-1]+following))
            matched = next((mapping for value, mapping in variants if value == joined), None)
            if matched is None:
                return []
            text, owners = joined, matched
        else:
            text, owners = clean, following
        raw += fragment
    start, end = mention_locator.get('char_start'), mention_locator.get('char_end')
    if text != sentence.get('exact_text') or not isinstance(start, int) or not isinstance(end, int) or not 0 <= start < end <= len(owners):
        return []
    result = []
    for owner in owners[start:end]:
        if owner is None:
            continue
        block_id, page, offset = owner
        if result and result[-1]['block_id'] == block_id:
            result[-1]['char_end'] = offset+1
        else:
            result.append({'block_id': block_id, 'page': page, 'char_start': offset, 'char_end': offset+1})
    return result
