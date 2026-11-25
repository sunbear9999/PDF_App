"""Explicit, read-only adoption of an identified on-disk PDF highlight."""
import hashlib
import json
import uuid

import fitz

from core.models.producer_models import deterministic


def fingerprint(annotation):
    value = {'info': annotation.info, 'vertices': annotation.vertices, 'type': annotation.type[0],
             'color': annotation.colors.get('stroke')}
    return hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()


def expand(service, selection):
    path, descriptor = selection.get('source_path'), selection.get('legacy_annotation')
    if not isinstance(descriptor, dict):
        raise ValueError('Choose an identified physical PDF annotation.')
    page_index, xref = descriptor.get('page_index'), descriptor.get('xref')
    if type(page_index) is not int or page_index < 0 or type(xref) is not int or xref <= 0:
        raise ValueError('Choose a valid annotation page and object number.')
    source = service.project_manager.get_source_record_by_path(path)
    if not source or source.get('source_type') != 'pdf':
        raise ValueError('Only an explicit project PDF annotation can be adopted.')
    source_hash = service.artifacts.source_input_hash(path)
    with fitz.open(path) as document:
        if page_index >= len(document):
            raise ValueError('The annotation page no longer exists.')
        page = document[page_index]
        annotation = page.load_annot(xref)
        if annotation is None or annotation.type[0] != fitz.PDF_ANNOT_HIGHLIGHT:
            raise ValueError('Only recorded text highlights can be adopted. Select source words for other annotation types.')
        if fingerprint(annotation) != descriptor.get('fingerprint'):
            raise ValueError('This physical annotation changed. Refresh Notes before adopting it.')
        info, vertices = dict(annotation.info), annotation.vertices or []
        regions = [fitz.Quad(vertices[index:index+4]).rect for index in range(0, len(vertices), 4)]
        words = sorted(page.get_text('words'), key=lambda word: (word[5], word[6], word[7]))
        chosen = [word for word in words if any(region.contains(fitz.Point((word[0]+word[2])/2, (word[1]+word[3])/2)) for region in regions)]
    if service.artifacts.source_input_hash(path) != source_hash or not chosen:
        raise ValueError('The highlight source changed or has no readable words. Refresh Notes or select a new passage.')
    quote = ' '.join(word[4] for word in chosen)
    key = json.dumps([source['id'], page_index, xref, [word[:4] for word in chosen], quote])
    return {**selection, 'quote': quote, 'source_hash': source_hash, 'page_index': page_index,
        'rects': [list(word[:4]) for word in chosen], 'legacy_snapshot': {
            'info': info, 'authorship': 'unrecorded', 'fingerprint': descriptor['fingerprint'],
            'request_id': str(uuid.uuid5(uuid.NAMESPACE_URL, key))}}


def note_lineage(prepared, body, producer, lineage):
    snapshot = prepared.get('selection', {}).get('legacy_snapshot')
    if snapshot is None:
        return producer, lineage
    lineage = {**lineage, 'legacy_authorship': 'unrecorded', 'legacy_original_content': snapshot['info'].get('content') or ''}
    if body == lineage['legacy_original_content']:
        producer = deterministic('legacy_annotation_import', '1.0')
        lineage['authored_by'] = 'unknown'
    return producer, lineage
