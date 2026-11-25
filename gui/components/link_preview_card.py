"""A focus-preserving reading preview using the shared theme."""
from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QComboBox, QFrame, QLabel, QPushButton, QTextBrowser, QVBoxLayout, QHBoxLayout
from gui.components.settings_styles import card_style, label_style, input_style, btn_default
from gui.theme.theme import ThemeManager


class LinkPreviewCard(QFrame):
    jump_requested = Signal()
    inspect_requested = Signal()

    def __init__(self, parent=None):
        super().__init__(parent, Qt.WindowType.ToolTip | Qt.WindowType.FramelessWindowHint)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        self.setObjectName("ReadingLinkPreview")
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.setFixedWidth(380)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 12, 12, 12)
        self.label = QLabel(self)
        self.label.setTextFormat(Qt.TextFormat.PlainText)
        layout.addWidget(self.label)
        self.targets = QComboBox(self)
        self.targets.setAccessibleName("Referenced sources")
        self.targets.currentIndexChanged.connect(self._select)
        layout.addWidget(self.targets)
        self.selected_hit, self._alternatives = None, []
        self.text = QTextBrowser(self)
        self.text.setOpenExternalLinks(False)
        self.text.setFixedHeight(150)
        layout.addWidget(self.text)
        actions = QHBoxLayout()
        jump = QPushButton("Go to", self)
        jump.clicked.connect(self.jump_requested)
        self.inspect = QPushButton("Open in Intelligence", self)
        self.inspect.clicked.connect(self.inspect_requested)
        actions.addWidget(jump)
        actions.addWidget(self.inspect)
        layout.addLayout(actions)

    def show_hit(self, hit, position):
        theme = ThemeManager().get_theme()
        self.setStyleSheet("QFrame#ReadingLinkPreview {"+card_style(theme)+"} "
                           "QLabel {"+label_style(theme)+"} "
                           "QTextBrowser, QComboBox {"+input_style(theme)+label_style(theme)+"} "
                           "QPushButton {"+btn_default(theme)+"}")
        self._alternatives = hit.get("alternatives") or [hit]
        self.targets.blockSignals(True)
        self.targets.clear()
        for alternative in self._alternatives:
            self.targets.addItem(alternative["label"] + " — " + " ".join(alternative.get("preview_text", "").split())[:90])
        self.targets.blockSignals(False)
        self.targets.setVisible(len(self._alternatives) > 1)
        self._select(0)
        self.adjustSize()
        screen = self.screen().availableGeometry()
        position.setX(max(screen.left(), min(position.x(), screen.right() - self.width())))
        position.setY(max(screen.top(), min(position.y(), screen.bottom() - self.height())))
        self.move(position)
        self.show()

    def _select(self, index):
        if not 0 <= index < len(self._alternatives):
            return
        hit = self.selected_hit = self._alternatives[index]
        self.label.setText(f"{hit['label']} · Page {hit['target_page'] + 1}")
        self.text.setPlainText(hit.get("preview_text") or "No preview text available.")
        self.inspect.setEnabled(bool(hit.get("target_artifact_id")))
