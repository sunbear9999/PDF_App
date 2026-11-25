"""Bounded temporal projections over canonical dates and sentence evidence."""
from dataclasses import asdict,replace
import json
from core.db import browse_cursors
from core.db.event_projection_queries import contexts,resolve_entity
from core.db.intelligence_artifact_queries import coerce_query,revision_token,fingerprint
from core.models.entity_expression import parse_expression,expression_ids,evaluate_expression,replace_entity_ids
from core.models.event_projection import coerce_filter,calendar_interval,interval_matches,MAX_EVENT_PAGE
from core.models.intelligence_models import ArtifactQuery
from core.services.intelligence.event_associations import participants

SCAN_BATCH_SIZE=128
MAX_SCAN_BATCHES=8
MAX_CONTEXT_PREVIEW=4000
MAX_PARTICIPANT_PREVIEW=32
EXCLUDED_CONTEXTS={'citation','reference','publication','copyright','metadata','bibliography','instrument_name'}


def query_timeline(service,query=None,filters=None,*,cancelled=lambda:False):
    if service.db is None:
        raise ValueError('Open a project before browsing event timelines.')
    query=coerce_query(query or ArtifactQuery())
    filters=resolve_filter(service,filters)
    expression=filters.expression
    if type(query.limit) is not int or not 1<=query.limit<=MAX_EVENT_PAGE:
        raise ValueError('Choose between 1 and 200 timeline events per page.')
    filters=replace(filters,expression=expression,date_from=filters.date_from or query.date_from,
                    date_to=filters.date_to or query.date_to)
    filters=coerce_filter(filters)
    binding=fingerprint(query,json.dumps(['event_projection_v1',asdict(filters)],sort_keys=True))
    revision=[id(service.db),id(service.db._conn),revision_token(service.db)]
    try:
        position=browse_cursors.unpack(query.cursor,binding,revision)
    except ValueError as exc:
        return {'items':[],'next_cursor':'','invalidated':True,'reason':str(exc)}
    base=replace(query,cursor=position.get('inner',''),artifact_types=['date_mention'],
        order_by='date',timeline_only=False,date_from='',date_to='',statuses=['complete'],
        review_states=['unreviewed','verified','corrected'],current_only=True)
    result=scan(service,base,filters,cancelled)
    if revision!=[id(service.db),id(service.db._conn),revision_token(service.db)]:
        return {'items':[],'next_cursor':'','invalidated':True,'reason':'Knowledge changed during timeline reading.'}
    if result.get('invalidated') or result.get('cancelled'):
        return result
    result['next_cursor']=browse_cursors.pack({'inner':result['next_cursor']},binding,revision) if result['next_cursor'] else ''
    result['snapshot_revision']=revision[-1]
    result['filter_expression']=asdict(expression) if expression else None
    return result


def resolve_filter(service,value):
    filters=coerce_filter(value)
    expression=filters.expression or parse_expression(filters.expression_text,
        lambda name:resolve_entity(service.db,name,filters.entity_bindings))
    identities={identity:resolve_entity(service.db,identity,{identity:identity}) for identity in expression_ids(expression)}
    return replace(filters,expression=replace_entity_ids(expression,identities))


def scan(service,base,filters,cancelled):
    items,artifacts,diagnostics=[],{},set()
    cursor=base.cursor
    for _ in range(MAX_SCAN_BATCHES):
        if cancelled():
            return {'items':[],'next_cursor':'','cancelled':True}
        page=service.db.query_event_dates(replace(base,cursor=cursor,limit=min(SCAN_BATCH_SIZE,base.limit-len(items))),filters)
        if page.get('invalidated'):
            return page
        dates=page.get('items') or []
        dates=[row for row in dates if temporal_candidate(row,filters) is not None]
        windows,mentions,truncated,blocks=contexts(service.db,dates)
        if truncated:
            diagnostics.add('A dense evidence batch exceeded its mention budget; unresolved Boolean matches were omitted. Narrow the source/date range.')
        for row in dates:
            context=windows.get(row['artifact_id']) or {}
            event=project_event(row,context,mentions.get(context.get('artifact_id'),[]),blocks,filters,truncated)
            if event is not None:
                items.append(event)
                artifacts[row['artifact_id']]=row
                if context:
                    artifacts[context['artifact_id']]=context
        cursor=page.get('next_cursor') or ''
        if not cursor or len(items)>=base.limit:
            break
    return {'items':items,'artifacts':list(artifacts.values()),'next_cursor':cursor,
            'diagnostics':sorted(diagnostics),'page_count':len(items)}


def project_event(row,context,mentions,blocks,filters,truncated=False):
    temporal=temporal_candidate(row,filters)
    if temporal is None:
        return None
    raw_date,interval,eligible,reason=temporal
    metadata=row.get('metadata') or {}
    hidden=row['artifact_id'] in filters.excluded_artifact_ids
    text=(context.get('exact_text') or row.get('exact_text') or '').strip()
    token=str(metadata.get('exact_text') or raw_date).strip()
    has_context=bool(text and (metadata.get('manual_event') or (text!=token and len(text)>len(token))))
    eligible=eligible and has_context
    if not eligible and not filters.include_excluded and row['artifact_id'] not in filters.included_artifact_ids:
        return None
    people,match,complete=participants(row,context,mentions,blocks,filters,truncated)
    for person in people:
        person['role']=metadata.get('participant_roles',{}).get(person['entity_id'],'co-mentioned')
    if evaluate_expression(filters.expression,match) is not True:
        return None
    return {'event_id':'event:'+row['artifact_id'],'artifact_id':row['artifact_id'],
        'source_id':row.get('source_id'),'date':raw_date if interval else '',
        'date_start':interval[0] if interval else None,'date_end':interval[1] if interval else None,
        'precision':metadata.get('precision') or ('unresolved' if interval is None else {4:'year',7:'month',10:'day'}.get(len(raw_date),'unresolved')),
        'certainty':metadata.get('certainty') or 'unrecorded','text':(metadata.get('event_summary') or text)[:MAX_CONTEXT_PREVIEW],
        'quote':text[:MAX_CONTEXT_PREVIEW], 'authored':bool(metadata.get('manual_event')),
        'event_decision':metadata.get('event_decision') or 'candidate',
        'personally_included':row['artifact_id'] in filters.included_artifact_ids,
        'context_truncated':len(text)>MAX_CONTEXT_PREVIEW,'context_artifact_id':context.get('artifact_id'),
        'association_reason':'' if complete else 'No complete, unique recorded evidence window or valid coordinate mapping; unknown Boolean matches abstain.',
        'participants':people[:MAX_PARTICIPANT_PREVIEW],'participant_count':len(people),
        'participants_truncated':len(people)>MAX_PARTICIPANT_PREVIEW,
        'association':filters.association,'association_complete':complete,
        'review_state':row.get('review_state'),'hidden':hidden,'event_candidate':eligible,
        'exclusion_reason':reason or ('' if eligible else 'No event context'),
        'explanation':('User-assigned participant roles on an accepted event; absence means no assigned role in this saved interpretation.' if filters.association=='event' else
            'Date and entities share a recorded sentence; co-mention does not establish participation.'
            if filters.association=='sentence' else f'Entities within {filters.window_words} whitespace-separated words of this date inside its recorded sentence; unknown coordinate mappings abstain.')}


def temporal_candidate(row,filters):
    metadata=row.get('metadata') or {}
    if row['artifact_id'] in filters.excluded_artifact_ids and not filters.show_hidden:
        return None
    eligible=(metadata.get('event_decision')=='accepted' or (metadata.get('timeline_eligible') is True
        and metadata.get('context_role') not in EXCLUDED_CONTEXTS)) and metadata.get('event_decision')!='dismissed'
    if not eligible and not filters.include_excluded and row['artifact_id'] not in filters.included_artifact_ids:
        return None
    raw=metadata.get('normalized_date') or row.get('normalized_text') or ''
    try:
        interval=calendar_interval(raw)
        end=calendar_interval(metadata.get('event_date_end') or '')
        if interval and end:
            interval=(interval[0],end[1]) if end[1]>=interval[0] else None
    except ValueError:
        interval=None
    if not interval_matches(interval,filters):
        return None
    reason='' if eligible else ('Dismissed event candidate' if metadata.get('event_decision')=='dismissed' else
        metadata.get('timeline_exclusion_reason') or metadata.get('context_role') or 'Event eligibility not recorded; rerun Dates or inspect in All date observations')
    return raw,interval,eligible,reason
