"""Simplified connection review: the sentence with both entities in bold, then save as a typed
connection or reject. Used for one connection, or to cycle through all under optional filters."""
from __future__ import annotations

import html

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QComboBox, QFrame, QHBoxLayout, QLabel, QPushButton, QVBoxLayout, QWidget

from core.models.entity_categories import MEASURE_TYPES, TYPE_LABELS
from gui.components.artifact_actions import bind_artifact_action
from gui.components.artifact_source_navigator import ArtifactSourceNavigator
from gui.components.artifact_tasks import ArtifactTaskRunner
from gui.components.intelligence_filter_bar import ChoiceMenu

MACHINE_LABELS = {"co_occurs_with": "Named together", "affiliation_candidate": "Suggested affiliation",
                  "same_as_candidate": "Possibly the same entity"}
DEFAULT_TYPE = "related_to"
SUGGESTED_TYPE = {"affiliation_candidate": "member_of"}   # the type a candidate proposes
MOUNT = "toolbar:intelligence:connections"


def sentence_html(text, spans):
    """Escaped sentence with the entity spans in bold."""
    out, last = [], 0
    for start, end in spans:
        out.append(html.escape(text[last:start]))
        out.append("<b>" + html.escape(text[start:end]) + "</b>")
        last = end
    out.append(html.escape(text[last:]))
    return "".join(out)


class ConnectionReviewPanel(QWidget):
    decided = Signal(str)

    def __init__(self, app_context, parent=None, cycle=True):
        super().__init__(parent)
        self.ctx, self.cycle = app_context, cycle
        self.card, self.source_id, self.skipped, self.last = None, "", [], None
        self._closing = False
        layout = QVBoxLayout(self)
        self._filters(layout)
        self.progress = QLabel("", self)
        layout.addWidget(self.progress)
        self.names = QLabel("", self)
        self.names.setTextFormat(Qt.TextFormat.RichText)
        layout.addWidget(self.names)
        self.sentence = QLabel("Loading…", self)
        self.sentence.setObjectName("connectionSentence")
        self.sentence.setTextFormat(Qt.TextFormat.RichText)
        self.sentence.setWordWrap(True)
        self.sentence.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.sentence.setFrameShape(QFrame.Shape.StyledPanel)
        self.sentence.setMargin(10)
        self.sentence.setAccessibleName("Sentence the connection is based on; the two entities are in bold")
        font = self.sentence.font()
        font.setPointSizeF(font.pointSizeF() * 1.15)
        self.sentence.setFont(font)
        layout.addWidget(self.sentence)
        self.why = QLabel("", self)
        self.why.setWordWrap(True)
        self.why.setEnabled(False)   # muted
        layout.addWidget(self.why)
        self._actions(layout)
        layout.addStretch(1)
        self.runner = ArtifactTaskRunner(app_context, self)
        self.runner.completed.connect(self._completed)
        self.runner.failed.connect(self._failed)
        self.runner.busy_changed.connect(self._busy)
        self.navigator = ArtifactSourceNavigator(app_context, self.progress.setText, self)
        self._busy(False)

    def _filters(self, layout):
        row = QHBoxLayout()
        self.edge_types = ChoiceMenu("edge_type", self, labels=self._edge_labels())
        self.edge_types.set_counts({key: None for key in self._edge_labels()})
        self.edge_types.setText("Connections: all")
        self.edge_types.setToolTip("Only show connections of the ticked kinds. Nothing ticked shows all.")
        self.entity_types = ChoiceMenu("entity_type", self, offer_measures=False)
        self.entity_types.set_counts({kind: None for kind in TYPE_LABELS if kind not in MEASURE_TYPES})
        self.entity_types.setToolTip("Only show connections involving an entity of a ticked type.")
        for menu in (self.edge_types, self.entity_types):
            menu.changed.connect(self.restart)
            row.addWidget(menu)
        row.addStretch(1)
        self.filter_row = QWidget(self)
        self.filter_row.setLayout(row)
        self.filter_row.setVisible(self.cycle)
        layout.addWidget(self.filter_row)

    @staticmethod
    def _edge_labels():
        from core.services.intelligence.connection_review import connection_types
        return {**MACHINE_LABELS, **connection_types()}

    def _actions(self, layout):
        row = QHBoxLayout()
        row.addWidget(QLabel("Save as:", self))
        self.kind = QComboBox(self)
        self.kind.setAccessibleName("Connection type to save")
        self.kind.setToolTip("What the sentence says connects them. Saving records this connection as confirmed.")
        row.addWidget(self.kind, 1)
        self.buttons = {}
        for key, label in (("save", "Save connection"), ("reject", "Reject"), ("skip", "Skip"),
                           ("undo", "Undo"), ("jump", "Go to sentence")):
            if key == "skip" and not self.cycle:
                continue
            button = QPushButton(label, self)
            button.clicked.connect(getattr(self, key))
            bind_artifact_action(self.ctx, self, "intelligence.connection." + key, label, getattr(self, key), MOUNT, button)
            self.buttons[key] = button
            row.addWidget(button)
        layout.addLayout(row)

    # -- loading ---------------------------------------------------------
    def start_cycle(self, source_id=""):
        self.source_id, self.skipped = source_id or "", []
        self.restart()

    def show_connection(self, assertion_id):
        self.runner.start("connection_card", assertion_id=assertion_id)

    def restart(self, *_):
        self.skipped = []
        self._next()

    def _next(self):
        if self.cycle and not self._closing:
            self.runner.start("next_connection", edge_types=sorted(self.edge_types.selected),
                              entity_types=sorted(self.entity_types.selected), source_id=self.source_id,
                              excluded=list(self.skipped))

    def _completed(self, operation):
        result, self.runner.result = self.runner.result or {}, None
        if self._closing:
            return
        if operation in {"next_connection", "connection_card"}:
            if "remaining" in result:
                self.progress.setText(f"{result['remaining']:,} connections to review"
                                      + (f" · {len(self.skipped)} skipped" if self.skipped else ""))
            self._show(result.get("card"))
        elif operation in {"save_connection", "reject_connection"}:
            self.last = (self.card["assertion_id"], result.get("receipt"))
            self.decided.emit(operation)
            if self.cycle:
                self._next()
            else:
                self.progress.setText("Saved as a confirmed connection." if operation == "save_connection" else "Rejected.")
                self.show_connection(self.card["assertion_id"])
        elif operation == "undo_connection":
            identity, self.last = self.last[0], None
            self.decided.emit("undo")
            self.progress.setText("Undone.")
            self.show_connection(identity)
        self._busy(False)

    def _show(self, card):
        self.card = card
        if not card:
            self.names.setText("")
            self.sentence.setText("Nothing left to review here. Change the filters or revisit skipped connections.")
            self.why.setText("")
            return
        first, second = card["entities"]
        self.names.setText(f"<b>{html.escape(first['name'])}</b> ({html.escape(first['type'])}) — "
                           f"<b>{html.escape(second['name'])}</b> ({html.escape(second['type'])})"
                           f" · now: {html.escape(MACHINE_LABELS.get(card['edge_type'], card['edge_type'].replace('_', ' ')))}")
        self.sentence.setText(sentence_html(card["sentence"], card["spans"]))
        page = card.get("page")
        self.why.setText(card["explanation"] + (f"  (page {page + 1})" if isinstance(page, int) else ""))
        self.kind.clear()
        for key, label in card["types"].items():
            self.kind.addItem(label, key)
        index = self.kind.findData(SUGGESTED_TYPE.get(card["edge_type"], card["edge_type"]))
        self.kind.setCurrentIndex(max(0, index if index >= 0 else self.kind.findData(DEFAULT_TYPE)))
        state = card.get("review_state")
        if state != "unreviewed":
            self.progress.setText(f"Already reviewed ({state}). Undo to change it.")

    # -- actions -----------------------------------------------------------
    def _open(self):
        return bool(self.card) and self.card.get("review_state") == "unreviewed" and not self.runner.busy

    def save(self):
        if self._open():
            self.runner.start("save_connection", assertion_id=self.card["assertion_id"], edge_type=self.kind.currentData())

    def reject(self):
        if self._open():
            self.runner.start("reject_connection", assertion_id=self.card["assertion_id"])

    def skip(self):
        if self.cycle and self.card and not self.runner.busy:
            self.skipped.append(self.card["assertion_id"])
            self._next()

    def undo(self):
        if self.last and self.last[1] and not self.runner.busy:
            self.runner.start("undo_connection", assertion_id=self.last[0], receipt=self.last[1])

    def jump(self):
        if self.card and self.card.get("sentence_id"):
            self.navigator.navigate(self.card["sentence_id"])

    def _failed(self, error):
        self.progress.setText(str(error))
        self._busy(False)

    def _busy(self, busy):
        busy = busy or (hasattr(self, "runner") and self.runner.busy)
        open_card = bool(self.card) and self.card.get("review_state") == "unreviewed"
        for key, button in self.buttons.items():
            enabled = not busy and (open_card if key in {"save", "reject"} else bool(self.last and self.last[1]) if key == "undo"
                                    else bool(self.card))
            button.setEnabled(enabled)
        self.kind.setEnabled(not busy and open_card)

    def shutdown(self):
        self._closing = True
        self.runner.shutdown()
        self.navigator.shutdown()
