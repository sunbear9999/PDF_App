"""Bounded mixed-source Notes pages, including read-only physical observations."""
import os
import fitz

from core.db import browse_cursors as browse_cursor
from core.services.content.linked_note_browsing import _note
from core.services.content.physical_note_browsing import physical_notes_page
from core.services.intelligence.reference_note_records import anchor_of, ids, PREFIX

MAX_NOTES = 25
SCAN_BATCHES = 8


def query(service, *, source_path='', tag='', cursor='', limit=MAX_NOTES, cancelled=lambda: False):
    if type(limit) is not int or not 1 <= limit <= MAX_NOTES:
        raise ValueError('Choose between 1 and 25 notes.')
    pm = service.project_manager
    binding = [source_path, tag, limit, 'notes_v1']
    position = browse_cursor.unpack(cursor, binding, browse_cursor.revision(pm))
    items, diagnostics = [], []
    for _ in range(SCAN_BATCHES):
        if cancelled():
            return {'items': [], 'next_cursor': '', 'cancelled': True}
        source = next_source(pm, source_path, position)
        if not source:
            position = None
            break
        position.setdefault('source', source['id'])
        position.setdefault('phase', 'records')
        remaining = limit-len(items)
        if position['phase'] == 'records':
            batch, position = record_page(service, source, tag, position, remaining)
        else:
            batch, position, issue = physical_page(pm, source, tag, position, remaining, cancelled)
            if issue:
                diagnostics.append(issue)
        items.extend(batch)
        if len(items) >= limit:
            break
    for item in items:
        item['title'] = os.path.basename(item['pdf_path']) + ' · ' + ('Project note' if item.get('project_note') else 'Legacy observation')
        item['preview'] = (item.get('content') or item.get('subject') or 'Empty note')[:240]
    tags = pm.query_tags() if hasattr(pm, 'query_tags') else []
    return {'items': items, 'diagnostics': diagnostics, 'tags': tags,
        'next_cursor': browse_cursor.pack(position, binding, browse_cursor.revision(pm))}


def next_source(pm, path, position):
    if path:
        source = pm.get_source_record_by_path(path)
        return source if not position.get('done') and source and not (source.get('state') or {}).get('is_removed') else None
    if position.get('source'):
        lookup = getattr(pm, 'get_source_record', None)
        if lookup:
            return lookup(position['source'])
    after = position.get('after_source', '')
    if hasattr(pm, 'query_source_records'):
        sources = pm.query_source_records(after_id=after, limit=1)
    else:  # Compatibility for injected headless project adapters.
        sources = sorted((s for s in pm.list_sources() if s['id'] > after), key=lambda s: s['id'])[:1]
    return sources[0] if sources else None


def record_page(service, source, tag, position, limit):
    pm = service.project_manager
    rows = pm.query_highlights(source['path'], after_id=position.get('after_id'), limit=limit)
    records = {}
    identities = []
    for row in rows:
        anchor = anchor_of(row)
        if anchor:
            identity = ids(anchor['source_id'], row['id'][len(PREFIX):])
            identities.append((row, anchor, identity))
    keys = [identity[key] for _, _, identity in identities for key in ('annotation_id', 'note_id')]
    if keys:
        records = service.db.get_artifacts_by_ids(keys)
    output = []
    doc_tags = pm.get_tags_for_doc(source['path'])
    for row, anchor, identity in identities:
        annotation, note = (records.get(identity[key]) for key in ('annotation_id', 'note_id'))
        if annotation and note and annotation['status'] == 'complete':
            item = _note(pm, row, anchor, identity, annotation, note, tag, doc_tags)
            if item:
                output.append(item)
    for row in rows:
        if not row['id'].startswith(PREFIX) and source['source_type'] != 'pdf':
            item = legacy_record(pm, source, row, tag, doc_tags)
            if item:
                output.append(item)
    position = {**position, 'after_id': rows[-1]['id']} if rows else {**position, 'phase': 'physical'}
    return output, position


def legacy_record(pm, source, row, tag, doc_tags):
    tags = pm.get_tags_for_node(row['id'])
    if tag and tag not in {value['name'] for value in tags + doc_tags}:
        return None
    return {'pdf_path': source['path'], 'source_type': source['source_type'], 'annot_id': row['id'],
        'page_num': row.get('page_num') or 0, 'subject': row.get('text_content') or '',
        'content': row.get('note_content') or '', 'color': row.get('color'), 'tags': tags,
        'is_ai': False, 'legacy_authorship': 'unrecorded'}


def physical_page(pm, source, tag, position, limit, cancelled):
    if source['source_type'] == 'pdf':
        try:
            stat = os.stat(source['path'])
            stamp = [stat.st_mtime_ns, stat.st_size]
            if position.get('file_stamp') and position['file_stamp'] != stamp:
                raise RuntimeError('The PDF changed. Refresh Notes before continuing.')
            result = physical_notes_page(pm, source['path'], tag, position=position.get('physical'), limit=limit, cancelled=cancelled)
            if result['position'] is not None:
                return result['items'], {**position, 'physical': result['position'], 'file_stamp': stamp}, ''
            return result['items'], {'after_source': source['id'], 'done': True}, ''
        except (OSError, ValueError, fitz.FileDataError, fitz.FileNotFoundError) as exc:
            return [], {'after_source': source['id'], 'done': True}, f"{source.get('title') or source['path']}: {exc}"
    return [], {'after_source': source['id'], 'done': True}, ''
