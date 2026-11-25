"""Worker-side text pages and image decoding for source viewers."""
from dataclasses import replace
import json
import os
from PySide6.QtCore import Qt

from core.utils.file_hash import file_sha256

TEXT_PAGE_CHARS = 16000
TEXT_PART_CHARS = 4000
TEXT_PAGE_BLOCKS = 32
IMAGE_DISPLAY_SIDE = 4096


def text_page(snapshot, cursor='', spans=None):
    from gui.components.raw_block_view import display_text
    blocks = snapshot['blocks']
    position = json.loads(cursor) if cursor else {'block': 0, 'char': 0}
    if spans:
        start = spans[0]
        index = next((i for i, block in enumerate(blocks) if block.block_id == start.get('block_id')), None)
        if index is None:
            raise ValueError('The recorded block is unavailable in this extraction.')
        position = {'block': index, 'char': start.get('char_start') or 0}
    index, offset = position['block'], position['char']
    if type(index) is not int or type(offset) is not int or not 0 <= index <= len(blocks) or offset < 0:
        raise ValueError('Invalid source text page. Reopen the source.')
    chosen, offsets, rendered, count = [], [], [], 0
    while index < len(blocks) and count < TEXT_PAGE_CHARS and len(chosen) < TEXT_PAGE_BLOCKS:
        block = blocks[index]
        text = block.exact_text[offset:offset+min(TEXT_PART_CHARS, TEXT_PAGE_CHARS-count)]
        if text:
            chosen.append(replace(block, exact_text=text, normalized_text=' '.join(text.split())))
            offsets.append(offset)
            rendered.append(display_text(text))
            count += len(text)
            offset += len(text)
        if offset >= len(block.exact_text):
            index, offset = index+1, 0
    summary = {key: snapshot[key] for key in ('source_id', 'source_path', 'source_type', 'source_hash', 'blocks_hash')}
    return {**summary, 'blocks': chosen, 'raw_offsets': offsets, 'rendered': rendered,
        'next_cursor': json.dumps({'block': index, 'char': offset}) if index < len(blocks) else ''}


def decode_image(path):
    from PySide6.QtGui import QImageReader
    before = os.stat(path)
    reader = QImageReader(path)
    size = reader.size()
    if not size.isValid():
        raise ValueError('Could not read this image.')
    if max(size.width(), size.height()) > IMAGE_DISPLAY_SIDE:
        reader.setScaledSize(size.scaled(IMAGE_DISPLAY_SIDE, IMAGE_DISPLAY_SIDE, Qt.AspectRatioMode.KeepAspectRatio))
    image = reader.read()
    if image.isNull():
        raise ValueError(reader.errorString())
    fingerprint = file_sha256(path)
    after = os.stat(path)
    if (before.st_mtime_ns, before.st_size) != (after.st_mtime_ns, after.st_size):
        raise ValueError('The image changed during decoding. Reopen it.')
    return {'image': image, 'width': size.width(), 'height': size.height(), 'source_hash': fingerprint}
