"""Verify an explicit manual selection without searching other passages or files."""
import math
import os
from copy import deepcopy

import fitz

from core.models.artifact_reference import ArtifactRef
from core.models.source_anchor_models import AnchorSpan, SourceNavigationTarget, NavigationPrecision, AnchorGranularity

MAX_WORD_RECTS = 4096
MAX_SEGMENTS = 128
RECT_TOLERANCE = .25


def prepare(service, selection):
    if not isinstance(selection, dict):
        raise ValueError("Choose a recorded selection first.")
    selection = deepcopy(selection)
    selection.pop('legacy_snapshot', None)  # Derived only from a verified on-disk annotation.
    if selection.get('legacy_annotation'):
        from core.services.intelligence.legacy_note_adoption import expand
        selection = expand(service, selection)
    source = selected_source(service.project_manager, selection)
    pm, path = service.project_manager, source['path']
    source_hash = service.artifacts.source_input_hash(path)
    if selection.get('source_hash') and selection['source_hash'] != source_hash:
        raise ValueError('The displayed source changed. Reopen it before selecting the passage again.')
    color = selection.get('color', '#b366ff')
    if not isinstance(color, str) or len(color) != 7 or color[0] != '#' or any(c not in '0123456789abcdefABCDEF' for c in color[1:]):
        raise ValueError('Choose a valid highlight color.')
    kind = source.get('source_type')
    if kind == 'pdf':
        spans, provenance = pdf_selection(path, selection)
        granularity = AnchorGranularity.REGION
    elif kind in {'video', 'audio'}:
        spans, provenance = media_selection(pm, source, selection)
        granularity = AnchorGranularity.TIME_RANGE
    else:
        from core.services.intelligence import block_selection_notes
        if kind not in block_selection_notes.TEXT_TYPES | {'image'}:
            raise ValueError('This source type has no verified selection adapter.')
        spans, provenance = block_selection_notes.prepare(service, source, selection)
        granularity = AnchorGranularity.REGION if kind == 'image' else AnchorGranularity.CHAR_RANGE
    if service.artifacts.source_input_hash(path) != source_hash:
        raise ValueError("The source changed while checking this selection. Select it again.")
    if selection.get('legacy_snapshot'):
        provenance['legacy_annotation'] = selection['legacy_annotation']
        provenance['original_annotation'] = selection['legacy_snapshot']
        provenance['explanation'] += ' Explicit adoption; original annotation retained and original authorship unrecorded.'
    quote = str(selection.get('quote') or '')
    anchor = SourceNavigationTarget('', NavigationPrecision.EXACT, granularity, source['id'], path, kind,
        tuple(spans), coordinate_basis=provenance['coordinate_basis'],
        reason=provenance['explanation']).to_dict()
    return {'reference': ArtifactRef(quote=quote, source_id=source['id'],
                source_title=source.get('title') or path).to_dict(),
            'current_artifact_id': '', 'anchor': anchor, 'source_hash': source_hash,
            'color': color,
            'selection': selection, 'selection_provenance': provenance,
            'summary': 'Manual selection verified against this source; no pre-existing artifact was assumed.'}


def selected_source(pm, selection):
    if selection.get('artifact_id') or selection.get('artifact_ref'):
        raise ValueError('Use the canonical reference command for an artifact-backed passage.')
    if not isinstance(selection.get('quote'), str) or not selection['quote'].strip():
        raise ValueError('Select a nonempty quoted passage.')
    path = selection.get('source_path')
    if not isinstance(path, str) or not path:
        raise ValueError('Select an explicit source path from this project.')
    source = pm.get_source_record_by_path(path) if path else None
    if not source or (selection.get('source_id') and selection['source_id'] != source['id']):
        raise ValueError("The selected source does not match this project. Select it again.")
    if (source.get('state') or {}).get('is_removed') or not os.path.isfile(path):
        raise ValueError("The selected source is removed or missing.")
    return source


def pdf_selection(path, selection):
    page_index, rects = selection.get('page_index'), selection.get('rects')
    if type(page_index) is not int or page_index < 0 or not isinstance(rects, list) or not 1 <= len(rects) <= MAX_WORD_RECTS:
        raise ValueError("Select a valid PDF page and its word rectangles.")
    if any(not valid_rect(rect) for rect in rects):
        raise ValueError("Invalid selected word rectangle.")
    with fitz.open(path) as document:
        if page_index >= len(document):
            raise ValueError("The selected page no longer exists.")
        words = document[page_index].get_text('words')
    words.sort(key=lambda word: (word[5], word[6], word[7]))
    word_positions = {tuple(word): index for index, word in enumerate(words)}
    grid = {}
    for word in words:
        grid.setdefault((math.floor(word[0]/RECT_TOLERANCE), math.floor(word[1]/RECT_TOLERANCE)), []).append(word)
    chosen, seen = [], set()
    for rect in rects:
        x, y = math.floor(rect[0]/RECT_TOLERANCE), math.floor(rect[1]/RECT_TOLERANCE)
        candidates = [word for dx in (-1,0,1) for dy in (-1,0,1) for word in grid.get((x+dx, y+dy), [])]
        matches = [word for word in candidates if all(abs(a-b) <= RECT_TOLERANCE for a,b in zip(rect, word[:4]))]
        if len(matches) != 1 or tuple(matches[0]) in seen:
            raise ValueError("Selected geometry no longer identifies unique source words. Select the passage again.")
        chosen.append(matches[0])
        seen.add(tuple(matches[0]))
    positions = [word_positions[tuple(word)] for word in chosen]
    if positions != list(range(positions[0], positions[0]+len(positions))):
        raise ValueError('Select consecutive PDF words in their recorded reading order.')
    if normalized(' '.join(word[4] for word in chosen)) != normalized(selection.get('quote')):
        raise ValueError("The selected quote does not agree with its recorded PDF words.")
    provenance = {'kind': 'manual_pdf_words', 'coordinate_basis': 'pdf_unrotated_points',
        'explanation': 'Verified selected word rectangles in this PDF page; no document-wide quote search.'}
    return [AnchorSpan(page_index=page_index, quote=selection['quote'], rects=tuple(tuple(r) for r in rects))], provenance


def media_selection(pm, source, selection):
    transcript = pm.get_video_transcript(source['id']) or {}
    indices = selection.get('segment_indices')
    segments = transcript.get('segments') or []
    if transcript.get('source_id', source['id']) != source['id'] or transcript.get('path', source['path']) != source['path']:
        raise ValueError("This transcript belongs to a different source.")
    if transcript.get('status') != 'completed' or not isinstance(indices, list) or not 1 <= len(indices) <= MAX_SEGMENTS:
        raise ValueError("Choose recorded segments from a completed transcript.")
    if any(type(i) is not int or not 0 <= i < len(segments) for i in indices) or indices != list(range(indices[0], indices[0]+len(indices))):
        raise ValueError("Select a contiguous recorded transcript range.")
    chosen = [segments[i] for i in indices]
    text = normalized(' '.join(str(seg.get('text') or '') for seg in chosen))
    quote = normalized(selection.get('quote'))
    if not quote or quote not in text:
        raise ValueError("The quote does not occur inside the selected transcript segments.")
    start, end = chosen[0].get('start'), chosen[-1].get('end')
    if any(not valid_time(seg.get('start')) or not valid_time(seg.get('end')) or seg['end'] < seg['start'] for seg in chosen):
        raise ValueError("The selected transcript has invalid time bounds.")
    if any(b['start'] < a['start'] or b['end'] < a['end'] for a,b in zip(chosen, chosen[1:])):
        raise ValueError("The selected transcript range is not in time order.")
    provenance = {'kind': 'manual_transcript_segments', 'coordinate_basis': 'transcript_segment_seconds',
        'segment_indices': indices, 'transcript_model': transcript.get('model') or 'unknown',
        'segments': chosen, 'explanation': 'Verified inside selected transcript segments; timing is segment-level, not word-level.'}
    return [AnchorSpan(quote=selection['quote'], time_start=start, time_end=end)], provenance


def valid_time(value):
    return type(value) in (int, float) and math.isfinite(value) and value >= 0


def valid_rect(rect):
    return (isinstance(rect, (list, tuple)) and len(rect) == 4
            and all(type(v) in (int, float) and math.isfinite(v) for v in rect)
            and rect[0] < rect[2] and rect[1] < rect[3])


def normalized(text):
    return ' '.join(str(text or '').split())
