"""AI Setup card: which method labels argument roles, and whether to enhance it.

Makes the classifier's real state explicit: the built-in heuristic is always
active and produces labels; the optional enhancer and custom model are
separate, optional installs. This replaces the old ambiguous "no sentence
classifier installed" status that contradicted labelled artifacts.
"""
from __future__ import annotations

from typing import Any, Dict, List

from PySide6.QtWidgets import QCheckBox, QFrame, QLabel, QVBoxLayout

ENHANCER_SETTING = "use_sentence_role_enhancer"


class SentenceRoleSetupCard(QFrame):
    def __init__(self, app_context: Any, theme: Dict[str, str], parent=None) -> None:
        super().__init__(parent)
        self._ctx = app_context
        self.setObjectName("SentenceRoleSetupCard")
        self.setStyleSheet(
            f"QFrame#SentenceRoleSetupCard {{ background: {theme.get('bg_panel', '#2b2b2b')}; "
            f"border: 1px solid {theme.get('border', '#444')}; border-radius: 6px; }}"
        )
        layout = QVBoxLayout(self)
        title = QLabel("Sentence-role classification (argument roles)")
        title.setStyleSheet("font-weight: 700; background: transparent;")
        layout.addWidget(title)
        self.status = QLabel()
        self.status.setWordWrap(True)
        self.status.setAccessibleName("Active sentence-role method")
        self.status.setStyleSheet("background: transparent;")
        layout.addWidget(self.status)
        self.use_enhancer = QCheckBox(
            "Use the sentence-role enhancer for sentences the heuristic leaves unknown"
        )
        self.use_enhancer.setToolTip(
            "Only scores eligible uncertain prose and may abstain. Changing this marks the "
            "Argument roles stage as needing a rerun."
        )
        self.use_enhancer.toggled.connect(self._save_setting)
        layout.addWidget(self.use_enhancer)
        self.refresh()

    def refresh(self) -> None:
        if getattr(self._ctx, 'model_readiness_service', None):
            from gui.components.artifact_tasks import ArtifactTaskRunner
            if not hasattr(self, '_reader'):
                self._reader = ArtifactTaskRunner(self._ctx, self, service_name='model_readiness_service')
                self._reader.completed.connect(self._ready)
                self._reader.failed.connect(self.status.setText)
                self.destroyed.connect(self._reader.shutdown)
            self._reader.start('read', catalog=False)
            return
        self.apply_snapshot({})

    def _ready(self, operation):
        result, self._reader.result = self._reader.result or {}, None
        self.apply_snapshot(result)

    def apply_snapshot(self, snapshot):
        policy = getattr(self._ctx, 'processing_policy_service', None)
        enabled = bool(snapshot.get('enhancer_enabled', True))
        status = snapshot.get('sentence_status') or {}
        self.status.setText(status_text(status))
        self.use_enhancer.blockSignals(True)
        self.use_enhancer.setChecked(enabled)
        self.use_enhancer.setEnabled(bool(status.get("enhancer_installed")) and policy is not None)
        self.use_enhancer.blockSignals(False)

    def _save_setting(self, checked: bool) -> None:
        if getattr(self, '_reader', None):
            self._reader.start('set_enhancer', enabled=bool(checked))
            return
        policy = getattr(self._ctx, "processing_policy_service", None)
        if policy:
            policy.set_project_settings({ENHANCER_SETTING: bool(checked)})
        self.refresh()


def status_text(status: Dict[str, Any]) -> str:
    if not status:
        return "Sentence-role classification is unavailable."
    lines = [f"Active now: {status.get('active', 'Built-in heuristic (no model)')}."]
    if not status.get("custom_model_installed") and not status.get("enhancer_installed"):
        lines.append(
            "The built-in rules keep background searchable and publish supported argument roles. "
            "No optional learned model is installed — that is not an error."
        )
    elif status.get("enhancer_installed") and not status.get("enhancer_enabled"):
        lines.append("The enhancer is installed but switched off for this project.")
    if status.get("custom_model_error"):
        lines.append(f"Custom model failed to load: {status['custom_model_error']}")
    return " ".join(lines)


def catalog_detail_labels(dep: Dict[str, Any], muted: str) -> List[QLabel]:
    """Hardware, evaluation and license lines for a model catalog row."""
    labels = []
    for caption, key in (("Hardware", "hardware"), ("Evaluation", "evaluation"),
                         ("License", "license")):
        if dep.get(key):
            label = QLabel(f"{caption}: {dep[key]}")
            label.setWordWrap(True)
            label.setStyleSheet(f"color: {muted}; background: transparent; font-size: 11px;")
            labels.append(label)
    return labels
