from __future__ import annotations

import os
import re

from PySide6.QtCore import QTimer

from core.models.workspace_models import NodeModel
from core.models.intelligence_models import ArtifactRecord
from core.models.artifact_reference import ArtifactRef
from core.services.intelligence.reference_note_records import anchor_of
from core.utils.workspace_utils import compute_node_dimensions, normalize_annotation_text
from core.events.domains.document_events import DocumentIntent, DocumentPayload


class WorkspaceAnnotationService:
    def __init__(self, main_window, event_bus=None, *, reference_note_opener=None, linked_note_manager=None):
        self.main_window = main_window
        self.bus = event_bus
        self.reference_note_opener = reference_note_opener
        self.linked_note_manager = linked_note_manager

    @property
    def pm(self):
        return getattr(self.main_window, "project_manager", None)

    def _dock_widgets(self, dock_id: str, legacy_attr: str = ""):
        dock_manager = getattr(self.main_window, "dock_manager", None)
        if dock_manager:
            return dock_manager.get_inner_widgets(dock_id)
        return getattr(self.main_window, legacy_attr, []) if legacy_attr else []

    def jump_to_node_source(self, node):
        if not node:
            return
        source = None
        quote = ""
        if hasattr(node, "quote_text"):
            quote = (node.quote_text() or "").strip()
        quote = quote or (getattr(node, "quote", "") or "").strip()
        if not getattr(node, "pdf_path", None) or node.page_num is None:
            source = self.find_source_for_node(node)
            if source:
                node.pdf_path = source.get("pdf_path") or source.get("doc_id")
                node.page_num = source.get("page_num")
                if source.get("id") and not getattr(node, "highlight_id", None):
                    node.highlight_id = source.get("id")

        if not getattr(node, "pdf_path", None) and not quote:
            return

        main_win = self.main_window
        annot_id = getattr(node, "highlight_id", None) or getattr(node, "node_id", None)
        if source and source.get("id"):
            annot_id = source["id"]
        source_type = ""
        if getattr(node, "pdf_path", None) and self.pm and hasattr(self.pm, "get_source_type"):
            source_type = self.pm.get_source_type(node.pdf_path)

        if source_type in ("video", "audio"):
            source_id = getattr(node, "source_id", None)
            if not source_id and self.pm and hasattr(self.pm, "get_source_entity_by_path"):
                source_entity = self.pm.get_source_entity_by_path(getattr(node, "pdf_path", None))
                source_id = source_entity.id if source_entity else None
            if hasattr(main_win, "viewer") and hasattr(main_win.viewer, "jump_to_video"):
                main_win.viewer.jump_to_video(
                    source_id=source_id,
                    path=getattr(node, "pdf_path", None),
                    timestamp=getattr(node, "page_num", 0) or 0,
                )
            return

        if getattr(node, "highlight_id", None) and getattr(node, "page_num", None) is not None:
            if getattr(node, "pdf_path", None) and self.bus:
                self.bus.document_action_requested.emit(
                    DocumentIntent.OPEN,
                    DocumentPayload(path=node.pdf_path, source_id=getattr(node, "source_id", None), source_type="pdf"),
                )
            if hasattr(main_win, "viewer") and hasattr(main_win.viewer, "jump_to_annotation"):
                QTimer.singleShot(75, lambda: main_win.viewer.jump_to_annotation(node.page_num, annot_id))
                return
            if hasattr(main_win, "viewer"):
                QTimer.singleShot(75, lambda: main_win.viewer.jump_to_page(node.page_num))
                return

        if quote and hasattr(main_win, "viewer") and hasattr(main_win.viewer, "jump_to_source"):
            doc_name = (
                (getattr(node, "entity_properties", {}) or {}).get("doc_name")
                or os.path.basename(getattr(node, "pdf_path", "") or "")
            )
            main_win.viewer.jump_to_source(doc_name, quote)

    def mirror_note_edit_to_notes(self, node):
        # Canvas commentary is a view copy. All canonical edits require an
        # explicit guarded inspector action; source files are never mirrored.
        return None

    def refresh_notes(self):
        for notes_dock in self._dock_widgets("notes", "notes_docks"):
            notes_dock.refresh_notes()

    def get_physical_annotations(self) -> dict:
        """Return highlight metadata directly from open project PDFs."""
        pm = self.pm
        annotations = {}
        if not pm:
            return annotations

        from core.services.content.physical_note_browsing import physical_notes
        for pdf_path in getattr(pm, 'pdfs', []) or []:
            for row in physical_notes(pm, pdf_path, ''):
                annotations[row['annot_id']] = {**row, 'id': row['annot_id'], 'doc_id': pdf_path,
                    'text_content': row['subject'], 'note_content': row['content']}

        return annotations

    def get_annotation_index(self) -> dict:
        """Merge DB records with physical PDF annotations, preferring live PDF metadata."""
        pm = self.pm
        db_annotations = pm.get_highlights() if pm and hasattr(pm, "get_highlights") else {}
        physical_annotations = self.get_physical_annotations()
        merged = dict(db_annotations)

        for annot_id, physical in physical_annotations.items():
            existing = merged.get(annot_id, {})
            merged[annot_id] = {**existing, **physical}

        return merged

    def find_source_for_node(self, node):
        pm = self.pm
        if not pm or not node:
            return None

        annot_id = getattr(node, "highlight_id", None) or getattr(node, "node_id", None)
        return pm.get_highlight(annot_id) if annot_id and hasattr(pm, 'get_highlight') else None

    def manage_linked_note(self, highlight_id):
        if self.linked_note_manager:
            return self.linked_note_manager(highlight_id)
        if self.bus:
            self.bus.status_message_requested.emit("Manage linked project notes in Notes using their guarded actions.", 5000)
        return None

    def delete_highlight_permanently(self, highlight_id: str, fallback_node=None):
        if str(highlight_id).startswith('LinkedNote|'):
            self.manage_linked_note(highlight_id)
        elif self.bus:
            self.bus.status_message_requested.emit('Physical annotations are read only. Adopt in Notes to manage a project copy; remove canvas cards separately.', 7000)
        return False

    def get_pdf_annotation_note(self, highlight_id: str, pdf_path: str, page_num: int) -> str:
        if str(highlight_id).startswith('LinkedNote|'):
            return ''
        from core.services.content.physical_note_browsing import physical_notes_page
        result = physical_notes_page(self.pm, pdf_path, position={'page': page_num, 'xref': 0})
        return next((row['content'] for row in result['items'] if row['annot_id'] == highlight_id), '')

    def node_model_from_annotation(self, annotation: dict, workspace_id: int, x: float = 0.0, y: float = 0.0) -> NodeModel:
        node_id = annotation["id"]
        quote, note = normalize_annotation_text(annotation)
        color = annotation.get("color") or ("#2d2238" if str(node_id).startswith("AINote") else "#2b2b2b")
        width, height = compute_node_dimensions(quote, note)
        pdf_path = annotation.get("pdf_path") or annotation.get("doc_id")
        source_id, reference = self._annotation_identity(annotation, quote)
        suggested_types = self._suggest_entity_types_for_quote(quote)
        return NodeModel(
            id=node_id,
            highlight_id=node_id,
            workspace_id=workspace_id,
            quote=quote,
            note=note,
            color=color,
            is_custom=False,
            pdf_path=pdf_path,
            page_num=annotation.get("page_num"),
            x=x,
            y=y,
            width=width,
            height=height,
            node_origin="human",
            original_text=note,
            entity_type="entity.quote",
            source_id=source_id,
            entity_properties={
                "quote": quote,
                "exact_text": quote,
                "text": quote,
                "note_text": note,
                "pdf_path": pdf_path,
                "page_num": annotation.get("page_num"),
                "highlight_id": node_id,
                "source_id": source_id,
                "suggested_entity_types": suggested_types,
                **({"artifact_ref": reference, "primary_evidence_id": reference["artifact_id"]} if reference else {}),
            },
        )

    def _annotation_identity(self, annotation, quote):
        if annotation.get('artifact_ref'):
            reference=ArtifactRef.from_value(annotation['artifact_ref'])
            if reference.source_id and reference.quote==quote:
                return reference.source_id,reference.to_dict()
        if annotation.get('source_id'):
            return annotation['source_id'],{}
        anchor = anchor_of(annotation)
        if isinstance(anchor, dict) and anchor.get('source_id'):
            source_id = anchor['source_id']
            identity = ArtifactRecord.stable_id(source_id, 'annotation', 'highlights', annotation['id'])
            return source_id, ArtifactRef(identity, quote, source_id=source_id).to_dict()
        path = annotation.get('pdf_path') or annotation.get('doc_id')
        lookup = getattr(self.pm, 'get_source_entity_by_path', None)
        source = lookup(path) if lookup and path else None
        if source and str(annotation['id']).startswith('LinkedNote|'):
            identity = ArtifactRecord.stable_id(source.id, 'annotation', 'highlights', annotation['id'])
            return source.id, ArtifactRef(identity, quote, source_id=source.id).to_dict()
        return source.id if source else None, {}

    def _suggest_entity_types_for_quote(self, quote: str) -> list[str]:
        suggestions = []
        text = quote or ""
        if re.search(r"\b(?:\d{1,2}[/-]\d{1,2}[/-]\d{2,4}|\d{4}|Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|Jun(?:e)?|Jul(?:y)?|Aug(?:ust)?|Sep(?:tember)?|Oct(?:ober)?|Nov(?:ember)?|Dec(?:ember)?)\b", text):
            suggestions.append("entity.timeline_event")
        if re.search(r"\b[A-Z][a-z]+(?:\s+[A-Z][a-z]+){1,3}\b", text):
            suggestions.append("entity.person_org")
        return suggestions

    def add_annotation_to_workspace(self, annotation: dict, workspace_id: int, x: float = 0.0, y: float = 0.0):
        pm = self.pm
        if not pm or not getattr(pm, "project_filepath", None):
            return None
        model = pm.get_workspace_data(workspace_id)
        node_id = annotation["id"]
        if not any(n.id == node_id for n in model.nodes):
            model.nodes.append(self.node_model_from_annotation(annotation, workspace_id, x, y))
            pm.sync_workspace(model)
        return node_id

    def attach_native_ai_annotations(self, nodes):
        """Graph output keeps its artifact references; importing it never writes source files."""
        return nodes

    def add_ai_annotation(self, quote, note, target_doc_name=None, allowed_paths=None,
                          forced_annot_id=None, emit_signal=True, *, reference=None, origin=None):
        """Compatibility draft opener; persistence requires the shared guarded editor."""
        ref = ArtifactRef.from_value(reference)
        if not ref.artifact_id or not self.reference_note_opener or forced_annot_id:
            if self.bus:
                self.bus.status_message_requested.emit("Choose a canonical source reference and use Save linked note. Filename/quote-search saves and caller-supplied highlight IDs are unsupported.", 6000)
            return False
        if quote != ref.quote:
            if self.bus:
                self.bus.status_message_requested.emit("The quote differs from its saved reference. Inspect the source reference before saving.", 6000)
            return False
        origin = origin or {"kind": "assistant", "suggested_text": note}
        return bool(self.reference_note_opener(ref.to_dict(), note, origin=origin))
