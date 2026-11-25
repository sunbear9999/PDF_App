"""Revision-guarded personal projection filters in existing project metadata."""
from contextlib import nullcontext
from dataclasses import asdict
import hashlib
import json
import uuid

from core.models.event_projection import coerce_filter
from core.models.network_projection import coerce_network_filter
from core.services.intelligence.event_projection import resolve_filter
from core.events.domains.intelligence_events import IntelligenceEvent,ProjectionViewPayload

METADATA_KEY='intelligence_projection_views_v1'
MAX_VIEWS=50
MAX_STORE_BYTES=1024*1024
EMPTY=json.dumps({'schema_version':1,'views':[]},sort_keys=True)


def check_ids(service, filters):
    """Offer only confirmed same-source correspondence, never guess replacements."""
    from core.db.event_projection_queries import read_artifacts
    filters=coerce_filter(filters)
    keys=set(filters.included_artifact_ids)|set(filters.excluded_artifact_ids)
    records=read_artifacts(service.db, keys)
    replacements,unresolved={},[]
    for identity in sorted(keys):
        original=records.get(identity)
        try:
            current=service.db.resolve_current_artifact_id(identity)
        except ValueError:
            unresolved.append(identity); continue
        row=service.db.get_artifact(current) if current!=identity else original
        alias=service.db.resolve_artifact_alias(current)
        conflict=(alias or {}).get('status')=='needs_review' or ((row or {}).get('metadata') or {}).get('migration_conflict')
        if conflict or not row or not original or row['source_id']!=original['source_id'] or row['artifact_type']!='date_mention' or row['status']!='complete' or row['review_state'] not in {'unreviewed','verified','corrected'}:
            unresolved.append(identity)
        elif current!=identity:
            replacements[identity]=current
    return {'replacements':replacements,'unresolved_ids':unresolved,
            'filters':asdict(filters), 'project_identity':(id(service.db),id(service.db._conn))}


def read(service):
    if service.db is None:
        raise ValueError('Open a project before saving research views.')
    raw=service.project_manager.get_metadata(METADATA_KEY,EMPTY) or EMPTY
    try:
        if not isinstance(raw,str) or len(raw.encode())>MAX_STORE_BYTES:
            raise ValueError()
        store=json.loads(raw)
        if store.get('schema_version')!=1 or not isinstance(store.get('views'),list) or len(store['views'])>MAX_VIEWS:
            raise ValueError()
        views=[validate_view(view) for view in store['views']]
        if len({view['id'] for view in views})!=len(views):
            raise ValueError()
    except (ValueError,TypeError,KeyError,AttributeError):
        raise ValueError('Saved research views could not be read; no stored data was changed.') from None
    return {'views':views,'token':hashlib.sha256(raw.encode()).hexdigest(),
            'project_identity':(id(service.db),id(service.db._conn))}


def validate_view(value):
    if not isinstance(value,dict) or set(value)-{'id','name','kind','filters','scope'} or not {'id','name','kind','filters'} <= set(value) or value['kind'] not in {'timeline','network'}:
        raise ValueError('Choose a valid timeline or network perspective.')
    if not isinstance(value['id'],str) or str(uuid.UUID(value['id']))!=value['id']:
        raise ValueError('Invalid saved view identity.')
    name=value['name']
    if not isinstance(name,str) or not 1<=len(name.strip())<=80:
        raise ValueError('Give the view a name between 1 and 80 characters.')
    validator = coerce_filter if value['kind'] == 'timeline' else coerce_network_filter
    scope = value.get('scope')
    if scope is not None and (not isinstance(scope, dict) or set(scope) != {'source_ids', 'text'}
            or not isinstance(scope['source_ids'], list) or len(scope['source_ids']) > 1000
            or any(not isinstance(key, str) or not 0 < len(key) <= 512 for key in scope['source_ids'])
            or not isinstance(scope['text'], str) or len(scope['text']) > 4096):
        raise ValueError('Choose valid saved source identities and search text.')
    return {**value,'name':name.strip(),'filters':asdict(validator(value['filters']))}


def write(service,*,name='',filters=None,view_id='',delete=False,kind='timeline',scope=None,expected_token,expected_project):
    db,pm=service.db,service.project_manager
    if db is None:
        raise ValueError('Open a project before saving research views.')
    with db._write_lock,getattr(pm,'db_lock',nullcontext()):
        state=read(service)
        if tuple(expected_project)!=state['project_identity'] or expected_token!=state['token']:
            raise ValueError('The project or saved views changed. Reload before saving; your draft is retained.')
        existing={view['id']:view for view in state['views']}
        if view_id and view_id not in existing:
            raise ValueError('The selected view no longer exists. Reload before saving.')
        identity=view_id or str(uuid.uuid4())
        if delete:
            if not view_id:
                raise ValueError('Choose a saved view to remove.')
            existing.pop(view_id)
        else:
            if kind not in {'timeline', 'network'}:
                raise ValueError('Choose a timeline or network perspective.')
            compiled=resolve_filter(service,filters) if kind == 'timeline' else coerce_network_filter(filters)
            if kind == 'network' and compiled.focus_entity_id:
                from core.db.event_projection_queries import resolve_entity
                resolve_entity(db, compiled.focus_entity_id, {compiled.focus_entity_id: compiled.focus_entity_id})
            existing[identity]=validate_view({'id':identity,'name':name,'kind':kind,'filters':asdict(compiled), 'scope':scope})
            if any(view['id']!=identity and view['name'].casefold()==name.strip().casefold() for view in existing.values()):
                raise ValueError('Choose a unique saved filter name, or load the existing view to update it.')
        if len(existing)>MAX_VIEWS:
            raise ValueError('Remove an unused saved view before adding another (maximum 50).')
        encoded=json.dumps({'schema_version':1,'views':list(existing.values())},sort_keys=True)
        if len(encoded.encode())>MAX_STORE_BYTES:
            raise ValueError('Saved view settings are too large; remove unused exclusions or views.')
        pm.set_metadata(METADATA_KEY,encoded)
        result=read(service)
    signal=getattr(getattr(pm,'bus',None),'intelligence_state_changed',None)
    if signal is not None:
        signal.emit(IntelligenceEvent.PROJECTION_VIEW_CHANGED,ProjectionViewPayload(identity))
    return {**result,'view_id':identity}
