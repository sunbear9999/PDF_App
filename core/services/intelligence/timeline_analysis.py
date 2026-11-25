"""Analysis envelope adapter for the same contextual event query used by the dock."""
import json
import uuid
from core.models.intelligence_models import AnalysisResultEnvelope, ArtifactQuery, SourceScope

MAX_ANALYSIS_EVENTS=100000


def run(service, source_ids, params, persist):
    artifact_service=service.artifact_service
    limit=params.get('artifact_limit',200)
    if type(limit) is not int or not 1<=limit<=MAX_ANALYSIS_EVENTS:
        raise ValueError('Choose between 1 and 100000 timeline events; results beyond the requested limit have a continuation.')
    query=ArtifactQuery(source_scope=SourceScope(source_ids=source_ids),limit=min(200,limit),cursor=params.get('event_cursor',''))
    filters=params.get('event_filters') or {}
    events,diagnostics=[],[]
    while True:
        page=artifact_service.query_timeline(query,filters)
        if page.get('invalidated') or page.get('cancelled'):
            raise ValueError(page.get('reason') or 'Timeline changed during analysis; restart it.')
        events.extend(page['items']); diagnostics.extend(page.get('diagnostics') or [])
        cursor=page.get('next_cursor') or ''
        if not cursor or len(events)>=limit: break
        from dataclasses import replace
        query=replace(query,cursor=cursor,limit=min(200,limit-len(events)))
    nodes=[event_node(row) for row in events]
    output={'timeline':events,'entities':nodes,'relations':[], 'event_next_cursor':cursor,
            'event_filters':filters,'source_ids':source_ids, 'complete_node_count':len(nodes),
            'visible_node_ids':[row['id'] for row in nodes[:200]],'has_more_events':bool(cursor),
            'diagnostics':list(dict.fromkeys(diagnostics))}
    identity=None
    if persist:
        identity=artifact_service.create_artifact(source_id=source_ids[0] if len(source_ids)==1 else None,
            artifact_type='analysis_result',storage_ref_type='deterministic_analysis',
            storage_ref_id=service._storage_key('timeline',source_ids,params),exact_text=json.dumps(output,ensure_ascii=False),
            metadata={'stage':'analysis','mode_id':'timeline','result_kind':'timeline','model_call_count':0,
                'source_ids':source_ids,'producer_version':service.PRODUCER_VERSION},
            created_by='deterministic_graph_analysis',extractor_name='contextual_event_projection')
    return AnalysisResultEnvelope(run_id=str(uuid.uuid4()),mode_id='timeline',result_kind='timeline',output=output,
        output_artifact_ids=[identity] if identity else [],provenance={'producer':'contextual_event_projection','source_ids':source_ids},
        diagnostics={'model_call_count':0,'messages':output['diagnostics'],'has_more_events':bool(cursor)},
        model_usage={'call_count':0},view_state={'initial_visible_limit':200,'visible_node_ids':output['visible_node_ids']})


def event_node(row):
    return {'id':row['event_id'],'type':'entity.timeline_event','text':row['text'],
        'date':row['date'],'date_start':row['date_start'],'date_end':row['date_end'],
        'precision':row['precision'],'certainty':row['certainty'],'source_id':row['source_id'],
        'artifact_id':row['artifact_id'],'event_decision':row['event_decision'],
        'artifact_ref':{'artifact_id':row.get('context_artifact_id') or row['artifact_id'],'source_id':row['source_id'],'quote':row['quote']},
        'date_artifact_id':row['artifact_id'],
        'exact_text':row['quote'],'summary':row['text'],'authored':row['authored']}
