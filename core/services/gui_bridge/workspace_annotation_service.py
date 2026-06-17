from __future__ import annotations

import os
import re

from core.models.workspace_models import NodeModel
from core.utils.workspace_utils import compute_node_dimensions, normalize_annotation_text
from core.events.domains.document_events import DocumentEvent, DocumentEventPayload, DocumentIntent, DocumentPayload


class WorkspaceAnnotationService:
    def __init__(self, main_window, event_bus=None):
        self.main_window = main_window
        self.bus = event_bus

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
        if getattr(node, "highlight_id", None) and getattr(node, "page_num", None) is not None:
            if hasattr(main_win, "viewer") and hasattr(main_win.viewer, "jump_to_annotation"):
                main_win.viewer.jump_to_annotation(node.page_num, annot_id)
                return
            if hasattr(main_win, "viewer"):
                main_win.viewer.jump_to_page(node.page_num)
                return

        if quote and hasattr(main_win, "viewer") and hasattr(main_win.viewer, "jump_to_source"):
            doc_name = (
                (getattr(node, "entity_properties", {}) or {}).get("doc_name")
                or os.path.basename(getattr(node, "pdf_path", "") or "")
            )
            main_win.viewer.jump_to_source(doc_name, quote)

    def mirror_note_edit_to_notes(self, node):
        if not node or getattr(node, "is_custom", False) or getattr(node, "pdf_path", None) is None:
            return
        annot_id = getattr(node, "highlight_id", None) or getattr(node, "node_id", None)
        for notes_dock in self._dock_widgets("notes", "notes_docks"):
            notes_dock._modify_note(
                node.pdf_path,
                node.page_num,
                annot_id,
                action="edit_content",
                content=getattr(node, "note", ""),
                refresh=False,
            )

    def refresh_notes(self):
        for notes_dock in self._dock_widgets("notes", "notes_docks"):
            notes_dock.refresh_notes()

    def get_physical_annotations(self) -> dict:
        """Return highlight metadata directly from open project PDFs."""
        pm = self.pm
        annotations = {}
        if not pm:
            return annotations

        for pdf_path in getattr(pm, "pdfs", []) or []:
            try:
                doc = pm.get_doc(pdf_path)
                if not doc:
                    continue

                for page_num in range(len(doc)):
                    page = doc.load_page(page_num)
                    for annot in page.annots() or []:
                        info = annot.info or {}
                        annot_id = info.get("title")
                        if not annot_id or not (annot_id.startswith("UserNote") or annot_id.startswith("AINote")):
                            continue

                        color = None
                        stroke = (annot.colors or {}).get("stroke")
                        if stroke:
                            try:
                                from PySide6.QtGui import QColor
                                color = QColor(int(stroke[0] * 255), int(stroke[1] * 255), int(stroke[2] * 255)).name()
                            except Exception:
                                color = None

                        annotations[annot_id] = {
                            "id": annot_id,
                            "doc_id": pdf_path,
                            "pdf_path": pdf_path,
                            "doc_name": os.path.basename(pdf_path),
                            "page_num": page_num,
                            "rect_coords": repr(list(annot.rect)),
                            "text_content": info.get("subject", ""),
                            "subject": info.get("subject", ""),
                            "content": info.get("content", ""),
                            "note_content": info.get("content", ""),
                            "color": color,
                        }
            except Exception as e:
                print(f"Error scanning annotations in {pdf_path}: {e}")

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
            if pm and hasattr(pm, "upsert_highlight"):
                pm.upsert_highlight({
                    "id": annot_id,
                    "doc_id": physical.get("pdf_path"),
                    "page_num": physical.get("page_num"),
                    "rect_coords": physical.get("rect_coords"),
                    "text_content": physical.get("text_content", ""),
                    "note_content": physical.get("content", ""),
                    "color": physical.get("color"),
                })

        return merged

    def find_source_for_node(self, node):
        pm = self.pm
        if not pm or not node:
            return None

        annot_id = getattr(node, "highlight_id", None) or getattr(node, "node_id", None)
        annotations = self.get_annotation_index()
        if annot_id in annotations:
            return annotations[annot_id]

        quote = (getattr(node, "quote", "") or "").strip()
        if not quote:
            return None
        for annotation in annotations.values():
            if quote == (annotation.get("text_content") or annotation.get("subject") or "").strip():
                return annotation
        return None

    def delete_highlight_permanently(self, highlight_id: str, fallback_node=None):
        pm = self.pm
        if not pm:
            return

        highlight_record = pm.get_highlight(highlight_id) if hasattr(pm, "get_highlight") else None
        pdf_path, page_num = None, None
        if highlight_record:
            pdf_path, page_num = highlight_record.get("doc_id"), highlight_record.get("page_num")
        elif fallback_node:
            pdf_path, page_num = getattr(fallback_node, "pdf_path", None), getattr(fallback_node, "page_num", None)

        if pdf_path is not None and page_num is not None:
            try:
                doc = pm.get_doc(pdf_path)
                if doc:
                    page = doc.load_page(page_num)
                    for annot in page.annots():
                        if annot.info and annot.info.get("title") == highlight_id:
                            page.delete_annot(annot)
                            break
                    pm.mark_dirty(pdf_path)
                    if pdf_path == getattr(self.main_window, "current_file_path", None) and hasattr(self.main_window, "viewer"):
                        self.main_window.viewer.reload_page(page_num)
            except Exception as e:
                print(f"Error removing physical annotation: {e}")

            self.refresh_notes()

        if hasattr(pm, "delete_highlight_record"):
            pm.delete_highlight_record(highlight_id)

    def get_pdf_annotation_note(self, highlight_id: str, pdf_path: str, page_num: int) -> str:
        pm = self.pm
        if not pm or not pdf_path or page_num is None:
            return ""
        doc = pm.get_doc(pdf_path)
        if not doc:
            return ""
        try:
            page = doc.load_page(page_num)
            for annot in page.annots():
                if annot.info and annot.info.get("title") == highlight_id:
                    return annot.info.get("content", "")
        except Exception:
            return ""
        return ""

    def node_model_from_annotation(self, annotation: dict, workspace_id: int, x: float = 0.0, y: float = 0.0) -> NodeModel:
        node_id = annotation["id"]
        quote, note = normalize_annotation_text(annotation)
        color = annotation.get("color") or ("#2d2238" if str(node_id).startswith("AINote") else "#2b2b2b")
        width, height = compute_node_dimensions(quote, note)
        pdf_path = annotation.get("pdf_path") or annotation.get("doc_id")
        source = self.pm.get_source_entity_by_path(pdf_path) if self.pm and pdf_path and hasattr(self.pm, "get_source_entity_by_path") else None
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
            node_origin="ai" if str(node_id).startswith("AINote") else "human",
            original_text=note,
            entity_type="entity.quote",
            source_id=source.id if source else None,
            entity_properties={
                "quote": quote,
                "exact_text": quote,
                "text": quote,
                "note_text": note,
                "pdf_path": pdf_path,
                "page_num": annotation.get("page_num"),
                "highlight_id": node_id,
                "source_id": source.id if source else None,
                "suggested_entity_types": suggested_types,
            },
        )

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
        pm = self.pm
        if not pm or not hasattr(self.main_window, "add_ai_annotation"):
            return nodes

        import uuid

        for node in nodes:
            if not node.pdf_path or node.highlight_id:
                continue
            new_annot_id = f"AINote|{uuid.uuid4()}"
            ok = self.main_window.add_ai_annotation(
                node.quote,
                node.note,
                target_doc_name=node.pdf_path,
                allowed_paths=pm.pdfs,
                forced_annot_id=new_annot_id,
                emit_signal=False,
            )
            if not ok:
                continue
            hl_record = pm.get_highlight(new_annot_id)
            if not hl_record:
                continue
            node.id = hl_record["id"]
            node.highlight_id = hl_record["id"]
            node.pdf_path = hl_record.get("doc_id")
            node.page_num = hl_record.get("page_num")
            node.color = hl_record.get("color", node.color)
            node.is_custom = False
        return nodes

    def add_ai_annotation(self, quote, note, target_doc_name=None, allowed_paths=None, forced_annot_id=None, emit_signal=True):
        if not quote:
            return False
        import uuid
        clean_quote = quote.strip()

        pm = getattr(self.main_window, "project_manager", None)
        if not pm:
            return False

        search_paths = allowed_paths if allowed_paths else pm.pdfs
        if target_doc_name:
            target = target_doc_name.lower().strip()
            search_paths = [p for p in search_paths if target in os.path.basename(p).lower()]

        found_any = False

        for path in search_paths:
            try:
                doc = pm.get_doc(path)
                if not doc:
                    continue

                for page_num in range(len(doc)):
                    page = doc.load_page(page_num)
                    rects = page.search_for(clean_quote)

                    if rects:
                        quads = [r.quad for r in rects]
                        annot = page.add_highlight_annot(quads)
                        annot.set_colors(stroke=(0.7, 0.4, 1.0))

                        annot_id = forced_annot_id or f"AINote|{uuid.uuid4()}"
                        annot.set_info(info={"title": annot_id, "content": note, "subject": clean_quote})
                        annot.update()
                        pm.mark_dirty(path)

                        if emit_signal:
                            self.bus.highlight_created.emit(
                                DocumentEvent.HIGHLIGHT_CREATED,
                                DocumentEventPayload(highlight_data={
                                    "id": annot_id,
                                    "subject": clean_quote,
                                    "content": note,
                                    "pdf_path": path,
                                    "page_num": page_num,
                                    "rect_coords": repr(list(annot.rect)),
                                    "color": "#b366ff",
                                }),
                            )

                        active_file = getattr(pm, "active_file", None)
                        if path == active_file:
                            self.bus.document_action_requested.emit(
                                DocumentIntent.RELOAD_PAGE,
                                DocumentPayload(page_num=page_num)
                            )

                        found_any = True
                        break

                if found_any and forced_annot_id:
                    break

            except Exception as e:
                print(f"Error adding AI annotation to {path}: {e}")

        return found_any
