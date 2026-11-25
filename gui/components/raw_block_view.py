"""Literal extracted text with reversible raw-codepoint/Qt UTF-16 coordinates."""
from PySide6.QtGui import QTextCursor, QTextCharFormat, QTextBlockFormat, QFont, QColor, QPalette
from PySide6.QtWidgets import QTextEdit

from gui.components.readable_text_renderer import ReadableTextRenderer


def display_text(raw):
    """Fold CRLF for Qt, keeping boundaries in the original block text."""
    output, boundaries, units, index = [], {0: 0}, 0, 0
    while index < len(raw):
        char = raw[index]
        consumed = 2 if char == '\r' and raw[index:index+2] == '\r\n' else 1
        char = '\n' if char in {'\r', '\u2029', '\u2028'} else char
        output.append(char)
        units += len(char.encode('utf-16-le')) // 2
        index += consumed
        boundaries[index] = units
    return ''.join(output), boundaries


class RawBlockView:
    def __init__(self, browser):
        self.browser, self.ranges = browser, []

    def render(self, blocks, settings, *, prepared=None, raw_offsets=None):
        self.ranges = []
        document = self.browser.document()
        document.clear()
        styled = ReadableTextRenderer.render_text('', settings)
        document.setDefaultStyleSheet(styled.defaultStyleSheet())
        document.setDefaultFont(styled.defaultFont())
        styled.deleteLater()
        text_color, bg_color = ReadableTextRenderer.colors(settings)
        palette = self.browser.palette()
        palette.setColor(QPalette.ColorRole.Text, QColor(text_color))
        palette.setColor(QPalette.ColorRole.Base, QColor(bg_color))
        self.browser.setPalette(palette)
        cursor = QTextCursor(document)
        for index, block in enumerate(blocks):
            if index:
                cursor.insertBlock()
            paragraph = QTextBlockFormat()
            paragraph.setBottomMargin(settings.paragraph_spacing * settings.font_size_base)
            paragraph.setLineHeight(settings.line_height * 100, QTextBlockFormat.LineHeightTypes.ProportionalHeight.value)
            cursor.setBlockFormat(paragraph)
            formatting = QTextCharFormat()
            formatting.setFontPointSize(settings.font_size_base + max(0, 7-block.heading_level) if block.heading_level else settings.font_size_base)
            formatting.setFontWeight(QFont.Weight.Bold if block.heading_level else QFont.Weight.Normal)
            text, offsets = prepared[index] if prepared is not None else display_text(block.exact_text)
            start = cursor.position()
            cursor.insertText(text, formatting)
            self.ranges.append({'block': block, 'start': start, 'offsets': offsets, 'end': cursor.position(),
                                'raw_offset': raw_offsets[index] if raw_offsets else 0})
        cursor.movePosition(QTextCursor.MoveOperation.Start)
        self.browser.setTextCursor(cursor)

    def selection(self):
        cursor = self.browser.textCursor()
        if not cursor.hasSelection():
            return None
        spans, quotes = [], []
        for item in self.ranges:
            start, end = max(cursor.selectionStart(), item['start']), min(cursor.selectionEnd(), item['end'])
            if start >= end:
                continue
            inverse = {position: raw for raw, position in item['offsets'].items()}
            if start-item['start'] not in inverse or end-item['start'] not in inverse:
                return None  # A cursor inside a surrogate pair is not a raw boundary.
            a, b = inverse[start-item['start']], inverse[end-item['start']]
            quote = item['block'].exact_text[a:b]
            a, b = a+item['raw_offset'], b+item['raw_offset']
            if spans and spans[-1]['block_id'] == item['block'].block_id and spans[-1]['char_end'] == a:
                spans[-1]['char_end'] = b
                quotes[-1] += quote
            else:
                spans.append({'block_id': item['block'].block_id, 'char_start': a, 'char_end': b})
                quotes.append(quote)
        return {'block_spans': spans, 'quote': '\n'.join(quotes)} if spans else None

    def show_spans(self, spans):
        selections = []
        for item, span, start, end in self.visible_spans(spans):
            selected = QTextEdit.ExtraSelection()
            selected.cursor = QTextCursor(self.browser.document())
            selected.cursor.setPosition(item['start'] + item['offsets'][start])
            selected.cursor.setPosition(item['start'] + item['offsets'][end], QTextCursor.MoveMode.KeepAnchor)
            selected.format.setBackground(self.browser.palette().highlight())
            selected.format.setForeground(self.browser.palette().highlightedText())
            selections.append(selected)
        self.browser.setExtraSelections(selections)
        if selections:
            self.browser.setTextCursor(selections[0].cursor)
            self.browser.ensureCursorVisible()
        return bool(selections)

    def visible_spans(self, spans):
        for item in self.ranges:
            offset = item['raw_offset']
            for span in spans:
                if span.get('block_id') != item['block'].block_id:
                    continue
                a, b = span.get('char_start'), span.get('char_end')
                if type(a) is not int or type(b) is not int or a >= b:
                    continue
                start, end = max(a, offset)-offset, min(b, offset+len(item['block'].exact_text))-offset
                if start not in item['offsets'] or end not in item['offsets'] or start >= end:
                    continue
                if item['block'].exact_text[start:end] == str(span.get('quote') or '')[start+offset-a:end+offset-a]:
                    yield item, span, start, end
