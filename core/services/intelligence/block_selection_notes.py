"""Verify selected raw block ranges against a file-bound extraction snapshot."""
from dataclasses import asdict

from core.models.source_anchor_models import AnchorSpan

TEXT_TYPES = frozenset({'txt', 'md', 'html', 'docx', 'epub', 'rtf', 'doc'})
MAX_SELECTED_BLOCKS = 128


def source_snapshot(service, source_path, block_ids=None):
    pm = service.project_manager
    source = pm.get_source_record_by_path(source_path)
    if not source or (source.get('state') or {}).get('is_removed'):
        raise ValueError('Choose a current source from this project.')
    extraction = service.artifacts.document_extraction_service
    if extraction is None:
        raise ValueError('Source extraction is unavailable.')
    if block_ids is not None and (not isinstance(block_ids, list) or not 1 <= len(block_ids) <= MAX_SELECTED_BLOCKS
                                  or any(not isinstance(value, str) or not value for value in block_ids)):
        raise ValueError('Choose valid recorded block IDs.')
    errors = []
    for key in dict.fromkeys((source['id'], source_path)):
        try:
            snapshot = extraction.get_cached_block_snapshot(key)
            if block_ids and not set(block_ids).issubset({block.block_id for block in snapshot['blocks']}):
                raise ValueError('The recorded blocks no longer exist in this extraction snapshot.')
            return {**snapshot, 'source_id': source['id'], 'source_path': source_path,
                    'source_type': source['source_type']}
        except ValueError as exc:
            errors.append(str(exc))
    raise ValueError(errors[-1])


def prepare(service, source, selection):
    chosen = selection.get('block_spans')
    if not isinstance(chosen, list) or not 1 <= len(chosen) <= MAX_SELECTED_BLOCKS:
        raise ValueError('Select a recorded block range (at most 128 blocks).')
    snapshot = source_snapshot(service, source['path'],
        [item.get('block_id') if isinstance(item, dict) else None for item in chosen])
    if any(selection.get(key) != snapshot[key] for key in ('source_hash', 'blocks_hash')):
        raise ValueError('The displayed source or extraction changed. Reopen the source before selecting it again.')
    blocks = snapshot['blocks']
    indices = {block.block_id: index for index, block in enumerate(blocks)}
    positions, spans, producers = [], [], {}
    for item in chosen:
        block_id = item.get('block_id') if isinstance(item, dict) else None
        if block_id not in indices:
            raise ValueError('The selected block no longer exists.')
        index = indices[block_id]
        block = blocks[index]
        start, end = item.get('char_start'), item.get('char_end')
        if type(start) is not int or type(end) is not int or not 0 <= start < end <= len(block.exact_text):
            raise ValueError('Select valid raw character bounds within this block.')
        rects = ()
        if source['source_type'] == 'image':
            rects = image_rect(source['path'], block)
        spans.append(AnchorSpan(block_id=block_id, char_start=start, char_end=end,
                               quote=block.exact_text[start:end], rects=rects))
        positions.append(index)
        producers[block_id] = asdict(block.provenance) if block.provenance else {'method': 'unknown'}
    validate_range(positions, spans, blocks, selection.get('quote'))
    return spans, {'kind': 'manual_image_block' if source['source_type'] == 'image' else 'manual_raw_blocks',
        'coordinate_basis': 'image_pixels' if source['source_type'] == 'image' else 'block_raw_text',
        'blocks_hash': snapshot['blocks_hash'], 'input_producers': producers,
        'explanation': 'Verified raw ranges in recorded extraction blocks bound to this source file. '
                       + ('Image geometry covers the OCR block, not individual selected characters.' if rects
                          else 'Coordinates refer to extracted block text, not file byte offsets.')}


def validate_range(positions, spans, blocks, quote):
    if positions != list(range(positions[0], positions[0] + len(positions))):
        raise ValueError('Select consecutive blocks in their recorded reading order.')
    for index, span in enumerate(spans):
        if (index and span.char_start != 0) or (index < len(spans)-1 and span.char_end != len(blocks[positions[index]].exact_text)):
            raise ValueError('A selection cannot skip text between its recorded endpoints.')
    if '\n'.join(span.quote for span in spans) != quote:
        raise ValueError('The quote does not agree exactly with the selected raw block ranges.')


def image_rect(path, block):
    from PIL import Image
    from core.services.intelligence.selection_note_source import valid_rect
    if not valid_rect(block.bbox):
        raise ValueError('This OCR block has no valid recorded image geometry.')
    with Image.open(path) as image:
        width, height = image.size
    if block.bbox[0] < 0 or block.bbox[1] < 0 or block.bbox[2] > width or block.bbox[3] > height:
        raise ValueError('This OCR block falls outside its recorded image.')
    return (tuple(block.bbox),)
