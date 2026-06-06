import os
from PySide6.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QLabel,
                             QRadioButton, QButtonGroup, QTextEdit, QPushButton,
                             QScrollArea, QFrame)
from core.events.event_bus import EventBus
from core.events.domains.tool_events import OCRIntent, OCRPayload, OCRStatus, OCRStatusPayload
class OCRTab(QWidget):
    def __init__(self, parent=None, main_window=None):
        super().__init__(parent)
        self.main_window = main_window
        self.bus = EventBus.get_instance()
        self.theme = None

        self._build_ui()

        # --- Event Listeners ---
        self.bus.ocr_status_updated.connect(self._handle_status_update)

    def _build_ui(self):
        outer_layout = QVBoxLayout(self)
        outer_layout.setContentsMargins(0, 0, 0, 0)

        self.tab_scroll_area = QScrollArea(self)
        self.tab_scroll_area.setWidgetResizable(True)
        self.tab_scroll_area.setFrameShape(QFrame.Shape.NoFrame)

        self.content_widget = QWidget()
        layout = QVBoxLayout(self.content_widget)

        self.header = QLabel("OCR Engine")
        layout.addWidget(self.header)

        modes_layout = QVBoxLayout()
        mode_row_1 = QHBoxLayout()
        mode_row_2 = QHBoxLayout()
        self.mode_group = QButtonGroup(self)

        self.rb_text = QRadioButton("Extract Text")
        self.rb_new = QRadioButton("Save New PDF")
        self.rb_replace = QRadioButton("Replace Original")
        self.rb_replace.setChecked(True)

        self.mode_group.addButton(self.rb_text, 1)
        self.mode_group.addButton(self.rb_new, 2)
        self.mode_group.addButton(self.rb_replace, 3)

        mode_row_1.addWidget(self.rb_text)
        mode_row_1.addWidget(self.rb_new)
        mode_row_1.addStretch()

        mode_row_2.addWidget(self.rb_replace)
        mode_row_2.addStretch()

        modes_layout.addLayout(mode_row_1)
        modes_layout.addLayout(mode_row_2)
        layout.addLayout(modes_layout)

        self.text_area = QTextEdit()
        self.text_area.setReadOnly(True)
        layout.addWidget(self.text_area, 1)

        control_layout = QHBoxLayout()
        self.run_ocr_btn = QPushButton("Run OCR")
        self.run_ocr_btn.clicked.connect(self.request_ocr)
        control_layout.addWidget(self.run_ocr_btn)

        self.status_label = QLabel("Ready")
        control_layout.addWidget(self.status_label)
        control_layout.addStretch()
        layout.addLayout(control_layout)

        self.tab_scroll_area.setWidget(self.content_widget)
        outer_layout.addWidget(self.tab_scroll_area)

    def update_theme(self, theme):
        self.theme = theme
        self.setStyleSheet(f"OCRTab {{ background-color: {theme['bg_main']}; color: {theme['text_main']}; }}")
        self.tab_scroll_area.setStyleSheet("QScrollArea { background: transparent; border: none; }")
        self.tab_scroll_area.viewport().setStyleSheet("background: transparent;")
        self.content_widget.setStyleSheet(f"QWidget {{ background-color: {theme['bg_main']}; color: {theme['text_main']}; }}")

        self.text_area.setStyleSheet(f"background-color: {theme['bg_input']}; color: {theme['text_main']}; border: 1px solid {theme['border']}; border-radius: 4px; padding: 4px;")
        self.header.setStyleSheet(f"font-size: 18px; margin-bottom: 10px; color: {theme['text_main']}; background: transparent;")

        radio_style = f"""
            QRadioButton {{ background: transparent; spacing: 8px; color: {theme['text_main']}; font-weight: bold; }}
            QRadioButton::indicator {{ width: 14px; height: 14px; border-radius: 7px; border: 2px solid {theme['border']}; background: {theme['bg_input']}; }}
            QRadioButton::indicator:hover {{ border: 2px solid {theme['accent']}; }}
            QRadioButton::indicator:checked {{ border: 2px solid {theme['accent']}; background: {theme['accent']}; }}
        """
        self.rb_text.setStyleSheet(radio_style)
        self.rb_new.setStyleSheet(radio_style)
        self.rb_replace.setStyleSheet(radio_style)

        self.run_ocr_btn.setStyleSheet(f"""
            QPushButton {{ background-color: {theme['success']}; color: #ffffff; padding: 8px 16px; font-weight: bold; border-radius: 4px; border: none; }}
            QPushButton:hover {{ background-color: {theme['accent_hover']}; }}
            QPushButton:disabled {{ background-color: {theme['bg_input']}; color: {theme['text_muted']}; }}
        """)

        if self.status_label.text() in ["Ready", ""] or self.status_label.text().startswith("Target:"):
            self.status_label.setStyleSheet(f"color: {theme['text_muted']}; font-size: 13px; font-weight: bold; margin-left: 10px; background: transparent;")

    def sync_file(self, file_path):
        self.text_area.clear()
        self.status_label.setText(f"Target: {os.path.basename(file_path)}")
        color = self.theme['text_muted'] if self.theme else "gray"
        self.status_label.setStyleSheet(f"color: {color}; font-size: 14px; margin-left: 10px;")

    def get_output_mode(self):
        if self.rb_text.isChecked(): return "text"
        if self.rb_new.isChecked(): return "save_new"
        if self.rb_replace.isChecked(): return "replace"

    def request_ocr(self):
        """Emits an intent to the background service to process the OCR."""
        current_file = self.main_window.current_file_path
        if not current_file:
            self.status_label.setText("No document loaded in viewer.")
            color = self.theme['error'] if self.theme else "#ff4444"
            self.status_label.setStyleSheet(f"color: {color}; font-size: 14px; margin-left: 10px;")
            return

        self.run_ocr_btn.setEnabled(False)
        self.text_area.clear()

        self.bus.ocr_action_requested.emit(
            OCRIntent.RUN,
            OCRPayload(file_path=current_file, mode=self.get_output_mode())
        )

    def _handle_status_update(self, event: OCRStatus, payload: OCRStatusPayload):
        """Reacts to state changes from the OCR service."""
        status = event
        msg = payload.get("msg", "")
        text_content = payload.get("text")

        self.status_label.setText(msg)

        if status == OCRStatus.RUNNING:
            color = self.theme['warning'] if self.theme else "#ffaa00"
            self.status_label.setStyleSheet(f"color: {color}; font-size: 14px; margin-left: 10px;")

        elif status in {OCRStatus.COMPLETE, OCRStatus.ERROR}:
            self.run_ocr_btn.setEnabled(True)
            if text_content is not None:
                self.text_area.setPlainText(text_content)

            color = self.theme['success'] if status == OCRStatus.COMPLETE else (self.theme['error'] if self.theme else "#ff4444")
            self.status_label.setStyleSheet(f"color: {color}; font-size: 14px; margin-left: 10px;")
