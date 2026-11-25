"""User-facing inspection of the exact ranked context behind an LLM response."""
from __future__ import annotations

import html

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QComboBox, QDialog, QFrame, QHBoxLayout, QLabel, QPushButton,
    QScrollArea, QTextBrowser, QVBoxLayout, QWidget,
)


class RagContextViewerDialog(QDialog):
    def __init__(self, audit: dict, theme=None, app_context=None, parent=None):
        super().__init__(parent)
        self.audit = audit or {}
        self.theme = theme or {}
        self.app_context = app_context
        self.setWindowTitle("Full RAG Context")
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, True)
        self.resize(920, 720)

        layout = QVBoxLayout(self)
        layout.addWidget(QLabel("<b>Ranked context for this response</b>"))
        self.summary = QLabel()
        self.summary.setWordWrap(True)
        layout.addWidget(self.summary)

        controls = QHBoxLayout()
        controls.addWidget(QLabel("Show:"))
        self.filter_combo = QComboBox()
        self.filter_combo.addItem("All ranked chunks", "all")
        self.filter_combo.addItem("Sent to the model", "sent")
        self.filter_combo.addItem("Next best — not sent", "not_sent")
        self.filter_combo.currentIndexChanged.connect(self._rebuild)
        controls.addWidget(self.filter_combo)
        controls.addStretch()
        layout.addLayout(controls)

        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.content = QWidget()
        self.cards = QVBoxLayout(self.content)
        self.cards.setContentsMargins(4, 4, 4, 4)
        self.cards.setSpacing(8)
        self.scroll.setWidget(self.content)
        layout.addWidget(self.scroll, 1)

        close_button = QPushButton("Close")
        close_button.clicked.connect(self.close)
        footer = QHBoxLayout()
        footer.addStretch()
        footer.addWidget(close_button)
        layout.addLayout(footer)
        self._apply_theme()
        self._rebuild()

    def set_audit(self, audit: dict):
        self.audit = audit or {}
        self._rebuild()

    def _rebuild(self):
        while self.cards.count():
            item = self.cards.takeAt(0)
            widget = item.widget()
            if widget:
                widget.deleteLater()
        candidates = list(self.audit.get("candidates") or [])
        sent = sum(bool(item.get("received_by_model")) for item in candidates)
        selected = sum(len(item.get("selected_quotes") or []) for item in candidates)
        self.summary.setText(
            f"The model received {sent} of {len(candidates)} displayed ranked chunks. "
            "The remainder are the next-highest retrieval results. "
            f"{selected} cited quote(s) are highlighted in their full raw chunks."
        )
        mode = self.filter_combo.currentData() or "all"
        visible = [
            item for item in candidates
            if mode == "all"
            or (mode == "sent" and item.get("received_by_model"))
            or (mode == "not_sent" and not item.get("received_by_model"))
        ]
        if not visible:
            label = QLabel("No chunks match this filter.")
            label.setAlignment(Qt.AlignmentFlag.AlignCenter)
            self.cards.addWidget(label)
        for candidate in visible:
            self.cards.addWidget(_RagContextCard(
                candidate, self.theme, self.app_context, parent=self.content
            ))
        self.cards.addStretch()

    def _apply_theme(self):
        bg = self.theme.get("bg_main", "#202124")
        panel = self.theme.get("bg_panel", "#292a2d")
        text = self.theme.get("text_main", "#f1f3f4")
        muted = self.theme.get("text_muted", "#aeb0b4")
        border = self.theme.get("border", "#4a4d52")
        accent = self.theme.get("accent", "#b366ff")
        self.setStyleSheet(
            f"QDialog {{ background: {bg}; color: {text}; }}"
            f"QLabel {{ color: {text}; }}"
            f"QScrollArea {{ background: {bg}; border: 1px solid {border}; }}"
            f"QComboBox {{ background: {panel}; color: {text}; border: 1px solid {border}; padding: 5px; }}"
            f"QPushButton {{ background: {panel}; color: {text}; border: 1px solid {border}; "
            f"padding: 5px 10px; border-radius: 4px; }}"
            f"QPushButton:hover {{ border-color: {accent}; }}"
        )
        self.summary.setStyleSheet(f"color: {muted};")


class _RagContextCard(QFrame):
    def __init__(self, candidate, theme, app_context, parent=None):
        super().__init__(parent)
        self.candidate = candidate
        self.theme = theme or {}
        self.app_context = app_context
        self.setFrameShape(QFrame.Shape.StyledPanel)
        layout = QVBoxLayout(self)

        received = bool(candidate.get("received_by_model"))
        badge = "SENT TO MODEL" if received else "NEXT BEST — NOT SENT"
        badge_color = (
            self.theme.get("success", "#55b979")
            if received else self.theme.get("text_muted", "#aeb0b4")
        )
        header = QHBoxLayout()
        rank = int(candidate.get("rank") or 0)
        source = candidate.get("doc_name") or candidate.get("source_id") or "Unknown source"
        header.addWidget(QLabel(f"<b>#{rank} · {html.escape(str(source))}</b>"))
        header.addStretch()
        status = QLabel(badge)
        status.setStyleSheet(
            f"color: {badge_color}; border: 1px solid {badge_color}; "
            "padding: 2px 6px; border-radius: 3px; font-size: 10px; font-weight: bold;"
        )
        header.addWidget(status)
        layout.addLayout(header)

        locator = candidate.get("source_locator") or {}
        locator_text = ", ".join(
            f"{key}: {value}" for key, value in locator.items()
            if value not in (None, "")
        )
        score = float(candidate.get("score") or 0.0)
        reason = str(candidate.get("reason") or "")
        detail_parts = [f"score {score:.4f}"]
        if locator_text:
            detail_parts.append(locator_text)
        if reason:
            detail_parts.append(reason)
        details = QLabel(" · ".join(detail_parts))
        details.setWordWrap(True)
        details.setStyleSheet(
            f"color: {self.theme.get('text_muted', '#aeb0b4')}; font-size: 11px;"
        )
        layout.addWidget(details)

        browser = QTextBrowser()
        browser.setHtml(_highlight_html(
            str(candidate.get("text") or ""),
            list(candidate.get("selected_quotes") or []),
            self.theme,
        ))
        browser.setMinimumHeight(130)
        browser.setMaximumHeight(260)
        browser.setStyleSheet(
            f"background: {self.theme.get('bg_input', '#1f2023')}; "
            f"color: {self.theme.get('text_main', '#f1f3f4')}; "
            f"border: 1px solid {self.theme.get('border', '#4a4d52')}; padding: 6px;"
        )
        layout.addWidget(browser)

        jump = QPushButton("Jump to source")
        jump.clicked.connect(self._jump)
        footer = QHBoxLayout()
        footer.addWidget(jump)
        footer.addStretch()
        layout.addLayout(footer)
        self.reference_widget = None
        if candidate.get("artifact_id") and self.app_context is not None:
            from gui.components.artifact_reference import ArtifactReferenceWidget
            self.reference_widget = ArtifactReferenceWidget(self.app_context, candidate, self, allow_note=True)
            layout.addWidget(self.reference_widget)
            jump.hide()

    def _jump(self):
        if self.reference_widget is not None:
            self.reference_widget.jump()
            return
        viewer = getattr(self.app_context, "viewer", None)
        if not viewer:
            return
        candidate = self.candidate
        locator = candidate.get("source_locator") or {}
        if candidate.get("source_type") in {"video", "audio"} and hasattr(viewer, "jump_to_video"):
            viewer.jump_to_video(
                source_id=candidate.get("source_id"),
                timestamp=locator.get("start", 0),
            )
        elif hasattr(viewer, "jump_to_source"):
            quote = (candidate.get("selected_quotes") or [candidate.get("text", "")])[0]
            viewer.jump_to_source(candidate.get("doc_name", ""), quote)


def _highlight_html(text: str, quotes: list[str], theme=None) -> str:
    """Escape raw text and mark all exact selected-quote spans."""
    spans = []
    lowered = text.casefold()
    for quote in quotes:
        quote = str(quote or "").strip()
        if not quote:
            continue
        start = 0
        needle = quote.casefold()
        while True:
            index = lowered.find(needle, start)
            if index < 0:
                break
            spans.append((index, index + len(quote)))
            start = index + max(1, len(quote))
    spans.sort()
    merged = []
    for start, end in spans:
        if merged and start <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(end, merged[-1][1]))
        else:
            merged.append((start, end))
    pieces = []
    cursor = 0
    highlight = (theme or {}).get("highlight", "#ffd54f")
    for start, end in merged:
        pieces.append(html.escape(text[cursor:start]))
        pieces.append(
            f'<mark style="background-color:{highlight};color:#111;">'
            f"{html.escape(text[start:end])}</mark>"
        )
        cursor = end
    pieces.append(html.escape(text[cursor:]))
    return (
        "<div style='white-space:pre-wrap; line-height:1.35;'>"
        + "".join(pieces)
        + "</div>"
    )
