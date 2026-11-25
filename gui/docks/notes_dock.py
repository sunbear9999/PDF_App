import os
import html
import uuid
import shiboken6
from PySide6.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QLabel,
                             QPushButton, QScrollArea, QFrame, QComboBox,
                             QMenu, QTabWidget, QTextEdit, QSizePolicy,
                             QInputDialog)
from PySide6.QtCore import Qt, QThread, QTimer, Signal

from gui.components.dialogs.tag_manager_dialog import TagAssignmentDialog
from gui.managers.dialog_manager import exec_as_modal, get_for_widget
from gui.utils.layout_helpers import make_shrinkable
from core.events.event_bus import EventBus
from core.events.domains.document_events import AnnotationIntent, AnnotationPayload, DocumentEvent, DocumentEventPayload, DocumentIntent, DocumentPayload
from core.events.domains.metadata_events import NotesEvent, NotesEventPayload, NotesIntent, NotesPayload
from core.events.domains.discovery_events import DiscoveryEvent, DiscoveryEventPayload, DiscoveryIntent, DiscoveryPayload
from core.events.domains.workspace_events import WorkspaceIntent, WorkspacePayload
from core.extraction.deterministic import ExtractedMention, ExtractedEntityGroup
from core.models.ontology_model import EntityIntent, EntityPayload
from core.ontology.registry import OntologyRegistry
from gui.components.dialogs.entity_editor_dialog import EntityEditorDialog

_ENTITY_TYPE_LABELS = {
    "PERSON": "People",
    "ORG": "Organizations",
    "LOCATION": "Places",
    "GPE": "Places",
    "LOC": "Places",
    "FAC": "Places",
    "NORP": "Groups & nationalities",
    "EVENT": "Events",
    "WORK": "Creative works",
    "WORK_OF_ART": "Creative works",
    "METHOD": "Methods",
    "DATASET": "Datasets",
    "CONCEPT": "Concepts",
    "LAW": "Laws",
    "PRODUCT": "Products",
    "METRIC": "Metrics",
    "MONEY": "Money",
    "QUANTITY": "Quantities",
    "DATE": "Dates",
    "entity.source": "Citations",
}
# Grace period for a discovery load worker to notice requestInterruption()
# before its owning widget is destroyed.
_WORKER_SHUTDOWN_MS = 3000
NOTE_PREVIEW_CHARS = 3000


def _friendly_entity_type(entity_type: str) -> str:
    return _ENTITY_TYPE_LABELS.get(entity_type, entity_type.replace("_", " ").title())


class NoteBubble(QFrame):
    def __init__(self, tab, data_dict):
        super().__init__(tab if isinstance(tab, QWidget) else None)
        self.tab = tab
        self.pdf_path = data_dict["pdf_path"]
        self.page_num = data_dict["page_num"]
        self.annot_id = data_dict["annot_id"]
        self.is_ai = data_dict["is_ai"]
        self.source_type = data_dict.get("source_type", "pdf")
        self._legacy_selection = data_dict.get('legacy_selection')
        self._legacy_body = data_dict.get('content') or ''
        self.linked_actions = None
        if data_dict.get("project_note"):
            from gui.components.linked_note_actions import LinkedNoteActions
            self.linked_actions = LinkedNoteActions(getattr(tab, "_ctx", None), data_dict, self)
        color = data_dict["color"]

        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 10, 10, 10)

        header_layout = QHBoxLayout()
        doc_name = os.path.basename(self.pdf_path)
        if self.source_type in ("video", "audio"):
            icon = "🎬" if self.source_type == "video" else "🎧"
            self.lbl_page = QLabel(f"{icon} {doc_name} - {self._fmt_time(self.page_num)}")
        elif self.source_type == 'pdf':
            self.lbl_page = QLabel(f"📄 {doc_name} - Pg {self.page_num + 1}")
        else:
            self.lbl_page = QLabel(f"📄 {doc_name} — recorded source selection")
        header_layout.addWidget(self.lbl_page)

        # Render Tag Dots from passed data
        for t in data_dict.get("tags", []):
            tag_name = t.get("name", "")
            tag_color = t.get("color", "#808080")
            btn_tag = QPushButton()
            btn_tag.setFixedSize(12, 12)
            btn_tag.setStyleSheet(f"background-color: {tag_color}; border-radius: 6px; border: none;")
            btn_tag.setToolTip(f"Filter by tag: {tag_name}")
            btn_tag.setCursor(Qt.CursorShape.PointingHandCursor)
            btn_tag.clicked.connect(lambda checked, name=tag_name: self.tab.apply_tag_filter(name))
            header_layout.addWidget(btn_tag)

        if self.is_ai:
            self.lbl_ai = QLabel("🤖 AI Note")
            header_layout.addWidget(self.lbl_ai)

        header_layout.addStretch()

        colors = [("Yellow", (1.0, 0.9, 0.0)), ("Green", (0.0, 0.8, 0.4)), ("Blue", (0.2, 0.6, 1.0)), ("Purple", (0.7, 0.4, 1.0)), ("Red", (1.0, 0.3, 0.3))]
        def is_close(c1, c2):
            if not c1 or not c2 or len(c1) != len(c2): return False
            return all(abs(c1[i] - c2[i]) < 0.05 for i in range(len(c1)))

        for c_name, c_val in colors:
            btn_c = QPushButton()
            btn_c.setFixedSize(16, 16)
            color_val = color
            if isinstance(color_val, str) and color_val.startswith("#"):
                color_val = tuple(int(color_val[i:i + 2], 16) / 255 for i in (1, 3, 5))
            border = "2px solid white" if is_close(c_val, color_val) else "none"
            btn_c.setStyleSheet(f"background-color: rgb({int(c_val[0]*255)}, {int(c_val[1]*255)}, {int(c_val[2]*255)}); border-radius: 8px; border: {border};")
            btn_c.setToolTip(f"Change to {c_name}")
            btn_c.setAccessibleName(f"Change project note color to {c_name}")
            btn_c.clicked.connect(lambda checked, c=c_val: self.linked_actions.set_color(c) if self.linked_actions else self.tab.bus.notes_action_requested.emit(
                NotesIntent.CHANGE_COLOR,
                NotesPayload(pdf_path=self.pdf_path, page_num=self.page_num, annot_id=self.annot_id, color=c),
            ))
            if not self.linked_actions:
                btn_c.setEnabled(False)
                btn_c.setToolTip('Adopt or reselect this legacy observation to edit a project copy.')
            header_layout.addWidget(btn_c)

        header_layout.addSpacing(10)

        self.btn_del = QPushButton("✖")
        self.btn_del.setFixedSize(24, 24)
        self.btn_del.setAccessibleName("Delete project note")
        self.btn_del.clicked.connect(lambda: self.linked_actions.delete() if self.linked_actions else self.tab.bus.notes_action_requested.emit(
            NotesIntent.DELETE,
            NotesPayload(pdf_path=self.pdf_path, page_num=self.page_num, annot_id=self.annot_id),
        ))
        if not self.linked_actions:
            self.btn_del.setEnabled(False)
            self.btn_del.setToolTip('The physical original is read only. Adopt or reselect to manage a project copy.')
        header_layout.addWidget(self.btn_del)
        layout.addLayout(header_layout)

        self.lbl_subj = QLabel(f'"{data_dict["subject"][:NOTE_PREVIEW_CHARS]}"')
        self.lbl_subj.setToolTip('Source quotation preview. Inspect the recorded reference to read the complete passage.')
        self.lbl_subj.setTextFormat(Qt.TextFormat.PlainText)
        self.lbl_subj.setWordWrap(True)
        self.lbl_subj.setTextInteractionFlags(Qt.TextInteractionFlag.NoTextInteraction)
        layout.addWidget(self.lbl_subj)

        if data_dict["content"]:
            self.lbl_content = QLabel(data_dict['content'][:NOTE_PREVIEW_CHARS])
            self.lbl_content.setToolTip('Commentary preview. Edit/Inspect opens the complete saved note.')
            self.lbl_content.setTextFormat(Qt.TextFormat.PlainText)
            self.lbl_content.setWordWrap(True)
            self.lbl_content.setTextInteractionFlags(Qt.TextInteractionFlag.NoTextInteraction)
            layout.addWidget(self.lbl_content)

        ctx = getattr(self.tab, '_ctx', None)
        if self.linked_actions:
            layout.addWidget(self.linked_actions.edit_button)
            layout.addWidget(self.linked_actions.status)
        if self._legacy_selection:
            from gui.components.artifact_actions import bind_artifact_action
            label = QLabel('Physical annotation · original authorship unrecorded', self)
            label.setWordWrap(True)
            layout.addWidget(label)
            adopt = QPushButton('Adopt as project note…', self)
            adopt.setToolTip('Preview a guarded copy. The physical annotation is retained; existing project notes are not overwritten.')
            adopt.clicked.connect(self.adopt_legacy)
            layout.addWidget(adopt)
            bind_artifact_action(ctx, self, 'intelligence.note.adopt', 'Adopt Physical Annotation',
                                 self.adopt_legacy, mount='toolbar:notes')
        self.reference_widget = None
        if ctx is not None:
            from gui.components.artifact_reference import ArtifactReferenceWidget
            self.reference_widget = ArtifactReferenceWidget(ctx, data_dict.get("artifact_ref") or {}, self)
            layout.addWidget(self.reference_widget)
        tm = getattr(ctx, 'theme_manager', None) if ctx else None
        if tm:
            self.apply_theme(tm.get_theme())

    def adopt_legacy(self):
        if self._legacy_selection:
            from gui.components.dialogs.reference_note_dialog import open_selection_note
            open_selection_note(getattr(self.tab, '_ctx', None), self._legacy_selection,
                                self._legacy_body, parent=self)

    def apply_theme(self, theme):
        pass

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            if self.reference_widget is not None and self.reference_widget.reference.artifact_id:
                self.reference_widget.jump()
                super().mousePressEvent(event)
                return
            if self.source_type in ("video", "audio"):
                ctx = getattr(self.tab, "_ctx", None)
                viewer = getattr(ctx, "viewer", None) if ctx else None
                source = None
                pm = getattr(ctx, "project_manager", None) if ctx else None
                if pm and hasattr(pm, "get_source_entity_by_path"):
                    source = pm.get_source_entity_by_path(self.pdf_path)
                if viewer and hasattr(viewer, "jump_to_video"):
                    viewer.jump_to_video(source_id=source.id if source else None, path=self.pdf_path, timestamp=self.page_num or 0)
            else:
                self.tab.bus.document_action_requested.emit(DocumentIntent.OPEN, DocumentPayload(path=self.pdf_path))
                self.tab.bus.annotation_action_requested.emit(AnnotationIntent.JUMP_TO_PAGE, AnnotationPayload(page_num=self.page_num))
        super().mousePressEvent(event)

    def _fmt_time(self, seconds):
        seconds = int(seconds or 0)
        return f"{seconds // 60:02d}:{seconds % 60:02d}"

    def contextMenuEvent(self, event):
        menu = QMenu(self)
        ctx = getattr(self.tab, '_ctx', None)
        tm = getattr(ctx, 'theme_manager', None) if ctx else None
        if tm:
            theme = tm.get_theme()
            menu.setStyleSheet(f"QMenu {{ background-color: {theme['bg_panel']}; color: {theme['text_main']}; border: 1px solid {theme['border']}; padding: 5px; font-weight: bold; }} QMenu::item:selected {{ background-color: {theme['accent']}; color: #ffffff; }}")

        tag_action = menu.addAction("🏷️ Manage Tags")
        tag_action.triggered.connect(self.manage_tags)
        # Inject DockActionSpec actions for the notes annotation mount
        try:
            from core.engine.dock_mounts import NOTES_CONTEXT_MENU_ANNOTATION
            from gui.components.dock_action_mixin import inject_dock_actions_into_menu
            inject_dock_actions_into_menu(
                menu,
                NOTES_CONTEXT_MENU_ANNOTATION,
                dock_id="notes_dock",
                zone="context_menu:annotation",
                app_context=ctx,
                source_path=self.pdf_path,
                extra={"annot_id": self.annot_id, "page_num": self.page_num},
            )
        except Exception:
            pass
        menu.exec(event.globalPos())

    def manage_tags(self):
        dlg = TagAssignmentDialog(self.annot_id, "node", self)
        annot_id = self.annot_id

        def _on_accepted():
            self.tab.bus.notes_action_requested.emit(
                NotesIntent.SYNC_TAGS, NotesPayload(annot_id=annot_id)
            )

        dlg.accepted.connect(_on_accepted)
        dm = get_for_widget(self)
        if dm:
            dm.show_instance(dlg)
        else:
            if exec_as_modal(dlg):
                _on_accepted()


# --- NEW: Discovered Entity Bubble UI ---
class DiscoveredEntityBubble(QFrame):
    def __init__(self, tab, entity_model, theme=None):
        super().__init__()
        self.tab = tab
        self.entity = entity_model
        self.theme = theme
        
        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 10, 10, 10)

        header_layout = QHBoxLayout()
        registry = OntologyRegistry()
        blueprint = registry.get_entity_blueprint(self.entity.entity_type)
        
        doc_name = os.path.basename(self.entity.properties.get("pdf_path") or self.entity.origin_id or "Unknown")
        self.lbl_type = QLabel(f"✨ {blueprint.display_name} | 📄 {doc_name}")
        self.lbl_type.setStyleSheet("font-weight: bold; color: #b366ff;")
        header_layout.addWidget(self.lbl_type)
        header_layout.addStretch()

        # --- Action Controls (Context Button Removed) ---
        self.btn_jump = QPushButton("↗️ Jump")
        self.btn_jump.setToolTip("Open document and jump exactly to this text")
        self.btn_jump.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_jump.clicked.connect(self._jump_to_source)
        header_layout.addWidget(self.btn_jump)

        self.btn_edit_save = QPushButton("✏️ Edit & Save")
        self.btn_edit_save.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_edit_save.clicked.connect(self._edit_and_save_entity)
        header_layout.addWidget(self.btn_edit_save)

        self.btn_reject = QPushButton("✖")
        self.btn_reject.setToolTip("Discard this extraction")
        self.btn_reject.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_reject.clicked.connect(self._reject_entity)
        header_layout.addWidget(self.btn_reject)

        layout.addLayout(header_layout)

        # --- Context Content Formatting ---
        target_text = self.entity.properties.get("text") or self.entity.properties.get("title") or ""
        context_text = self.entity.properties.get("context", "")

        # Fallback if context is missing
        if not context_text or context_text == "Context not available.":
            display_html = html.escape(target_text)
        else:
            # Escape HTML to prevent rendering bugs from math/code symbols in PDFs
            safe_context = html.escape(context_text)
            safe_target = html.escape(target_text)
            
            # Bold the specific extracted entity inside the paragraph
            if safe_target:
                # Use a slightly tinted bold so the exact extraction pops instantly
                accent_color = theme['accent'] if theme and 'accent' in theme else "#b366ff"
                highlighted_target = f"<strong style='color: {accent_color}; font-size: 110%;'>{safe_target}</strong>"
                display_html = safe_context.replace(safe_target, highlighted_target)
            else:
                display_html = safe_context

        # Set up the QTextEdit to render the HTML
        self.txt_content = QTextEdit()
        self.txt_content.setReadOnly(True)
        self.txt_content.setHtml(display_html)
        self.txt_content.setMinimumHeight(60)
        self.txt_content.setMaximumHeight(120)
        self.txt_content.setFrameShape(QFrame.Shape.NoFrame)
        layout.addWidget(self.txt_content)

        if theme:
            self.apply_theme(theme)

    def _jump_to_source(self):
        text_to_find = self.entity.properties.get("text", "")
        doc_path = self.entity.properties.get("pdf_path") or self.entity.origin_id

        ctx = getattr(self.tab, '_ctx', None)
        viewer = getattr(ctx, 'viewer', None) if ctx else None
        if text_to_find and viewer:
            viewer.jump_to_source(os.path.basename(doc_path), text_to_find)
        else:
            # Fallback for empty text: just open the document and page
            if doc_path:
                self.tab.bus.document_action_requested.emit(DocumentIntent.OPEN, DocumentPayload(path=doc_path))
                page_num = self.entity.properties.get("page_num")
                if page_num is not None:
                    self.tab.bus.annotation_action_requested.emit(AnnotationIntent.JUMP_TO_PAGE, AnnotationPayload(page_num=page_num))

    def _edit_and_save_entity(self):
        dialog = EntityEditorDialog(self.entity, self.theme, self)
        entity_id = self.entity.id

        def _on_accepted():
            try:
                updated_props = dialog.get_updated_properties()
                self.tab.bus.entity_action_requested.emit(
                    EntityIntent.UPDATE_PROPERTIES, EntityPayload(entity_id=entity_id, data=updated_props)
                )
                self.tab.bus.entity_action_requested.emit(
                    EntityIntent.VERIFY, EntityPayload(entity_id=entity_id)
                )
            except RuntimeError:
                pass

        dialog.accepted.connect(_on_accepted)
        dm = get_for_widget(self)
        if dm:
            dm.show_instance(dialog)
        else:
            if exec_as_modal(dialog):
                _on_accepted()

    def _reject_entity(self):
        self.tab.bus.entity_action_requested.emit(EntityIntent.PURGE_GLOBALLY, EntityPayload(entity_id=self.entity.id))

    def apply_theme(self, theme):
        self.setStyleSheet(f"background-color: {theme['bg_panel']}; border: 1px solid {theme['border']}; border-radius: 6px;")
        
        # Style the text edit area to blend with the bubble
        self.txt_content.setStyleSheet(f"background-color: {theme['bg_input']}; color: {theme['text_main']}; border-radius: 4px; padding: 4px;")
        
        # FIX: Wrap standard properties inside the QPushButton selector
        btn_style = f"background-color: transparent; color: {theme['text_main']}; border: 1px solid {theme['border']}; padding: 4px 8px; border-radius: 4px;"
        btn_hover = f"QPushButton:hover {{ background-color: {theme['bg_input']}; }}"
        safe_stylesheet = f"QPushButton {{ {btn_style} }} {btn_hover}"
        
        self.btn_jump.setStyleSheet(safe_stylesheet)
        self.btn_reject.setStyleSheet(safe_stylesheet)
        
        self.btn_edit_save.setStyleSheet(f"background-color: {theme['accent']}; color: #1e1e1e; border: none; padding: 4px 8px; border-radius: 4px; font-weight: bold;")
# ---------------------------------------------------------------------------
# Discovery Tab: New deterministic extraction UI
# ---------------------------------------------------------------------------

class MentionRow(QFrame):
    """One occurrence row: context sentence with entity highlighted + page + Jump."""
    def __init__(self, tab, mention, entity_text: str, source_path: str, theme=None):
        super().__init__()
        self._tab = tab
        self._mention = mention
        self._source_path = getattr(mention, "source_path", "") or source_path

        layout = QHBoxLayout(self)
        layout.setContentsMargins(6, 4, 6, 4)
        layout.setSpacing(6)

        doc_prefix = ""
        if getattr(mention, "show_source_name", False) and self._source_path:
            doc_prefix = f"{os.path.basename(self._source_path)} · "
        page_lbl = QLabel(f"{doc_prefix}p.{mention.page_num + 1}")
        page_lbl.setMinimumWidth(30)
        page_lbl.setStyleSheet("color: #888; font-size: 10px;")
        layout.addWidget(page_lbl)

        safe_ctx = html.escape(mention.context[:300])
        safe_ent = html.escape(entity_text)
        accent = (theme or {}).get("accent", "#b366ff")
        if safe_ent:
            highlighted = f"<b style='color:{accent}'>{safe_ent}</b>"
            ctx_html = safe_ctx.replace(safe_ent, highlighted, 1)
        else:
            ctx_html = safe_ctx

        lbl = QLabel(ctx_html)
        lbl.setWordWrap(True)
        lbl.setTextFormat(Qt.TextFormat.RichText)
        lbl.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        layout.addWidget(lbl)

        btn_jump = QPushButton("↗")
        btn_jump.setFixedSize(26, 26)
        btn_jump.setToolTip("Jump to this occurrence in the document")
        btn_jump.setCursor(Qt.CursorShape.PointingHandCursor)
        btn_jump.clicked.connect(self._jump)
        layout.addWidget(btn_jump)

        if theme:
            self.apply_theme(theme)

    def _jump(self):
        ctx = getattr(self._tab, "_ctx", None)
        viewer = getattr(ctx, "viewer", None) if ctx else None
        if not viewer:
            return
        # Jump to the page directly — more reliable than text search for
        # short citation strings like "[1]" that appear many times in a document.
        page = self._mention.page_num
        if self._source_path:
            self._tab.bus.document_action_requested.emit(
                DocumentIntent.OPEN,
                DocumentPayload(path=self._source_path, source_type="pdf"),
            )
            if hasattr(viewer, "jump_to_page"):
                QTimer.singleShot(120, lambda p=page: viewer.jump_to_page(p))
        elif hasattr(viewer, "jump_to_page"):
            viewer.jump_to_page(page)
        elif self._source_path and hasattr(viewer, "jump_to_source"):
            viewer.jump_to_source(os.path.basename(self._source_path), self._mention.context[:60])

    def apply_theme(self, theme):
        self.setStyleSheet(
            f"background-color: {theme['bg_input']}; border-radius: 4px; margin: 1px 0;"
        )


def _build_refs_rows(content_widget, refs, bus):
    """Populate *content_widget*'s layout with per-occurrence rows for each document in *refs*.

    Each doc gets a header label followed by up to 5 occurrence rows showing a context
    snippet and a Jump button that opens the document at the matching page.
    """
    layout = content_widget.layout()
    while layout.count():
        child = layout.takeAt(0)
        if child.widget():
            child.widget().deleteLater()

    if not refs:
        lbl = QLabel("No mentions found in project.")
        lbl.setStyleSheet("color: #888; font-size: 11px;")
        layout.addWidget(lbl)
        return

    for ref in refs:
        doc_name = ref.get("doc_name", "Unknown")
        count = ref.get("mention_count", 0)
        doc_path = ref.get("doc_path", "")

        hdr = QLabel(
            f"<b>📄 {html.escape(doc_name)}</b>"
            f"<span style='color:#888; font-size:11px;'>"
            f"  — {count} mention{'s' if count != 1 else ''}</span>"
        )
        hdr.setTextFormat(Qt.TextFormat.RichText)
        hdr.setWordWrap(True)
        layout.addWidget(hdr)

        occurrences = ref.get("occurrences", [])
        if not occurrences:
            occurrences = [{"page_num": 0, "context": c} for c in ref.get("contexts", [])[:5]]

        for occ in occurrences[:5]:
            pg = occ.get("page_num", 0)
            ctx = occ.get("context", "").strip()

            row = QWidget()
            row_layout = QHBoxLayout(row)
            row_layout.setContentsMargins(8, 0, 0, 0)
            row_layout.setSpacing(4)

            display = f"p.{pg + 1}  {ctx[:120]}" if ctx else f"p.{pg + 1}"
            ctx_lbl = QLabel(display)
            ctx_lbl.setWordWrap(True)
            ctx_lbl.setStyleSheet("color: #aaa; font-size: 10px; font-style: italic;")
            ctx_lbl.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
            row_layout.addWidget(ctx_lbl)

            jump_btn = QPushButton("Jump")
            jump_btn.setFixedSize(42, 20)
            jump_btn.setCursor(Qt.CursorShape.PointingHandCursor)
            jump_btn.setStyleSheet(
                "QPushButton { background: transparent; color: #6ac; border: 1px solid #6ac; "
                "border-radius: 3px; font-size: 10px; padding: 0; }"
                "QPushButton:hover { background: #6ac; color: #111; }"
            )
            def _jump(checked=False, path=doc_path, p=pg):
                bus.document_action_requested.emit(
                    DocumentIntent.OPEN,
                    DocumentPayload(path=path, source_type="pdf"),
                )
                if p is not None:
                    QTimer.singleShot(120, lambda _p=p, _path=path: bus.document_action_requested.emit(
                        DocumentIntent.JUMP_TO_LOCATION,
                        DocumentPayload(path=_path, page_num=_p),
                    ))
            jump_btn.clicked.connect(_jump)
            row_layout.addWidget(jump_btn)

            layout.addWidget(row)

    layout.addStretch()


class EntityGroupCard(QFrame):
    """Collapsible card for one deduplicated entity (people, dates, generic)."""
    def __init__(self, tab, group, source_path: str, theme=None):
        super().__init__()
        self._tab = tab
        self._group = group
        self._source_path = source_path
        self._theme = theme
        self._expanded = False
        self._build_ui()
        if theme:
            self.apply_theme(theme)

    def _build_ui(self):
        self._outer = QVBoxLayout(self)
        self._outer.setContentsMargins(10, 8, 10, 8)
        self._outer.setSpacing(4)

        # Header
        header = QHBoxLayout()
        count = len(self._group.mentions)
        type_label = _friendly_entity_type(self._group.entity_type)
        self._lbl_type = QLabel(type_label)
        self._lbl_type.setToolTip(
            "Entity category used by the shared intelligence extractor."
        )
        self._lbl_type.setStyleSheet("color: #888; font-size: 10px;")
        header.addWidget(self._lbl_type)
        self._lbl_name = QLabel(f"<b>{html.escape(self._group.canonical_text)}</b>")
        self._lbl_name.setTextFormat(Qt.TextFormat.RichText)
        self._lbl_name.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        header.addWidget(self._lbl_name)

        self._lbl_count = QLabel(f"{count}×")
        source_count = int(self._group.extra_properties.get("source_count") or 0)
        if source_count > 1:
            self._lbl_count.setText(f"{count}× · {source_count} sources")
            self._lbl_count.setMinimumWidth(105)
        else:
            self._lbl_count.setFixedWidth(30)
        self._lbl_count.setStyleSheet("color: #888; font-size: 11px;")
        header.addWidget(self._lbl_count)

        self._btn_expand = QPushButton("▶")
        self._btn_expand.setFixedSize(24, 24)
        self._btn_expand.setToolTip("Expand / collapse occurrences")
        self._btn_expand.setCursor(Qt.CursorShape.PointingHandCursor)
        self._btn_expand.clicked.connect(self._toggle)
        header.addWidget(self._btn_expand)

        self._btn_refs = QPushButton("🔗")
        self._btn_refs.setFixedSize(24, 24)
        self._btn_refs.setToolTip("Find mentions in other documents")
        self._btn_refs.setCursor(Qt.CursorShape.PointingHandCursor)
        self._btn_refs.clicked.connect(self._find_references)
        header.addWidget(self._btn_refs)

        self._btn_save = QPushButton("💾 Save")
        self._btn_save.setFixedHeight(24)
        self._btn_save.setCursor(Qt.CursorShape.PointingHandCursor)
        self._btn_save.clicked.connect(self._save)
        header.addWidget(self._btn_save)

        self._btn_dismiss = QPushButton("✖")
        self._btn_dismiss.setFixedSize(24, 24)
        self._btn_dismiss.setToolTip("Dismiss (don't save)")
        self._btn_dismiss.setCursor(Qt.CursorShape.PointingHandCursor)
        self._btn_dismiss.clicked.connect(self._dismiss)
        header.addWidget(self._btn_dismiss)

        self._outer.addLayout(header)

        # Editable description field — shown inline below header, always visible
        self._desc_edit = QTextEdit()
        self._desc_edit.setPlaceholderText(
            "Add a description, e.g. 'Major pharmaceutical company, cited for 2020 trial results…'"
        )
        initial_desc = self._group.extra_properties.get("description", "")
        self._desc_edit.setPlainText(initial_desc)
        self._desc_edit.setFixedHeight(54)
        self._desc_edit.setAcceptRichText(False)
        self._desc_edit.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self._desc_edit.textChanged.connect(self._on_desc_changed)
        self._outer.addWidget(self._desc_edit)

        # Collapsible body (mention rows)
        self._body = QWidget()
        body_layout = QVBoxLayout(self._body)
        body_layout.setContentsMargins(0, 4, 0, 0)
        body_layout.setSpacing(2)
        for mention in self._group.mentions:
            row = MentionRow(self._tab, mention, self._group.canonical_text, self._source_path, self._theme)
            body_layout.addWidget(row)
        self._body.setVisible(False)
        self._outer.addWidget(self._body)

        # Inline references panel (hidden until 🔗 clicked)
        self._refs_panel = QWidget()
        self._refs_panel.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        refs_layout = QVBoxLayout(self._refs_panel)
        refs_layout.setContentsMargins(0, 0, 0, 0)
        refs_layout.setSpacing(2)
        self._refs_label = QLabel("Searching…")
        self._refs_label.setStyleSheet("color: #888; font-size: 10px; padding: 0px;")
        self._refs_label.setMaximumHeight(14)
        self._refs_label.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        refs_layout.addWidget(self._refs_label)
        self._refs_content = QWidget()
        self._refs_content_layout = QVBoxLayout(self._refs_content)
        self._refs_content_layout.setContentsMargins(0, 0, 0, 0)
        self._refs_content_layout.setSpacing(3)
        self._refs_scroll = QScrollArea()
        self._refs_scroll.setWidgetResizable(True)
        self._refs_scroll.setWidget(self._refs_content)
        self._refs_scroll.setFixedHeight(150)
        self._refs_scroll.setFrameShape(QFrame.Shape.NoFrame)
        refs_layout.addWidget(self._refs_scroll)
        self._refs_panel.setVisible(False)
        self._outer.addWidget(self._refs_panel)

    def _toggle(self):
        self._expanded = not self._expanded
        self._body.setVisible(self._expanded)
        self._btn_expand.setText("▼" if self._expanded else "▶")

    def _on_desc_changed(self):
        """Keep extra_properties in sync so Save and LLM analysis both see the latest description."""
        self._group.extra_properties["description"] = self._desc_edit.toPlainText()

    def _save(self):
        self._tab.bus.discovery_action_requested.emit(
            DiscoveryIntent.SAVE_ENTITY,
            DiscoveryPayload(
                source_path=self._source_path,
                entity_type=self._group.entity_type,
                entity_groups=[self._group],
            ),
        )
        self.setVisible(False)

    def _dismiss(self):
        self.setVisible(False)

    def _find_references(self):
        """Request cross-document references for this entity from the backend."""
        self._refs_label.setText("Searching…")
        self._refs_panel.setVisible(True)
        # Notify the parent DiscoveryTabWidget so it can route the response back here
        if hasattr(self._tab, "_set_pending_refs_card"):
            self._tab._set_pending_refs_card(self)
        self._tab.bus.discovery_action_requested.emit(
            DiscoveryIntent.FIND_REFERENCES,
            DiscoveryPayload(
                entity_name=self._group.canonical_text,
                entity_type=self._group.entity_type,
            ),
        )

    def show_references(self, references):
        """Display per-occurrence reference results with context snippets and Jump buttons."""
        self._refs_label.setText(
            f"Found in {len(references)} document(s):" if references else "No other documents found."
        )
        _build_refs_rows(self._refs_content, references, self._tab.bus)

    def apply_theme(self, theme):
        self._theme = theme
        self.setStyleSheet(
            f"QFrame {{ background-color: {theme['bg_panel']}; border: 1px solid {theme['border']}; border-radius: 6px; }}"
        )
        btn_style = (
            f"QPushButton {{ background-color: transparent; color: {theme['text_main']}; "
            f"border: 1px solid {theme['border']}; padding: 2px 6px; border-radius: 4px; }}"
            f"QPushButton:hover {{ background-color: {theme['bg_input']}; }}"
        )
        self._btn_expand.setStyleSheet(btn_style)
        self._btn_dismiss.setStyleSheet(btn_style)
        self._btn_refs.setStyleSheet(btn_style)
        self._btn_save.setStyleSheet(
            f"QPushButton {{ background-color: {theme['accent']}; color: #1e1e1e; "
            f"border: none; padding: 2px 8px; border-radius: 4px; font-weight: bold; }}"
        )
        self._lbl_name.setStyleSheet(f"color: {theme['text_main']};")
        self._lbl_type.setStyleSheet(
            f"color: {theme.get('text_muted', '#888')}; font-size: 10px;"
        )
        if hasattr(self, "_desc_edit"):
            self._desc_edit.setStyleSheet(
                f"QTextEdit {{ background-color: {theme['bg_input']}; color: {theme['text_main']}; "
                f"border: 1px solid {theme['border']}; border-radius: 4px; padding: 3px; font-size: 11px; }}"
            )
        # Refresh mention rows
        body_layout = self._body.layout()
        if body_layout:
            for i in range(body_layout.count()):
                w = body_layout.itemAt(i).widget()
                if isinstance(w, MentionRow):
                    w.apply_theme(theme)


class CitationGroupCard(QFrame):
    """Specialized collapsible card for citation entities (entity.source)."""
    def __init__(self, tab, group, source_path: str, theme=None):
        super().__init__()
        self._tab = tab
        self._group = group
        self._source_path = source_path
        self._theme = theme
        self._expanded = bool(group.mentions)  # start expanded when there are quotes
        self._build_ui()
        if theme:
            self.apply_theme(theme)

    def _build_ui(self):
        self._outer = QVBoxLayout(self)
        self._outer.setContentsMargins(10, 8, 10, 8)
        self._outer.setSpacing(4)

        props = self._group.extra_properties
        authors = props.get("authors") or []
        year = props.get("year", "")
        title = str(self._group.canonical_text or "Bibliography entry")
        journal = str(props.get("journal") or props.get("source_title") or "")

        author_str = ", ".join(str(a) for a in authors[:3])
        if len(authors) > 3:
            author_str += " et al."
        header_text = f"<b>{html.escape(title)}</b>"
        meta_text = f"{html.escape(author_str)} ({html.escape(str(year or 'n.d.'))})"
        if journal:
            meta_text += f" — <i>{html.escape(journal[:60])}</i>"

        # Header row
        header = QHBoxLayout()
        self._lbl_title = QLabel(header_text)
        self._lbl_title.setTextFormat(Qt.TextFormat.RichText)
        self._lbl_title.setWordWrap(True)
        self._lbl_title.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        header.addWidget(self._lbl_title)

        count = len(self._group.mentions)
        self._lbl_count = QLabel(f"{count} in-text" if count else "bib only")
        self._lbl_count.setStyleSheet("color: #888; font-size: 11px;")
        header.addWidget(self._lbl_count)

        expand_icon = "▼" if self._expanded else "▶"
        self._btn_expand = QPushButton(expand_icon)
        self._btn_expand.setFixedSize(24, 24)
        self._btn_expand.setToolTip("Show/hide citing sentences")
        self._btn_expand.setCursor(Qt.CursorShape.PointingHandCursor)
        self._btn_expand.clicked.connect(self._toggle)
        header.addWidget(self._btn_expand)

        self._btn_save = QPushButton("💾 Save as Source")
        self._btn_save.setFixedHeight(24)
        self._btn_save.setCursor(Qt.CursorShape.PointingHandCursor)
        self._btn_save.clicked.connect(self._save)
        header.addWidget(self._btn_save)

        self._btn_workspace = QPushButton("📌 To Workspace")
        self._btn_workspace.setFixedHeight(24)
        self._btn_workspace.setCursor(Qt.CursorShape.PointingHandCursor)
        self._btn_workspace.setToolTip("Add this source as a node in the workspace canvas")
        self._btn_workspace.clicked.connect(self._add_to_workspace)
        header.addWidget(self._btn_workspace)

        self._btn_refs = QPushButton("🔗")
        self._btn_refs.setFixedSize(24, 24)
        self._btn_refs.setToolTip("Find mentions in other documents")
        self._btn_refs.setCursor(Qt.CursorShape.PointingHandCursor)
        self._btn_refs.clicked.connect(self._find_references)
        header.addWidget(self._btn_refs)

        self._btn_dismiss = QPushButton("✖")
        self._btn_dismiss.setFixedSize(24, 24)
        self._btn_dismiss.setCursor(Qt.CursorShape.PointingHandCursor)
        self._btn_dismiss.clicked.connect(self._dismiss)
        header.addWidget(self._btn_dismiss)

        self._outer.addLayout(header)

        # Meta line
        self._lbl_meta = QLabel(meta_text)
        self._lbl_meta.setTextFormat(Qt.TextFormat.RichText)
        self._lbl_meta.setStyleSheet("color: #888; font-size: 11px; padding-left: 2px;")
        self._lbl_meta.setWordWrap(True)
        self._outer.addWidget(self._lbl_meta)

        # Collapsible body with citing sentences (quotes)
        self._body = QWidget()
        body_layout = QVBoxLayout(self._body)
        body_layout.setContentsMargins(0, 4, 0, 0)
        body_layout.setSpacing(2)

        props = self._group.extra_properties
        bibliography_mentions = list(props.get("bibliography_mentions") or [])
        bib_page = props.get("bib_page_num", -1)
        bib_raw = props.get("bibliography_raw", "")
        if not bibliography_mentions and bib_page >= 0 and bib_raw:
            bibliography_mentions = [ExtractedMention(
                text="Bibliography entry", context=bib_raw, page_num=bib_page
            )]

        # Always show the bibliography entry itself as the first row so users
        # can jump to it the same way they jump to any in-text mention.
        for bib_mention in bibliography_mentions:
            bib_row = MentionRow(self._tab, bib_mention, "", self._source_path, self._theme)
            body_layout.addWidget(bib_row)

        if self._group.mentions:
            for mention in self._group.mentions:
                row = MentionRow(self._tab, mention, mention.text, self._source_path, self._theme)
                body_layout.addWidget(row)
        elif not bibliography_mentions:
            lbl_no_cite = QLabel("No in-text citations found — bibliography entry only.")
            lbl_no_cite.setStyleSheet("color: #888; font-size: 11px; padding: 4px;")
            body_layout.addWidget(lbl_no_cite)

        # Reparent before showing: a visible parentless widget is a top-level
        # window, which flashes an empty frame on screen for every card.
        self._outer.addWidget(self._body)
        self._body.setVisible(self._expanded)

        # Inline references panel
        self._refs_panel = QWidget()
        self._refs_panel.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        refs_layout = QVBoxLayout(self._refs_panel)
        refs_layout.setContentsMargins(0, 0, 0, 0)
        refs_layout.setSpacing(2)
        self._refs_label = QLabel("Searching…")
        self._refs_label.setStyleSheet("color: #888; font-size: 10px; padding: 0px;")
        self._refs_label.setMaximumHeight(14)
        self._refs_label.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        refs_layout.addWidget(self._refs_label)
        self._refs_content = QWidget()
        self._refs_content_layout = QVBoxLayout(self._refs_content)
        self._refs_content_layout.setContentsMargins(0, 0, 0, 0)
        self._refs_content_layout.setSpacing(3)
        self._refs_scroll = QScrollArea()
        self._refs_scroll.setWidgetResizable(True)
        self._refs_scroll.setWidget(self._refs_content)
        self._refs_scroll.setFixedHeight(150)
        self._refs_scroll.setFrameShape(QFrame.Shape.NoFrame)
        refs_layout.addWidget(self._refs_scroll)
        self._refs_panel.setVisible(False)
        self._outer.addWidget(self._refs_panel)

    def _toggle(self):
        self._expanded = not self._expanded
        self._body.setVisible(self._expanded)
        self._btn_expand.setText("▼" if self._expanded else "▶")

    def _find_references(self):
        self._refs_label.setText("Searching…")
        self._refs_panel.setVisible(True)
        if hasattr(self._tab, "_set_pending_refs_card"):
            self._tab._set_pending_refs_card(self)
        self._tab.bus.discovery_action_requested.emit(
            DiscoveryIntent.FIND_REFERENCES,
            DiscoveryPayload(
                entity_name=self._group.canonical_text,
                entity_type=self._group.entity_type,
            ),
        )

    def show_references(self, references):
        """Display per-occurrence reference results with context snippets and Jump buttons."""
        self._refs_label.setText(
            f"Found in {len(references)} document(s):" if references else "No other documents found."
        )
        _build_refs_rows(self._refs_content, references, self._tab.bus)

    def _save(self):
        self._tab.bus.discovery_action_requested.emit(
            DiscoveryIntent.SAVE_ENTITY,
            DiscoveryPayload(
                source_path=self._source_path,
                entity_type=self._group.entity_type,
                entity_groups=[self._group],
            ),
        )
        self._btn_save.setText("✓ Saved")
        self._btn_save.setEnabled(False)

    def _add_to_workspace(self):
        props = self._group.extra_properties
        authors = props.get("authors") or []
        author_str = ", ".join(str(a) for a in authors[:3])
        if len(authors) > 3:
            author_str += " et al."
        year = props.get("year", "")
        title = self._group.canonical_text
        journal = props.get("journal", "")
        label = f"{author_str} ({year}). {title}" if author_str else f"({year}) {title}"
        entity_dict = {
            "id": str(uuid.uuid4()),
            "type": "entity.source",
            "text": label[:120],
            "title": title,
            "authors": author_str,
            "year": year,
            "journal": journal,
            "citation_key": props.get("citation_key", ""),
            "mention_count": len(self._group.mentions),
            "in_project": False,
        }
        self._tab.bus.workspace_action_requested.emit(
            WorkspaceIntent.IMPORT_GRAPH,
            WorkspacePayload(extra={"graph": {"entities": [entity_dict], "relations": []}}),
        )
        self._btn_workspace.setText("✓ Added")
        self._btn_workspace.setEnabled(False)

    def _dismiss(self):
        self.setVisible(False)

    def apply_theme(self, theme):
        self._theme = theme
        self.setStyleSheet(
            f"QFrame {{ background-color: {theme['bg_panel']}; border: 1px solid {theme['border']}; border-radius: 6px; }}"
        )
        btn_style = (
            f"QPushButton {{ background-color: transparent; color: {theme['text_main']}; "
            f"border: 1px solid {theme['border']}; padding: 2px 6px; border-radius: 4px; }}"
            f"QPushButton:hover {{ background-color: {theme['bg_input']}; }}"
        )
        self._btn_expand.setStyleSheet(btn_style)
        self._btn_dismiss.setStyleSheet(btn_style)
        if hasattr(self, "_btn_workspace"):
            self._btn_workspace.setStyleSheet(btn_style)
        if hasattr(self, "_btn_refs"):
            self._btn_refs.setStyleSheet(btn_style)
        self._btn_save.setStyleSheet(
            f"QPushButton {{ background-color: {theme['accent']}; color: #1e1e1e; "
            f"border: none; padding: 2px 8px; border-radius: 4px; font-weight: bold; }}"
        )
        self._lbl_title.setStyleSheet(f"color: {theme['text_main']};")
        body_layout = self._body.layout()
        if body_layout:
            for i in range(body_layout.count()):
                w = body_layout.itemAt(i).widget()
                if isinstance(w, MentionRow):
                    w.apply_theme(theme)


class _DiscoveryLoadWorker(QThread):
    loaded = Signal(object, str)
    failed = Signal(str)

    def __init__(
        self, service, source_id, source_path,
        selected_type, project_scope, sort_mode, parent=None,
    ):
        super().__init__(parent)
        self.service = service
        self.source_id = source_id
        self.source_path = source_path
        self.selected_type = selected_type
        self.project_scope = project_scope
        self.sort_mode = sort_mode

    def run(self):
        try:
            groups = self.service.get_discovery_groups(
                self.source_id,
                selected_type=self.selected_type,
                project_scope=self.project_scope,
                sort_mode=self.sort_mode,
                cancel_check=self.isInterruptionRequested,
            )
            if self.isInterruptionRequested():
                return
            self.loaded.emit(groups, self.source_path)
        except Exception as exc:
            self.failed.emit(str(exc))


class DiscoveryTabWidget(QWidget):
    """Main discovery tab: deterministic extraction with collapsible entity cards."""
    def __init__(self, app_context=None, parent=None):
        super().__init__(parent)
        self._ctx = app_context
        self.bus = EventBus.get_instance()
        self.theme = None
        self._is_destroyed = False
        self._current_source_path: str = ""
        self._runner = None
        self._load_worker = None
        self._reload_pending = False
        self._pending_refs_card = None  # card waiting for REFERENCES_FOUND response
        self._build_ui()
        self._try_register_shortcuts()
        self._type_combo.currentIndexChanged.connect(self._load_canonical_entities)
        self._scope_combo.currentIndexChanged.connect(self._load_canonical_entities)
        self._sort_combo.currentIndexChanged.connect(self._load_canonical_entities)
        QTimer.singleShot(0, self._load_canonical_entities)

        self.bus.discovery_state_changed.connect(self._on_discovery_state)
        self.destroyed.connect(self._on_destroyed)

    def _set_pending_refs_card(self, card):
        self._pending_refs_card = card

    def _try_register_shortcuts(self):
        ctx = self._ctx
        registry = getattr(ctx, "keybinding_registry", None) if ctx else None
        if not registry:
            return
        try:
            from gui.managers.keybinding_registry import KeySpec
            registry.register_many([
                KeySpec(
                    action_id="discovery.extract",
                    label="Run Extraction",
                    scope="research",
                    description="Run the selected deterministic extractor on the active document",
                    default_key="Ctrl+Shift+E",
                ),
                KeySpec(
                    action_id="discovery.save_all",
                    label="Save All Discovered Entities",
                    scope="research",
                    description="Save all currently extracted entities to the workspace graph",
                    default_key="Ctrl+Shift+S",
                ),
            ])
            registry.bind("discovery.extract", self._run_extraction, self)
            registry.bind("discovery.save_all", self._save_all, self)
        except Exception:
            pass

    def closeEvent(self, event):
        # Stop the loader here, not only on destroyed(): by the time destroyed()
        # fires, Qt is already tearing the widget down and is about to delete its
        # QThread child, which aborts the process if the thread is still running.
        self._stop_load_worker()
        super().closeEvent(event)

    def _stop_load_worker(self):
        worker = self._load_worker
        if worker is not None and worker.isRunning():
            worker.requestInterruption()
            worker.wait(_WORKER_SHUTDOWN_MS)

    def _on_destroyed(self, *_):
        self._is_destroyed = True
        self._stop_load_worker()
        try:
            self.bus.discovery_state_changed.disconnect(self._on_discovery_state)
        except (RuntimeError, TypeError):
            pass

    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.setSpacing(4)

        # Top toolbar — filters and actions on separate rows so the dock can be
        # dragged narrow; one combined row pinned it to ~760px.
        filters_row = QHBoxLayout()
        layout.addLayout(filters_row)
        actions_row = QHBoxLayout()
        layout.addLayout(actions_row)

        self._type_combo = QComboBox()
        self._populate_type_combo()
        filters_row.addWidget(self._type_combo)

        self._scope_combo = QComboBox()
        self._scope_combo.addItem("This PDF", "source")
        self._scope_combo.addItem("Whole project", "project")
        self._scope_combo.setToolTip("Show occurrences in this PDF or shared entities across the project")
        filters_row.addWidget(self._scope_combo)

        self._sort_combo = QComboBox()
        self._sort_combo.addItem("Most mentions", "mentions")
        self._sort_combo.addItem("Most unique sources", "sources")
        self._sort_combo.setToolTip(
            "Unique sources prioritizes entities repeated across different project documents"
        )
        filters_row.addWidget(self._sort_combo)

        self._btn_extract = QPushButton("🔍 Run/Rebuild intelligence")
        self._btn_extract.setCursor(Qt.CursorShape.PointingHandCursor)
        self._btn_extract.setToolTip(
            "Run canonical entity extraction and/or citation matching for the active source"
        )
        self._btn_extract.clicked.connect(self._run_extraction)
        actions_row.addWidget(self._btn_extract)

        self._btn_save_all = QPushButton("💾 Save All")
        self._btn_save_all.setCursor(Qt.CursorShape.PointingHandCursor)
        self._btn_save_all.setToolTip("Save all discovered entities to the workspace graph")
        self._btn_save_all.clicked.connect(self._save_all)
        actions_row.addWidget(self._btn_save_all)

        self._btn_citation_map = QPushButton("🗺 Citation Map")
        self._btn_citation_map.setCursor(Qt.CursorShape.PointingHandCursor)
        self._btn_citation_map.setToolTip(
            "Import this PDF, its bibliography entries, and every matched in-text citation into the workspace"
        )
        self._btn_citation_map.clicked.connect(self._import_citation_map)
        actions_row.addWidget(self._btn_citation_map)

        make_shrinkable(
            self._type_combo, self._scope_combo, self._sort_combo,
            self._btn_extract, self._btn_save_all, self._btn_citation_map,
        )

        # Status label
        self._lbl_status = QLabel("Select a type and click Extract.")
        self._lbl_status.setStyleSheet("color: #888; font-size: 11px; padding: 2px;")
        layout.addWidget(self._lbl_status)

        # Scroll area for cards
        self._scroll = QScrollArea()
        self._scroll.setWidgetResizable(True)
        self._scroll_content = QWidget()
        self._cards_layout = QVBoxLayout(self._scroll_content)
        self._cards_layout.setAlignment(Qt.AlignmentFlag.AlignTop)
        self._cards_layout.setSpacing(4)
        self._scroll.setWidget(self._scroll_content)
        layout.addWidget(self._scroll)

    def _populate_type_combo(self):
        self._type_combo.clear()
        self._type_combo.addItem("All entity types", "")
        for label, value in (
            ("People", "PERSON"),
            ("Organizations", "ORG"),
            ("Places", "LOCATION"),
            ("Events", "EVENT"),
            ("Creative works", "WORK"),
            ("Methods", "METHOD"),
            ("Datasets", "DATASET"),
            ("Concepts", "CONCEPT"),
            ("Citations & bibliography", "CITATION"),
        ):
            self._type_combo.addItem(label, value)

    def _get_active_source_path(self) -> str:
        ctx = self._ctx
        viewer = getattr(ctx, "viewer", None) if ctx else None
        return (
            getattr(viewer, "current_source_path", None)
            or getattr(viewer, "pdf_path", None)
            or ""
        )

    def _run_extraction(self):
        source_path = self._get_active_source_path()
        if not source_path:
            self._lbl_status.setText("No document open.")
            return
        self._current_source_path = source_path
        selected_type = self._type_combo.currentData() or ""
        stages = (
            ["citations"] if selected_type == "CITATION"
            else ["entities"] if selected_type
            else ["entities", "citations"]
        )
        self._lbl_status.setText(
            f"Queueing shared intelligence: {', '.join(stages)}…"
        )
        self._btn_extract.setEnabled(False)
        source = None
        pm = getattr(self._ctx, "project_manager", None) if self._ctx else None
        if pm and hasattr(pm, "get_source_record_by_path"):
            source = pm.get_source_record_by_path(source_path)
        service = getattr(self._ctx, "source_intelligence_service", None) if self._ctx else None
        if not source or not service:
            self._lbl_status.setText("Source intelligence service is unavailable.")
            self._btn_extract.setEnabled(True)
            return
        for stage in stages:
            service.run_stage(source.get("id"), stage)
        # The scheduler is asynchronous. Refreshing is idempotent and preserves
        # the current canonical artifacts while replacement output is pending.
        QTimer.singleShot(250, self._load_canonical_entities)
        QTimer.singleShot(1200, self._load_canonical_entities)

    def _save_all(self):
        self.bus.discovery_action_requested.emit(
            DiscoveryIntent.SAVE_ALL,
            DiscoveryPayload(source_path=self._current_source_path),
        )
        self._lbl_status.setText("Saved all entities.")

    def _import_citation_map(self):
        source_path = self._get_active_source_path()
        pm = getattr(self._ctx, "project_manager", None) if self._ctx else None
        source = pm.get_source_record_by_path(source_path) if pm and source_path else None
        service = getattr(self._ctx, "source_intelligence_service", None) if self._ctx else None
        if not source or not service:
            self._lbl_status.setText("Citation intelligence is unavailable.")
            return
        graph = service.build_citation_map(source.get("id"))
        if not graph.get("entities"):
            self._lbl_status.setText(
                "No citation artifacts yet. Run/Rebuild citations from source controls first."
            )
            return
        self.bus.workspace_action_requested.emit(
            WorkspaceIntent.IMPORT_GRAPH,
            WorkspacePayload(extra={"graph": graph, "source": "citation_map"}),
        )
        diagnostics = graph.get("diagnostics") or {}
        self._lbl_status.setText(
            f"Imported citation map: {diagnostics.get('bibliography_entries', 0)} "
            f"sources and {diagnostics.get('in_text_citations', 0)} in-text citations."
        )

    def _load_canonical_entities(self):
        """Render canonical intelligence entities; never re-extract in this tab."""
        if not self._can_use():
            return
        if self._load_worker and self._load_worker.isRunning():
            self._reload_pending = True
            return
        source_path = self._get_active_source_path()
        pm = getattr(self._ctx, "project_manager", None) if self._ctx else None
        source = pm.get_source_record_by_path(source_path) if pm and source_path else None
        service = getattr(
            self._ctx, "source_intelligence_service", None
        ) if self._ctx else None
        if not source or not service:
            self._btn_extract.setEnabled(True)
            return
        self._lbl_status.setText("Loading canonical intelligence…")
        self._load_worker = _DiscoveryLoadWorker(
            service,
            str(source.get("id") or ""),
            source_path,
            self._type_combo.currentData() or "",
            self._scope_combo.currentData() == "project",
            self._sort_combo.currentData() or "mentions",
            self,
        )
        self._load_worker.loaded.connect(self._on_discovery_loaded)
        self._load_worker.failed.connect(self._on_discovery_load_failed)
        self._load_worker.finished.connect(self._on_discovery_loader_finished)
        self._load_worker.start()

    def _on_discovery_loaded(self, groups, source_path):
        if not self._can_use():
            return
        self._current_source_path = source_path
        self._populate_cards(groups or [], source_path)
        self._btn_extract.setEnabled(True)

    def _on_discovery_load_failed(self, error):
        if not self._can_use():
            return
        self._lbl_status.setText(f"Could not load intelligence: {error}")
        self._btn_extract.setEnabled(True)

    def _on_discovery_loader_finished(self):
        worker = self._load_worker
        self._load_worker = None
        if worker:
            worker.deleteLater()
        if self._reload_pending and self._can_use():
            self._reload_pending = False
            QTimer.singleShot(0, self._load_canonical_entities)

    def _on_discovery_state(self, event: DiscoveryEvent, payload: DiscoveryEventPayload):
        if not self._can_use():
            return
        if event == DiscoveryEvent.EXTRACTION_STARTED:
            self._lbl_status.setText("Extracting…")
        elif event == DiscoveryEvent.EXTRACTION_COMPLETE:
            self._btn_extract.setEnabled(True)
            if payload.error:
                self._lbl_status.setText(f"Error: {payload.error}")
                return
            # Compatibility events from the retired extractor never become a
            # second source of truth in Discovery.
            self._load_canonical_entities()
        elif event == DiscoveryEvent.SAVE_COMPLETE:
            self._lbl_status.setText(f"Saved {len(payload.saved_ids)} entities.")
        elif event == DiscoveryEvent.REFERENCES_FOUND:
            card = self._pending_refs_card
            self._pending_refs_card = None
            if card is not None and hasattr(card, "show_references"):
                try:
                    card.show_references(payload.references or [])
                except RuntimeError:
                    pass

    def _populate_cards(self, groups, source_path: str):
        if not self._can_use():
            return
        # Clear existing cards
        while self._cards_layout.count():
            item = self._cards_layout.takeAt(0)
            w = item.widget()
            if w and shiboken6.isValid(w):
                w.deleteLater()

        if not groups:
            lbl = QLabel("No entities found.")
            lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
            lbl.setStyleSheet("color: #888; margin-top: 20px;")
            self._cards_layout.addWidget(lbl)
            self._lbl_status.setText("No entities found.")
            return

        citation_type = "entity.source"
        for group in groups:
            if group.entity_type == citation_type:
                card = CitationGroupCard(self, group, source_path, self.theme)
            else:
                card = EntityGroupCard(self, group, source_path, self.theme)
            self._cards_layout.addWidget(card)

        scope_label = (
            "the whole project"
            if self._scope_combo.currentData() == "project"
            else os.path.basename(source_path)
        )
        self._lbl_status.setText(f"Found {len(groups)} intelligence items in {scope_label}.")

    def update_theme(self, theme):
        if not self._can_use():
            return
        self.theme = theme
        self._scroll.setStyleSheet("background: transparent; border: none;")
        self._scroll_content.setStyleSheet(f"background-color: {theme['bg_main']};")
        combo_style = (
            f"background-color: {theme['bg_input']}; color: {theme['text_main']}; border: 1px solid {theme['border']};"
        )
        for combo in (self._type_combo, self._scope_combo, self._sort_combo):
            combo.setStyleSheet(combo_style)
        btn_style = (
            f"QPushButton {{ background-color: {theme['bg_panel']}; color: {theme['text_main']}; "
            f"border: 1px solid {theme['border']}; padding: 4px 10px; border-radius: 4px; }}"
            f"QPushButton:hover {{ background-color: {theme['bg_input']}; }}"
            f"QPushButton:disabled {{ color: {theme['border']}; }}"
        )
        for btn in (self._btn_extract, self._btn_save_all, self._btn_citation_map):
            btn.setStyleSheet(btn_style)
        self._lbl_status.setStyleSheet(f"color: {theme['text_muted'] if 'text_muted' in theme else '#888'}; font-size: 11px;")
        # Re-theme all cards
        for i in range(self._cards_layout.count()):
            w = self._cards_layout.itemAt(i).widget()
            if hasattr(w, "apply_theme"):
                w.apply_theme(theme)

    def _can_use(self) -> bool:
        try:
            return (
                not self._is_destroyed
                and shiboken6.isValid(self)
                and hasattr(self, "_cards_layout")
                and shiboken6.isValid(self._cards_layout)
            )
        except RuntimeError:
            return False


# ---------------------------------------------------------------------------
# Legacy DiscoveredEntityBubble — kept for compatibility if referenced elsewhere
# ---------------------------------------------------------------------------

class ExtractedEntitiesWidget(DiscoveryTabWidget):
    """Backwards-compat alias: old code that instantiated ExtractedEntitiesWidget
    will get the new DiscoveryTabWidget instead."""
    pass


class NotesTab(QWidget):
    def __init__(self, app_context=None, parent=None):
        super().__init__(parent)
        self._ctx = app_context
        self.bus = EventBus.get_instance()
        self._is_destroyed = False
        self._notes_dirty = True

        self._build_ui()

        # Listen to Event Bus for Annotations
        self.bus.notes_data_ready.connect(self._handle_notes_event)
        self.bus.highlight_created.connect(self._handle_document_event)
        self.bus.highlight_updated.connect(self._handle_document_event)
        self.bus.highlight_deleted.connect(self._handle_document_event)
        self.bus.pdf_switched.connect(self._handle_document_event)
        self.bus.source_switched.connect(self._source_changed)
        self.bus.intelligence_state_changed.connect(self._handle_intelligence_event)
        self.destroyed.connect(self._on_destroyed)
        QTimer.singleShot(0, self._safe_refresh_notes)

    def _safe_refresh_notes(self):
        try:
            self.refresh_notes()
        except Exception as e:
            import traceback
            print(f"[NotesTab] refresh_notes error: {e}")
            traceback.print_exc()

    def _on_destroyed(self, *_args):
        self._is_destroyed = True
        for signal, slot in (
            (self.bus.notes_data_ready, self._handle_notes_event),
            (self.bus.highlight_created, self._handle_document_event),
            (self.bus.highlight_updated, self._handle_document_event),
            (self.bus.highlight_deleted, self._handle_document_event),
            (self.bus.pdf_switched, self._handle_document_event),
            (self.bus.source_switched, self._source_changed),
            (self.bus.intelligence_state_changed, self._handle_intelligence_event),
        ):
            try:
                signal.disconnect(slot)
            except (RuntimeError, TypeError):
                pass

    def _handle_notes_event(self, event: NotesEvent, payload: NotesEventPayload):
        if event == NotesEvent.DATA_READY:
            self.refresh_notes()

    def _source_changed(self, *_):
        self.refresh_notes()

    def _handle_intelligence_event(self, event, payload):
        from core.events.domains.intelligence_events import IntelligenceEvent
        if event in {IntelligenceEvent.LINKED_NOTE_CHANGED, IntelligenceEvent.ARTIFACT_REVIEWED}:
            self._safe_refresh_notes()

    def _handle_document_event(self, event: DocumentEvent, payload: DocumentEventPayload):
        if event in {
            DocumentEvent.HIGHLIGHT_CREATED,
            DocumentEvent.HIGHLIGHT_UPDATED,
            DocumentEvent.HIGHLIGHT_DELETED,
            DocumentEvent.PDF_SWITCHED,
        }:
            self.refresh_notes()

    def _build_ui(self):
        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(0, 0, 0, 0)

        self.tab_widget = QTabWidget()

        # 1. Annotations Tab (original UI)
        self.annotations_widget = QWidget()
        annot_layout = QVBoxLayout(self.annotations_widget)
        annot_layout.setContentsMargins(5, 5, 5, 5)

        ctrl_layout = QHBoxLayout()
        self.lbl = QLabel("Notes")
        ctrl_layout.addWidget(self.lbl)

        self.scope_combo = QComboBox()
        self.scope_combo.addItems(["Current Document", "Entire Project"])
        self.scope_combo.currentIndexChanged.connect(self.refresh_notes)
        ctrl_layout.addWidget(self.scope_combo)

        self.tag_combo = QComboBox()
        self.tag_combo.setEditable(True)
        self.tag_combo.setToolTip("Choose a suggested tag or type an exact tag name.")
        self.tag_combo.addItem("All Tags", None)
        self.tag_combo.currentIndexChanged.connect(self.refresh_notes)
        self.tag_combo.textActivated.connect(self.refresh_notes)
        self.tag_combo.lineEdit().editingFinished.connect(self.refresh_notes)
        self.tag_combo.setAccessibleName('Filter notes by tag; type an exact tag name and press Enter')
        ctrl_layout.addWidget(self.tag_combo)
        refresh = QPushButton('Refresh notes', self)
        refresh.clicked.connect(self.refresh_notes)
        ctrl_layout.addWidget(refresh)
        from gui.components.artifact_actions import bind_artifact_action
        bind_artifact_action(self._ctx, self, 'intelligence.notes.refresh', 'Refresh Notes', self.refresh_notes, 'toolbar:notes')
        annot_layout.addLayout(ctrl_layout)
        make_shrinkable(self.lbl, self.scope_combo, self.tag_combo)

        from gui.components.paged_collection import PagedCollection
        from gui.components.artifact_tasks import ArtifactTaskRunner
        self.collection = PagedCollection(self._ctx, self)
        self.collection.pager.register_actions('intelligence.notes', 'toolbar:notes')
        self.collection.pager.requested.connect(self._load_page)
        self.collection.selected.connect(self._select_note)
        annot_layout.addWidget(self.collection, 1)
        self.scroll_area = QScrollArea(self)
        self.scroll_area.setWidgetResizable(True)
        self.scroll_content = QWidget(self.scroll_area)
        self.scroll_layout = QVBoxLayout(self.scroll_content)
        self.scroll_area.setWidget(self.scroll_content)
        self.collection.set_detail(self.scroll_area, persistent=True)
        self.collection.splitter.setStretchFactor(0, 1)
        self.collection.splitter.setStretchFactor(1, 1)
        self.collection.splitter.setSizes([350, 300])
        self.runner = ArtifactTaskRunner(self._ctx, self, service_name='reference_note_service')
        self.runner.completed.connect(self._notes_page)
        self.runner.failed.connect(self._notes_failed)
        self.destroyed.connect(self.runner.shutdown)
        if getattr(self._ctx, 'bus', None):
            self._ctx.bus.project_clearing_started.connect(self._clear_notes)

        self.tab_widget.addTab(self.annotations_widget, "📝 Notes")

        # 2. Discovery Tab (new deterministic extraction UI)
        self.extracted_widget = DiscoveryTabWidget(self._ctx, self)
        self.tab_widget.addTab(self.extracted_widget, "🔍 Discovery")

        self.tab_widget.currentChanged.connect(self._on_tab_changed)
        main_layout.addWidget(self.tab_widget)

    def _on_tab_changed(self, index):
        if index == 0 and self._notes_dirty:
            self.refresh_notes()
        if index == 1 and hasattr(self, "extracted_widget"):
            try:
                self.extracted_widget._populate_type_combo()
            except Exception as e:
                import traceback
                print(f"[NotesTab] _on_tab_changed error: {e}")
                traceback.print_exc()

    def apply_tag_filter(self, tag_name):
        self.tab_widget.setCurrentIndex(0)
        index = self.tag_combo.findData(tag_name)
        if index >= 0:
            self.tag_combo.setCurrentIndex(index)
        else:
            self.tag_combo.setEditText(tag_name or 'All Tags')
        self.refresh_notes()

    def refresh_tag_list(self):
        """Compatibility callers use the same bounded, asynchronous snapshot."""
        self.refresh_notes()

    def refresh_notes(self):
        if not self._can_use_notes_layout():
            return
        if not self.isVisible() or not self.annotations_widget.isVisible():
            self._notes_dirty = True
            return
        self._notes_dirty = False
        if getattr(self._ctx, 'reference_note_service', None) is None:
            self._clear_notes()
            return
        self.collection.pager.reset()

    def showEvent(self, event):
        super().showEvent(event)
        if self._notes_dirty:
            QTimer.singleShot(0, self._safe_refresh_notes)

    def _load_page(self):
        viewer = getattr(self._ctx, 'viewer', None)
        path = getattr(viewer, 'current_source_path', None) or getattr(viewer, 'pdf_path', None)
        if self.scope_combo.currentIndex() == 0 and not path:
            self.collection.apply({'items': []})
            return
        self.collection.status.setText('Loading notes…')
        self.collection.setEnabled(False)
        self.runner.start('query_notes', source_path=path if self.scope_combo.currentIndex() == 0 else '',
                          tag=self.tag_combo.currentData() or (self.tag_combo.currentText() if self.tag_combo.currentText() != 'All Tags' else ''), cursor=self.collection.pager.cursor)

    def _notes_page(self, operation):
        if operation != 'query_notes':
            return
        snapshot, self.runner.result = self.runner.result or {}, None
        self.collection.setEnabled(True)
        selected_tag = self.tag_combo.currentText()
        self.tag_combo.blockSignals(True)
        self.tag_combo.clear()
        self.tag_combo.addItem('All Tags', None)
        for tag in snapshot.get('tags') or []:
            self.tag_combo.addItem(tag['name'], tag['name'])
        self.tag_combo.setCurrentText(selected_tag)
        self.tag_combo.blockSignals(False)
        self.collection.apply(snapshot)

    def _notes_failed(self, message):
        self.collection.setEnabled(True)
        self.collection.status.setText(message + ' Change scope or refresh to restart.')

    def _clear_notes(self, *_):
        if hasattr(self, 'runner'):
            self.runner.cancel()
        if hasattr(self, 'collection'):
            self.collection.apply({'items': []})
        self._render_notes([])

    def _select_note(self, index):
        self._render_notes([self.collection.model.items[index]] if index >= 0 else [])

    def _render_notes(self, notes_data_list):
        if not self._can_use_notes_layout():
            return

        while self.scroll_layout.count():
            item = self.scroll_layout.takeAt(0)
            widget = item.widget()
            if widget and shiboken6.isValid(widget):
                widget.deleteLater()

        for data in notes_data_list:
            bubble = NoteBubble(self, data)
            self.scroll_layout.addWidget(bubble)
        if not notes_data_list:
            empty = QLabel("No notes found for this scope.")
            empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
            empty.setStyleSheet("color: #888; margin-top: 20px;")
            self.scroll_layout.addWidget(empty)

    def update_theme(self, theme):
        if self._is_destroyed or not shiboken6.isValid(self):
            return
        self.setStyleSheet(f"background-color: {theme['bg_main']};")

        self.scroll_area.setStyleSheet("background: transparent; border: none;")
        self.scroll_area.viewport().setStyleSheet(f"background-color: {theme['bg_main']};")
        self.scroll_content.setStyleSheet(f"background-color: {theme['bg_main']};")
        self.lbl.setStyleSheet(f"font-size: 16px; font-weight: bold; padding-left: 5px; color: {theme['text_main']};")
        self.scope_combo.setStyleSheet(f"background-color: {theme['bg_input']}; color: {theme['text_main']}; border: 1px solid {theme['border']};")
        self.tag_combo.setStyleSheet(f"background-color: {theme['bg_input']}; color: {theme['text_main']}; border: 1px solid {theme['border']};")

        if self._can_use_notes_layout():
            for i in range(self.scroll_layout.count()):
                widget = self.scroll_layout.itemAt(i).widget()
                if isinstance(widget, NoteBubble):
                    widget.apply_theme(theme)

        if hasattr(self, "extracted_widget") and shiboken6.isValid(self.extracted_widget):
            self.extracted_widget.update_theme(theme)

        self.tab_widget.setStyleSheet(f"""
            QTabWidget::pane {{ border-top: 1px solid {theme['border']}; }}
            QTabBar::tab {{ background: {theme['bg_panel']}; color: {theme['text_main']}; padding: 6px 12px; border: 1px solid transparent; border-top-left-radius: 4px; border-top-right-radius: 4px; }}
            QTabBar::tab:selected {{ background: {theme['bg_main']}; font-weight: bold; border: 1px solid {theme['border']}; border-bottom-color: {theme['bg_main']}; }}
        """)

    def _can_use_notes_layout(self):
        try:
            return (
                not self._is_destroyed
                and shiboken6.isValid(self)
                and hasattr(self, "scroll_layout")
                and shiboken6.isValid(self.scroll_layout)
                and hasattr(self, "scroll_content")
                and shiboken6.isValid(self.scroll_content)
            )
        except RuntimeError:
            return False
