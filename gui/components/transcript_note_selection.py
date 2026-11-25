"""Keep transcript note selection attached to its rendered segment, not playback."""
import html

from PySide6.QtGui import QTextCursor, QTextBlockUserData


class SegmentDisplayData(QTextBlockUserData):
    def __init__(self, prefix_length):
        super().__init__()
        self.prefix_length = prefix_length


def mark_segments(viewer):
    block = viewer.transcript_view.document().begin()
    for index, segment in enumerate(viewer.segments):
        if not block.isValid():
            break
        prefix = f"{viewer._fmt_seconds(float(segment.get('start', 0)))} " if viewer._timestamps_visible else ''
        block.setUserState(index)
        block.setUserData(SegmentDisplayData(len(prefix)))
        block = block.next()


def selected_segments(viewer):
    cursor = viewer.transcript_view.textCursor()
    if not cursor.hasSelection():
        segment = viewer._current_segment()
        if segment is None:
            raise ValueError('No transcript text is available at this timestamp.')
        return str(segment.get('text') or '').strip(), [viewer.segments.index(segment)]
    start, end = cursor.selectionStart(), cursor.selectionEnd()
    block = viewer.transcript_view.document().findBlock(start)
    indices, parts = [], []
    while block.isValid() and block.position() < end:
        data, index = block.userData(), block.userState()
        if not isinstance(data, SegmentDisplayData) or not 0 <= index < len(viewer.segments):
            raise ValueError('The transcript display changed. Select the passage again.')
        left = max(start, block.position() + data.prefix_length)
        right = min(end, block.position() + block.length() - 1)
        if right > left:
            piece = QTextCursor(viewer.transcript_view.document())
            piece.setPosition(left)
            piece.setPosition(right, QTextCursor.MoveMode.KeepAnchor)
            text = piece.selectedText().strip()
            if text:
                parts.append(text)
                indices.append(index)
        block = block.next()
    if not parts:
        raise ValueError('Select transcript text, rather than a timestamp label.')
    return ' '.join(parts), indices


def render_transcript(viewer):
    if not hasattr(viewer, "transcript_view"):
        return
    if not viewer.segments:
        viewer.transcript_view.setHtml("<i>No transcript is available yet.</i>")
        return
    parts = []
    for idx, seg in enumerate(viewer.segments):
        text = html.escape(str(seg.get("text", "")).strip())
        stamp = f"{viewer._fmt_seconds(float(seg.get('start', 0)))} " if viewer._timestamps_visible else ""
        if idx == viewer._active_segment_index:
            parts.append(f"<p style='background:#31424a; margin:2px 0; padding:4px;'><b>{html.escape(stamp)}</b>{text}</p>")
        else:
            parts.append(f"<p style='margin:2px 0; padding:4px;'><span style='color:#9fb0b8;'>{html.escape(stamp)}</span>{text}</p>")
    scrollbar = viewer.transcript_view.verticalScrollBar()
    old_value = scrollbar.value()
    viewer.transcript_view.setHtml("".join(parts))
    mark_segments(viewer)
    if viewer._active_segment_index >= 0:
        cursor = viewer.transcript_view.document().find(str(viewer.segments[viewer._active_segment_index].get("text", "")).strip()[:40])
        if not cursor.isNull():
            viewer.transcript_view.setTextCursor(cursor)
            viewer.transcript_view.ensureCursorVisible()
    elif old_value:
        scrollbar.setValue(old_value)

