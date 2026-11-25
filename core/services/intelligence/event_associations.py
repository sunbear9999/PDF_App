"""Truthful event-context associations, separate from extraction and semantic relations."""
from bisect import bisect_left

from core.services.extraction.sentence_segmentation_service import normalize_with_offset_map

MAX_MENTION_PROOF_PREVIEW=16
MAX_WINDOW_CONTEXT_CHARS=32000


def date_position(date_row,context,blocks):
    if (date_row.get('metadata') or {}).get('manual_event'):
        return None  # Authored dates need not occur literally; no invented word proximity.
    locator=context.get('locator') or {}
    date_locator=date_row.get('locator') or {}
    text=context.get('exact_text') or ''
    if len(text)>MAX_WINDOW_CONTEXT_CHARS:
        return None
    if len(locator.get('source_spans') or [])>1:
        return None
    block=blocks.get(context.get('parent_artifact_id')) or {}
    start,end=locator.get('char_start'),locator.get('char_end')
    if context.get('artifact_type')=='transcript_segment' and date_row.get('parent_artifact_id')==context.get('artifact_id'):
        raw,offset=text,0
    elif (block.get('artifact_type')=='block' and block.get('source_id')==context.get('source_id')
          and type(start) is int and type(end) is int and 0<=start<end<=len(block.get('exact_text') or '')
          and end-start<=MAX_WINDOW_CONTEXT_CHARS
          and locator.get('block_id')==date_locator.get('block_id')):
        raw,offset=block['exact_text'][start:end],start
    else:
        return None
    clean,positions=normalize_with_offset_map(raw)
    leading=len(clean)-len(clean.lstrip())
    clean=clean.strip(); positions=positions[leading:leading+len(clean)]
    left,right=date_locator.get('char_start'),date_locator.get('char_end')
    if clean!=text or type(left) is not int or type(right) is not int or not offset<=left<right<=offset+len(raw):
        return None
    phrase=(date_row.get('metadata') or {}).get('exact_text')
    if phrase and normalize_with_offset_map(raw[left-offset:right-offset])[0].strip()!=normalize_with_offset_map(str(phrase))[0].strip():
        return None
    left,right=bisect_left(positions,left-offset),bisect_left(positions,right-offset)
    return (left,right) if 0<=left<right<=len(text) else None


def word_distance(date_span,mention,text):
    locator=mention.get('locator') or {}
    left,right=locator.get('char_start'),locator.get('char_end')
    if date_span is None or type(left) is not int or type(right) is not int or not 0<=left<right<=len(text):
        return None
    if text[left:right] != mention.get('exact_text'):
        return None
    if right<=date_span[0]:
        gap=text[right:date_span[0]]
    elif left>=date_span[1]:
        gap=text[date_span[1]:left]
    else:
        gap=''
    return sum(any(char.isalnum() for char in token) for token in gap.split())


def participants(date_row,context,mentions,blocks,filters,truncated=False):
    text=context.get('exact_text') or ''
    span=date_position(date_row,context,blocks) if filters.association=='word_window' else None
    complete=bool(context) and not truncated and (filters.association in {'sentence','event'} or span is not None)
    roles=(date_row.get('metadata') or {}).get('participant_roles') or {}
    if filters.association=='event':
        complete=complete and (date_row.get('metadata') or {}).get('event_decision')=='accepted'
        if not complete:
            return [],lambda _identity:None,False
    selected={}
    unknown=set()
    for mention in mentions:
        identity=mention['parent_artifact_id']
        if filters.association=='event' and roles.get(identity,'co-mentioned')=='co-mentioned':
            continue
        distance=word_distance(span,mention,text) if filters.association=='word_window' else None
        if filters.association=='word_window' and distance is None:
            unknown.add(identity)
            continue
        if filters.association=='word_window' and distance>filters.window_words:
            continue
        entity=mention['entity']
        participant=selected.setdefault(identity,{'entity_id':identity,'name':entity.get('exact_text') or entity.get('normalized_key') or 'Unnamed entity',
            'entity_type':(entity.get('metadata') or {}).get('entity_type') or 'Entity', 'role':'co-mentioned',
            'mention_ids':[],'mention_count':0,'distance_words':distance})
        participant['mention_count']+=1
        if len(participant['mention_ids'])<MAX_MENTION_PROOF_PREVIEW:
            participant['mention_ids'].append(mention['artifact_id'])
        participant['mentions_truncated']=participant['mention_count']>MAX_MENTION_PROOF_PREVIEW
        if distance is not None:
            participant['distance_words']=min(participant['distance_words'],distance)
    def match(identity):
        if identity in selected:
            return True
        return None if not complete or identity in unknown else False
    return list(selected.values()),match,complete and not unknown
