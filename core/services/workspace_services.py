from __future__ import annotations

import copy
import os
import re
from typing import Iterable, Optional

from PySide6.QtCore import QObject, QThread, Signal

from core.api.workspace_ai import WorkspaceAIApi
from core.models.workspace_models import NodeModel, WorkspaceModel
from core.utils.workspace_utils import compute_node_dimensions, normalize_annotation_text
from core.events.domains.document_events import DocumentEvent, DocumentEventPayload, DocumentIntent, DocumentPayload
from core.events.domains.workspace_events import WorkspaceEvent, WorkspaceEventPayload, WorkspaceIntent, WorkspacePayload

class WorkspaceService(QObject):
    workspace_changed = Signal(int, dict)
    workspace_saved = Signal(int)
    workspace_loaded = Signal(int)

    def __init__(self, project_manager, event_bus=None, parent=None):
        super().__init__(parent)
        self.pm = project_manager
        self.bus = event_bus

    def load_workspace(self, workspace_id: int = 1) -> WorkspaceModel:
        model = self.pm.get_workspace_data(workspace_id) if self.pm else WorkspaceModel(workspace_id)
        self.workspace_loaded.emit(workspace_id)
        if self.bus and hasattr(self.bus, "workspace_loaded"):
            self.bus.workspace_loaded.emit(WorkspaceEvent.LOADED, WorkspaceEventPayload(workspace_id=workspace_id))
        return model

    def sync_workspace(self, model: WorkspaceModel):
        if not self.pm:
            return
        self.pm.sync_workspace(model)
        self.workspace_saved.emit(model.workspace_id)
        if self.bus and hasattr(self.bus, "workspace_saved"):
            self.bus.workspace_saved.emit(WorkspaceEvent.SAVED, WorkspaceEventPayload(workspace_id=model.workspace_id))

    def sync_delta(self, delta: WorkspaceModel):
        if not self.pm:
            return
        self.pm.sync_workspace_delta(delta)
        summary = {
            "nodes": len(delta.nodes),
            "edges": len(delta.edges),
            "deleted_nodes": len(delta.deleted_node_ids),
            "deleted_edges": len(delta.deleted_edge_ids),
        }
        self.workspace_changed.emit(delta.workspace_id, summary)
        if self.bus and hasattr(self.bus, "workspace_changed"):
            self.bus.workspace_changed.emit(
                WorkspaceEvent.CHANGED,
                WorkspaceEventPayload(workspace_id=delta.workspace_id, summary=summary),
            )

    def mark_dirty(self, workspace_id: int, autosave: bool = False, model: Optional[WorkspaceModel] = None):
        if not self.pm:
            return
        self.pm.mark_dirty("workspace")
        if self.bus and hasattr(self.bus, "workspace_changed"):
            self.bus.workspace_changed.emit(
                WorkspaceEvent.CHANGED,
                WorkspaceEventPayload(workspace_id=workspace_id, summary={"autosave": autosave}),
            )
        if autosave and model is not None:
            self.sync_workspace(model)

    def create_workspace(self, name: str) -> Optional[int]:
        if not self.pm:
            return None
        new_id = self.pm.create_workspace(name)
        if new_id and self.bus and hasattr(self.bus, "workspace_changed"):
            self.bus.workspace_changed.emit(
                WorkspaceEvent.CHANGED,
                WorkspaceEventPayload(workspace_id=int(new_id), summary={"created": True}),
            )
        return new_id


class WorkspaceGraphService:
    def __init__(self, event_bus=None):
        self.bus = event_bus

    def selected_subset(self, model: WorkspaceModel, selected_ids: Iterable[str]) -> WorkspaceModel:
        selected = set(selected_ids)
        subset = WorkspaceModel(workspace_id=model.workspace_id)
        subset.nodes = [n for n in model.nodes if n.id in selected]
        subset.edges = [e for e in model.edges if e.source in selected and e.target in selected]
        return subset

    def validate_delta(self, delta: WorkspaceModel, existing_node_ids: Iterable[str]) -> WorkspaceModel:
        known = set(existing_node_ids) | {n.id for n in delta.nodes}
        delta.edges = [e for e in delta.edges if e.source in known and e.target in known]
        delta.deleted_edge_ids = [e_id for e_id in delta.deleted_edge_ids if e_id]
        delta.deleted_node_ids = [n_id for n_id in delta.deleted_node_ids if n_id]
        return delta

    def copy_selection_payload(self, nodes, edges) -> dict:
        selected_node_set = set(nodes)
        return {
            "nodes": [
                {
                    "old_id": n.node_id,
                    "highlight_id": n.highlight_id,
                    "quote": n.quote,
                    "note_text": n.note,
                    "color": n.color,
                    "is_custom": n.is_custom,
                    "pdf_path": n.pdf_path,
                    "page_num": n.page_num,
                    "manual_font_size": n.manual_font_size,
                    "width": n.base_width,
                    "height": n.base_height,
                    "x": n.pos().x(),
                    "y": n.pos().y(),
                    "node_type_id": getattr(n, "node_type_id", ""),
                    "entity_type": getattr(n, "entity_type", ""),
                    "source_id": getattr(n, "source_id", None),
                    "entity_properties": dict(getattr(n, "entity_properties", {}) or {}),
                    "entity_state": dict(getattr(n, "entity_state", {}) or {}),
                    "tags": n.get_tag_names() if hasattr(n, "get_tag_names") else [],
                }
                for n in nodes
            ],
            "edges": [
                {
                    "source_old_id": e.source_node.node_id,
                    "dest_old_id": e.dest_node.node_id,
                    "label": e.label_text,
                    "color": e.base_color.name(),
                    "weight": e.weight,
                    "relation_type": getattr(e, "relation_type", "relation.basic"),
                    "evidence_ids": list(getattr(e, "evidence_ids", []) or []),
                    "relation_properties": dict(getattr(e, "relation_properties", {}) or {}),
                    "relation_state": dict(getattr(e, "relation_state", {}) or {}),
                }
                for e in edges
                if e.source_node in selected_node_set and e.dest_node in selected_node_set
            ],
        }


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
        if not getattr(node, "pdf_path", None) or node.page_num is None:
            source = self.find_source_for_node(node)
            if source:
                node.pdf_path = source.get("pdf_path") or source.get("doc_id")
                node.page_num = source.get("page_num")
                if source.get("id") and not getattr(node, "highlight_id", None):
                    node.highlight_id = source.get("id")

        if not getattr(node, "pdf_path", None) or node.page_num is None:
            return

        main_win = self.main_window
        annot_id = getattr(node, "highlight_id", None) or getattr(node, "node_id", None)
        if source and source.get("id"):
            annot_id = source["id"]
        if hasattr(main_win, "switch_to_pdf"):
            main_win.switch_to_pdf(node.pdf_path)
        if hasattr(main_win, "viewer"):
            if hasattr(main_win.viewer, "jump_to_annotation"):
                main_win.viewer.jump_to_annotation(node.page_num, annot_id)
            else:
                main_win.viewer.jump_to_page(node.page_num)

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
        if not quote: return False
        import os, uuid
        clean_quote = quote.strip()

        # Access the project manager safely (assuming self.main_window was passed in __init__)
        pm = getattr(self.main_window, "project_manager", None)
        if not pm: return False

        search_paths = allowed_paths if allowed_paths else pm.pdfs
        if target_doc_name:
            target = target_doc_name.lower().strip()
            search_paths = [p for p in search_paths if target in os.path.basename(p).lower()]

        found_any = False

        for path in search_paths:
            try:
                doc = pm.get_doc(path)
                if not doc: continue

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

                        # --- MODULAR FIX: Tell the bus to reload the page, don't touch the Viewer! ---
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


class WorkspaceLayoutWorker(QThread):
    layout_ready = Signal(dict)
    layout_failed = Signal(str)

    def __init__(self, payload: dict, llm_manager=None, project_manager=None, parent=None):
        super().__init__(parent)
        self.payload = payload
        self.llm_manager = llm_manager
        self.project_manager = project_manager

    def run(self):
        try:
            from core.layout_engine import calculate_force_directed_layout
            from core.utils.text_utils import get_semantic_similarity_matrix

            node_ids = self.payload.get("node_ids", [])
            texts = self.payload.get("texts", [])
            similarity_matrix = {}
            if self.payload.get("use_ai") and self.llm_manager and getattr(self.llm_manager, "ai_enabled", False):
                similarity_matrix = get_semantic_similarity_matrix(node_ids, texts, self.llm_manager, self.project_manager)

            positions = calculate_force_directed_layout(
                self.payload.get("nodes_info", {}),
                self.payload.get("edges_info", []),
                self.payload.get("center_x", 0),
                self.payload.get("center_y", 0),
                similarity_matrix=similarity_matrix,
                semantic_strength=self.payload.get("semantic_strength", 1.0),
            )
            self.layout_ready.emit(positions or {})
        except Exception as exc:
            self.layout_failed.emit(str(exc))


class WorkspaceLayoutService(QObject):
    def __init__(self, project_manager=None, llm_manager=None, event_bus=None, parent=None):
        super().__init__(parent)
        self.pm = project_manager
        self.llm = llm_manager
        self.bus = event_bus
        self.workers = []
        if self.bus:
            self.bus.workspace_action_requested.connect(self._handle_intent)

    def _handle_intent(self, intent: WorkspaceIntent, payload: WorkspacePayload):
        if intent != WorkspaceIntent.CALCULATE_LAYOUT:
            return
        self.calculate_layout(payload.get("extra", {}))

    def calculate_layout(self, layout_payload: dict):
        worker = WorkspaceLayoutWorker(layout_payload, self.llm, self.pm, self)
        self.workers.append(worker)
        worker.layout_ready.connect(self._emit_layout_ready)
        worker.layout_failed.connect(self._emit_layout_failed)
        worker.finished.connect(lambda w=worker: self._release_worker(w))
        worker.start()

    def _emit_layout_ready(self, positions: dict):
        if self.bus:
            self.bus.workspace_changed.emit(
                WorkspaceEvent.LAYOUT_READY,
                WorkspaceEventPayload(changes={"positions": positions}),
            )

    def _emit_layout_failed(self, message: str):
        if self.bus:
            self.bus.status_message_requested.emit(f"Layout failed: {message}", 8000)

    def _release_worker(self, worker: WorkspaceLayoutWorker):
        if worker in self.workers:
            self.workers.remove(worker)
        worker.deleteLater()


class WorkspaceAIService(QObject):
    error = Signal(str)
    dialog_result = Signal(object, object)
    graph_result = Signal(object, bool)

    def __init__(self, main_window, workspace_service: WorkspaceService, graph_service: WorkspaceGraphService, annot_service: WorkspaceAnnotationService, ai_tools_registry, event_bus=None, workflow_runner_service=None, parent=None):
        super().__init__(parent)
        self.main_window = main_window
        self.workspace_service = workspace_service
        self.graph_service = graph_service
        self.annot_service = annot_service
        self.ai_tools_registry = ai_tools_registry
        self.bus = event_bus
        self.workflow_runner_service = workflow_runner_service
        self.api = WorkspaceAIApi(getattr(main_window, "project_manager", None))

        self.active_model = "gemma4:e2b"
        self.current_workspace_id = 1

        # --- PHASE 4: Autonomous Event Listeners ---
        if self.bus:
            self.bus.active_model_changed.connect(self._on_model_changed)
            self.bus.workspace_loaded.connect(self._on_ws_loaded)
            self.bus.run_ai_tool.connect(self._on_run_tool)
            self.bus.ai_graph_generated.connect(self._on_graph_generated)

    def _on_model_changed(self, event: WorkspaceEvent, payload: WorkspaceEventPayload):
        if event == WorkspaceEvent.ACTIVE_MODEL_CHANGED:
            self.active_model = payload.model_name

    def _on_ws_loaded(self, event: WorkspaceEvent, payload: WorkspaceEventPayload):
        if event == WorkspaceEvent.LOADED and payload.workspace_id is not None:
            self.current_workspace_id = payload.workspace_id

    def resolve_active_model(self) -> str:
        # Replaces the old UI scraping hack!
        return self.active_model

    def _on_run_tool(self, event: WorkspaceEvent, payload: WorkspaceEventPayload):
        if event != WorkspaceEvent.RUN_AI_TOOL:
            return
        """Intercepts tool requests from context menus."""
        tool_id = payload.get("tool_id")
        selected_ids = payload.get("selected_ids", [])

        tool = self.ai_tools_registry.get(tool_id) if self.ai_tools_registry else None
        if not tool:
            self.error.emit(f"Workspace AI tool '{tool_id}' is not registered.")
            return

        current_model = self.workspace_service.load_workspace(self.current_workspace_id)
        ok, message = self.enqueue_tool(tool, current_model, selected_ids)
        if not ok:
            self.error.emit(message)

    def _on_graph_generated(self, event: WorkspaceEvent, payload: WorkspaceEventPayload):
        if event != WorkspaceEvent.AI_GRAPH_GENERATED:
            return
        ai_output_string = payload.result_text or ""
        """Processes the LLM output, validates it, and pushes it to the DB completely blindly."""
        current_model = self.workspace_service.load_workspace(self.current_workspace_id)
        success, result = self.process_response(ai_output_string, self.current_workspace_id, current_model)

        if not success:
            self.error.emit(f"AI Formatting Error: {result[:250]}...")
            return

        # Validate and inject annotations
        pm = getattr(self.main_window, "project_manager", None)
        existing_keys = [n.id for n in current_model.nodes]
        delta_model = self.graph_service.validate_delta(result, existing_keys)

        self.annot_service.attach_native_ai_annotations(delta_model.nodes)

        # Save to DB (this automatically emits workspace_changed to redraw the UI)
        self.workspace_service.sync_delta(delta_model)


    def build_context(self, model: WorkspaceModel, filters=None) -> str:
        return self.api.build_ai_context(model, filters)

    def process_response(self, raw_ai_text: str, current_workspace_id: int, current_workspace: WorkspaceModel | None = None):
        return self.api.process_ai_response(raw_ai_text, current_workspace_id, current_workspace)


    def resolve_blueprint(self, tool_definition):
        blueprint_manager = getattr(self.main_window, "blueprint_manager", None)
        prompt_manager = getattr(self.main_window, "prompt_manager", None)
        if blueprint_manager:
            return blueprint_manager.get_blueprint(
                tool_definition.blueprint_key,
                tool_definition.fallback_factory,
                prompt_manager,
            )
        return copy.deepcopy(tool_definition.fallback_factory(prompt_manager))

    def enqueue_tool(self, tool_definition, workspace_model: WorkspaceModel, selected_ids):
        llm = getattr(self.main_window, "shared_llm_manager", None)
        if not llm or not getattr(llm, "ai_enabled", False):
            return False, "Local AI is not running."

        blueprint = self.resolve_blueprint(tool_definition)
        context_model = workspace_model
        if tool_definition.requires_selection:
            context_model = self.graph_service.selected_subset(workspace_model, selected_ids)
            if not context_model.nodes:
                return False, "Please select nodes to process."

        permissions = tool_definition.resolve_filters(blueprint)
        initial_state = {
            "workspace_data": self.build_context(context_model, permissions),
            "selected_model": self.resolve_active_model(),
        }

        runtime_blueprint = copy.deepcopy(blueprint)
        if not runtime_blueprint.name.startswith("Workspace:"):
            runtime_blueprint.name = f"Workspace: {runtime_blueprint.name}"

        if not self.workflow_runner_service:
            return False, "Workflow runner service is not configured."

        runner = self.workflow_runner_service.prepare_runner(runtime_blueprint, initial_state)

        def _handle_completion(state):
            if not runtime_blueprint.steps:
                return
            last_step = runtime_blueprint.steps[-1]
            ai_output = state.get(last_step.output_key, "")
            output_mode = getattr(last_step, "output_mode", "workspace_update")
            ui_format = getattr(last_step, "ui_format", "")
            if output_mode == "dialog":
                self.dialog_result.emit(runtime_blueprint, ai_output)
            elif ui_format == "workspace_graph" or output_mode == "workspace_update":
                self.graph_result.emit(ai_output, tool_definition.review_before_apply)

        def _handle_error(err):
            self.error.emit(str(err))

        runner.action_complete.connect(_handle_completion)
        runner.error.connect(_handle_error)
        self.workflow_runner_service.start_runner(runner, job_name=runtime_blueprint.name)
        return True, "Queued"
# Add to core/services/workspace_services.py

import json
import dataclasses

class WorkspaceStateService(QObject):
    """Manages the history (undo/redo) of workspaces, fully decoupled from the UI."""
    def __init__(self, event_bus=None, parent=None):
        super().__init__(parent)
        self.bus = event_bus
        self.undo_stack = []
        self.redo_stack = []
        self.is_restoring = False

        if self.bus:
            self.bus.workspace_action_requested.connect(self._handle_intent)

    def _handle_intent(self, intent_name: WorkspaceIntent, payload: WorkspacePayload):
        if intent_name == WorkspaceIntent.SAVE_UNDO_STATE:
            self._save_state(payload.get("model"))
        elif intent_name == WorkspaceIntent.UNDO_TRIGGERED:
            self._undo()
        elif intent_name == WorkspaceIntent.REDO_TRIGGERED:
            self._redo()

    def _save_state(self, current_model: WorkspaceModel):
        if self.is_restoring or not current_model:
            return

        state_str = json.dumps(dataclasses.asdict(current_model), sort_keys=True)

        if not self.undo_stack or self.undo_stack[-1][0] != state_str:
            self.undo_stack.append((state_str, current_model))
            if len(self.undo_stack) > 50:
                self.undo_stack.pop(0)
            self.redo_stack.clear()
            self._broadcast_button_states()

    def _undo(self):
        if not self.undo_stack: return
        self.is_restoring = True

        # Pop the last state
        _, prev_state = self.undo_stack.pop()

        # We need the UI to give us its *current* state before we override it,
        # so we can put it in the redo stack. We'll handle this smoothly by requesting it if needed,
        # but for now, we just restore the previous state.

        if self.bus:
            self.bus.workspace_state_restored.emit(WorkspaceEvent.STATE_RESTORED, WorkspaceEventPayload(model=prev_state))

        self.is_restoring = False
        self._broadcast_button_states()

    def _redo(self):
        if not self.redo_stack: return
        self.is_restoring = True

        _, next_state = self.redo_stack.pop()

        if self.bus:
            self.bus.workspace_state_restored.emit(WorkspaceEvent.STATE_RESTORED, WorkspaceEventPayload(model=next_state))

        self.is_restoring = False
        self._broadcast_button_states()

    def _broadcast_button_states(self):
        """Tells the UI if the undo/redo buttons should be enabled/disabled."""
        if self.bus:
            self.bus.workspace_action_requested.emit(
                WorkspaceIntent.UPDATE_HISTORY_BUTTONS,
                WorkspacePayload(can_undo=len(self.undo_stack) > 0, can_redo=len(self.redo_stack) > 0),
            )
