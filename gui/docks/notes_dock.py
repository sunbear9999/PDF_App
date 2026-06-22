import os
import html
import uuid
import shiboken6
from PySide6.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QLabel,
                             QPushButton, QScrollArea, QFrame, QComboBox,
                             QMenu, QTabWidget, QTextEdit, QSizePolicy,
                             QInputDialog)
from PySide6.QtCore import Qt, QTimer

from gui.components.dialogs.tag_manager_dialog import TagAssignmentDialog
from gui.managers.dialog_manager import exec_as_modal, get_for_widget
from core.events.event_bus import EventBus
from core.events.domains.document_events import AnnotationIntent, AnnotationPayload, DocumentEvent, DocumentEventPayload, DocumentIntent, DocumentPayload
from core.events.domains.metadata_events import NotesEvent, NotesEventPayload, NotesIntent, NotesPayload
from core.events.domains.discovery_events import DiscoveryEvent, DiscoveryEventPayload, DiscoveryIntent, DiscoveryPayload
from core.events.domains.workspace_events import WorkspaceIntent, WorkspacePayload
from core.services.content.deterministic_extractor import ExtractedMention
from core.models.ontology_model import EntityIntent, EntityPayload
from core.ontology.registry import OntologyRegistry
from gui.components.dialogs.entity_editor_dialog import EntityEditorDialog

class NoteBubble(QFrame):
    # [KEEP EXISTING NoteBubble EXACTLY AS IS]
    def __init__(self, tab, data_dict):
        super().__init__()
        self.tab = tab
        self.pdf_path = data_dict["pdf_path"]
        self.page_num = data_dict["page_num"]
        self.annot_id = data_dict["annot_id"]
        self.is_ai = data_dict["is_ai"]
        self.source_type = data_dict.get("source_type", "pdf")
        color = data_dict["color"]

        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 10, 10, 10)

        header_layout = QHBoxLayout()
        doc_name = os.path.basename(self.pdf_path)
        if self.source_type == "video":
            self.lbl_page = QLabel(f"🎬 {doc_name} - {self._fmt_time(self.page_num)}")
        else:
            self.lbl_page = QLabel(f"📄 {doc_name} - Pg {self.page_num + 1}")
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
            btn_c.clicked.connect(lambda checked, c=c_val: self.tab.bus.notes_action_requested.emit(
                NotesIntent.CHANGE_COLOR,
                NotesPayload(pdf_path=self.pdf_path, page_num=self.page_num, annot_id=self.annot_id, color=c),
            ))
            header_layout.addWidget(btn_c)

        header_layout.addSpacing(10)

        self.btn_del = QPushButton("✖")
        self.btn_del.setFixedSize(24, 24)
        self.btn_del.clicked.connect(lambda: self.tab.bus.notes_action_requested.emit(
            NotesIntent.DELETE,
            NotesPayload(pdf_path=self.pdf_path, page_num=self.page_num, annot_id=self.annot_id),
        ))
        header_layout.addWidget(self.btn_del)
        layout.addLayout(header_layout)

        self.lbl_subj = QLabel(f'"{data_dict["subject"]}"')
        self.lbl_subj.setWordWrap(True)
        self.lbl_subj.setTextInteractionFlags(Qt.TextInteractionFlag.NoTextInteraction)
        layout.addWidget(self.lbl_subj)

        if data_dict["content"]:
            self.lbl_content = QLabel(data_dict["content"])
            self.lbl_content.setWordWrap(True)
            self.lbl_content.setTextInteractionFlags(Qt.TextInteractionFlag.NoTextInteraction)
            layout.addWidget(self.lbl_content)

        ctx = getattr(self.tab, '_ctx', None)
        tm = getattr(ctx, 'theme_manager', None) if ctx else None
        if tm:
            self.apply_theme(tm.get_theme())

    def apply_theme(self, theme):
        pass

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            if self.source_type == "video":
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
        self._source_path = source_path

        layout = QHBoxLayout(self)
        layout.setContentsMargins(6, 4, 6, 4)
        layout.setSpacing(6)

        page_lbl = QLabel(f"p.{mention.page_num + 1}")
        page_lbl.setFixedWidth(30)
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
        if hasattr(viewer, "jump_to_page"):
            viewer.jump_to_page(page)
        elif self._source_path and hasattr(viewer, "jump_to_source"):
            viewer.jump_to_source(os.path.basename(self._source_path), self._mention.context[:60])

    def apply_theme(self, theme):
        self.setStyleSheet(
            f"background-color: {theme['bg_input']}; border-radius: 4px; margin: 1px 0;"
        )


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
        self._lbl_name = QLabel(f"<b>{html.escape(self._group.canonical_text)}</b>")
        self._lbl_name.setTextFormat(Qt.TextFormat.RichText)
        self._lbl_name.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        header.addWidget(self._lbl_name)

        self._lbl_count = QLabel(f"{count}×")
        self._lbl_count.setStyleSheet("color: #888; font-size: 11px;")
        self._lbl_count.setFixedWidth(30)
        header.addWidget(self._lbl_count)

        self._btn_expand = QPushButton("▶")
        self._btn_expand.setFixedSize(24, 24)
        self._btn_expand.setToolTip("Expand / collapse occurrences")
        self._btn_expand.setCursor(Qt.CursorShape.PointingHandCursor)
        self._btn_expand.clicked.connect(self._toggle)
        header.addWidget(self._btn_expand)

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

        # Collapsible body
        self._body = QWidget()
        body_layout = QVBoxLayout(self._body)
        body_layout.setContentsMargins(0, 4, 0, 0)
        body_layout.setSpacing(2)
        for mention in self._group.mentions:
            row = MentionRow(self._tab, mention, self._group.canonical_text, self._source_path, self._theme)
            body_layout.addWidget(row)
        self._body.setVisible(False)
        self._outer.addWidget(self._body)

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
        self._btn_save.setStyleSheet(
            f"QPushButton {{ background-color: {theme['accent']}; color: #1e1e1e; "
            f"border: none; padding: 2px 8px; border-radius: 4px; font-weight: bold; }}"
        )
        self._lbl_name.setStyleSheet(f"color: {theme['text_main']};")
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
        title = self._group.canonical_text
        journal = props.get("journal", "")

        author_str = ", ".join(str(a) for a in authors[:3])
        if len(authors) > 3:
            author_str += " et al."
        header_text = f"<b>{html.escape(title)}</b>"
        meta_text = f"{html.escape(author_str)} ({html.escape(year)})"
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
        bib_page = props.get("bib_page_num", -1)
        bib_raw = props.get("bibliography_raw", "")

        # Always show the bibliography entry itself as the first row so users
        # can jump to it the same way they jump to any in-text mention.
        if bib_page >= 0 and bib_raw:
            bib_mention = ExtractedMention(
                text="📖 Bibliography entry",
                context=bib_raw,
                page_num=bib_page,
            )
            bib_row = MentionRow(self._tab, bib_mention, "", self._source_path, self._theme)
            body_layout.addWidget(bib_row)

        if self._group.mentions:
            for mention in self._group.mentions:
                row = MentionRow(self._tab, mention, mention.text, self._source_path, self._theme)
                body_layout.addWidget(row)
        elif bib_page < 0:
            lbl_no_cite = QLabel("No in-text citations found — bibliography entry only.")
            lbl_no_cite.setStyleSheet("color: #888; font-size: 11px; padding: 4px;")
            body_layout.addWidget(lbl_no_cite)

        self._body.setVisible(self._expanded)
        self._outer.addWidget(self._body)

    def _toggle(self):
        self._expanded = not self._expanded
        self._body.setVisible(self._expanded)
        self._btn_expand.setText("▼" if self._expanded else "▶")

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
        self._build_ui()
        self._try_register_shortcuts()

        self.bus.discovery_state_changed.connect(self._on_discovery_state)
        self.destroyed.connect(self._on_destroyed)

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
                KeySpec(
                    action_id="discovery.analyze_ai",
                    label="Analyze with AI",
                    scope="research",
                    description="Run LLM graph analysis on the last extracted entities",
                    default_key="",
                ),
            ])
            registry.bind("discovery.extract", self._run_extraction, self)
            registry.bind("discovery.save_all", self._save_all, self)
            registry.bind("discovery.analyze_ai", self._run_llm_analysis, self)
        except Exception:
            pass

    def _on_destroyed(self, *_):
        self._is_destroyed = True
        try:
            self.bus.discovery_state_changed.disconnect(self._on_discovery_state)
        except (RuntimeError, TypeError):
            pass

    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.setSpacing(4)

        # Top toolbar
        toolbar = QHBoxLayout()

        self._type_combo = QComboBox()
        self._populate_type_combo()
        toolbar.addWidget(self._type_combo)

        self._btn_extract = QPushButton("🔍 Extract")
        self._btn_extract.setCursor(Qt.CursorShape.PointingHandCursor)
        self._btn_extract.setToolTip("Run deterministic extraction on the active document")
        self._btn_extract.clicked.connect(self._run_extraction)
        toolbar.addWidget(self._btn_extract)

        self._btn_save_all = QPushButton("💾 Save All")
        self._btn_save_all.setCursor(Qt.CursorShape.PointingHandCursor)
        self._btn_save_all.setToolTip("Save all discovered entities to the workspace graph")
        self._btn_save_all.clicked.connect(self._save_all)
        toolbar.addWidget(self._btn_save_all)

        self._btn_llm = QPushButton("🤖 Analyze with AI")
        self._btn_llm.setCursor(Qt.CursorShape.PointingHandCursor)
        self._btn_llm.setToolTip("Use AI to build a knowledge graph from extracted entities")
        self._btn_llm.clicked.connect(self._run_llm_analysis)
        toolbar.addWidget(self._btn_llm)

        layout.addLayout(toolbar)

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
        try:
            from core.services.content.deterministic_extractor import DeterministicExtractorRegistry
            for spec in DeterministicExtractorRegistry.all_specs():
                self._type_combo.addItem(spec.display_name, spec.entity_type)
        except Exception:
            pass

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
        entity_type = self._type_combo.currentData() or ""
        if not entity_type:
            self._lbl_status.setText("Select an extraction type first.")
            return
        self._current_source_path = source_path
        self._lbl_status.setText("Extracting…")
        self._btn_extract.setEnabled(False)
        self.bus.discovery_action_requested.emit(
            DiscoveryIntent.RUN_EXTRACTION,
            DiscoveryPayload(source_path=source_path, entity_type=entity_type),
        )

    def _save_all(self):
        self.bus.discovery_action_requested.emit(
            DiscoveryIntent.SAVE_ALL,
            DiscoveryPayload(source_path=self._current_source_path),
        )
        self._lbl_status.setText("Saved all entities.")

    def _run_llm_analysis(self):
        instructions, ok = QInputDialog.getText(
            self,
            "AI Analysis Instructions",
            "Optional instructions (e.g. 'connect people to dates they appear with'):",
        )
        if not ok:
            return

        ctx = self._ctx
        if not ctx:
            return

        extractor_svc = getattr(ctx, "deterministic_extractor_service", None)
        if not extractor_svc:
            self._lbl_status.setText("Extractor service not available.")
            return

        discovery_context = extractor_svc.get_last_results_as_context()

        from core.engine.blueprints.discovery_analysis import (
            build_discovery_blueprint, build_ontology_schema_text,
        )
        from core.engine.master_runner import MasterActionRunner

        ontology_registry = getattr(ctx, "ontology_registry", None)
        blueprint = build_discovery_blueprint()
        initial_state = {
            "user_instructions": instructions or "Build a comprehensive knowledge graph connecting all entities.",
            "discovery_context": discovery_context,
            "ontology_schema": build_ontology_schema_text(ontology_registry),
            "selected_model": ctx.get_active_ai_model() if hasattr(ctx, "get_active_ai_model") else "",
            "active_workspace_id": 1,
        }
        process_registry = getattr(ctx, "process_registry", None)
        self._runner = MasterActionRunner(
            blueprint,
            initial_state,
            llm_manager=ctx.llm_manager,
            prompt_manager=ctx.prompt_manager,
            project_manager=ctx.project_manager,
            ontology_registry=getattr(ctx, "ontology_registry", None),
            process_registry=process_registry,
        )
        self._runner.error.connect(lambda msg: self._lbl_status.setText(f"AI error: {msg}"))
        self._runner.action_complete.connect(lambda _: self._lbl_status.setText("AI graph sent to workspace."))
        self._runner.finished.connect(lambda: self._btn_llm.setEnabled(True))
        self._lbl_status.setText("Running AI analysis…")
        self._btn_llm.setEnabled(False)
        if process_registry and hasattr(process_registry, "enqueue_runner"):
            process_registry.enqueue_runner(self._runner, "Discovery AI Analysis", is_express=False)
        else:
            self._runner.start()

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
            self._populate_cards(payload.entity_groups or [], payload.source_path or self._current_source_path)
        elif event == DiscoveryEvent.SAVE_COMPLETE:
            self._lbl_status.setText(f"Saved {len(payload.saved_ids)} entities.")

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

        self._lbl_status.setText(f"Found {len(groups)} entities in {os.path.basename(source_path)}.")

    def update_theme(self, theme):
        if not self._can_use():
            return
        self.theme = theme
        self._scroll.setStyleSheet("background: transparent; border: none;")
        self._scroll_content.setStyleSheet(f"background-color: {theme['bg_main']};")
        self._type_combo.setStyleSheet(
            f"background-color: {theme['bg_input']}; color: {theme['text_main']}; border: 1px solid {theme['border']};"
        )
        btn_style = (
            f"QPushButton {{ background-color: {theme['bg_panel']}; color: {theme['text_main']}; "
            f"border: 1px solid {theme['border']}; padding: 4px 10px; border-radius: 4px; }}"
            f"QPushButton:hover {{ background-color: {theme['bg_input']}; }}"
            f"QPushButton:disabled {{ color: {theme['border']}; }}"
        )
        for btn in (self._btn_extract, self._btn_save_all, self._btn_llm):
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

        self._build_ui()

        # Listen to Event Bus for Annotations
        self.bus.notes_data_ready.connect(self._handle_notes_event)
        self.bus.highlight_created.connect(self._handle_document_event)
        self.bus.highlight_updated.connect(self._handle_document_event)
        self.bus.highlight_deleted.connect(self._handle_document_event)
        self.bus.pdf_switched.connect(self._handle_document_event)
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
        ):
            try:
                signal.disconnect(slot)
            except (RuntimeError, TypeError):
                pass

    def _handle_notes_event(self, event: NotesEvent, payload: NotesEventPayload):
        if event == NotesEvent.DATA_READY:
            self._render_notes(payload.notes)

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
        self.lbl = QLabel("Highlights & Notes")
        ctrl_layout.addWidget(self.lbl)

        self.scope_combo = QComboBox()
        self.scope_combo.addItems(["Current Document", "Entire Project"])
        self.scope_combo.currentIndexChanged.connect(self.refresh_notes)
        ctrl_layout.addWidget(self.scope_combo)

        self.tag_combo = QComboBox()
        self.tag_combo.addItem("All Tags", None)
        self.tag_combo.currentIndexChanged.connect(self.refresh_notes)
        ctrl_layout.addWidget(self.tag_combo)
        annot_layout.addLayout(ctrl_layout)

        self.scroll_area = QScrollArea()
        self.scroll_area.setWidgetResizable(True)
        self.scroll_content = QWidget()
        self.scroll_layout = QVBoxLayout(self.scroll_content)
        self.scroll_layout.setAlignment(Qt.AlignmentFlag.AlignTop)
        self.scroll_area.setWidget(self.scroll_content)
        annot_layout.addWidget(self.scroll_area)

        self.tab_widget.addTab(self.annotations_widget, "📝 Notes")

        # 2. Discovery Tab (new deterministic extraction UI)
        self.extracted_widget = DiscoveryTabWidget(self._ctx, self)
        self.tab_widget.addTab(self.extracted_widget, "🔍 Discovery")

        self.tab_widget.currentChanged.connect(self._on_tab_changed)
        main_layout.addWidget(self.tab_widget)

        if self._ctx and hasattr(self._ctx, "blueprint_registry"):
            from gui.components.workflow_panel import WorkflowPanel
            self._workflow_panel = WorkflowPanel(
                app_context=self._ctx,
                mount_point="notes_dock",
                output_target_id="notes_dock_workflow",
                label="Workflow",
                parent=self,
            )
            main_layout.addWidget(self._workflow_panel)

    def _on_tab_changed(self, index):
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

    def refresh_tag_list(self):
        current_tag = self.tag_combo.currentData()
        self.tag_combo.blockSignals(True)
        self.tag_combo.clear()
        self.tag_combo.addItem("All Tags", None)

        pm = getattr(self._ctx, "project_manager", None) if self._ctx else None
        if pm:
            tags = pm.get_all_tags()
            for t in tags:
                self.tag_combo.addItem(t["name"], t["name"])

        index = self.tag_combo.findData(current_tag)
        if index >= 0:
            self.tag_combo.setCurrentIndex(index)
        self.tag_combo.blockSignals(False)

    def refresh_notes(self):
        self.refresh_tag_list()
        viewer = getattr(self._ctx, "viewer", None) if self._ctx else None
        active_source = (
            getattr(viewer, "current_source_path", None)
            or getattr(viewer, "pdf_path", None)
        )
        self.bus.notes_action_requested.emit(
            NotesIntent.FETCH,
            NotesPayload(
                scope=self.scope_combo.currentText(),
                tag=self.tag_combo.currentData(),
                active_pdf=active_source,
            ),
        )

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

