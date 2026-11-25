"""Separate active artifact selection bounds from legacy chunk text controls."""
from PySide6.QtCore import Signal
from PySide6.QtWidgets import QFormLayout, QGroupBox, QLabel, QSpinBox, QVBoxLayout, QWidget

LEGACY_LIMIT_DEFAULTS = dict(chunk_pages=4, max_chunk_chars=14000, max_master_chars=36000,
                             num_ctx=24576, chunk_num_predict=1400, synthesis_num_predict=3500,
                             master_num_predict=4000, max_entities_per_chunk=12, max_relations_per_chunk=16)


class AnalysisTemplateLimits(QWidget):
    changed = Signal()

    def __init__(self, app_context, parent=None):
        super().__init__(parent)
        self.ctx = app_context
        self._loading = False
        self._invalid = {}
        layout = QVBoxLayout(self)
        note = QLabel('Selection caps limit the candidate pool. Whole source quotations stay verbatim; '
                      'the selected model’s context may fit fewer evidence groups.', self)
        note.setWordWrap(True)
        layout.addWidget(note)
        form = QFormLayout()
        self.quotes_per_chunk = self._spin(form, 'Quotes per section', 1, 4, 3)
        self.max_sections = self._spin(form, 'Relevant sections', 1, 12, 7)
        self.max_analysis_quotes = self._spin(form, 'Total candidate quotations', 1, 28, 16)
        layout.addLayout(form)
        self.legacy_panel = QGroupBox('Legacy chunk text targets', self)
        legacy = QFormLayout(self.legacy_panel)
        legacy.addRow(QLabel('These targets do not shorten canonical quotations in artifact analysis.'))
        self.quote_words = self._spin(legacy, 'Target quote words', 4, 30, 10)
        self.max_quote_words = self._spin(legacy, 'Maximum quote words', 6, 40, 18)
        self.explanation_words = self._spin(legacy, 'Explanation words', 4, 30, 10)

    def _spin(self, form, label, minimum, maximum, value):
        spin = QSpinBox(self)
        spin.setRange(minimum, maximum)
        spin.setValue(value)
        spin.setAccessibleName(label)
        form.addRow(label, spin)
        spin.valueChanged.connect(self._edited)
        return spin

    def _edited(self, *_):
        if not self._loading:
            self._invalid.pop(self.sender(), None)
            self.changed.emit()

    def load(self, template):
        self._loading = True
        self._invalid.clear()
        limits = template.get('limits') if isinstance(template.get('limits'), dict) else {}
        contract = template.get('graph_contract') or {}
        selection = contract.get('evidence_selection') or {} if isinstance(contract, dict) else {}
        fields = ((self.quotes_per_chunk, 'quotes_per_section', 'max_quotes_per_chunk', 3),
                  (self.max_sections, 'max_sections', 'max_sections', 7),
                  (self.max_analysis_quotes, 'max_quotes', 'max_analysis_quotes', 16),
                  (self.quote_words, None, 'quote_words', 10),
                  (self.max_quote_words, None, 'max_quote_words', 18),
                  (self.explanation_words, None, 'explanation_words', 10))
        for widget, modern, old, default in fields:
            value = selection.get(modern, limits.get(old, default))
            if type(value) is not int or not widget.minimum() <= value <= widget.maximum():
                self._invalid[widget] = old
                widget.setToolTip(f'Saved value {value!r} is invalid. Explicitly choose a valid replacement.')
                value = default
            else:
                widget.setToolTip('')
            widget.setValue(value)
        self._loading = False

    def selection(self):
        invalid = [name for widget, name in self._invalid.items()
                   if widget in (self.quotes_per_chunk, self.max_sections, self.max_analysis_quotes)]
        if invalid:
            raise ValueError('Saved selection limits are invalid: ' + ', '.join(invalid) +
                             '. Explicitly choose valid replacements.')
        return dict(quotes_per_section=self.quotes_per_chunk.value(), max_sections=self.max_sections.value(),
                    max_quotes=self.max_analysis_quotes.value())

    def legacy_values(self, template):
        # Preserve limits outside this form, including user-configured model budgets.
        previous = template.get('limits') if isinstance(template.get('limits'), dict) else {}
        values = dict(max_quotes_per_chunk=self.quotes_per_chunk.value(), max_sections=self.max_sections.value(),
                      max_analysis_quotes=self.max_analysis_quotes.value(), quote_words=self.quote_words.value(),
                      max_quote_words=self.max_quote_words.value(), explanation_words=self.explanation_words.value())
        for widget, key in self._invalid.items():
            if key in previous:
                values[key] = previous[key]  # An untouched invalid legacy-only target is not silently repaired.
        return {**LEGACY_LIMIT_DEFAULTS, **previous, **values}
