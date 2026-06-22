"""
gui/components/dialogs/ai_setup_tab.py

AI Setup settings tab — the global home for local AI backend and model management.

Layout:
  ┌─ Hardware ──────────────────────────────────────────────────────────┐
  ├─ AI Backends ───────────────────────────────────────────────────────┤
  │    [Ollama card]   [llama.cpp card]   [plugin backends...]          │
  ├─ Models (active backend) ───────────────────────────────────────────┤
  │    Installed list + Available catalog with filter tabs + progress   │
  ├─ Text-to-Speech Voices ─────────────────────────────────────────────┤
  │    Piper status + installed voices + downloadable catalog           │
  └─ Defaults ──────────────────────────────────────────────────────────┘

GUI-only — all actions are emitted via bus.ai_setup_action_requested.
All state updates are received via bus.ai_setup_state_changed.
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Dict, List, Optional

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (
    QButtonGroup,
    QComboBox,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from gui.base.core import FALLBACK_THEME

if TYPE_CHECKING:
    from gui.app_context import AppContext


def _t(theme: dict, key: str, fallback: str = "") -> str:
    return theme.get(key) or FALLBACK_THEME.get(key, fallback)


# ---------------------------------------------------------------------------
# Reusable styling helpers (functions, not methods, so helpers can use them)
# ---------------------------------------------------------------------------

def _section_header_style(t: dict) -> str:
    return (
        f"color: {_t(t,'text_main','#fff')}; background: transparent; "
        f"font-size: 13px; font-weight: 700; margin-top: 8px; margin-bottom: 2px;"
    )


def _label_style(t: dict, muted: bool = False, size: int = 13) -> str:
    col = _t(t, "text_muted", "#aaa") if muted else _t(t, "text_main", "#fff")
    return f"color: {col}; background: transparent; font-size: {size}px;"


def _card_style(t: dict) -> str:
    return (
        f"background: {_t(t,'bg_panel','#2b2b2b')}; "
        f"border: 1px solid {_t(t,'border','#444')}; border-radius: 8px;"
    )


def _btn_primary(t: dict) -> str:
    return (
        f"background: {_t(t,'accent','#0078D7')}; color: #fff; border: none; "
        f"border-radius: 5px; padding: 5px 14px; font-weight: 600; font-size: 12px;"
    )


def _btn_danger(t: dict) -> str:
    return (
        f"background: {_t(t,'error','#cc3333')}; color: #fff; border: none; "
        f"border-radius: 5px; padding: 5px 14px; font-weight: 600; font-size: 12px;"
    )


def _btn_default(t: dict) -> str:
    return (
        f"background: {_t(t,'bg_panel','#333')}; color: {_t(t,'text_main','#fff')}; "
        f"border: 1px solid {_t(t,'border','#555')}; "
        f"border-radius: 5px; padding: 5px 14px; font-weight: 600; font-size: 12px;"
    )


def _btn_small_remove(t: dict) -> str:
    return (
        f"background: transparent; color: {_t(t,'error','#cc3333')}; "
        f"border: 1px solid {_t(t,'error','#cc3333')}; "
        f"border-radius: 4px; padding: 1px 8px; font-size: 11px;"
    )


def _input_style(t: dict) -> str:
    return (
        f"background: {_t(t,'bg_input','#333')}; color: {_t(t,'text_main','#fff')}; "
        f"border: 1px solid {_t(t,'border','#555')}; border-radius: 5px; "
        f"padding: 5px 10px; font-size: 12px; selection-background-color: {_t(t,'accent','#0078D7')};"
    )


def _progress_bar_style(t: dict) -> str:
    return (
        f"QProgressBar {{ border: 1px solid {_t(t,'border','#555')}; border-radius: 3px; "
        f"background: {_t(t,'bg_input','#333')}; height: 10px; text-align: center; color: transparent; }}"
        f"QProgressBar::chunk {{ background: {_t(t,'accent','#0078D7')}; border-radius: 2px; }}"
    )


# ---------------------------------------------------------------------------
# Tag badge
# ---------------------------------------------------------------------------

class _TagBadge(QLabel):
    _TAG_COLORS = {
        "chat":      ("#1a1a4a", "#8888ff"),
        "embedding": ("#0a2a1a", "#44cc88"),
        "vision":    ("#2a1a0a", "#cc8844"),
        "tts":       ("#2a0a3a", "#aa66cc"),
    }

    def __init__(self, tag: str, parent=None):
        super().__init__(tag, parent)
        bg, fg = self._TAG_COLORS.get(tag, ("#222", "#888"))
        self.setStyleSheet(
            f"background: {bg}; color: {fg}; border-radius: 4px; "
            f"padding: 1px 6px; font-size: 10px; font-weight: 600;"
        )


# ---------------------------------------------------------------------------
# Inline progress row
# ---------------------------------------------------------------------------

class _ProgressRow(QWidget):
    def __init__(self, t: dict, parent=None):
        super().__init__(parent)
        self._t = t
        self.setVisible(False)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 2, 0, 2)
        lay.setSpacing(8)
        self._bar = QProgressBar()
        self._bar.setRange(0, 100)
        self._bar.setFixedHeight(10)
        self._bar.setTextVisible(False)
        self._bar.setStyleSheet(_progress_bar_style(t))
        self._lbl = QLabel()
        self._lbl.setStyleSheet(_label_style(t, muted=True, size=11))
        self._lbl.setFixedWidth(180)
        lay.addWidget(self._bar, 1)
        lay.addWidget(self._lbl)

    def show_progress(self, msg: str, pct: int) -> None:
        self._bar.setValue(pct)
        self._lbl.setText(msg)
        self.setVisible(True)

    def hide_progress(self) -> None:
        self.setVisible(False)
        self._bar.setValue(0)
        self._lbl.setText("")


# ---------------------------------------------------------------------------
# Backend card
# ---------------------------------------------------------------------------

class _BackendCard(QFrame):
    def __init__(self, spec, t: dict, bus, parent=None):
        super().__init__(parent)
        self._spec = spec
        self._t = t
        self._bus = bus
        self.setFrameShape(QFrame.Shape.StyledPanel)
        self.setStyleSheet(f"QFrame {{ {_card_style(t)} }}")
        self._build_ui(t)

    def _build_ui(self, t: dict) -> None:
        lay = QVBoxLayout(self)
        lay.setContentsMargins(14, 12, 14, 12)
        lay.setSpacing(6)

        # Header row
        h = QHBoxLayout()
        name = QLabel(self._spec.name)
        name.setStyleSheet(
            f"color: {_t(t,'text_main','#fff')}; background: transparent; "
            f"font-size: 14px; font-weight: 700; border: none;"
        )
        self._status_lbl = QLabel()
        self._status_lbl.setStyleSheet("background: transparent; border: none;")
        h.addWidget(name)
        h.addStretch()
        h.addWidget(self._status_lbl)
        lay.addLayout(h)

        desc = QLabel(self._spec.description)
        desc.setWordWrap(True)
        desc.setStyleSheet(_label_style(t, muted=True, size=12) + " border: none;")
        lay.addWidget(desc)

        self._progress = _ProgressRow(t)
        lay.addWidget(self._progress)

        # Error label (shown when an install/uninstall fails)
        self._error_lbl = QLabel()
        self._error_lbl.setWordWrap(True)
        self._error_lbl.setVisible(False)
        self._error_lbl.setStyleSheet(
            f"color: {_t(t,'error','#ff6666')}; background: {_t(t,'error','#cc3333')}22; "
            f"border: 1px solid {_t(t,'error','#cc3333')}66; border-radius: 4px; "
            f"padding: 6px 10px; font-size: 12px;"
        )
        lay.addWidget(self._error_lbl)

        # Post-install notes (shown only after successful install)
        self._notes_lbl = QLabel()
        self._notes_lbl.setWordWrap(True)
        self._notes_lbl.setVisible(False)
        self._notes_lbl.setStyleSheet(
            f"color: {_t(t,'text_main','#ddd')}; background: {_t(t,'accent','#0078D7')}18; "
            f"border: 1px solid {_t(t,'accent','#0078D7')}44; border-radius: 4px; "
            f"padding: 6px 10px; font-size: 12px; border: none;"
        )
        lay.addWidget(self._notes_lbl)

        btn_row = QHBoxLayout()
        self._install_btn = QPushButton("Install")
        self._install_btn.setFixedHeight(30)
        self._install_btn.setStyleSheet(_btn_primary(t))
        self._uninstall_btn = QPushButton("Uninstall")
        self._uninstall_btn.setFixedHeight(30)
        self._uninstall_btn.setStyleSheet(_btn_danger(t))
        self._activate_btn = QPushButton("Set Active")
        self._activate_btn.setFixedHeight(30)
        self._activate_btn.setStyleSheet(_btn_default(t))
        btn_row.addWidget(self._install_btn)
        btn_row.addWidget(self._activate_btn)
        btn_row.addStretch()
        btn_row.addWidget(self._uninstall_btn)
        lay.addLayout(btn_row)

        self._install_btn.clicked.connect(self._on_install)
        self._uninstall_btn.clicked.connect(self._on_uninstall)
        self._activate_btn.clicked.connect(self._on_activate)

    def refresh(self, active_backend_id: str = "") -> None:
        installed = False
        version = None
        try:
            installed = self._spec.is_installed()
            if installed:
                version = self._spec.get_version()
        except Exception:
            pass

        is_active = self._spec.id == active_backend_id
        success = _t(self._t, "success", "#00cc66")
        muted = _t(self._t, "text_muted", "#888")

        if installed:
            ver_str = f"  v{version}" if version else ""
            label = f"Active{ver_str}" if is_active else f"Installed{ver_str}"
            color = success
        else:
            label, color = "Not installed", muted

        dot = "●" if installed else "○"
        self._status_lbl.setText(
            f"<span style='color:{color}; font-weight:600;'>{dot} {label}</span>"
        )
        self._install_btn.setVisible(not installed)
        self._install_btn.setEnabled(True)
        self._install_btn.setText("Install")
        self._uninstall_btn.setVisible(installed)
        self._uninstall_btn.setEnabled(True)
        self._activate_btn.setVisible(installed and not is_active)

        # Show setup notes when installed
        notes = getattr(self._spec, "install_notes", "")
        if installed and notes:
            self._notes_lbl.setText(notes.replace("\n", "<br>"))
            self._notes_lbl.setVisible(True)
        else:
            self._notes_lbl.setVisible(False)

    def show_error(self, msg: str) -> None:
        # Show only the first line — the rest is usually a Python traceback
        first_line = msg.strip().splitlines()[0] if msg.strip() else msg
        self._error_lbl.setText(f"Install failed: {first_line}")
        self._error_lbl.setVisible(True)
        self._install_btn.setEnabled(True)
        self._install_btn.setText("Retry")

    def clear_error(self) -> None:
        self._error_lbl.setVisible(False)
        self._error_lbl.setText("")

    def show_progress(self, msg: str, pct: int) -> None:
        self.clear_error()
        self._progress.show_progress(msg, pct)

    def hide_progress(self) -> None:
        self._progress.hide_progress()

    def _on_install(self) -> None:
        from core.events.domains.ai_setup_events import AISetupIntent, AISetupPayload
        self._install_btn.setEnabled(False)
        self._install_btn.setText("Installing…")
        self._bus.ai_setup_action_requested.emit(
            AISetupIntent.INSTALL_BACKEND, AISetupPayload(backend_id=self._spec.id),
        )

    def _on_uninstall(self) -> None:
        from core.events.domains.ai_setup_events import AISetupIntent, AISetupPayload
        self._uninstall_btn.setEnabled(False)
        self._bus.ai_setup_action_requested.emit(
            AISetupIntent.UNINSTALL_BACKEND, AISetupPayload(backend_id=self._spec.id),
        )

    def _on_activate(self) -> None:
        from core.events.domains.ai_setup_events import AISetupIntent, AISetupPayload
        self._bus.ai_setup_action_requested.emit(
            AISetupIntent.SET_ACTIVE_BACKEND, AISetupPayload(backend_id=self._spec.id),
        )


# ---------------------------------------------------------------------------
# Installed model row
# ---------------------------------------------------------------------------

class _InstalledModelRow(QWidget):
    def __init__(self, model_info, backend_id: str, t: dict, bus, parent=None):
        super().__init__(parent)
        self._model = model_info
        self._backend_id = backend_id
        self._bus = bus
        self.setStyleSheet(f"background: transparent;")
        lay = QHBoxLayout(self)
        lay.setContentsMargins(4, 3, 4, 3)
        lay.setSpacing(8)

        name = QLabel(model_info.name or model_info.id)
        name.setStyleSheet(f"color: {_t(t,'text_main','#fff')}; font-weight: 600; background: transparent;")

        size_lbl = QLabel(f"{model_info.size_gb:.1f} GB")
        size_lbl.setStyleSheet(_label_style(t, muted=True, size=11) + " background: transparent;")
        size_lbl.setFixedWidth(52)

        tags_w = QWidget()
        tags_w.setStyleSheet("background: transparent;")
        tags_lay = QHBoxLayout(tags_w)
        tags_lay.setContentsMargins(0, 0, 0, 0)
        tags_lay.setSpacing(4)
        for tag in (model_info.tags or []):
            tags_lay.addWidget(_TagBadge(tag))
        tags_lay.addStretch()

        rm_btn = QPushButton("Remove")
        rm_btn.setFixedHeight(24)
        rm_btn.setFixedWidth(68)
        rm_btn.setStyleSheet(_btn_small_remove(t))
        rm_btn.clicked.connect(self._on_remove)

        lay.addWidget(name)
        lay.addWidget(size_lbl)
        lay.addWidget(tags_w, 1)
        lay.addWidget(rm_btn)

    def _on_remove(self) -> None:
        from core.events.domains.ai_setup_events import AISetupIntent, AISetupPayload
        self._bus.ai_setup_action_requested.emit(
            AISetupIntent.REMOVE_MODEL,
            AISetupPayload(backend_id=self._backend_id, model_id=self._model.id),
        )


# ---------------------------------------------------------------------------
# Catalog model row (downloadable)
# ---------------------------------------------------------------------------

class _CatalogModelRow(QFrame):
    def __init__(self, model_info, backend_id: str, t: dict, bus, parent=None):
        super().__init__(parent)
        self._model = model_info
        self._backend_id = backend_id
        self._t = t
        self._bus = bus
        self.setFrameShape(QFrame.Shape.NoFrame)
        self.setStyleSheet(
            f"QFrame {{ background: {_t(t,'bg_input','#2a2a2a')}; "
            f"border: 1px solid {_t(t,'border','#444')}; border-radius: 6px; }}"
        )

        lay = QVBoxLayout(self)
        lay.setContentsMargins(10, 8, 10, 8)
        lay.setSpacing(4)

        top = QHBoxLayout()

        # Name + recommended badge
        name_col = QVBoxLayout()
        name_row = QHBoxLayout()
        name_row.setSpacing(8)
        name_lbl = QLabel(model_info.name)
        name_lbl.setStyleSheet(
            f"color: {_t(t,'text_main','#fff')}; font-weight: 600; font-size: 13px; "
            f"background: transparent; border: none;"
        )
        name_row.addWidget(name_lbl)
        if model_info.recommended:
            star = QLabel("★ Recommended")
            star.setStyleSheet(
                f"color: {_t(t,'warning','#ffaa00')}; font-size: 11px; "
                f"font-weight: 600; background: transparent; border: none;"
            )
            name_row.addWidget(star)
        name_row.addStretch()
        name_col.addLayout(name_row)

        if model_info.description:
            desc = QLabel(model_info.description)
            desc.setStyleSheet(_label_style(t, muted=True, size=11) + " border: none;")
            desc.setWordWrap(True)
            name_col.addWidget(desc)

        size_lbl = QLabel(f"{model_info.size_gb:.1f} GB")
        size_lbl.setStyleSheet(_label_style(t, muted=True, size=11) + " border: none;")
        size_lbl.setFixedWidth(52)

        tags_w = QWidget()
        tags_w.setStyleSheet("background: transparent;")
        tags_lay = QHBoxLayout(tags_w)
        tags_lay.setContentsMargins(0, 0, 0, 0)
        tags_lay.setSpacing(4)
        for tag in (model_info.tags or []):
            tags_lay.addWidget(_TagBadge(tag))

        self._dl_btn = QPushButton("Download")
        self._dl_btn.setFixedHeight(28)
        self._dl_btn.setFixedWidth(86)
        self._dl_btn.setStyleSheet(_btn_primary(t))
        self._dl_btn.clicked.connect(self._on_download)

        top.addLayout(name_col, 1)
        top.addWidget(size_lbl)
        top.addWidget(tags_w)
        top.addWidget(self._dl_btn)
        lay.addLayout(top)

        self._progress = _ProgressRow(t)
        lay.addWidget(self._progress)

    def mark_installed(self) -> None:
        self._dl_btn.setText("Installed")
        self._dl_btn.setEnabled(False)
        self._dl_btn.setStyleSheet(
            f"background: {_t(self._t,'success','#00aa55')}33; "
            f"color: {_t(self._t,'success','#00cc66')}; "
            f"border: 1px solid {_t(self._t,'success','#00aa55')}; "
            f"border-radius: 4px; padding: 1px 10px; font-size: 12px;"
        )

    def show_progress(self, msg: str, pct: int) -> None:
        self._dl_btn.setEnabled(False)
        self._progress.show_progress(msg, pct)

    def reset_button(self) -> None:
        self._dl_btn.setEnabled(True)
        self._dl_btn.setText("Download")
        self._dl_btn.setStyleSheet(_btn_primary(self._t))
        self._progress.hide_progress()

    def _on_download(self) -> None:
        from core.events.domains.ai_setup_events import AISetupIntent, AISetupPayload
        self._dl_btn.setEnabled(False)
        self._dl_btn.setText("Queued…")
        self._bus.ai_setup_action_requested.emit(
            AISetupIntent.PULL_MODEL,
            AISetupPayload(backend_id=self._backend_id, model_id=self._model.id),
        )


# ---------------------------------------------------------------------------
# TTS voice rows
# ---------------------------------------------------------------------------

class _InstalledVoiceRow(QWidget):
    def __init__(self, voice_info, t: dict, bus, parent=None):
        super().__init__(parent)
        self._voice = voice_info
        self._bus = bus
        self.setStyleSheet("background: transparent;")
        lay = QHBoxLayout(self)
        lay.setContentsMargins(4, 3, 4, 3)
        lay.setSpacing(8)

        if voice_info.recommended:
            star = QLabel("★")
            star.setStyleSheet(f"color: {_t(t,'warning','#ffaa00')}; background: transparent;")
            lay.addWidget(star)

        name = QLabel(voice_info.name or voice_info.id)
        name.setStyleSheet(f"color: {_t(t,'text_main','#fff')}; font-weight: 600; background: transparent;")
        size_lbl = QLabel(f"{voice_info.size_mb:.0f} MB")
        size_lbl.setStyleSheet(_label_style(t, muted=True, size=11) + " background: transparent;")
        size_lbl.setFixedWidth(52)
        lang_lbl = QLabel(voice_info.language)
        lang_lbl.setStyleSheet(_label_style(t, muted=True, size=11) + " background: transparent;")

        rm_btn = QPushButton("Remove")
        rm_btn.setFixedHeight(24)
        rm_btn.setFixedWidth(68)
        rm_btn.setStyleSheet(_btn_small_remove(t))
        rm_btn.clicked.connect(self._on_remove)

        lay.addWidget(name)
        lay.addWidget(size_lbl)
        lay.addWidget(lang_lbl)
        lay.addStretch()
        lay.addWidget(rm_btn)

    def _on_remove(self) -> None:
        from core.events.domains.ai_setup_events import AISetupIntent, AISetupPayload
        self._bus.ai_setup_action_requested.emit(
            AISetupIntent.REMOVE_TTS_VOICE, AISetupPayload(voice_id=self._voice.id),
        )


class _CatalogVoiceRow(QFrame):
    def __init__(self, voice_info, t: dict, bus, parent=None):
        super().__init__(parent)
        self._voice = voice_info
        self._t = t
        self._bus = bus
        self.setFrameShape(QFrame.Shape.NoFrame)
        self.setStyleSheet(
            f"QFrame {{ background: {_t(t,'bg_input','#2a2a2a')}; "
            f"border: 1px solid {_t(t,'border','#444')}; border-radius: 6px; }}"
        )

        lay = QVBoxLayout(self)
        lay.setContentsMargins(10, 8, 10, 8)
        lay.setSpacing(4)

        top = QHBoxLayout()
        name = QLabel(voice_info.name or voice_info.id)
        name.setStyleSheet(
            f"color: {_t(t,'text_main','#fff')}; font-weight: 600; font-size: 13px; "
            f"background: transparent; border: none;"
        )
        size_lbl = QLabel(f"{voice_info.size_mb:.0f} MB")
        size_lbl.setStyleSheet(_label_style(t, muted=True, size=11) + " border: none;")
        size_lbl.setFixedWidth(52)
        lang_lbl = QLabel(voice_info.language)
        lang_lbl.setStyleSheet(_label_style(t, muted=True, size=11) + " border: none;")
        lang_lbl.setFixedWidth(52)

        self._dl_btn = QPushButton("Download")
        self._dl_btn.setFixedHeight(28)
        self._dl_btn.setFixedWidth(86)
        self._dl_btn.setStyleSheet(_btn_primary(t))
        self._dl_btn.clicked.connect(self._on_download)

        top.addWidget(name, 1)
        top.addWidget(size_lbl)
        top.addWidget(lang_lbl)
        top.addWidget(self._dl_btn)
        lay.addLayout(top)

        if voice_info.description:
            desc = QLabel(voice_info.description)
            desc.setStyleSheet(_label_style(t, muted=True, size=11) + " border: none;")
            lay.addWidget(desc)

        self._progress = _ProgressRow(t)
        lay.addWidget(self._progress)

    def mark_installed(self) -> None:
        self._dl_btn.setText("Installed")
        self._dl_btn.setEnabled(False)
        self._dl_btn.setStyleSheet(
            f"background: {_t(self._t,'success','#00aa55')}33; "
            f"color: {_t(self._t,'success','#00cc66')}; "
            f"border: 1px solid {_t(self._t,'success','#00aa55')}; "
            f"border-radius: 4px; padding: 1px 10px; font-size: 12px;"
        )

    def show_progress(self, msg: str, pct: int) -> None:
        self._dl_btn.setEnabled(False)
        self._progress.show_progress(msg, pct)

    def reset_button(self) -> None:
        self._dl_btn.setEnabled(True)
        self._dl_btn.setText("Download")
        self._dl_btn.setStyleSheet(_btn_primary(self._t))
        self._progress.hide_progress()

    def _on_download(self) -> None:
        from core.events.domains.ai_setup_events import AISetupIntent, AISetupPayload
        self._dl_btn.setEnabled(False)
        self._dl_btn.setText("Queued…")
        self._bus.ai_setup_action_requested.emit(
            AISetupIntent.PULL_TTS_VOICE, AISetupPayload(voice_id=self._voice.id),
        )


# ---------------------------------------------------------------------------
# Main settings tab
# ---------------------------------------------------------------------------

class AISetupSettingsTab(QWidget):
    """
    AI Setup tab for GlobalSettingsDialog.

    All state mutations go through the EventBus (ai_setup_action_requested).
    State updates come back via ai_setup_state_changed.
    """

    def __init__(self, app_context: "AppContext", theme: dict, parent=None):
        super().__init__(parent)
        self._ctx = app_context
        self._bus = app_context.bus
        self._theme = {**FALLBACK_THEME, **theme}

        self._backend_cards: Dict[str, _BackendCard] = {}
        self._catalog_rows: Dict[str, Dict[str, _CatalogModelRow]] = {}
        self._voice_catalog_rows: Dict[str, _CatalogVoiceRow] = {}
        self._active_backend_id: str = ""

        self._chat_model_combo: Optional[QComboBox] = None
        self._embed_model_combo: Optional[QComboBox] = None

        self._build_ui()
        self._connect_signals()

        QTimer.singleShot(0, self._refresh_all)

    # -----------------------------------------------------------------------
    # UI construction
    # -----------------------------------------------------------------------

    def _build_ui(self) -> None:
        t = self._theme
        bg = _t(t, "bg_main")
        bg_panel = _t(t, "bg_panel")
        border = _t(t, "border")

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setStyleSheet(
            f"QScrollArea {{ background: {bg}; border: none; }}"
            f"QScrollArea > QWidget > QWidget {{ background: {bg}; }}"
            f"QScrollBar:vertical {{ background: {bg_panel}; width: 8px; border: none; }}"
            f"QScrollBar::handle:vertical {{ background: {border}; border-radius: 4px; min-height: 20px; }}"
            f"QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0; }}"
        )
        root.addWidget(scroll)

        content = QWidget()
        content.setStyleSheet(f"background: {bg};")
        scroll.setWidget(content)
        lay = QVBoxLayout(content)
        lay.setContentsMargins(20, 16, 20, 24)
        lay.setSpacing(16)

        lay.addWidget(self._build_global_toggle(t))
        lay.addWidget(self._make_divider(t))
        lay.addWidget(self._build_hardware_section(t))
        lay.addWidget(self._make_divider(t))
        lay.addWidget(self._build_backends_section(t))
        lay.addWidget(self._make_divider(t))
        self._models_section = self._build_models_section(t)
        lay.addWidget(self._models_section)
        lay.addWidget(self._make_divider(t))
        lay.addWidget(self._build_tts_section(t))
        lay.addWidget(self._make_divider(t))
        lay.addWidget(self._build_defaults_section(t))
        lay.addStretch()

    def _make_divider(self, t: dict) -> QFrame:
        div = QFrame()
        div.setFrameShape(QFrame.Shape.HLine)
        div.setFixedHeight(1)
        div.setStyleSheet(f"background: {_t(t,'border','#444')}; border: none;")
        return div

    def _make_section_hdr(self, text: str, t: dict) -> QLabel:
        lbl = QLabel(text)
        lbl.setStyleSheet(_section_header_style(t))
        return lbl

    # -- Global AI toggle --------------------------------------------------

    def _build_global_toggle(self, t: dict) -> QWidget:
        bg = _t(t, "bg_main")
        fg = _t(t, "text_main")
        muted = _t(t, "text_muted")
        accent = _t(t, "accent")
        warning = _t(t, "warning", "#ffaa00")

        w = QWidget()
        w.setStyleSheet(f"background: {bg};")
        lay = QHBoxLayout(w)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(12)

        lbl = QLabel("AI Features")
        lbl.setStyleSheet(f"color: {fg}; font-size: 14px; font-weight: 700; background: transparent;")

        desc = QLabel("Enable or disable all AI features app-wide")
        desc.setStyleSheet(f"color: {muted}; font-size: 12px; background: transparent;")

        self._ai_toggle_btn = QPushButton("Enabled")
        self._ai_toggle_btn.setCheckable(True)
        self._ai_toggle_btn.setChecked(True)
        self._ai_toggle_btn.setFixedHeight(32)
        self._ai_toggle_btn.setFixedWidth(100)
        self._ai_toggle_btn.setStyleSheet(
            f"QPushButton {{ background: {accent}; color: #fff; border: none; "
            f"border-radius: 5px; font-weight: 700; font-size: 12px; }}"
            f"QPushButton:!checked {{ background: {_t(t,'bg_panel','#333')}; "
            f"color: {warning}; border: 1px solid {warning}88; }}"
        )
        self._ai_toggle_btn.toggled.connect(self._on_ai_toggle_clicked)

        lay.addWidget(lbl)
        lay.addWidget(desc)
        lay.addStretch()
        lay.addWidget(self._ai_toggle_btn)
        return w

    # -- Hardware ----------------------------------------------------------

    def _build_hardware_section(self, t: dict) -> QWidget:
        bg = _t(t, "bg_main")
        bg_panel = _t(t, "bg_panel")
        border = _t(t, "border")
        fg = _t(t, "text_main")
        muted = _t(t, "text_muted")

        w = QWidget()
        w.setStyleSheet(f"background: {bg};")
        lay = QVBoxLayout(w)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(6)
        lay.addWidget(self._make_section_hdr("Hardware", t))

        card = QFrame()
        card.setFrameShape(QFrame.Shape.StyledPanel)
        card.setStyleSheet(f"QFrame {{ {_card_style(t)} }}")
        grid = QGridLayout(card)
        grid.setContentsMargins(14, 10, 14, 10)
        grid.setSpacing(8)

        def _lbl(text):
            l = QLabel(text)
            l.setStyleSheet(f"color: {muted}; background: transparent; border: none;")
            return l

        def _val():
            l = QLabel("Detecting…")
            l.setStyleSheet(f"color: {fg}; background: transparent; border: none;")
            return l

        self._hw_ram_lbl = _val()
        self._hw_gpu_lbl = _val()
        self._hw_rec_lbl = _val()
        self._hw_rec_lbl.setWordWrap(True)

        grid.addWidget(_lbl("RAM:"), 0, 0)
        grid.addWidget(self._hw_ram_lbl, 0, 1)
        grid.addWidget(_lbl("GPU:"), 1, 0)
        grid.addWidget(self._hw_gpu_lbl, 1, 1)
        grid.addWidget(_lbl("Recommendation:"), 2, 0, Qt.AlignmentFlag.AlignTop)
        grid.addWidget(self._hw_rec_lbl, 2, 1)
        grid.setColumnStretch(1, 1)

        lay.addWidget(card)
        return w

    # -- Backends ----------------------------------------------------------

    def _build_backends_section(self, t: dict) -> QWidget:
        bg = _t(t, "bg_main")
        w = QWidget()
        w.setStyleSheet(f"background: {bg};")
        lay = QVBoxLayout(w)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(8)
        lay.addWidget(self._make_section_hdr("AI Backends", t))
        self._backends_container_lay = QVBoxLayout()
        self._backends_container_lay.setSpacing(8)
        lay.addLayout(self._backends_container_lay)
        return w

    # -- Models ------------------------------------------------------------

    def _build_models_section(self, t: dict) -> QWidget:
        bg = _t(t, "bg_main")
        fg = _t(t, "text_main")
        muted = _t(t, "text_muted")
        accent = _t(t, "accent")
        border = _t(t, "border")
        bg_panel = _t(t, "bg_panel")

        w = QWidget()
        w.setStyleSheet(f"background: {bg};")
        lay = QVBoxLayout(w)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(8)

        hdr_row = QHBoxLayout()
        self._models_hdr = self._make_section_hdr("Models", t)
        self._models_backend_lbl = QLabel("")
        self._models_backend_lbl.setStyleSheet(_label_style(t, muted=True, size=12))
        hdr_row.addWidget(self._models_hdr)
        hdr_row.addWidget(self._models_backend_lbl)
        hdr_row.addStretch()
        lay.addLayout(hdr_row)

        # Sub-header: installed
        inst_hdr = QLabel("Installed")
        inst_hdr.setStyleSheet(
            f"color: {muted}; font-size: 12px; font-weight: 600; background: transparent;"
        )
        lay.addWidget(inst_hdr)

        self._installed_models_container = QWidget()
        self._installed_models_container.setStyleSheet(f"background: {bg};")
        self._installed_models_lay = QVBoxLayout(self._installed_models_container)
        self._installed_models_lay.setContentsMargins(0, 0, 0, 0)
        self._installed_models_lay.setSpacing(2)
        lay.addWidget(self._installed_models_container)

        self._no_models_lbl = QLabel("No models installed yet.")
        self._no_models_lbl.setStyleSheet(_label_style(t, muted=True, size=12))
        lay.addWidget(self._no_models_lbl)

        # Filter tabs + available header
        filter_row = QHBoxLayout()
        avail_hdr = QLabel("Available")
        avail_hdr.setStyleSheet(
            f"color: {muted}; font-size: 12px; font-weight: 600; background: transparent;"
        )
        filter_row.addWidget(avail_hdr)
        filter_row.addStretch()

        self._filter_group = QButtonGroup(self)
        self._filter_group.setExclusive(True)
        for label in ("All", "Chat", "Embedding", "Vision"):
            btn = QPushButton(label)
            btn.setCheckable(True)
            btn.setFixedHeight(26)
            btn.setStyleSheet(
                f"QPushButton {{ background: {bg_panel}; color: {muted}; border: 1px solid {border}; "
                f"border-radius: 4px; padding: 0 12px; font-size: 11px; }}"
                f"QPushButton:checked {{ background: {accent}; color: #fff; border-color: {accent}; }}"
                f"QPushButton:hover:!checked {{ color: {fg}; }}"
            )
            filter_row.addWidget(btn)
            self._filter_group.addButton(btn)
        self._filter_group.buttons()[0].setChecked(True)
        self._filter_group.buttonClicked.connect(self._on_filter_changed)
        lay.addLayout(filter_row)

        self._catalog_container = QWidget()
        self._catalog_container.setStyleSheet(f"background: {bg};")
        self._catalog_lay = QVBoxLayout(self._catalog_container)
        self._catalog_lay.setContentsMargins(0, 0, 0, 0)
        self._catalog_lay.setSpacing(4)
        lay.addWidget(self._catalog_container)

        # HuggingFace browse link — populated with backend-specific URL in _refresh_models_section
        self._hf_link_lbl = QLabel()
        self._hf_link_lbl.setOpenExternalLinks(True)
        self._hf_link_lbl.setStyleSheet(
            f"color: {accent}; background: transparent; font-size: 12px;"
        )
        self._hf_link_lbl.setVisible(False)
        lay.addWidget(self._hf_link_lbl)
        return w

    # -- TTS ---------------------------------------------------------------

    def _build_tts_section(self, t: dict) -> QWidget:
        bg = _t(t, "bg_main")
        muted = _t(t, "text_muted")
        accent = _t(t, "accent")

        w = QWidget()
        w.setStyleSheet(f"background: {bg};")
        lay = QVBoxLayout(w)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(8)

        hdr_row = QHBoxLayout()
        hdr_row.addWidget(self._make_section_hdr("Text-to-Speech Voices", t))
        hdr_row.addStretch()
        self._piper_status_lbl = QLabel("Checking…")
        self._piper_status_lbl.setStyleSheet(_label_style(t, muted=True, size=12))
        self._piper_install_btn = QPushButton("Install Piper")
        self._piper_install_btn.setFixedHeight(28)
        self._piper_install_btn.setStyleSheet(_btn_primary(t))
        self._piper_install_btn.setVisible(False)
        self._piper_install_btn.clicked.connect(self._on_install_piper)
        hdr_row.addWidget(self._piper_status_lbl)
        hdr_row.addWidget(self._piper_install_btn)
        lay.addLayout(hdr_row)

        self._piper_progress = _ProgressRow(t)
        lay.addWidget(self._piper_progress)

        inst_hdr = QLabel("Installed Voices")
        inst_hdr.setStyleSheet(
            f"color: {muted}; font-size: 12px; font-weight: 600; background: transparent;"
        )
        lay.addWidget(inst_hdr)

        self._installed_voices_container = QWidget()
        self._installed_voices_container.setStyleSheet(f"background: {bg};")
        self._installed_voices_lay = QVBoxLayout(self._installed_voices_container)
        self._installed_voices_lay.setContentsMargins(0, 0, 0, 0)
        self._installed_voices_lay.setSpacing(2)
        lay.addWidget(self._installed_voices_container)

        self._no_voices_lbl = QLabel("No voices installed. Download one below.")
        self._no_voices_lbl.setStyleSheet(_label_style(t, muted=True, size=12))
        lay.addWidget(self._no_voices_lbl)

        avail_hdr = QLabel("Available Voices")
        avail_hdr.setStyleSheet(
            f"color: {muted}; font-size: 12px; font-weight: 600; background: transparent;"
        )
        lay.addWidget(avail_hdr)

        self._voice_catalog_container = QWidget()
        self._voice_catalog_container.setStyleSheet(f"background: {bg};")
        self._voice_catalog_lay = QVBoxLayout(self._voice_catalog_container)
        self._voice_catalog_lay.setContentsMargins(0, 0, 0, 0)
        self._voice_catalog_lay.setSpacing(4)
        lay.addWidget(self._voice_catalog_container)
        return w

    # -- Defaults ----------------------------------------------------------

    def _build_defaults_section(self, t: dict) -> QWidget:
        bg = _t(t, "bg_main")
        fg = _t(t, "text_main")
        muted = _t(t, "text_muted")

        w = QWidget()
        w.setStyleSheet(f"background: {bg};")
        lay = QVBoxLayout(w)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(8)
        lay.addWidget(self._make_section_hdr("Defaults", t))

        grid = QGridLayout()
        grid.setSpacing(8)
        grid.setColumnStretch(1, 1)

        def _lbl(text):
            l = QLabel(text)
            l.setStyleSheet(f"color: {fg}; background: transparent;")
            return l

        self._chat_model_combo = QComboBox()
        self._chat_model_combo.setStyleSheet(_input_style(t))
        self._embed_model_combo = QComboBox()
        self._embed_model_combo.setStyleSheet(_input_style(t))

        grid.addWidget(_lbl("Chat / Workflow model:"), 0, 0)
        grid.addWidget(self._chat_model_combo, 0, 1)
        grid.addWidget(_lbl("Embedding model:"), 1, 0)
        grid.addWidget(self._embed_model_combo, 1, 1)
        lay.addLayout(grid)

        save_btn = QPushButton("Save Defaults")
        save_btn.setFixedHeight(32)
        save_btn.setFixedWidth(140)
        save_btn.setStyleSheet(_btn_primary(t))
        save_btn.clicked.connect(self._on_save_defaults)
        lay.addWidget(save_btn, alignment=Qt.AlignmentFlag.AlignRight)
        return w

    # -----------------------------------------------------------------------
    # Signal wiring
    # -----------------------------------------------------------------------

    def _connect_signals(self) -> None:
        self._bus.ai_setup_state_changed.connect(self._on_state_changed)

    def _on_state_changed(self, event, payload) -> None:
        from core.events.domains.ai_setup_events import AISetupEvent

        if event == AISetupEvent.BACKEND_STATUS_CHANGED:
            self._refresh_backend_cards()
            if payload.message in ("installed", "uninstalled", "active", "startup_check_complete"):
                self._refresh_models_section()
        elif event == AISetupEvent.MODEL_LIST_CHANGED:
            self._refresh_models_section()
        elif event == AISetupEvent.TTS_VOICE_LIST_CHANGED:
            self._refresh_tts_section()
        elif event == AISetupEvent.PROGRESS:
            self._handle_progress(payload)
        elif event in (AISetupEvent.OPERATION_COMPLETE, AISetupEvent.OPERATION_FAILED):
            self._handle_op_result(event, payload)
        elif event == AISetupEvent.AI_TOGGLE_CHANGED:
            enabled = bool((payload.data or {}).get("enabled", True))
            self._ai_toggle_btn.blockSignals(True)
            self._ai_toggle_btn.setChecked(enabled)
            self._ai_toggle_btn.setText("Enabled" if enabled else "Disabled")
            self._ai_toggle_btn.blockSignals(False)

    def _handle_progress(self, payload) -> None:
        bid, mid, vid = payload.backend_id, payload.model_id, payload.voice_id
        msg, pct = payload.message, payload.pct

        if bid and not mid and not vid:
            card = self._backend_cards.get(bid)
            if card:
                card.show_progress(msg, pct)
        elif bid and mid:
            row = self._catalog_rows.get(bid, {}).get(mid)
            if row:
                row.show_progress(msg, pct)
        elif vid:
            row = self._voice_catalog_rows.get(vid)
            if row:
                row.show_progress(msg, pct)
            else:
                self._piper_progress.show_progress(msg, pct)

    def _handle_op_result(self, event, payload) -> None:
        from core.events.domains.ai_setup_events import AISetupEvent

        success = (event == AISetupEvent.OPERATION_COMPLETE)
        bid, mid, vid = payload.backend_id, payload.model_id, payload.voice_id

        if bid and not mid:
            card = self._backend_cards.get(bid)
            if card:
                card.hide_progress()
                if success:
                    card.clear_error()
                    card.refresh(self._active_backend_id)
                else:
                    card.show_error(payload.message)
                    card.refresh(self._active_backend_id)
        elif bid and mid:
            row = self._catalog_rows.get(bid, {}).get(mid)
            if row:
                row.mark_installed() if success else row.reset_button()
            if success:
                self._refresh_models_section()
        elif vid:
            row = self._voice_catalog_rows.get(vid)
            if row:
                row.mark_installed() if success else row.reset_button()
            self._piper_progress.hide_progress()
            if success:
                self._refresh_tts_section()

    # -----------------------------------------------------------------------
    # Data loading
    # -----------------------------------------------------------------------

    def _refresh_all(self) -> None:
        self._refresh_hardware()
        self._sync_ai_toggle()
        self._populate_backend_cards()
        self._refresh_backend_cards()
        self._refresh_models_section()
        self._refresh_tts_section()

    def _sync_ai_toggle(self) -> None:
        svc = getattr(self._ctx, "ai_setup_service", None)
        if not svc:
            return
        try:
            enabled = not svc.is_ai_disabled_by_user()
            self._ai_toggle_btn.blockSignals(True)
            self._ai_toggle_btn.setChecked(enabled)
            self._ai_toggle_btn.setText("Enabled" if enabled else "Disabled")
            self._ai_toggle_btn.blockSignals(False)
        except Exception:
            pass

    def _refresh_hardware(self) -> None:
        t = self._theme
        fg = _t(t, "text_main")
        muted = _t(t, "text_muted")
        warning = _t(t, "warning", "#ffaa00")
        success = _t(t, "success", "#00cc66")

        try:
            svc = self._ctx.ai_setup_service
            info = svc.get_hardware_info()
        except Exception:
            info = {}

        ram = info.get("ram_gb", 0)
        gpu = info.get("gpu_name", "Not detected")
        vram = info.get("vram_gb", 0)

        self._hw_ram_lbl.setText(f"{ram} GB" if ram else "Unknown")
        gpu_has_vram = vram and vram > 0
        self._hw_gpu_lbl.setText(f"{gpu}  ({vram} GB VRAM)" if gpu_has_vram else gpu)

        gpu_detected = gpu and gpu not in ("Not detected", "Unknown")
        if vram >= 10:
            rec = "GPU detected — can run large models (13B+ at Q4)."
        elif vram >= 6:
            rec = "GPU detected — recommended: models up to 8B at Q4."
        elif vram >= 4:
            rec = "GPU detected — recommended: 3B–7B models at Q4."
        elif gpu_detected and not gpu_has_vram:
            # GPU visible via lspci but VRAM couldn't be queried (AMD without ROCm)
            if ram >= 8:
                rec = "GPU detected (VRAM unknown). Try small models first (E2B / 3B at Q4)."
            else:
                rec = "GPU detected (VRAM unknown). Recommend Gemma 4 E2B or Llama 3.2 3B."
        elif ram >= 16:
            rec = "No dedicated GPU detected. CPU inference available — recommend 3B–7B models."
        elif ram >= 8:
            rec = "Limited RAM. Recommend small models (3B at Q4) for CPU inference."
        else:
            rec = "Hardware details unavailable. Start with a small model (3B at Q4)."

        self._hw_rec_lbl.setText(rec)

    def _populate_backend_cards(self) -> None:
        registry = getattr(self._ctx, "llm_backend_registry", None)
        if not registry:
            return
        self._clear_layout(self._backends_container_lay)
        self._backend_cards.clear()

        for spec in registry.get_all():
            card = _BackendCard(spec, self._theme, self._bus, self)
            self._backend_cards[spec.id] = card
            self._backends_container_lay.addWidget(card)

    def _refresh_backend_cards(self) -> None:
        svc = getattr(self._ctx, "ai_setup_service", None)
        if svc:
            try:
                self._active_backend_id = svc.get_active_backend_id()
            except Exception:
                pass
        for card in self._backend_cards.values():
            card.refresh(self._active_backend_id)

    def _refresh_models_section(self) -> None:
        bid = self._active_backend_id
        registry = getattr(self._ctx, "llm_backend_registry", None)
        if not bid or not registry:
            self._models_section.setVisible(False)
            return
        self._models_section.setVisible(True)

        spec = registry.get(bid)
        if not spec:
            return

        self._models_backend_lbl.setText(f"({spec.name})")
        self._clear_layout(self._installed_models_lay)

        try:
            installed = spec.list_installed_models()
        except Exception:
            installed = []

        self._no_models_lbl.setVisible(len(installed) == 0)
        for m in installed:
            self._installed_models_lay.addWidget(
                _InstalledModelRow(m, bid, self._theme, self._bus)
            )

        installed_ids = {m.id for m in installed}
        installed_base = {m.id.split(":")[0] for m in installed}

        self._clear_layout(self._catalog_lay)
        self._catalog_rows[bid] = {}
        active_filter = self._get_active_filter()

        for m in spec.catalog:
            if active_filter != "all" and active_filter not in m.tags:
                continue
            row = _CatalogModelRow(m, bid, self._theme, self._bus)
            if m.id in installed_ids or m.id.split(":")[0] in installed_base:
                row.mark_installed()
            self._catalog_lay.addWidget(row)
            self._catalog_rows[bid][m.id] = row

        # HuggingFace browse link
        if bid == "ollama":
            hf_url = "https://ollama.com/library"
            link_text = "Browse more models on ollama.com →"
        else:
            hf_url = "https://huggingface.co/models?library=gguf&sort=trending"
            link_text = "Browse GGUF models on HuggingFace →"
        accent = _t(self._theme, "accent", "#0078D7")
        self._hf_link_lbl.setText(
            f"<a href='{hf_url}' style='color:{accent}; text-decoration:none;'>{link_text}</a>"
        )
        self._hf_link_lbl.setVisible(True)

        self._refresh_defaults_combos(installed)

    def _refresh_tts_section(self) -> None:
        from core.backends.tts_backend import VOICE_CATALOG, is_piper_installed, list_installed_voices
        t = self._theme
        user_data_dir = getattr(getattr(self._ctx, "core", None), "user_data_dir", "")

        piper_ok = is_piper_installed(user_data_dir)
        success = _t(t, "success", "#00cc66")
        muted = _t(t, "text_muted", "#888")

        if piper_ok:
            self._piper_status_lbl.setText(
                f"<span style='color:{success}; font-weight:600;'>● Piper installed</span>"
            )
        else:
            self._piper_status_lbl.setText(
                f"<span style='color:{muted};'>○ Piper not installed</span>"
            )
        self._piper_install_btn.setVisible(not piper_ok)

        self._clear_layout(self._installed_voices_lay)
        try:
            installed = list_installed_voices(user_data_dir)
        except Exception:
            installed = []
        self._no_voices_lbl.setVisible(len(installed) == 0)
        for v in installed:
            self._installed_voices_lay.addWidget(_InstalledVoiceRow(v, t, self._bus))

        installed_ids = {v.id for v in installed}
        self._clear_layout(self._voice_catalog_lay)
        self._voice_catalog_rows.clear()
        for v in VOICE_CATALOG:
            row = _CatalogVoiceRow(v, t, self._bus)
            if v.id in installed_ids:
                row.mark_installed()
            self._voice_catalog_lay.addWidget(row)
            self._voice_catalog_rows[v.id] = row

    def _refresh_defaults_combos(self, installed_models) -> None:
        if not self._chat_model_combo:
            return
        svc = getattr(self._ctx, "ai_setup_service", None)
        current_chat = svc.get_default_chat_model() if svc else ""
        current_embed = svc.get_default_embedding_model() if svc else ""

        chat_models = [m for m in installed_models if "chat" in m.tags or "vision" in m.tags]
        embed_models = [m for m in installed_models if "embedding" in m.tags]

        for combo, models, current in [
            (self._chat_model_combo, chat_models, current_chat),
            (self._embed_model_combo, embed_models, current_embed),
        ]:
            combo.blockSignals(True)
            combo.clear()
            for m in models:
                combo.addItem(m.name or m.id, m.id)
            idx = next((i for i in range(combo.count()) if combo.itemData(i) == current), 0)
            combo.setCurrentIndex(idx)
            combo.blockSignals(False)

    # -----------------------------------------------------------------------
    # Actions
    # -----------------------------------------------------------------------

    def _on_filter_changed(self, _btn) -> None:
        self._refresh_models_section()

    def _get_active_filter(self) -> str:
        checked = self._filter_group.checkedButton()
        return checked.text().lower() if checked else "all"

    def _on_ai_toggle_clicked(self, checked: bool) -> None:
        from core.events.domains.ai_setup_events import AISetupIntent, AISetupPayload
        self._ai_toggle_btn.setText("Enabled" if checked else "Disabled")
        self._bus.ai_setup_action_requested.emit(
            AISetupIntent.TOGGLE_AI, AISetupPayload(data={"enabled": checked}),
        )

    def _on_save_defaults(self) -> None:
        from core.events.domains.ai_setup_events import AISetupIntent, AISetupPayload
        chat_id = self._chat_model_combo.currentData() if self._chat_model_combo else ""
        embed_id = self._embed_model_combo.currentData() if self._embed_model_combo else ""
        if chat_id:
            self._bus.ai_setup_action_requested.emit(
                AISetupIntent.SET_DEFAULT_MODEL, AISetupPayload(model_id=chat_id, data={"type": "chat"}),
            )
        if embed_id:
            self._bus.ai_setup_action_requested.emit(
                AISetupIntent.SET_DEFAULT_MODEL, AISetupPayload(model_id=embed_id, data={"type": "embedding"}),
            )

    def _on_install_piper(self) -> None:
        from core.events.domains.ai_setup_events import AISetupIntent, AISetupPayload
        self._piper_install_btn.setEnabled(False)
        self._piper_install_btn.setText("Installing…")
        self._bus.ai_setup_action_requested.emit(
            AISetupIntent.PULL_TTS_VOICE, AISetupPayload(voice_id="__piper__"),
        )

    # -----------------------------------------------------------------------
    # Theme update (called when app theme changes)
    # -----------------------------------------------------------------------

    def apply_theme(self, theme: dict) -> None:
        self._theme = {**FALLBACK_THEME, **theme}
        # Rebuild the entire UI with the new theme
        # (simpler than re-styling every individual widget)
        old_lay = self.layout()
        while old_lay.count():
            item = old_lay.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        self._backend_cards.clear()
        self._catalog_rows.clear()
        self._voice_catalog_rows.clear()
        self._build_ui()
        self._connect_signals()
        QTimer.singleShot(0, self._refresh_all)

    # -----------------------------------------------------------------------
    # Helpers
    # -----------------------------------------------------------------------

    @staticmethod
    def _clear_layout(layout) -> None:
        while layout.count():
            item = layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
