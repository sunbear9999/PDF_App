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
import threading

from core.events.domains.intelligence_events import IntelligenceEvent, ProcessingStatePayload
from gui.components.model_dependency_tasks import ModelDependencyWorker as _ModelInstallWorker

from PySide6.QtCore import Qt, QThread, QTimer, Signal
from PySide6.QtWidgets import (
    QButtonGroup,
    QCheckBox,
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
    QFileDialog,
    QMessageBox,
)

from gui.base.core import FALLBACK_THEME
from gui.components.sentence_role_setup_card import (
    SentenceRoleSetupCard, catalog_detail_labels,
)

if TYPE_CHECKING:
    from gui.app_context import AppContext

# Optional models are optional: "missing" reads as a fault, so say it plainly.
_STATE_LABELS = {"missing": "Not installed (optional)", "ready": "Installed ✓"}


from gui.components.settings_styles import divider, section_header
from gui.components.settings_styles import (
    theme_value as _t,
    section_header_style as _section_header_style,
    label_style as _label_style,
    card_style as _card_style,
    btn_primary as _btn_primary,
    btn_danger as _btn_danger,
    btn_default as _btn_default,
    btn_small_remove as _btn_small_remove,
    input_style as _input_style,
    progress_bar_style as _progress_bar_style,
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

    def refresh(self, active_backend_id: str = "", state=None) -> None:
        if state is None:
            self._status_lbl.setText('Checking readiness…')
            for button in (self._install_btn, self._uninstall_btn, self._activate_btn):
                button.setEnabled(False)
            return
        installed = bool((state or {}).get('installed'))
        version = (state or {}).get('version')
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
        self._status_lbl.setToolTip(str(state.get('error') or ''))
        self._install_btn.setVisible(not installed)
        self._install_btn.setEnabled(True)
        self._install_btn.setText("Install")
        self._uninstall_btn.setVisible(installed)
        self._uninstall_btn.setEnabled(True)
        self._activate_btn.setVisible(installed and not is_active)
        self._activate_btn.setEnabled(installed and not is_active)

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

        self._role_combos: Dict[str, QComboBox] = {}
        self._role_download_buttons: Dict[str, QPushButton] = {}
        self._role_help_labels: Dict[str, QLabel] = {}
        self._role_downloads_by_model: Dict[str, set[str]] = {}
        self._processing_mode_combo: Optional[QComboBox] = None
        self._background_level_combo: Optional[QComboBox] = None
        self._auto_transcribe_combo: Optional[QComboBox] = None
        self._intelligence_checks: Dict[str, QCheckBox] = {}
        self._dep_rows: Dict[str, Dict[str, object]] = {}
        self._install_workers: Dict[str, _ModelInstallWorker] = {}

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
        try:
            lay.addWidget(self._build_pipeline_models_section(t))
            lay.addWidget(self._make_divider(t))
        except Exception as exc:  # never let this optional section break Settings
            print(f"[AISetup] pipeline models section failed: {exc}")
        lay.addWidget(self._build_defaults_section(t))
        lay.addStretch()

    def _make_divider(self, t: dict) -> QFrame:
        return divider(t)

    def _make_section_hdr(self, text: str, t: dict) -> QLabel:
        return section_header(text, t)

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

    # -- Pipeline models (optional deterministic-pipeline enhancers) -------

    def _build_pipeline_models_section(self, t: dict) -> QWidget:
        bg = _t(t, "bg_main")
        fg = _t(t, "text_main")
        muted = _t(t, "text_muted")
        border = _t(t, "border", "#444")
        accent = _t(t, "accent", "#4a9eff")

        w = QWidget()
        w.setStyleSheet(f"background: {bg};")
        lay = QVBoxLayout(w)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(8)
        lay.addWidget(self._make_section_hdr("Pipeline Models", t))

        blurb = QLabel(
            "Optional models that upgrade the local analysis pipeline. Each has a "
            "deterministic fallback, so the app works without them — install to improve "
            "entity resolution, reranking, or date/claim handling."
        )
        blurb.setWordWrap(True)
        blurb.setStyleSheet(f"color: {muted}; background: transparent; font-size: 12px;")
        lay.addWidget(blurb)

        self._role_card = SentenceRoleSetupCard(self._ctx, t)
        lay.addWidget(self._role_card)
        svc = getattr(self._ctx, "model_dependency_service", None)
        self._dep_rows.clear()
        if not svc:
            note = QLabel("Model dependency service unavailable.")
            note.setStyleSheet(f"color: {muted}; background: transparent;")
            lay.addWidget(note)
            return w

        try:
            dependencies = svc.list_dependencies(probe=False)
        except Exception as exc:
            err = QLabel(f"Could not load pipeline models: {exc}")
            err.setWordWrap(True)
            err.setStyleSheet(f"color: {muted}; background: transparent;")
            lay.addWidget(err)
            return w

        for dep in dependencies:
            row = QFrame()
            row.setStyleSheet(
                f"QFrame {{ background: {_t(t,'bg_panel')}; border: 1px solid {border}; "
                f"border-radius: 6px; }}"
            )
            rlay = QVBoxLayout(row)
            rlay.setContentsMargins(12, 10, 12, 10)
            rlay.setSpacing(4)

            top = QHBoxLayout()
            name = QLabel(dep["label"])
            name.setStyleSheet(f"color: {fg}; background: transparent; font-weight: 700;")
            top.addWidget(name)
            size = QLabel(dep.get("size_hint") or "")
            size.setStyleSheet(f"color: {muted}; background: transparent; font-size: 11px;")
            top.addWidget(size)
            top.addStretch()

            status = QLabel("")
            status.setStyleSheet(f"color: {muted}; background: transparent; font-size: 12px;")
            top.addWidget(status)

            btn = QPushButton("Install")
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            btn.setStyleSheet(
                f"QPushButton {{ background: {accent}; color: white; border: none; "
                f"border-radius: 4px; padding: 4px 12px; }} "
                f"QPushButton:disabled {{ background: {border}; color: {muted}; }}"
            )
            btn.clicked.connect(lambda _=False, d=dep["id"]: self._on_install_dependency(d))
            top.addWidget(btn)
            rlay.addLayout(top)

            desc = QLabel(dep["description"])
            desc.setWordWrap(True)
            desc.setStyleSheet(f"color: {muted}; background: transparent; font-size: 11px;")
            rlay.addWidget(desc)
            for detail in catalog_detail_labels(dep, muted):
                rlay.addWidget(detail)
            if dep.get("repository"):
                manifest = QLabel(
                    f"{dep.get('repository')} @ {dep.get('revision') or 'unpinned'}"
                )
                manifest.setWordWrap(True)
                manifest.setTextInteractionFlags(
                    Qt.TextInteractionFlag.TextSelectableByMouse
                )
                manifest.setStyleSheet(
                    f"color: {muted}; background: transparent; font-size: 10px;"
                )
                rlay.addWidget(manifest)
            destination = QLabel(dep.get("destination") or "")
            destination.setWordWrap(True)
            destination.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
            destination.setStyleSheet(f"color: {muted}; background: transparent; font-size: 10px;")
            rlay.addWidget(destination)

            if dep.get("kind") in {"onnx_hf", "manual"}:
                model_actions = QHBoxLayout()
                import_btn = QPushButton("Import model folder…")
                import_btn.clicked.connect(
                    lambda _=False, d=dep["id"]: self._on_import_dependency(d)
                )
                model_actions.addWidget(import_btn)
                remove_btn = QPushButton("Remove")
                remove_btn.clicked.connect(
                    lambda _=False, d=dep["id"]: self._on_remove_dependency(d)
                )
                model_actions.addWidget(remove_btn)
                model_actions.addStretch()
                rlay.addLayout(model_actions)
            else:
                import_btn = None
                remove_btn = None

            lay.addWidget(row)
            self._dep_rows[dep["id"]] = {
                "status": status, "button": btn, "import_button": import_btn,
                "remove_button": remove_btn,
                "log": [], "destination": dep.get("destination") or "",
            }

        self._refresh_pipeline_models()
        return w

    def _refresh_pipeline_models(self) -> None:
        if not getattr(self._ctx, 'model_readiness_service', None):
            return
        from gui.components.artifact_tasks import ArtifactTaskRunner
        if not hasattr(self, '_readiness_runner'):
            self._readiness_runner = ArtifactTaskRunner(self._ctx, self, service_name='model_readiness_service')
            self._readiness_runner.completed.connect(self._readiness_ready)
            self._readiness_runner.failed.connect(self._readiness_failed)
            self.destroyed.connect(self._readiness_runner.shutdown)
        self._readiness_runner.start('read')

    def _readiness_failed(self, error):
        for refs in self._dep_rows.values():
            refs['status'].setText('Readiness check failed')
            refs['status'].setToolTip(error)

    def _readiness_ready(self, operation):
        result, self._readiness_runner.result = self._readiness_runner.result or {}, None
        if getattr(self, '_role_card', None):
            self._role_card.apply_snapshot(result)
        self._apply_pipeline_models(result.get('dependencies') or [])

    def _apply_pipeline_models(self, dependencies):
        for dep in dependencies:
            refs = self._dep_rows.get(dep["id"])
            if not refs:
                continue
            status: QLabel = refs["status"]
            btn: QPushButton = refs["button"]
            state = str(dep.get("status") or "missing").lower()
            status.setText(_STATE_LABELS.get(state, state.title()))
            status.setToolTip(
                f"State: {state}\nDestination: {dep.get('destination') or ''}\n"
                f"Repository: {dep.get('repository') or ''}\n"
                f"Revision: {dep.get('revision') or ''}\n"
                + "\n".join(refs.get("log") or [])
            )
            remove_btn = refs.get("remove_button")
            if remove_btn:
                remove_btn.setEnabled(state in {"ready", "broken", "incompatible"})
            if state == "ready":
                btn.setText("Ready ✓")
                btn.setEnabled(False)
            elif not dep["installable"]:
                btn.setText("Manual")
                btn.setEnabled(False)
            else:
                btn.setText("Repair" if state in {"broken", "incompatible"} else "Install")
                btn.setEnabled(True)

    def _on_install_dependency(self, dep_id: str) -> None:
        svc = getattr(self._ctx, "model_dependency_service", None)
        if not svc:
            return
        if dep_id in self._install_workers:
            self._install_workers[dep_id].cancel()
            refs = self._dep_rows.get(dep_id) or {}
            button = refs.get("button")
            if button:
                button.setEnabled(False)
                button.setText("Cancelling…")
            return
        refs = self._dep_rows.get(dep_id) or {}
        btn: Optional[QPushButton] = refs.get("button")
        status: Optional[QLabel] = refs.get("status")
        if btn:
            btn.setEnabled(True)
            btn.setText("Cancel")
        if status:
            status.setText("Downloading…")
        # Parent the QThread to the application, not this tab, so closing the
        # Settings dialog mid-install can't destroy a running thread (crash/leak).
        from PySide6.QtWidgets import QApplication
        worker = _ModelInstallWorker(svc, dep_id, QApplication.instance())
        worker.done.connect(self._on_dependency_installed, Qt.ConnectionType.QueuedConnection)
        worker.progress.connect(self._on_dependency_progress, Qt.ConnectionType.QueuedConnection)
        worker.log.connect(self._on_dependency_log, Qt.ConnectionType.QueuedConnection)
        self._install_workers[dep_id] = worker
        worker.start()

    def _on_dependency_installed(self, dep_id: str, ok: bool, error: str) -> None:
        worker = self._install_workers.pop(dep_id, None)
        refs = self._dep_rows.get(dep_id) or {}
        status: Optional[QLabel] = refs.get("status")
        if not ok and status:
            status.setText(f"Failed: {error[:60]}")
            status.setToolTip(
                f"{error}\nDestination: {refs.get('destination') or ''}\n"
                + "\n".join(refs.get("log") or [])
            )
        self._refresh_pipeline_models()
        self._bus.intelligence_state_changed.emit(IntelligenceEvent.MODEL_DEPENDENCY_CHANGED, ProcessingStatePayload(dependency_id=dep_id))

    def _on_dependency_progress(self, dep_id: str, current: int, total: int, message: str):
        refs = self._dep_rows.get(dep_id) or {}
        status = refs.get("status")
        if status:
            pct = int(current * 100 / total) if total else 0
            status.setText(f"{message} ({pct}%)")

    def _on_dependency_log(self, dep_id: str, message: str):
        refs = self._dep_rows.get(dep_id) or {}
        refs.setdefault("log", []).append(message)
        status = refs.get("status")
        if status:
            status.setToolTip(
                f"Destination: {refs.get('destination') or ''}\n"
                + "\n".join(refs["log"][-30:])
            )

    def _on_import_dependency(self, dep_id: str):
        svc = getattr(self._ctx, "model_dependency_service", None)
        if not svc:
            return
        folder = QFileDialog.getExistingDirectory(self, "Import model folder")
        if not folder:
            return
        if dep_id in self._install_workers:
            return
        worker = _ModelInstallWorker(svc, dep_id, QApplication.instance(), action="import", source_dir=folder)
        worker.done.connect(self._on_dependency_installed, Qt.ConnectionType.QueuedConnection)
        worker.progress.connect(self._on_dependency_progress, Qt.ConnectionType.QueuedConnection)
        worker.log.connect(self._on_dependency_log, Qt.ConnectionType.QueuedConnection)
        self._install_workers[dep_id] = worker
        worker.start()

    def _on_remove_dependency(self, dep_id: str):
        svc = getattr(self._ctx, "model_dependency_service", None)
        if not svc:
            return
        answer = QMessageBox.question(
            self,
            "Remove optional model",
            "Remove this locally downloaded model? It can be installed or "
            "imported again later.",
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        result = svc.remove(dep_id)
        refs = self._dep_rows.get(dep_id) or {}
        if not result.get("ok") and refs.get("status"):
            refs["status"].setText("Remove failed")
            refs["status"].setToolTip(str(result.get("error") or "Remove failed"))
        self._refresh_pipeline_models()

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

        def _lbl(text):
            l = QLabel(text)
            l.setStyleSheet(f"color: {fg}; background: transparent;")
            return l

        role_hdr = QLabel("Model roles")
        role_hdr.setStyleSheet(f"color: {muted}; background: transparent; font-weight: 700;")
        lay.addWidget(role_hdr)

        role_grid = QGridLayout()
        role_grid.setSpacing(8)
        role_grid.setColumnStretch(1, 1)
        self._role_combos.clear()
        self._role_download_buttons.clear()
        self._role_help_labels.clear()
        role_rows = [
            ("main_answer_model", "Main answer model:"),
            ("fast_worker_model", "Fast worker model:"),
            ("embedding_model", "Embedding model:"),
            ("reranker_model", "Reranker model:"),
            ("vision_model", "Vision model:"),
            ("transcription_model", "Transcription model:"),
            ("extraction_repair_model", "Extraction repair model:"),
        ]
        for row, (role, label) in enumerate(role_rows):
            combo = QComboBox()
            combo.setStyleSheet(_input_style(t))
            combo.currentIndexChanged.connect(lambda _idx=0, r=role: self._update_role_download_button(r))
            download_btn = QPushButton("Download")
            download_btn.setFixedWidth(92)
            download_btn.setStyleSheet(_btn_primary(t))
            download_btn.clicked.connect(lambda _checked=False, r=role: self._on_download_role_model(r))
            reset_btn = QPushButton("Reset")
            reset_btn.setFixedWidth(70)
            reset_btn.setStyleSheet(_btn_default(t))
            reset_btn.clicked.connect(lambda _checked=False, r=role: self._on_reset_model_role(r))
            help_lbl = QLabel()
            help_lbl.setWordWrap(True)
            help_lbl.setStyleSheet(_label_style(t, muted=True, size=11))
            role_grid.addWidget(_lbl(label), row, 0)
            role_grid.addWidget(combo, row, 1)
            role_grid.addWidget(download_btn, row, 2)
            role_grid.addWidget(reset_btn, row, 3)
            role_grid.addWidget(help_lbl, row + len(role_rows), 1, 1, 3)
            self._role_combos[role] = combo
            self._role_download_buttons[role] = download_btn
            self._role_help_labels[role] = help_lbl
        lay.addLayout(role_grid)

        intel_hdr = QLabel("Background intelligence")
        intel_hdr.setStyleSheet(f"color: {muted}; background: transparent; font-weight: 700;")
        lay.addWidget(intel_hdr)

        intel_grid = QGridLayout()
        intel_grid.setSpacing(8)
        intel_grid.setColumnStretch(1, 1)
        self._processing_mode_combo = QComboBox()
        self._processing_mode_combo.setStyleSheet(_input_style(t))
        for label, value in [
            ("Minimal", "minimal"), ("Balanced", "balanced"),
            ("Deep", "deep"), ("Manual", "manual"),
        ]:
            self._processing_mode_combo.addItem(label, value)
        self._background_level_combo = QComboBox()
        self._background_level_combo.setStyleSheet(_input_style(t))
        for label, value in [
            ("Off", "off"), ("Low", "low"), ("Normal", "normal"), ("Aggressive", "aggressive"),
        ]:
            self._background_level_combo.addItem(label, value)
        self._auto_transcribe_combo = QComboBox()
        self._auto_transcribe_combo.setStyleSheet(_input_style(t))
        for label, value in [("Never", "never"), ("Ask", "ask"), ("Always", "always")]:
            self._auto_transcribe_combo.addItem(label, value)
        intel_grid.addWidget(_lbl("Default processing mode:"), 0, 0)
        intel_grid.addWidget(self._processing_mode_combo, 0, 1)
        intel_grid.addWidget(_lbl("Background level:"), 1, 0)
        intel_grid.addWidget(self._background_level_combo, 1, 1)
        intel_grid.addWidget(_lbl("Auto-transcribe audio/video:"), 2, 0)
        intel_grid.addWidget(self._auto_transcribe_combo, 2, 1)
        lay.addLayout(intel_grid)

        stored_hdr = QLabel("Stored intelligence")
        stored_hdr.setStyleSheet(f"color: {muted}; background: transparent; font-weight: 700;")
        lay.addWidget(stored_hdr)
        checks = [
            ("auto_embed_sources", "Auto-embed sources"),
            ("auto_extract_entities", "Auto-extract entities"),
            ("auto_extract_dates", "Auto-extract dates"),
            ("auto_extract_citations", "Auto-extract citations"),
            ("auto_extract_tables", "Auto-extract tables"),
            ("store_transcripts", "Store transcripts"),
            ("store_embeddings", "Store embeddings"),
            ("pause_background_while_answering", "Pause background jobs while answering"),
        ]
        checks_grid = QGridLayout()
        checks_grid.setSpacing(6)
        self._intelligence_checks.clear()
        for idx, (key, label) in enumerate(checks):
            cb = QCheckBox(label)
            cb.setStyleSheet(_label_style(t, muted=False, size=12))
            self._intelligence_checks[key] = cb
            checks_grid.addWidget(cb, idx // 2, idx % 2)
        lay.addLayout(checks_grid)

        warn = QLabel("Changing the embedding role marks embedding-dependent artifacts stale for rebuild.")
        warn.setWordWrap(True)
        warn.setStyleSheet(_label_style(t, muted=True, size=11))
        lay.addWidget(warn)

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
        elif event in (AISetupEvent.MODEL_ROLES_CHANGED, AISetupEvent.INTELLIGENCE_SETTINGS_CHANGED):
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
            for role in self._role_downloads_by_model.get(mid, set()):
                btn = self._role_download_buttons.get(role)
                if btn:
                    btn.setVisible(True)
                    btn.setEnabled(False)
                    label = f"{max(0, min(100, int(pct or 0)))}%" if pct is not None else "Downloading"
                    btn.setText(label)
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
                    card.refresh(self._active_backend_id, getattr(self, "_setup_snapshot", {}).get("backends", {}).get(card._spec.id))
                else:
                    card.show_error(payload.message)
                    card.refresh(self._active_backend_id, getattr(self, "_setup_snapshot", {}).get("backends", {}).get(card._spec.id))
        elif bid and mid:
            row = self._catalog_rows.get(bid, {}).get(mid)
            if row:
                row.mark_installed() if success else row.reset_button()
            for role in list(self._role_downloads_by_model.get(mid, set())):
                btn = self._role_download_buttons.get(role)
                if btn:
                    if success:
                        btn.setText("Installed")
                        btn.setEnabled(False)
                        btn.setVisible(False)
                    else:
                        btn.setText("Download")
                        btn.setEnabled(True)
                        btn.setVisible(True)
            self._role_downloads_by_model.pop(mid, None)
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

        info = getattr(self, '_setup_snapshot', {}).get('hardware') or {}

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
            card.refresh(self._active_backend_id, getattr(self, "_setup_snapshot", {}).get("backends", {}).get(card._spec.id))

        self._request_setup_snapshot()

    def _request_setup_snapshot(self):
        if not getattr(self._ctx, 'model_readiness_service', None):
            return
        from gui.components.artifact_tasks import ArtifactTaskRunner
        if not hasattr(self, '_setup_reader'):
            self._setup_reader = ArtifactTaskRunner(self._ctx, self, service_name='model_readiness_service')
            self._setup_reader.completed.connect(self._setup_ready)
            self._setup_reader.failed.connect(self._readiness_failed)
            self.destroyed.connect(self._setup_reader.shutdown)
        self._setup_reader.start('read_setup', backend_id=self._active_backend_id)

    def _setup_ready(self, operation):
        result, self._setup_reader.result = self._setup_reader.result or {}, None
        if result.get('backend_id') != self._active_backend_id:
            return
        self._setup_snapshot = result
        self._models_backend_lbl.setToolTip('\n'.join(result.get('diagnostics') or []))
        self._refresh_hardware()
        for card in self._backend_cards.values():
            card.refresh(self._active_backend_id, result.get('backends', {}).get(card._spec.id))
        self._apply_models_section(result.get('installed_models') or [])
        self._apply_tts_section()

    def _refresh_models_section(self) -> None:
        self._request_setup_snapshot()

    def _apply_models_section(self, installed):
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
        self._request_setup_snapshot()

    def _apply_tts_section(self):
        from core.backends.tts_backend import VOICE_CATALOG, is_piper_installed, list_installed_voices
        t = self._theme
        user_data_dir = getattr(getattr(self._ctx, "core", None), "user_data_dir", "")

        piper_ok = bool(getattr(self, '_setup_snapshot', {}).get('piper_installed'))
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
        installed = getattr(self, '_setup_snapshot', {}).get('voices') or []
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
        svc = getattr(self._ctx, "ai_setup_service", None)
        role_svc = getattr(self._ctx, "model_role_service", None)
        policy_svc = getattr(self._ctx, "processing_policy_service", None)
        catalog = svc.get_active_backend_catalog() if svc and hasattr(svc, "get_active_backend_catalog") else []
        installed_ids = {model.id for model in installed_models}
        catalog_by_id = {m.id: m for m in catalog}

        all_models = list(installed_models)

        role_capability = {
            "embedding_model": "embedding",
            "vision_model": "vision",
        }
        for role, combo in self._role_combos.items():
            required = role_capability.get(role)
            recommendations = getattr(self, '_setup_snapshot', {}).get('role_descriptions', {}).get(role, {}).get('recommendations') or []
            models = [m for m in all_models if not required or required in getattr(m, "tags", [])]
            current = self._current_role_model(role)
            combo.blockSignals(True)
            combo.clear()
            combo.addItem("(fallback)", "")
            added = {""}
            for rec_id in recommendations[:3]:
                info = catalog_by_id.get(rec_id)
                label = info.name if info else rec_id
                suffix = "installed" if rec_id in installed_ids else "download"
                if role == "transcription_model":
                    suffix = "local Whisper"
                combo.addItem(f"Suggested: {label} ({suffix})", rec_id)
                added.add(rec_id)
            for m in models:
                if m.id in added:
                    continue
                combo.addItem(m.name or m.id, m.id)
                added.add(m.id)
            if current and current not in added:
                combo.addItem(f"Custom: {current}", current)
            idx = next((i for i in range(combo.count()) if combo.itemData(i) == current), 0)
            combo.setCurrentIndex(idx)
            combo.blockSignals(False)
            desc = getattr(self, '_setup_snapshot', {}).get('role_descriptions', {}).get(role, {})
            guide = desc.get("guide", "")
            tier = desc.get("hardware_tier")
            max_ctx = desc.get("max_context_tokens")
            if tier:
                guide = f"{guide} Hardware tier: {tier}; suggested context ceiling: {max_ctx or 'auto'} tokens."
            if guide and role in self._role_help_labels:
                self._role_help_labels[role].setText(guide)
            self._update_role_download_button(role)

        if policy_svc:
            settings = getattr(self, '_setup_snapshot', {}).get('settings') or {}
            self._set_combo_data(self._processing_mode_combo, settings.get("default_processing_mode", "balanced"))
            self._set_combo_data(self._background_level_combo, settings.get("background_level", "normal"))
            self._set_combo_data(self._auto_transcribe_combo, settings.get("auto_transcribe", "ask"))
            for key, cb in self._intelligence_checks.items():
                cb.blockSignals(True)
                cb.setChecked(bool(settings.get(key, False)))
                cb.blockSignals(False)

    def _set_combo_data(self, combo: Optional[QComboBox], value: str) -> None:
        if not combo:
            return
        idx = next((i for i in range(combo.count()) if combo.itemData(i) == value), 0)
        combo.blockSignals(True)
        combo.setCurrentIndex(idx)
        combo.blockSignals(False)

    def _current_role_model(self, role: str) -> str:
        return str(getattr(self, '_setup_snapshot', {}).get('current_roles', {}).get(role) or '')

    def _fallback_save_model_for_role(self, role: str) -> str:
        svc = getattr(self._ctx, "ai_setup_service", None)
        role_svc = getattr(self._ctx, "model_role_service", None)
        if role == "main_answer_model" and svc and hasattr(svc, "get_default_chat_model"):
            default = svc.get_default_chat_model() or ""
            if default:
                return default
        if role == "embedding_model" and svc and hasattr(svc, "get_default_embedding_model"):
            default = svc.get_default_embedding_model() or ""
            if default:
                return default
        if role in {"main_answer_model", "embedding_model", "transcription_model"} and role_svc:
            try:
                recs = role_svc.recommendations_for_role(role)
                return recs[0] if recs else ""
            except Exception:
                return ""
        return ""

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
        selected_roles = {}
        for role, combo in self._role_combos.items():
            model_id = combo.currentData()
            if model_id is None:
                model_id = ""
            if not model_id:
                model_id = self._fallback_save_model_for_role(role)
            selected_roles[role] = model_id
        for role, model_id in selected_roles.items():
            self._bus.ai_setup_action_requested.emit(
                AISetupIntent.SET_MODEL_ROLE,
                AISetupPayload(data={"role": role, "model": model_id}),
            )
        settings = {
            "default_processing_mode": self._processing_mode_combo.currentData() if self._processing_mode_combo else "balanced",
            "background_level": self._background_level_combo.currentData() if self._background_level_combo else "normal",
            "auto_transcribe": self._auto_transcribe_combo.currentData() if self._auto_transcribe_combo else "ask",
        }
        settings.update({key: cb.isChecked() for key, cb in self._intelligence_checks.items()})
        settings["background_processing_enabled"] = settings["background_level"] != "off"
        self._bus.ai_setup_action_requested.emit(
            AISetupIntent.SET_INTELLIGENCE_SETTINGS,
            AISetupPayload(data=settings),
        )
        # Refresh models section to show updated saved values
        QTimer.singleShot(200, self._refresh_models_section)

    def apply_changes(self) -> None:
        self._on_save_defaults()

    def _on_reset_model_role(self, role: str) -> None:
        from core.events.domains.ai_setup_events import AISetupIntent, AISetupPayload
        self._bus.ai_setup_action_requested.emit(
            AISetupIntent.RESET_MODEL_ROLE,
            AISetupPayload(data={"role": role}),
        )
        QTimer.singleShot(0, self._refresh_models_section)

    def _on_download_role_model(self, role: str) -> None:
        from core.events.domains.ai_setup_events import AISetupIntent, AISetupPayload
        combo = self._role_combos.get(role)
        svc = getattr(self._ctx, "ai_setup_service", None)
        if not combo or not svc:
            return
        model_id = combo.currentData() or ""
        backend_id = svc.get_active_backend_id() if hasattr(svc, "get_active_backend_id") else ""
        if not model_id or not backend_id:
            return
        self._role_downloads_by_model.setdefault(model_id, set()).add(role)
        btn = self._role_download_buttons.get(role)
        if btn:
            btn.setVisible(True)
            btn.setEnabled(False)
            btn.setText("0%")
        self._bus.ai_setup_action_requested.emit(
            AISetupIntent.PULL_MODEL,
            AISetupPayload(backend_id=backend_id, model_id=model_id),
        )

    def _update_role_download_button(self, role: str) -> None:
        combo = self._role_combos.get(role)
        btn = self._role_download_buttons.get(role)
        svc = getattr(self._ctx, "ai_setup_service", None)
        if not combo or not btn or not svc:
            return
        model_id = combo.currentData() or ""
        installed = {model.id for model in getattr(self, '_setup_snapshot', {}).get('installed_models') or []}
        catalog_ids = {m.id for m in svc.get_active_backend_catalog()} if hasattr(svc, "get_active_backend_catalog") else set()
        can_download = bool(model_id and model_id in catalog_ids and model_id not in installed)
        if role in self._role_downloads_by_model.get(model_id, set()):
            btn.setVisible(True)
            btn.setEnabled(False)
            if btn.text() == "Download":
                btn.setText("Downloading")
            return
        btn.setText("Download")
        btn.setVisible(can_download)
        btn.setEnabled(can_download)

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
