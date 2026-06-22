import shiboken6
from PySide6.QtCore import QObject, QThread, Signal
from core.events.event_bus import EventBus
import fitz
from core.events.domains.metadata_events import NotesEvent, NotesEventPayload, NotesIntent, NotesPayload
from core.events.domains.document_events import AnnotationIntent, AnnotationPayload, DocumentEvent, DocumentEventPayload
from core.events.domains.workspace_events import WorkspaceIntent, WorkspacePayload
class NotesFetchWorker(QThread):
    notes_ready = Signal(list)
    notes_failed = Signal(str)

    def __init__(self, pm, scope, tag_name, active_pdf, parent=None):
        super().__init__(parent)
        self.pm = pm
        self.scope = scope
        self.tag_name = tag_name
        self.active_pdf = active_pdf

    def run(self):
        try:
            if self.scope in {"Current PDF", "Current Document"} and self.active_pdf:
                paths_to_check = [self.active_pdf]
            elif hasattr(self.pm, "list_sources"):
                paths_to_check = [s.get("path") for s in self.pm.list_sources() if s.get("path")]
            else:
                paths_to_check = self.pm.pdfs
            notes_data = []

            for path in paths_to_check:
                source_type = self.pm.get_source_type(path) if hasattr(self.pm, "get_source_type") else "pdf"
                if source_type == "video":
                    doc_tags = [t["name"] for t in self.pm.get_tags_for_doc(path)]
                    for record in (self.pm.get_highlights() or {}).values():
                        if record.get("doc_id") != path:
                            continue
                        annot_id = record.get("id", "")
                        if not (annot_id.startswith("UserNote") or annot_id.startswith("AINote") or annot_id.startswith("VideoNote")):
                            continue
                        node_tags = self.pm.get_tags_for_node(annot_id)
                        node_tag_names = [t["name"] for t in node_tags]
                        if self.tag_name and self.tag_name not in doc_tags and self.tag_name not in node_tag_names:
                            continue
                        notes_data.append({
                            "pdf_path": path,
                            "page_num": record.get("page_num") or 0,
                            "annot_id": annot_id,
                            "subject": record.get("text_content", ""),
                            "content": record.get("note_content", ""),
                            "color": record.get("color") or "#b366ff",
                            "is_ai": annot_id.startswith("AINote"),
                            "source_type": "video",
                            "tags": node_tags,
                        })
                    continue

                doc = self.pm.get_doc(path)
                if not doc:
                    continue

                doc_tags = [t["name"] for t in self.pm.get_tags_for_doc(path)]

                for i in range(len(doc)):
                    page = doc.load_page(i)
                    for annot in page.annots() or []:
                        if not annot.info:
                            continue

                        title = annot.info.get("title", "")
                        if title.startswith("UserNote") or title.startswith("AINote"):
                            node_tags = self.pm.get_tags_for_node(title)
                            node_tag_names = [t["name"] for t in node_tags]

                            if self.tag_name and self.tag_name not in doc_tags and self.tag_name not in node_tag_names:
                                continue

                            notes_data.append({
                                "pdf_path": path,
                                "page_num": i,
                                "annot_id": title,
                                "subject": annot.info.get("subject", ""),
                                "content": annot.info.get("content", ""),
                                "color": annot.colors.get("stroke"),
                                "is_ai": title.startswith("AINote"),
                                "source_type": "pdf",
                                "tags": node_tags,
                            })

            self.notes_ready.emit(notes_data)
        except Exception as exc:
            self.notes_failed.emit(str(exc))


class NotesAppService(QObject):
    def __init__(self, project_manager):
        super().__init__()
        self.pm = project_manager
        self.bus = EventBus.get_instance()
        self.bus.notes_action_requested.connect(self._handle_intent)
        self.worker = None

    def _handle_intent(self, intent: NotesIntent, payload: NotesPayload):
        if intent == NotesIntent.FETCH:
            self._fetch_notes(payload)
        elif intent == NotesIntent.DELETE:
            self._modify_note(payload, action="delete")
        elif intent == NotesIntent.CHANGE_COLOR:
            self._modify_note(payload, action="color")
        elif intent == NotesIntent.SYNC_TAGS:
            # Tell the Workspace Service to update its nodes
            self.bus.workspace_action_requested.emit(WorkspaceIntent.SYNC_TAGS_FROM_ANNOT, WorkspacePayload(annot_id=payload.get("annot_id")))
            self.pm.mark_dirty("workspace")
            # Refresh the notes list
            self._fetch_notes(NotesPayload(scope="Entire Project", tag=None, active_pdf=None))

    def _fetch_notes(self, payload: NotesPayload):
        if self.worker and shiboken6.isValid(self.worker) and self.worker.isRunning(): return

        self.worker = NotesFetchWorker(
            self.pm,
            payload.get("scope", "Current PDF"),
            payload.get("tag"),
            payload.get("active_pdf")
        )
        self.worker.notes_ready.connect(
            lambda data: self.bus.notes_data_ready.emit(NotesEvent.DATA_READY, NotesEventPayload(notes=data))
        )
        self.worker.notes_failed.connect(
            lambda message: self.bus.status_message_requested.emit(f"Notes failed to load: {message}", 7000)
        )
        self.worker.finished.connect(self.worker.deleteLater)
        self.worker.start()

    def _modify_note(self, payload: NotesPayload, action: str):
        pdf_path = payload.get("pdf_path")
        page_num = payload.get("page_num")
        annot_id = payload.get("annot_id")
        
        try:
            source_type = self.pm.get_source_type(pdf_path) if hasattr(self.pm, "get_source_type") else "pdf"
            if source_type == "video":
                if action == "delete":
                    self.pm.delete_highlight_record(annot_id)
                    self.bus.highlight_deleted.emit(DocumentEvent.HIGHLIGHT_DELETED, DocumentEventPayload(annot_id=annot_id))
                elif action == "color":
                    color = payload.get("color")
                    if not isinstance(color, str):
                        color = "#{:02x}{:02x}{:02x}".format(*(max(0, min(255, int(c * 255))) for c in color[:3]))
                    self.pm.update_highlight_color(annot_id, color)
                    self.bus.highlight_updated.emit(
                        DocumentEvent.HIGHLIGHT_UPDATED,
                        DocumentEventPayload(annot_id=annot_id, changes={"color": color, "pdf_path": pdf_path, "page_num": page_num}),
                    )
                return
            doc = self.pm.get_doc(pdf_path)
            if not doc: return
            page = doc.load_page(page_num)
            
            for annot in page.annots():
                if annot.info and annot.info.get("title") == annot_id:
                    if action == "delete":
                        page.delete_annot(annot)
                    elif action == "color":
                        annot.set_colors(stroke=payload.get("color"))
                        annot.update()
                    break
                    
            self.pm.mark_dirty(pdf_path)
            
            # Tell the viewer to physically redraw the page if it's currently open
            self.bus.annotation_action_requested.emit(AnnotationIntent.FORCE_REDRAW, AnnotationPayload(page_num=page_num, pdf_path=pdf_path))
            
            # Trigger a re-fetch so the dock updates
            self.bus.highlight_updated.emit(
                DocumentEvent.HIGHLIGHT_UPDATED,
                DocumentEventPayload(annot_id=annot_id, changes={}),
            )
            
        except Exception as e:
            print(f"Note Modification Error: {e}")
