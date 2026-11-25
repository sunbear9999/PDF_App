"""Owned on-disk PDF readers; never borrow a GUI's mutable fitz document."""
import fitz
import logging

from core.services.intelligence.legacy_note_adoption import fingerprint


def physical_notes(pm, path, tag_name, *, cancelled=lambda: False):
    try:
        return _read_notes(pm, path, tag_name, cancelled)
    except (OSError, fitz.FileDataError, fitz.FileNotFoundError) as exc:
        logging.getLogger(__name__).warning('Physical annotation source unavailable: %s', exc)
        return []


def _read_notes(pm, path, tag_name, cancelled):
    rows, position = [], {}
    while position is not None and not cancelled():
        page = physical_notes_page(pm, path, tag_name, position=position, cancelled=cancelled)
        rows.extend(page['items'])
        position = page['position']
    return rows


def physical_notes_page(pm, path, tag_name='', *, position=None, limit=25, cancelled=lambda: False):
    """Read at most eight PDF pages and a bounded annotation batch."""
    if type(limit) is not int or not 1 <= limit <= 50:
        raise ValueError('Choose between 1 and 50 notes.')
    position = position or {}
    output = []
    doc_tags = {tag['name'] for tag in pm.get_tags_for_doc(path)}
    with fitz.open(path) as document:
        if document.needs_pass:
            raise ValueError('Unlock this PDF before reading its physical annotations.')
        page_index, after = position.get('page', 0), position.get('xref', 0)
        stop = min(len(document), page_index + 8)
        while page_index < stop and not cancelled():
            page = document[page_index]
            xrefs = sorted(xref for xref, kind, *_ in page.annot_xrefs() if xref > after and kind == fitz.PDF_ANNOT_HIGHLIGHT)
            for xref in xrefs:
                if cancelled():
                    break
                annotation = page.load_annot(xref)
                after = xref
                item = annotation_record(pm, path, page_index, annotation, tag_name, doc_tags)
                if item:
                    output.append(item)
                if len(output) >= limit:
                    return {'items': output, 'position': {'page': page_index, 'xref': after}}
            page_index, after = page_index + 1, 0
        position = {'page': page_index, 'xref': 0} if page_index < len(document) else None
    return {'items': output, 'position': position}


def annotation_record(pm, path, index, annotation, tag_name, doc_tags):
    title = annotation.info.get('title') or ''
    if not title.startswith(('UserNote', 'AINote')):
        return None
    tags = pm.get_tags_for_node(title)
    if tag_name and tag_name not in doc_tags | {tag['name'] for tag in tags}:
        return None
    return {'pdf_path': path, 'page_num': index, 'annot_id': title,
        'subject': annotation.info.get('subject') or '', 'content': annotation.info.get('content') or '',
        'color': annotation.colors.get('stroke'), 'is_ai': False, 'source_type': 'pdf', 'tags': tags,
        'legacy_authorship': 'unrecorded', 'legacy_selection': {
            'source_path': path, 'color': annotation_color(annotation),
            'legacy_annotation': {'page_index': index, 'xref': annotation.xref,
                'fingerprint': fingerprint(annotation)}}}


def annotation_color(annotation):
    rgb = annotation.colors.get('stroke') or (0.7, 0.4, 1.0)
    if len(rgb) != 3:
        return '#b366ff'
    return '#' + ''.join(f'{max(0, min(255, round(channel*255))):02x}' for channel in rgb)
