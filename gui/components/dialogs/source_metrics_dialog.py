"""
gui/components/dialogs/source_metrics_dialog.py

Displays the source quality score and scoring ledger for a PDF.

Designed to be opened modelessly via DialogManager.show_instance() so the
user can keep interacting with the rest of the application.  The dialog
listens on the EventBus and updates itself live when a re-score completes.
"""
from __future__ import annotations

import os
from typing import Dict, List, Optional

from PySide6.QtCore import Qt, QRectF, QSize
from PySide6.QtGui import QBrush, QColor, QFont, QPainter, QPen
from PySide6.QtWidgets import (
    QDialog, QDialogButtonBox, QFrame, QGroupBox, QHBoxLayout, QLabel,
    QLineEdit, QProgressBar, QPushButton, QScrollArea, QSizePolicy,
    QTableWidget, QTableWidgetItem, QTextEdit, QVBoxLayout, QWidget,
)


# ---------------------------------------------------------------------------
# Score badge
# ---------------------------------------------------------------------------

class _ScoreBadge(QWidget):
    def __init__(self, score: int, parent=None) -> None:
        super().__init__(parent)
        self._score = max(0, min(100, score))
        self.setFixedSize(QSize(90, 90))

    def set_score(self, score: int) -> None:
        self._score = max(0, min(100, score))
        self.update()

    @property
    def _color(self) -> QColor:
        if self._score >= 65:
            return QColor("#4CAF50")
        if self._score >= 40:
            return QColor("#FF9800")
        return QColor("#F44336")

    def paintEvent(self, _event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        r = QRectF(4, 4, 82, 82)
        painter.setBrush(QBrush(self._color))
        painter.setPen(QPen(self._color.darker(120), 2))
        painter.drawEllipse(r)
        painter.setPen(QColor("white"))
        painter.setFont(QFont("Arial", 22, QFont.Weight.Bold))
        painter.drawText(r, Qt.AlignmentFlag.AlignCenter, str(self._score))


# ---------------------------------------------------------------------------
# Main dialog
# ---------------------------------------------------------------------------

class SourceMetricsDialog(QDialog):
    """
    Modeless source quality report.  Call via DialogManager.show_instance().

    The dialog subscribes to source_eval_state_changed so it updates itself
    automatically if the user triggers a re-score (e.g. after editing the DOI
    or journal name in the Update Metadata section or in the citation dock).
    """

    def __init__(
        self,
        score: int,
        ledger: List[Dict],
        pdf_path: str,
        is_retracted: bool = False,
        needs_manual_review: bool = False,
        theme: Optional[Dict] = None,
        parent=None,
    ) -> None:
        # WindowMaximizeButtonHint signals DialogManager to use resizable flags
        super().__init__(
            parent,
            Qt.WindowType.Dialog | Qt.WindowType.WindowMaximizeButtonHint,
        )
        self._score = score
        self._ledger = ledger
        self._pdf_path = pdf_path
        self._is_retracted = is_retracted
        self._needs_review = needs_manual_review
        self._theme = theme or {}

        self.setWindowTitle("Source Quality Metrics")
        self.setMinimumSize(700, 650)
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)

        self._build_ui()
        self._apply_theme()
        self._connect_bus()

    # ------------------------------------------------------------------
    # Bus lifecycle
    # ------------------------------------------------------------------

    def _connect_bus(self) -> None:
        from core.events.event_bus import EventBus
        self._bus = EventBus.get_instance()
        self._bus.source_eval_state_changed.connect(self._on_eval_event)
        self.destroyed.connect(self._disconnect_bus)

    def _disconnect_bus(self) -> None:
        try:
            self._bus.source_eval_state_changed.disconnect(self._on_eval_event)
        except Exception:
            pass

    def _on_eval_event(self, event, payload) -> None:
        from core.events.domains.evaluation_events import SourceEvalEvent
        if event == SourceEvalEvent.EVALUATION_STARTED:
            if getattr(payload, "pdf_path", "") == self._pdf_path:
                self._rescore_btn.setEnabled(False)
                self._rescore_btn.setText("Scoring…")
            return
        if event != SourceEvalEvent.EVALUATION_COMPLETE:
            return
        if getattr(payload, "pdf_path", "") != self._pdf_path:
            return
        self._rescore_btn.setEnabled(True)
        self._rescore_btn.setText("Re-score")
        if payload.score is not None:
            self.update_display(
                payload.score,
                payload.ledger,
                payload.is_retracted,
                payload.needs_manual_review,
            )

    # ------------------------------------------------------------------
    # UI construction
    # ------------------------------------------------------------------

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(16, 16, 16, 12)
        root.setSpacing(8)

        # ── Title + filename ──────────────────────────────────────────────
        title_lbl = QLabel("<b>Source Quality Report</b>")
        title_lbl.setObjectName("DialogTitle")
        root.addWidget(title_lbl)
        fname_lbl = QLabel(os.path.basename(self._pdf_path))
        fname_lbl.setObjectName("FilenameLabel")
        fname_lbl.setWordWrap(True)
        root.addWidget(fname_lbl)
        root.addWidget(_hsep())

        # ── Alert banners ─────────────────────────────────────────────────
        self._banner_container = QVBoxLayout()
        self._banner_container.setSpacing(4)
        root.addLayout(self._banner_container)
        self._rebuild_banners()

        # ── Score row ─────────────────────────────────────────────────────
        score_row = QHBoxLayout()
        score_row.setSpacing(16)
        self._badge = _ScoreBadge(self._score)
        score_row.addWidget(self._badge)

        score_col = QVBoxLayout()
        score_col.setSpacing(4)
        self._score_lbl = QLabel(f"<b>Overall Score: {self._score} / 100</b>")
        self._score_lbl.setObjectName("ScoreLabel")
        score_col.addWidget(self._score_lbl)
        self._bar = QProgressBar()
        self._bar.setRange(0, 100)
        self._bar.setValue(self._score)
        self._bar.setTextVisible(False)
        self._bar.setFixedHeight(12)
        self._bar.setObjectName("ScoreBar")
        self._update_bar_color()
        score_col.addWidget(self._bar)
        self._grade_lbl = QLabel()
        self._update_grade_label()
        score_col.addWidget(self._grade_lbl)
        score_col.addStretch()
        score_row.addLayout(score_col)
        score_row.addStretch()
        root.addLayout(score_row)
        root.addWidget(_hsep())

        # ── Score breakdown table (fills all available vertical space) ────
        root.addWidget(QLabel("<b>Score Breakdown</b>  "
                              "<i style='font-size:10px;'>— click a row to see full explanation</i>"))
        self._table = self._build_table()
        self._table.setMinimumHeight(160)
        root.addWidget(self._table, 1)   # stretch=1 → expands when dialog is resized

        # ── Detail panel (fixed height, always visible) ───────────────────
        self._detail = QTextEdit()
        self._detail.setReadOnly(True)
        self._detail.setFixedHeight(62)
        self._detail.setPlaceholderText("Click a row above to view the full explanation…")
        self._detail.setObjectName("DetailPanel")
        root.addWidget(self._detail)

        root.addWidget(_hsep())

        # ── Update Metadata + Databases + Disclaimer in a compact footer ──
        footer = QHBoxLayout()
        footer.setSpacing(12)

        # Left: Update Metadata
        meta_box = QGroupBox("Update Metadata")
        meta_box.setObjectName("MetaGroup")
        ml = QVBoxLayout(meta_box)
        ml.setSpacing(4)
        ml.setContentsMargins(8, 8, 8, 8)

        doi_row = QHBoxLayout()
        doi_row.addWidget(QLabel("DOI:"))
        self._doi_edit = QLineEdit()
        self._doi_edit.setPlaceholderText("10.xxxx/…")
        doi_row.addWidget(self._doi_edit, 1)
        ml.addLayout(doi_row)

        journal_row = QHBoxLayout()
        journal_row.addWidget(QLabel("Journal:"))
        self._journal_edit = QLineEdit()
        self._journal_edit.setPlaceholderText("Nature, PLOS ONE…")
        journal_row.addWidget(self._journal_edit, 1)
        ml.addLayout(journal_row)

        # Pre-fill from ledger if we can find the values
        self._prefill_metadata_fields()

        self._rescore_btn = QPushButton("Re-score")
        self._rescore_btn.clicked.connect(self._on_rescore_clicked)
        ml.addWidget(self._rescore_btn, 0, Qt.AlignmentFlag.AlignRight)
        footer.addWidget(meta_box, 2)

        # Right: Databases + Disclaimer
        right_col = QVBoxLayout()
        right_col.setSpacing(4)
        self._db_lbl = QLabel()
        self._db_lbl.setObjectName("DbLabel")
        self._db_lbl.setWordWrap(True)
        self._rebuild_db_label()
        right_col.addWidget(self._db_lbl)
        disclaimer = QLabel(
            "<i>⚠️ Heuristic estimate only — not a definitive quality judgment. "
            "Always evaluate sources critically.</i>"
        )
        disclaimer.setWordWrap(True)
        disclaimer.setObjectName("DisclaimerLabel")
        right_col.addWidget(disclaimer)
        right_col.addStretch()
        footer.addLayout(right_col, 3)

        root.addLayout(footer)

        # ── Close button ──────────────────────────────────────────────────
        btns = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        btns.rejected.connect(self.reject)
        root.addWidget(btns)

    # ------------------------------------------------------------------
    # Metadata field prefill
    # ------------------------------------------------------------------

    def _prefill_metadata_fields(self) -> None:
        """Populate DOI/Journal inputs from the current ledger reasoning text."""
        doi = ""
        journal = ""
        for r in self._ledger:
            mid = r.get("metric_id", "")
            reasoning = r.get("reasoning", "")
            if mid == "doi" and "DOI found: " in reasoning:
                doi = reasoning.split("DOI found: ")[-1].split()[0].rstrip(".,;)")
            if mid == "venue" and r.get("points", 0) > 0:
                # "SCImago SJR quartile Q1 journal: Nature" → extract after last ": "
                if ": " in reasoning:
                    journal = reasoning.rsplit(": ", 1)[-1].strip()
        self._doi_edit.setText(doi)
        self._journal_edit.setText(journal)

    # ------------------------------------------------------------------
    # Re-score action
    # ------------------------------------------------------------------

    def _on_rescore_clicked(self) -> None:
        from core.events.domains.evaluation_events import SourceEvalIntent, SourceEvalPayload
        doi = self._doi_edit.text().strip() or None
        journal = self._journal_edit.text().strip() or None
        self._bus.source_eval_action_requested.emit(
            SourceEvalIntent.RUN_EVALUATION,
            SourceEvalPayload(
                pdf_path=self._pdf_path,
                doi=doi,
                journal=journal,
            ),
        )

    # ------------------------------------------------------------------
    # Table
    # ------------------------------------------------------------------

    def _build_table(self) -> QTableWidget:
        cols = ["Metric", "Points", "Max", "Reasoning", "Database"]
        tbl = QTableWidget(len(self._ledger), len(cols))
        tbl.setHorizontalHeaderLabels(cols)
        tbl.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        tbl.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        tbl.setAlternatingRowColors(True)
        tbl.verticalHeader().setVisible(False)
        tbl.horizontalHeader().setStretchLastSection(False)
        tbl.setWordWrap(True)

        for row, entry in enumerate(self._ledger):
            pts = entry.get("points", 0)
            pts_text = f"+{pts}" if pts > 0 else str(pts)
            pts_color = "#4CAF50" if pts > 0 else "#F44336" if pts < 0 else "#888888"
            cells = [
                entry.get("label", ""),
                pts_text,
                str(entry.get("max_points", 0)),
                entry.get("reasoning", ""),
                entry.get("db_used") or "—",
            ]
            for col, text in enumerate(cells):
                item = QTableWidgetItem(text)
                item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEditable)
                item.setToolTip(text)
                if col == 1:
                    item.setForeground(QColor(pts_color))
                    item.setFont(QFont("Arial", 10, QFont.Weight.Bold))
                tbl.setItem(row, col, item)

        tbl.resizeColumnsToContents()
        tbl.horizontalHeader().setSectionResizeMode(
            3, tbl.horizontalHeader().ResizeMode.Stretch
        )
        tbl.itemClicked.connect(self._on_cell_clicked)
        return tbl

    def _on_cell_clicked(self, item: QTableWidgetItem) -> None:
        row = item.row()
        entry = self._ledger[row] if row < len(self._ledger) else {}
        label = entry.get("label", "")
        pts = entry.get("points", 0)
        max_pts = entry.get("max_points", 0)
        reasoning = entry.get("reasoning", "")
        db = entry.get("db_used") or "—"
        sign = "+" if pts > 0 else ""
        full = (
            f"{label}  ({sign}{pts} / {max_pts})\n"
            f"Database: {db}\n\n"
            f"{reasoning}"
        )
        self._detail.setPlainText(full)

    # ------------------------------------------------------------------
    # Live update helpers
    # ------------------------------------------------------------------

    def update_display(
        self,
        score: int,
        ledger: List[Dict],
        is_retracted: bool = False,
        needs_manual_review: bool = False,
    ) -> None:
        """Replace the displayed data in place — no need to reopen the dialog."""
        self._score = score
        self._ledger = ledger
        self._is_retracted = is_retracted
        self._needs_review = needs_manual_review

        self._badge.set_score(score)
        self._score_lbl.setText(f"<b>Overall Score: {score} / 100</b>")
        self._bar.setValue(score)
        self._update_bar_color()
        self._update_grade_label()
        self._rebuild_banners()
        self._rebuild_db_label()
        self._prefill_metadata_fields()
        self._detail.clear()

        # Rebuild the table in place
        old_table = self._table
        new_table = self._build_table()
        layout: QVBoxLayout = self.layout()
        idx = layout.indexOf(old_table)
        if idx >= 0:
            layout.takeAt(idx)
            old_table.deleteLater()
            layout.insertWidget(idx, new_table, 1)
        self._table = new_table

    def _rebuild_banners(self) -> None:
        while self._banner_container.count():
            item = self._banner_container.takeAt(0)
            w = item.widget()
            if w:
                w.deleteLater()
        if self._is_retracted:
            self._banner_container.addWidget(
                _banner("⚠️  RETRACTED — DOI matched Retraction Watch. Do not cite.",
                        "#c0392b", "#ff6b6b")
            )
        if self._needs_review:
            self._banner_container.addWidget(
                _banner("⚠️  PREDATORY JOURNAL — Manual review strongly recommended.",
                        "#8a5e00", "#FFA500")
            )

    def _rebuild_db_label(self) -> None:
        dbs = sorted({r.get("db_used") for r in self._ledger if r.get("db_used")})
        if dbs:
            self._db_lbl.setText(
                "<b>Databases used:</b>  " + ",  ".join(dbs)
            )
            self._db_lbl.setVisible(True)
        else:
            self._db_lbl.setVisible(False)

    def _update_bar_color(self) -> None:
        color = (
            "#4CAF50" if self._score >= 65
            else "#FF9800" if self._score >= 40
            else "#F44336"
        )
        self._bar.setStyleSheet(
            f"QProgressBar::chunk {{ background-color: {color}; border-radius: 7px; }}"
            f"QProgressBar {{ border: 1px solid #555; border-radius: 7px; "
            f"background-color: #444; }}"
        )

    def _update_grade_label(self) -> None:
        text, color = _grade(self._score)
        self._grade_lbl.setText(
            f'<span style="color:{color}; font-weight:bold;">{text}</span>'
        )

    # ------------------------------------------------------------------
    # Theming
    # ------------------------------------------------------------------

    def _apply_theme(self) -> None:
        t = self._theme
        if not t:
            return
        self.setStyleSheet(f"""
            QDialog {{
                background-color: {t.get('bg_panel', '#2b2b2b')};
                color: {t.get('text_main', '#ffffff')};
            }}
            QLabel {{ color: {t.get('text_main', '#ffffff')}; }}
            QLabel#DialogTitle {{ font-size: 16px; font-weight: bold; }}
            QLabel#FilenameLabel {{
                color: {t.get('text_secondary', '#aaaaaa')}; font-size: 11px;
            }}
            QLabel#ScoreLabel {{ font-size: 15px; }}
            QLabel#DbLabel {{
                color: {t.get('text_secondary', '#cccccc')}; font-size: 12px;
            }}
            QLabel#DisclaimerLabel {{
                color: {t.get('text_secondary', '#aaaaaa')}; font-size: 11px;
            }}
            QGroupBox#MetaGroup {{
                border: 1px solid {t.get('border', '#555')};
                border-radius: 6px;
                margin-top: 8px;
                color: {t.get('text_main', '#ffffff')};
                font-weight: bold;
            }}
            QGroupBox#MetaGroup::title {{
                subcontrol-origin: margin;
                left: 8px;
                padding: 0 4px;
            }}
            QLineEdit {{
                background-color: {t.get('bg_input', '#3a3a3a')};
                color: {t.get('text_main', '#ffffff')};
                border: 1px solid {t.get('border', '#555555')};
                border-radius: 4px;
                padding: 5px;
            }}
            QTableWidget {{
                background-color: {t.get('bg_input', '#3a3a3a')};
                color: {t.get('text_main', '#ffffff')};
                border: 1px solid {t.get('border', '#555')};
                gridline-color: {t.get('border', '#444')};
            }}
            QTableWidget::item:alternate {{
                background-color: {t.get('bg_main', '#252525')};
            }}
            QHeaderView::section {{
                background-color: {t.get('bg_panel', '#2b2b2b')};
                color: {t.get('text_main', '#ffffff')};
                padding: 5px;
                border: 1px solid {t.get('border', '#555')};
                font-weight: bold;
            }}
            QTextEdit#DetailPanel {{
                background-color: {t.get('bg_input', '#3a3a3a')};
                color: {t.get('text_main', '#dddddd')};
                border: 1px solid {t.get('border', '#555')};
                border-radius: 4px;
                font-size: 12px;
            }}
            QPushButton {{
                background-color: {t.get('accent', '#4a90d9')};
                color: #ffffff;
                border: none;
                padding: 6px 16px;
                border-radius: 4px;
                font-weight: bold;
            }}
            QPushButton:hover {{ background-color: {t.get('accent_hover', '#357abd')}; }}
            QPushButton:disabled {{ background-color: {t.get('border', '#555')}; }}
            QFrame[frameShape="4"] {{ color: {t.get('border', '#555')}; }}
        """)


# ---------------------------------------------------------------------------
# Module-level helpers
# ---------------------------------------------------------------------------

def _hsep() -> QFrame:
    sep = QFrame()
    sep.setFrameShape(QFrame.Shape.HLine)
    return sep


def _banner(text: str, bg: str, border: str) -> QLabel:
    lbl = QLabel(text)
    lbl.setWordWrap(True)
    lbl.setStyleSheet(
        f"background-color: {bg}; color: white; border: 1px solid {border}; "
        f"border-radius: 6px; padding: 8px; font-weight: bold;"
    )
    return lbl


def _grade(score: int) -> tuple[str, str]:
    if score >= 80:
        return "Excellent", "#4CAF50"
    if score >= 65:
        return "Good", "#8BC34A"
    if score >= 40:
        return "Fair — limited verification available", "#FF9800"
    return "Low — exercise caution", "#F44336"
