"""Bounded canonical/import-observation Inbox, shared by GUI and plugins."""
from dataclasses import asdict, replace
from core.db import browse_cursors
from core.models.intelligence_models import ArtifactQuery
from core.models.artifact_reference import ArtifactRef
from core.services.intelligence.reference_note_service import digest

PAGE_SIZE=25
MAX_SCAN_PAGES=8


def query(service, *, kind='notes', excluded_ids=(), cursor='', limit=PAGE_SIZE, cancelled=lambda:False):
    if kind not in {'notes','legacy_notes','entities','discoveries'} or type(limit) is not int or not 1<=limit<=PAGE_SIZE:
        raise ValueError('Choose notes, canonical entities or saved discoveries, with at most 25 records.')
    if len(excluded_ids)>100000 or any(not isinstance(key,str) for key in excluded_ids):
        raise ValueError('Supply valid current workspace identities.')
    pm=service.project_manager
    revision=browse_cursors.revision(pm,'inbox')
    binding=digest([kind,sorted(set(excluded_ids)),limit,'inbox_v1'])
    excluded_ids=set(excluded_ids)
    position=browse_cursors.unpack(cursor,binding,revision)
    items,diagnostics=[],[]
    for _ in range(MAX_SCAN_PAGES):
        if cancelled(): return {'items':[],'next_cursor':'','cancelled':True}
        batch,next_position,issues=read_page(service,kind,position,limit,cancelled)
        diagnostics.extend(issues)
        items.extend(row for row in batch if row['inbox_id'] not in excluded_ids)
        position=next_position
        if position is None or items: break
    if revision!=browse_cursors.revision(pm,'inbox'):
        raise ValueError('The project changed while reading the Inbox. Refresh before continuing.')
    return {'items':items,'next_cursor':browse_cursors.pack(position,binding,revision),
        'project_identity':service.project_identity,'selection_revision':revision,
        'diagnostics':list(dict.fromkeys(diagnostics)),'page_count':len(items)}


def read_page(service,kind,position,limit,cancelled):
    if kind=='legacy_notes':
        return legacy_page(service,position,limit)
    if kind=='notes':
        page=service.query_notes(cursor=position.get('inner',''),limit=limit,cancelled=cancelled)
        rows=[{**row,'inbox_kind':'note','inbox_id':row['annot_id']} for row in page['items']]
        return rows,{'inner':page['next_cursor']} if page['next_cursor'] else None,page.get('diagnostics') or []
    if kind=='entities':
        page=service.artifacts.query_entities(ArtifactQuery(cursor=position.get('inner',''),limit=limit,
            statuses=['complete'],review_states=['unreviewed','verified','corrected'],current_only=True,mention_limit=0))
        if page.get('invalidated'): raise ValueError('Entities changed; refresh the Inbox.')
        rows=[{'inbox_id':row['artifact_id'],'inbox_kind':'entity','row':row,
            'title':row['exact_text'],'preview':(row['metadata'].get('entity_type') or 'Entity')+' · canonical intelligence entity'} for row in page['items']]
        return rows,{'inner':page['next_cursor']} if page['next_cursor'] else None,[]
    entities=service.project_manager.query_saved_discoveries(after_id=position.get('after',''),limit=limit)
    rows=[]
    for entity in entities:
        text=entity.properties.get('name') or entity.properties.get('title') or entity.properties.get('text') or ''
        if text.strip():
            rows.append({'inbox_id':entity.id,'inbox_kind':'discovery','entity':asdict(entity),
                'title':text.strip(),'preview':entity.entity_type+' · saved discovery (legacy)'})
    return rows,{'after':entities[-1].id} if entities else None,[]


def legacy_page(service,position,limit):
    from core.services.content.note_queries import next_source
    from core.services.intelligence.reference_note_records import PREFIX
    source=next_source(service.project_manager,'',position)
    if not source: return [],None,[]
    records=service.project_manager.query_highlights(source['path'],after_id=position.get('after_id'),limit=limit)
    output=[]
    for row in records:
        if row['id'].startswith(PREFIX): continue
        output.append({'inbox_id':row['id'],'inbox_kind':'note','annot_id':row['id'],
            'pdf_path':source['path'],'source_type':source['source_type'],'page_num':row.get('page_num'),
            'subject':row.get('text_content') or '', 'content':row.get('note_content') or '', 'color':row.get('color'),
            'legacy_record':row,'legacy_authorship':'unrecorded',
            'title':'Stored legacy note · '+(source.get('title') or source['path']),
            'preview':(row.get('note_content') or row.get('text_content') or 'Empty legacy record')[:240]})
    following={'source':source['id'],'after_id':records[-1]['id']} if records else {'after_source':source['id'],'done':True}
    return output,following,['Stored legacy snapshots may differ from physical annotations. This collection preserves DB-only notes; authorship/location are unverified.']


def prepare_import(service, items, *, expected_project, expected_revision):
    if tuple(expected_project)!=service.project_identity or expected_revision!=browse_cursors.revision(service.project_manager,'inbox'):
        raise ValueError('The project changed. Refresh the Inbox before importing.')
    if not isinstance(items,list) or not 1<=len(items)<=PAGE_SIZE:
        raise ValueError('Select between 1 and 25 records from this page.')
    output=[]
    for item in items:
        kind=item['inbox_kind']
        if kind=='note':
            reference=item.get('artifact_ref') or {}
            source=service.project_manager.get_source_record_by_path(item['pdf_path']) or {}
            if item.get('project_note'):
                current=service.note_snapshot(item['annot_id'])
                if current['revision']!=item['revision'] or current['note_revision']!=item['note_revision']:
                    raise ValueError('A selected note changed. Refresh before importing.')
            elif item.get('legacy_selection'):
                service.prepare_selection(item['legacy_selection'])  # Rechecks its physical fingerprint off Qt.
            elif item.get('legacy_record') and service.project_manager.get_highlight(item['annot_id'])!=item['legacy_record']:
                raise ValueError('The stored legacy note changed. Refresh before importing.')
            output.append({'kind':'annotation','data':{'id':item['annot_id'],'subject':item['subject'],
                'content':item['content'],'pdf_path':item['pdf_path'],'page_num':item['page_num'],'color':item.get('color'),
                'artifact_ref':reference,'source_id':source.get('id')}})
        elif kind=='discovery':
            entity=service.project_manager.get_entity(item['inbox_id'])
            if not entity or asdict(entity)!=item['entity']:
                raise ValueError('A saved discovery changed. Refresh before importing.')
            props=entity.properties
            output.append({'kind':'graph','data':{'id':entity.id,'type':entity.entity_type,
                'text':props.get('name') or props.get('title') or props.get('text') or '',**props}})
        elif kind=='entity':
            row=service.db.get_artifact(item['inbox_id'])
            if not row or row['revision']!=item['row']['revision'] or row['status']!='complete' or row['review_state'] not in {'unreviewed','verified','corrected'}:
                raise ValueError('An entity changed. Refresh before importing.')
            category=row['metadata'].get('entity_type')
            output.append({'kind':'graph','data':{'id':row['artifact_id'],
                'type':'entity.person_org' if category in {'PERSON','ORG','ORGANIZATION'} else 'entity.concept',
                'text':row['exact_text'],'artifact_ref':ArtifactRef.from_value(row).to_dict()}})
        else:
            raise ValueError('Unsupported Inbox record.')
    return {'items':output,'project_identity':service.project_identity}
