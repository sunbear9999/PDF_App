import os
import shiboken6
from PySide6.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QLabel,
                             QPushButton, QScrollArea, QFrame, QComboBox,
                             QMenu, QTabWidget,QTextEdit)
from PySide6.QtCore import Qt, QTimer

from gui.components.dialogs.tag_manager_dialog import TagAssignmentDialog
from core.events.event_bus import EventBus
from core.events.domains.document_events import AnnotationIntent, AnnotationPayload, DocumentEvent, DocumentEventPayload, DocumentIntent, DocumentPayload
from core.events.domains.metadata_events import NotesEvent, NotesEventPayload, NotesIntent, NotesPayload
from core.models.ontology_model import EntityIntent, EntityPayload
from core.ontology.registry import OntologyRegistry
from gui.components.dialogs.entity_editor_dialog import EntityEditorDialog
import html

class NoteBubble(QFrame):
    # [KEEP EXISTING NoteBubble EXACTLY AS IS]
    def __init__(self, tab, data_dict):
        super().__init__()
        self.tab = tab
        self.pdf_path = data_dict["pdf_path"]
        self.page_num = data_dict["page_num"]
        self.annot_id = data_dict["annot_id"]
        self.is_ai = data_dict["is_ai"]
        color = data_dict["color"]

        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 10, 10, 10)

        header_layout = QHBoxLayout()
        doc_name = os.path.basename(self.pdf_path)
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
            border = "2px solid white" if is_close(c_val, color) else "none"
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

        if hasattr(self.tab, 'main_window') and hasattr(self.tab.main_window, 'theme_manager'):
            self.apply_theme(self.tab.main_window.theme_manager.get_theme())

    def apply_theme(self, theme):
        pass

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self.tab.bus.document_action_requested.emit(DocumentIntent.OPEN, DocumentPayload(path=self.pdf_path))
            self.tab.bus.annotation_action_requested.emit(AnnotationIntent.JUMP_TO_PAGE, AnnotationPayload(page_num=self.page_num))
        super().mousePressEvent(event)

    def contextMenuEvent(self, event):
        menu = QMenu(self)
        if hasattr(self.tab, 'main_window') and hasattr(self.tab.main_window, 'theme_manager'):
            theme = self.tab.main_window.theme_manager.get_theme()
            menu.setStyleSheet(f"QMenu {{ background-color: {theme['bg_panel']}; color: {theme['text_main']}; border: 1px solid {theme['border']}; padding: 5px; font-weight: bold; }} QMenu::item:selected {{ background-color: {theme['accent']}; color: #ffffff; }}")

        tag_action = menu.addAction("🏷️ Manage Tags")
        if menu.exec(event.globalPos()) == tag_action:
            self.manage_tags()

    def manage_tags(self):
        dlg = TagAssignmentDialog(self.annot_id, "node", self)
        if dlg.exec():
            self.tab.bus.notes_action_requested.emit(NotesIntent.SYNC_TAGS, NotesPayload(annot_id=self.annot_id))


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
        
        # Leverage the viewer's framing search to find the exact coordinates
        if text_to_find and hasattr(self.tab.main_window, 'viewer'):
            self.tab.main_window.viewer.jump_to_source(os.path.basename(doc_path), text_to_find)
        else:
            # Fallback for empty text: just open the document and page
            if doc_path:
                self.tab.bus.document_action_requested.emit(DocumentIntent.OPEN, DocumentPayload(path=doc_path))
                page_num = self.entity.properties.get("page_num")
                if page_num is not None:
                    self.tab.bus.annotation_action_requested.emit(AnnotationIntent.JUMP_TO_PAGE, AnnotationPayload(page_num=page_num))

    def _edit_and_save_entity(self):
        # Assumes EntityEditorDialog is defined higher up in notes_dock.py
        dialog = EntityEditorDialog(self.entity, self.theme, self)
        if dialog.exec():
            updated_props = dialog.get_updated_properties()
            self.tab.bus.entity_action_requested.emit(EntityIntent.UPDATE_PROPERTIES, EntityPayload(entity_id=self.entity.id, data=updated_props))
            self.tab.bus.entity_action_requested.emit(EntityIntent.VERIFY, EntityPayload(entity_id=self.entity.id))

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
class ExtractedEntitiesWidget(QWidget):
    def __init__(self, main_window, parent=None):
        super().__init__(parent)
        self.main_window = main_window
        self.bus = EventBus.get_instance()
        self.theme = None
        self._is_destroyed = False
        self._page_size = 80
        self._visible_limit = self._page_size
        self._refresh_timer = QTimer(self)
        self._refresh_timer.setSingleShot(True)
        self._refresh_timer.setInterval(150)
        self._refresh_timer.timeout.connect(self.refresh_entities)
        self._build_ui()

        # Listen for global graph discoveries or verify/reject events to refresh the list
        self.bus.discovery_items_changed.connect(self._on_discovery_items_changed)
        self.bus.entity_changed.connect(self._on_entity_changed)
        self.destroyed.connect(self._on_destroyed)

    def _on_destroyed(self, *_args):
        self._is_destroyed = True
        self._disconnect_bus_signals()

    def _disconnect_bus_signals(self):
        for signal, slot in (
            (self.bus.discovery_items_changed, self._on_discovery_items_changed),
            (self.bus.entity_changed, self._on_entity_changed),
        ):
            try:
                signal.disconnect(slot)
            except (RuntimeError, TypeError):
                pass

    def _on_discovery_items_changed(self, _intent, _payload):
        self.schedule_refresh()

    def _on_entity_changed(self, intent: EntityIntent, payload: EntityPayload):
        if intent in {EntityIntent.VERIFY, EntityIntent.PURGE_GLOBALLY}:
            self.schedule_refresh()

    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        # Dynamic Dropdown Header
        header_layout = QHBoxLayout()
        header_layout.addWidget(QLabel("Filter by Type:"))
        
        self.type_combo = QComboBox()
        self.type_combo.addItem("All Extracted Types", "ALL")
        
        # Dynamically populate from the Ontology Registry based on extraction capabilities
        registry = OntologyRegistry()
        for blueprint in registry.all_entities():
            # If the blueprint has extraction_hints, it implies the system can auto-generate it
            if blueprint.extraction_hints:
                self.type_combo.addItem(blueprint.display_name, blueprint.type_key)
                
        self.type_combo.currentIndexChanged.connect(self._on_type_filter_changed)
        header_layout.addWidget(self.type_combo)
        layout.addLayout(header_layout)

        # Scroll Area for Entity Bubbles
        self.scroll_area = QScrollArea()
        self.scroll_area.setWidgetResizable(True)
        self.scroll_content = QWidget()
        self.scroll_layout = QVBoxLayout(self.scroll_content)
        self.scroll_layout.setAlignment(Qt.AlignmentFlag.AlignTop)
        self.scroll_area.setWidget(self.scroll_content)
        layout.addWidget(self.scroll_area)

    def _on_type_filter_changed(self):
        self._visible_limit = self._page_size
        self.schedule_refresh()

    def schedule_refresh(self):
        if not self._can_use_entity_layout():
            return
        self._refresh_timer.start()

    def refresh_entities(self):
        if not self._can_use_entity_layout():
            return
        if not hasattr(self.main_window, 'project_manager') or not getattr(self.main_window.project_manager, 'db_graph', None):
            return

        # Clear existing
        self._clear_entity_layout()

        selected_type = self.type_combo.currentData()
        limit = self._visible_limit + 1
        pm = self.main_window.project_manager
        if hasattr(pm, "get_unverified_entities"):
            unverified_entities = pm.get_unverified_entities(
                limit=limit,
                entity_type=None if selected_type == "ALL" else selected_type,
            )
        else:
            unverified_entities = pm.db_graph.get_unverified_entities(limit=limit, entity_type=None if selected_type == "ALL" else selected_type)
        has_more = len(unverified_entities) > self._visible_limit
        unverified_entities = unverified_entities[:self._visible_limit]

        count = 0
        for entity in unverified_entities:
            bubble = DiscoveredEntityBubble(self, entity, self.theme)
            self.scroll_layout.addWidget(bubble)
            count += 1

        if count == 0:
            empty_lbl = QLabel("No unverified entities found for this type.")
            empty_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
            empty_lbl.setStyleSheet("color: #888; margin-top: 20px;")
            self.scroll_layout.addWidget(empty_lbl)
        elif has_more:
            btn_more = QPushButton(f"Load {self._page_size} more")
            btn_more.clicked.connect(self._load_more_entities)
            if self.theme:
                btn_more.setStyleSheet(f"background-color: {self.theme['bg_input']}; color: {self.theme['text_main']}; border: 1px solid {self.theme['border']}; padding: 6px; border-radius: 4px;")
            self.scroll_layout.addWidget(btn_more)

    def _load_more_entities(self):
        self._visible_limit += self._page_size
        self.refresh_entities()

    def update_theme(self, theme):
        if not self._can_use_entity_layout():
            return
        self.theme = theme
        self.scroll_area.setStyleSheet("background: transparent; border: none;")
        self.scroll_content.setStyleSheet(f"background-color: {theme['bg_main']};")
        self.type_combo.setStyleSheet(f"background-color: {theme['bg_input']}; color: {theme['text_main']}; border: 1px solid {theme['border']};")
        for i in range(self.scroll_layout.count()):
            widget = self.scroll_layout.itemAt(i).widget()
            if isinstance(widget, DiscoveredEntityBubble):
                widget.apply_theme(theme)

    def _can_use_entity_layout(self):
        try:
            return (
                not self._is_destroyed
                and shiboken6.isValid(self)
                and hasattr(self, "scroll_layout")
                and shiboken6.isValid(self.scroll_layout)
                and hasattr(self, "scroll_content")
                and shiboken6.isValid(self.scroll_content)
                and hasattr(self, "type_combo")
                and shiboken6.isValid(self.type_combo)
            )
        except RuntimeError:
            return False

    def _clear_entity_layout(self):
        if not self._can_use_entity_layout():
            return
        try:
            while self.scroll_layout.count():
                item = self.scroll_layout.takeAt(0)
                widget = item.widget()
                if widget and shiboken6.isValid(widget):
                    widget.deleteLater()
        except RuntimeError:
            self._is_destroyed = True


class NotesTab(QWidget):
    def __init__(self, parent=None, main_window=None):
        super().__init__(parent)
        self.main_window = main_window
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

        # --- Wrap the UI in a Tab Widget ---
        self.tab_widget = QTabWidget()
        
        # 1. Annotations Tab (Original UI)
        self.annotations_widget = QWidget()
        annot_layout = QVBoxLayout(self.annotations_widget)
        annot_layout.setContentsMargins(5, 5, 5, 5)

        # Header controls for Annotations
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

        # Annotations Scroll Area
        self.scroll_area = QScrollArea()
        self.scroll_area.setWidgetResizable(True)
        self.scroll_content = QWidget()
        self.scroll_layout = QVBoxLayout(self.scroll_content)
        self.scroll_layout.setAlignment(Qt.AlignmentFlag.AlignTop)
        self.scroll_area.setWidget(self.scroll_content)
        annot_layout.addWidget(self.scroll_area)
        
        self.tab_widget.addTab(self.annotations_widget, "📝 Notes")

        # 2. Auto-Extracted Entities Tab (New UI)
        self.extracted_widget = ExtractedEntitiesWidget(self.main_window, self)
        self.tab_widget.addTab(self.extracted_widget, "✨ Discovered")

        # Refresh entities when tab changes
        self.tab_widget.currentChanged.connect(self._on_tab_changed)
        main_layout.addWidget(self.tab_widget)

    def _on_tab_changed(self, index):
        if index == 1: # Discovered Tab
            self.extracted_widget.refresh_entities()

    def apply_tag_filter(self, tag_name):
        self.tab_widget.setCurrentIndex(0) # Ensure we are on Annotations tab
        index = self.tag_combo.findData(tag_name)
        if index >= 0:
            self.tag_combo.setCurrentIndex(index)

    def refresh_tag_list(self):
        current_tag = self.tag_combo.currentData()
        self.tag_combo.blockSignals(True)
        self.tag_combo.clear()
        self.tag_combo.addItem("All Tags", None)

        if self.main_window and hasattr(self.main_window, 'project_manager'):
            tags = self.main_window.project_manager.get_all_tags()
            for t in tags:
                self.tag_combo.addItem(t["name"], t["name"])

        index = self.tag_combo.findData(current_tag)
        if index >= 0: self.tag_combo.setCurrentIndex(index)
        self.tag_combo.blockSignals(False)

    def refresh_notes(self):
        self.refresh_tag_list()
        self.bus.notes_action_requested.emit(
            NotesIntent.FETCH,
            NotesPayload(
                scope=self.scope_combo.currentText(),
                tag=self.tag_combo.currentData(),
                active_pdf=self.main_window.current_file_path if self.main_window else None,
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

    def update_theme(self, theme):
        if self._is_destroyed or not shiboken6.isValid(self):
            return
        self.setStyleSheet(f"background-color: {theme['bg_main']};")
        
        # Update Annotations Tab Theme
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

        # Update Extracted Tab Theme
        if hasattr(self, "extracted_widget") and shiboken6.isValid(self.extracted_widget):
            self.extracted_widget.update_theme(theme)
        
        # Style the main TabWidget
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
