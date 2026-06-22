from __future__ import annotations

import json
import os
import uuid
import hashlib
from dataclasses import asdict, is_dataclass
from pathlib import Path
from typing import Any

from core.events.domains.document_events import DocumentEvent, DocumentEventPayload, DocumentIntent, DocumentPayload, SourceEvent
from core.events.domains.project_events import ProjectIntent, ProjectPayload
from core.events.domains.workflow_events import WorkflowIntent, WorkflowPayload
from core.models.workspace_models import EdgeModel, NodeModel, WorkspaceModel

from .serialization import json_safe
from .state import WebState


def _hex_rgb(value: str | list | tuple | None) -> tuple[float, float, float]:
    if isinstance(value, (list, tuple)) and len(value) >= 3:
        nums = [float(v) for v in value[:3]]
        return tuple(v / 255 if v > 1 else v for v in nums)
    text = str(value or "#ffe500").lstrip("#")
    if len(text) != 6:
        text = "ffe500"
    return tuple(int(text[i:i + 2], 16) / 255 for i in (0, 2, 4))


class WebCommands:
    """Main-thread adapters over Papyrus services and domain intents."""
    def __init__(self, app_context, state: WebState):
        self.ctx = app_context
        self.pm = app_context.project_manager
        self.bus = app_context.bus
        self.state = state

    def install(self, dispatcher) -> None:
        for name in dir(self):
            if name.startswith("cmd_"):
                dispatcher.register(name[4:].replace("__", "."), getattr(self, name))

    def _all_sources(self) -> list[dict]:
        """Return registry sources plus legacy PDFs not yet represented there."""
        rows = [dict(source) for source in (self.pm.list_sources() or [])]
        known = {os.path.realpath(str(source.get("path") or "")) for source in rows}
        for path in getattr(self.pm, "pdfs", []) or []:
            real = os.path.realpath(str(path))
            if not real or real in known:
                continue
            # Legacy projects may have a populated `pdfs` table but no source
            # registry row.  Use a stable opaque identifier without exposing
            # the laptop path; normal source operations still resolve through
            # this adapter until the desktop migrates the record.
            rows.append({
                "id": f"legacy-{hashlib.sha256(real.encode('utf-8')).hexdigest()[:24]}",
                "path": real,
                "source_type": "pdf",
                "mime_type": "application/pdf",
                "title": os.path.basename(real),
                "metadata": {},
                "state": {},
            })
            known.add(real)
        return rows

    def _source(self, source_id: str) -> dict:
        source = next((s for s in self._all_sources() if s.get("id") == source_id), None)
        if not source:
            raise KeyError("Source not found")
        return source

    def _source_public(self, source: dict) -> dict:
        path = source.get("path") or ""
        item = {k: v for k, v in source.items() if k != "path"}
        item["filename"] = os.path.basename(path) or item.get("title") or "Untitled source"
        item["exists"] = os.path.isfile(path)
        try:
            item["tags"] = self.pm.get_tags_for_doc(path)
        except Exception:
            item["tags"] = []
        return json_safe(item)

    def public_data(self, value: Any) -> Any:
        """Remove laptop paths from project payloads and substitute opaque IDs."""
        path_to_id = {str(source.get("path")): str(source.get("id")) for source in self._all_sources() if source.get("path") and source.get("id")}
        path_keys = {"path", "pdf_path", "doc_id", "source_path", "file_path", "origin_id", "old_path", "new_path"}

        def scrub(item):
            if is_dataclass(item):
                item = asdict(item)
            if isinstance(item, dict):
                result = {}
                for key, child in item.items():
                    if key in {"doc", "runner", "blueprint"}:
                        continue
                    if key in path_keys and isinstance(child, str) and os.path.isabs(child):
                        source_id = path_to_id.get(child)
                        if source_id:
                            result.setdefault("document_source_id", source_id)
                        continue
                    result[str(key)] = scrub(child)
                return result
            if isinstance(item, (list, tuple, set)):
                return [scrub(child) for child in item]
            return json_safe(item)
        return scrub(value)

    def cmd_project__snapshot(self, _payload):
        # Project bootstrap must remain available even when an optional AI
        # backend is absent, disabled, or still initialising.  Older Papyrus
        # builds exposed ``ai_enabled`` as a method; current builds expose it
        # as a boolean property, so accept both shapes.
        llm_manager = getattr(self.ctx, "llm_manager", None)
        enabled = getattr(llm_manager, "ai_enabled", False)
        try:
            if callable(enabled):
                enabled = enabled()
        except Exception:
            enabled = False

        try:
            active_model = self.ctx.get_active_ai_model()
        except Exception:
            active_model = None

        sources = self._all_sources() if self.pm.project_filepath else []
        return {
            "open": bool(self.pm.project_filepath),
            "name": self.pm.project_name if self.pm.project_filepath else None,
            "source_count": len(sources),
            "active_source_id": next((s["id"] for s in sources if s.get("path") == self.pm.active_file), None),
            "ai_available": bool(enabled),
            "active_model": active_model,
        }

    def cmd_project__save(self, _payload):
        self.bus.project_action_requested.emit(ProjectIntent.SAVE, ProjectPayload())
        return {"saved": True}

    def cmd_project__assets_dir(self, _payload):
        if not self.pm.project_filepath:
            raise RuntimeError("Open a project before uploading sources")
        return {"path": os.path.realpath(self.pm.project_filepath) + ".assets"}

    def cmd_sources__list(self, _payload):
        return [self._source_public(s) for s in self._all_sources()]

    def cmd_sources__resolve_media(self, payload):
        source = self._source(payload["source_id"])
        path = os.path.realpath(source["path"])
        if not os.path.isfile(path):
            raise FileNotFoundError("Source file is missing")
        return {"path": path, "mime_type": source.get("mime_type") or "application/octet-stream", "filename": os.path.basename(path)}

    def cmd_sources__add_uploaded(self, payload):
        path = os.path.realpath(payload["path"])
        project = os.path.realpath(self.pm.project_filepath or "")
        assets = project + ".assets"
        if not project or os.path.commonpath([path, assets]) != assets:
            raise PermissionError("Upload is outside the project assets directory")
        source_type = self.pm.detect_source_type(path)
        if source_type not in {"pdf", "video"}:
            raise ValueError("Only PDF and supported video files may be added")
        self.bus.document_action_requested.emit(DocumentIntent.ADD_FILES, DocumentPayload(paths=[path]))
        source = self.pm.get_source_record_by_path(path)
        return self._source_public(source) if source else {"filename": os.path.basename(path)}

    def cmd_sources__rename(self, payload):
        source = self._source(payload["source_id"])
        old_path = source["path"]
        name = Path(payload.get("name") or "").name.strip()
        if not name:
            raise ValueError("A filename is required")
        suffix = Path(old_path).suffix
        if Path(name).suffix.lower() != suffix.lower():
            name += suffix
        new_path = str(Path(old_path).with_name(name))
        if os.path.exists(new_path):
            raise FileExistsError("A source with that filename already exists")
        if not self.pm.rename_source(old_path, new_path):
            raise RuntimeError("Could not rename source")
        renamed = self.pm.get_source_record_by_path(new_path)
        event_payload = DocumentEventPayload(
            path=new_path, old_path=old_path, new_path=new_path,
            old_source_id=source["id"], new_source_id=renamed.get("id") if renamed else source["id"],
            source_id=renamed.get("id") if renamed else source["id"], source_type=source.get("source_type"),
        )
        self.bus.source_renamed.emit(SourceEvent.RENAMED, event_payload)
        self.bus.pdf_renamed.emit(DocumentEvent.PDF_RENAMED, event_payload)
        return self._source_public(renamed)

    def cmd_sources__remove(self, payload):
        source = self._source(payload["source_id"])
        removed = bool(self.pm.remove_source(source["path"]))
        if removed:
            event_payload = DocumentEventPayload(path=source["path"], source_id=source["id"], source_type=source.get("source_type"))
            self.bus.source_removed.emit(SourceEvent.REMOVED, event_payload)
            self.bus.pdf_removed.emit(DocumentEvent.PDF_REMOVED, event_payload)
        return {"removed": removed}

    def cmd_sources__details(self, payload):
        source = self._source(payload["source_id"])
        result = self._source_public(source)
        path = source["path"]
        result["citation"] = self.pm.get_citation(path)
        result["transcript"] = self.pm.get_video_transcript(source["id"]) if source.get("source_type") == "video" else None
        if result["citation"]:
            result["citation"].pop("doc_id", None)
        if result["transcript"]:
            result["transcript"].pop("path", None)
        if source.get("source_type") == "pdf":
            doc = self.pm.get_doc(path)
            result["page_count"] = len(doc) if doc else 0
            result["pages"] = [
                {"index": i, "width": float(doc[i].rect.width), "height": float(doc[i].rect.height)}
                for i in range(len(doc))
            ] if doc else []
        return self.public_data(result)

    def cmd_pdf__page_text(self, payload):
        source = self._source(payload["source_id"])
        doc = self.pm.get_doc(source["path"])
        page_num = int(payload.get("page_num", 0))
        if not doc or page_num < 0 or page_num >= len(doc):
            raise IndexError("Page not found")
        page = doc[page_num]
        return {"page_num": page_num, "text": page.get_text(), "words": [list(w[:8]) for w in page.get_text("words")]}

    def cmd_pdf__search(self, payload):
        source = self._source(payload["source_id"])
        query = str(payload.get("query") or "").strip()
        if not query:
            return []
        doc = self.pm.get_doc(source["path"])
        hits = []
        for index, page in enumerate(doc or []):
            for rect in page.search_for(query)[:100]:
                hits.append({"page_num": index, "rect": list(rect), "text": query})
            if len(hits) >= 500:
                break
        return hits

    def cmd_annotations__list(self, payload):
        source = self._source(payload["source_id"])
        return [json_safe(v) for v in self.pm.get_highlights().values() if v.get("doc_id") == source["path"]]

    def cmd_annotations__create(self, payload):
        source = self._source(payload["source_id"])
        annot_id = f"UserNote|{uuid.uuid4()}"
        self.bus.document_action_requested.emit(DocumentIntent.CREATE_HIGHLIGHT, DocumentPayload(
            path=source["path"], page_num=int(payload["page_num"]), rects=payload.get("rects") or [],
            text=str(payload.get("text") or ""), note=str(payload.get("note") or ""),
            color=_hex_rgb(payload.get("color")), annot_id=annot_id,
        ))
        self.pm.save_all_docs()
        return self.pm.get_highlight(annot_id) or {"id": annot_id}

    def cmd_annotations__update(self, payload):
        item = self.pm.get_highlight(payload["annot_id"])
        if not item:
            raise KeyError("Annotation not found")
        if "note" in payload:
            self.bus.document_action_requested.emit(DocumentIntent.UPDATE_HIGHLIGHT_NOTE, DocumentPayload(
                path=item["doc_id"], page_num=item["page_num"], annot_id=item["id"], note=str(payload["note"])))
        if "color" in payload:
            self.bus.document_action_requested.emit(DocumentIntent.UPDATE_HIGHLIGHT_COLOR, DocumentPayload(
                path=item["doc_id"], page_num=item["page_num"], annot_id=item["id"], color=_hex_rgb(payload["color"])))
        self.pm.save_all_docs()
        return self.pm.get_highlight(item["id"])

    def cmd_annotations__delete(self, payload):
        item = self.pm.get_highlight(payload["annot_id"])
        if not item:
            return {"deleted": False}
        self.bus.document_action_requested.emit(DocumentIntent.DELETE_HIGHLIGHT, DocumentPayload(
            path=item["doc_id"], page_num=item["page_num"], annot_id=item["id"]))
        self.pm.save_all_docs()
        return {"deleted": True}

    def cmd_tags__list(self, _payload):
        return self.pm.get_all_tags()

    def cmd_tags__create(self, payload):
        self.pm.create_tag(str(payload["name"]).strip(), payload.get("color") or "#b366ff")
        tags = self.pm.get_all_tags()
        from core.events.domains.metadata_events import TagEvent, TagEventPayload
        self.bus.tag_data_updated.emit(TagEvent.ALL_TAGS, TagEventPayload(tags=tags))
        return tags

    def cmd_tags__delete(self, payload):
        self.pm.delete_tag(payload["tag_id"])
        from core.events.domains.metadata_events import TagEvent, TagEventPayload
        self.bus.tag_data_updated.emit(TagEvent.ALL_TAGS, TagEventPayload(tags=self.pm.get_all_tags()))
        return {"deleted": True}

    def cmd_tags__assign(self, payload):
        target_type = payload.get("target_type", "document")
        target_id = payload["target_id"]
        if target_type == "document":
            target_id = self._source(target_id)["path"]
            fn = self.pm.assign_tag_to_doc if payload.get("assigned", True) else self.pm.remove_tag_from_doc
        else:
            fn = self.pm.assign_tag_to_node if payload.get("assigned", True) else self.pm.remove_tag_from_node
        fn(target_id, payload["tag_id"])
        from core.events.domains.metadata_events import TagEvent, TagEventPayload
        self.bus.tag_data_updated.emit(TagEvent.TARGET_ASSIGNMENTS, TagEventPayload())
        return {"updated": True}

    def cmd_notes__list(self, payload):
        source_id = payload.get("source_id")
        path = self._source(source_id)["path"] if source_id else None
        items = []
        for item in self.pm.get_highlights().values():
            if path and item.get("doc_id") != path:
                continue
            if item.get("content") or str(item.get("id", "")).startswith(("UserNote", "AINote", "VideoNote")):
                public = dict(item)
                public["source_id"] = next((s["id"] for s in self.pm.list_sources() if s.get("path") == item.get("doc_id")), None)
                public.pop("doc_id", None)
                items.append(public)
        return items

    def cmd_workspaces__list(self, _payload):
        return json_safe(self.pm.get_workspaces())

    def cmd_workspaces__get(self, payload):
        return self.public_data(self.pm.get_workspace_data(int(payload.get("workspace_id", 1))))

    def cmd_workspaces__create(self, payload):
        return {"workspace_id": self.pm.create_workspace(str(payload.get("name") or "New Workspace"))}

    def cmd_workspaces__sync(self, payload):
        nodes = []
        for raw_node in payload.get("nodes", []):
            node = dict(raw_node)
            opaque_source_id = node.pop("document_source_id", None)
            if opaque_source_id and not node.get("pdf_path"):
                node["pdf_path"] = self._source(opaque_source_id)["path"]
            nodes.append(NodeModel(**node))
        model = WorkspaceModel(
            workspace_id=int(payload.get("workspace_id", 1)),
            nodes=nodes,
            edges=[EdgeModel(**edge) for edge in payload.get("edges", [])],
            deleted_node_ids=list(payload.get("deleted_node_ids", [])),
            deleted_edge_ids=list(payload.get("deleted_edge_ids", [])),
        )
        self.ctx.workspace_service.sync_delta(model)
        return self.public_data(self.pm.get_workspace_data(model.workspace_id))

    def cmd_essays__list(self, _payload):
        return self.pm.get_all_essays()

    def cmd_essays__get(self, payload):
        return self.pm.get_essay(payload["essay_id"])

    def cmd_essays__save(self, payload):
        essay_id = payload.get("essay_id") or str(uuid.uuid4())
        self.pm.upsert_essay(essay_id, str(payload.get("title") or "Untitled"), str(payload.get("content") or ""))
        return self.pm.get_essay(essay_id)

    def cmd_citations__list(self, _payload):
        results = []
        for source in self.pm.list_sources():
            citation = dict(self.pm.get_citation(source["path"]))
            citation.pop("doc_id", None)
            citation.update(source_id=source["id"], filename=os.path.basename(source["path"]))
            results.append(citation)
        return results

    def cmd_citations__save(self, payload):
        source = self._source(payload["source_id"])
        data = dict(payload.get("citation") or {})
        data["doc_id"] = source["path"]
        self.pm.upsert_citation(data)
        result = self.pm.get_citation(source["path"])
        result.pop("doc_id", None)
        return result

    def cmd_dictionary__search(self, payload):
        return json_safe(self.ctx.dictionary_manager.exact_search(str(payload.get("query") or "")[:200]))

    def cmd_data__list(self, _payload):
        return self.ctx.data_dock_service.list_datasets() if self.ctx.data_dock_service else []

    def cmd_data__get(self, payload):
        state = self.ctx.data_dock_service.load_dataset(payload["dataset_id"])
        return state.to_dict() if state else None

    def cmd_data__new(self, payload):
        state = self.ctx.data_dock_service.new_dataset(payload.get("name", "Untitled Dataset"), int(payload.get("rows", 8)), int(payload.get("columns", 4)))
        return state.to_dict()

    def cmd_data__update(self, payload):
        state = self.ctx.data_dock_service.update_grid(payload["dataset_id"], payload.get("headers", []), payload.get("rows", []), payload.get("column_types"), payload.get("row_headers"))
        if payload.get("save", True) and state:
            state = self.ctx.data_dock_service.save_dataset(state.dataset_id)
        return state.to_dict() if state else None

    def cmd_ai__catalog(self, _payload):
        prompts = dict(getattr(self.ctx.prompt_manager, "DEFAULT_PROMPTS", {}))
        prompts.update(getattr(self.ctx.prompt_manager, "custom_prompts", {}))
        return {
            "models": self.ctx.llm_manager.get_available_models(),
            "blueprints": self.ctx.blueprint_registry.agent_tools(),
            "templates": self.pm.get_analysis_templates(),
            "prompts": prompts,
        }

    def cmd_ai__history(self, payload):
        return self.pm.get_chat_history(payload.get("target_id") or "chat_dock")

    def cmd_ai__clear_history(self, payload):
        self.pm.clear_chat_history(payload.get("target_id") or "chat_dock")
        return {"cleared": True}

    def cmd_ai__run(self, payload):
        blueprint_id = payload["blueprint_id"]
        blueprint = self.ctx.blueprint_registry.create(blueprint_id, pm=self.ctx.prompt_manager)
        if blueprint is None:
            raise KeyError("Blueprint not found")
        request_id = payload.get("job_id") or str(uuid.uuid4())
        initial = dict(payload.get("initial_state") or {})
        initial.setdefault("selected_model", self.ctx.get_active_ai_model())
        try:
            initial.update({k: v for k, v in self.ctx.build_rag_context_payload().items() if k not in initial})
        except Exception:
            pass
        self.bus.workflow_action_requested.emit(WorkflowIntent.RUN_BLUEPRINT, WorkflowPayload(
            blueprint=blueprint, blueprint_id=blueprint_id, job_id=request_id,
            initial_state=initial, target_id=payload.get("target_id") or "chat_dock",
            job_name=getattr(blueprint, "name", blueprint_id), is_express=bool(payload.get("is_express", False)),
        ))
        return {"job_id": request_id, "queued": True}

    def cmd_ai__jobs(self, _payload):
        registry = self.ctx.process_registry
        jobs = ([registry.active_job] if registry.active_job else []) + list(registry.express_jobs) + list(registry.pending_queue) + list(registry.recent_jobs)
        return [json_safe({"id": j.id, "name": j.name, "type": j.type, "status": j.status, "trace_id": j.trace_id}) for j in jobs]

    def cmd_ai__cancel(self, payload):
        self.ctx.process_registry.cancel_job(payload["job_id"])
        return {"cancelled": True}

    def cmd_analysis__saved(self, payload):
        source = self._source(payload["source_id"])
        return self.public_data(self.pm.get_document_analyses(source["path"], payload["template_id"]))

    def cmd_analysis__run(self, payload):
        from core.engine.default_blueprints import DefaultBlueprints
        source = self._source(payload["source_id"])
        template_id = payload.get("template_id") or "default_argument_map"
        template = next((item for item in self.pm.get_analysis_templates() if item.get("id") == template_id), None)
        if not template:
            raise KeyError("Analysis template not found")
        raw = f"{source['path']}:{template_id}:{os.path.getmtime(source['path'])}"
        run_id = hashlib.sha1(raw.encode("utf-8")).hexdigest()[:16]
        blueprint = DefaultBlueprints.get_analysis_blueprint(self.ctx.prompt_manager)
        request_id = str(uuid.uuid4())
        self.bus.workflow_action_requested.emit(WorkflowIntent.RUN_BLUEPRINT, WorkflowPayload(
            blueprint=blueprint, blueprint_id="Document Analysis", job_id=request_id, job_name=blueprint.name,
            target_id="analysis_tab", initial_state={
                "selected_model": self.ctx.get_active_ai_model(), "analysis_doc_path": source["path"],
                "target_doc": source["path"], "analysis_template_id": template_id,
                "analysis_template": template, "analysis_run_id": run_id,
            },
        ))
        return {"job_id": request_id, "run_id": run_id, "queued": True}

    def cmd_ai__trace(self, payload):
        return self.pm.get_prompt_trace(payload["trace_id"])

    def cmd_ai__audit(self, payload):
        if not self.pm._conn:
            return []
        limit = max(1, min(1000, int(payload.get("limit", 200))))
        rows = self.pm._conn.execute(
            "SELECT timestamp, prompt, response, model_used FROM ai_audit_log ORDER BY timestamp DESC LIMIT ?", (limit,)
        ).fetchall()
        return [{"timestamp": row[0], "prompt": row[1], "response": row[2], "model": row[3]} for row in rows]

    def cmd_prompts__save(self, payload):
        self.ctx.prompt_manager.save_prompt(str(payload["key"]), str(payload.get("content") or ""))
        from core.events.domains.metadata_events import PromptEvent, PromptEventPayload
        self.bus.prompt_data_updated.emit(PromptEvent.UPDATED, PromptEventPayload(data={"key": payload["key"]}))
        return {"saved": True}

    def cmd_prompts__restore(self, payload):
        self.ctx.prompt_manager.restore_default(str(payload["key"]))
        from core.events.domains.metadata_events import PromptEvent, PromptEventPayload
        self.bus.prompt_data_updated.emit(PromptEvent.UPDATED, PromptEventPayload(data={"key": payload["key"]}))
        return {"restored": True}

    def cmd_project__metadata(self, payload):
        keys = payload.get("keys") or ["project_manifest", "active_rag_docs", "active_rag_tags", "active_rag_tag_logic", "global_ai_settings", "web_slides"]
        return {key: self.pm.get_metadata(str(key)) for key in keys}

    def cmd_project__set_metadata(self, payload):
        allowed = {"project_manifest", "active_rag_docs", "active_rag_tags", "active_rag_tag_logic", "global_ai_settings", "web_slides"}
        key = str(payload["key"])
        if key not in allowed:
            raise PermissionError("Metadata key is not web-editable")
        value = payload.get("value")
        self.pm.set_metadata(key, json.dumps(value) if isinstance(value, (dict, list)) else str(value or ""))
        return {"saved": True}

    def cmd_tools__ocr(self, payload):
        from core.events.domains.ocr_events import OCRIntent, OCRPayload
        source = self._source(payload["source_id"])
        self.bus.ocr_action_requested.emit(OCRIntent.RUN, OCRPayload(file_path=source["path"], mode=payload.get("mode") or "text"))
        return {"queued": True}

    def cmd_tools__tts(self, payload):
        from core.events.domains.tts_events import TTSIntent, TTSPayload
        source = self._source(payload["source_id"]) if payload.get("source_id") else None
        text = str(payload.get("text") or "").strip()
        if not text and source and source.get("source_type") == "pdf":
            from core.pdf_utils import extract_filtered_blocks
            from core.utils.text_utils import sanitize_extracted_text
            text = sanitize_extracted_text(
                extract_filtered_blocks(source["path"], True, int(payload.get("start_page", 1)), int(payload.get("end_page", 9999))),
                collapse_whitespace=True,
            )
        self.bus.tts_action_requested.emit(TTSIntent.GENERATE, TTSPayload(
            text=text, path=source["path"] if source else None,
            voice_file=payload.get("voice_file") or "voice1.onnx", speed=float(payload.get("speed", 1)),
            start_page=int(payload.get("start_page", 1)), end_page=int(payload.get("end_page", 9999)),
        ))
        return {"queued": True}

    def cmd_tools__resolve_audio(self, payload):
        filename = Path(str(payload.get("filename") or "")).name
        audio_dir = os.path.realpath(os.path.join(os.getcwd(), "audio"))
        path = os.path.realpath(os.path.join(audio_dir, filename))
        if not filename or os.path.commonpath([path, audio_dir]) != audio_dir or not os.path.isfile(path):
            raise FileNotFoundError("Generated audio was not found")
        return {"path": path, "filename": filename, "mime_type": "audio/wav"}

    def cmd_tools__transcribe(self, payload):
        from core.events.domains.document_events import SourceIntent, SourcePayload
        source = self._source(payload["source_id"])
        self.bus.source_action_requested.emit(SourceIntent.TRANSCRIBE, SourcePayload(path=source["path"], source_id=source["id"], source_type="video"))
        return {"queued": True}

    def cmd_tools__evaluate_source(self, payload):
        from core.events.domains.evaluation_events import SourceEvalIntent, SourceEvalPayload
        source = self._source(payload["source_id"])
        self.bus.source_eval_action_requested.emit(SourceEvalIntent.RUN_EVALUATION, SourceEvalPayload(
            pdf_path=source["path"], doi=payload.get("doi"), journal=payload.get("journal"), context={"web": True}))
        return {"queued": True}

    def cmd_citations__format(self, payload):
        paths = [self._source(source_id)["path"] for source_id in payload.get("source_ids", [])]
        self.ctx.citation_manager.set_style(payload.get("style") or "APA")
        return {"entries": self.ctx.citation_manager.format_works_cited(paths)}

    def cmd_ontology__catalog(self, _payload):
        registry = self.ctx.ontology_registry
        return {
            "entity_types": json_safe(list(getattr(registry, "entities", {}).values())),
            "relation_types": json_safe(list(getattr(registry, "relations", {}).values())),
            "views": json_safe(self.pm.get_views()),
        }

    def cmd_ontology__upsert_entity(self, payload):
        from core.models.ontology_model import EntityModel
        entity = EntityModel(
            id=payload.get("id") or str(uuid.uuid4()), entity_type=payload.get("entity_type") or "entity.text",
            origin_id=payload.get("origin_id"), properties=dict(payload.get("properties") or {}), state=dict(payload.get("state") or {}),
        )
        self.pm.upsert_entity(entity)
        return json_safe(entity)

    def cmd_ontology__delete_entity(self, payload):
        self.pm.delete_entity(payload["id"])
        return {"deleted": True}

    def cmd_ontology__upsert_relation(self, payload):
        from core.models.ontology_model import RelationModel
        relation = RelationModel(
            id=payload.get("id") or str(uuid.uuid4()), source_id=payload["source_id"], target_id=payload["target_id"],
            relation_type=payload.get("relation_type") or "relation.basic", evidence_ids=list(payload.get("evidence_ids") or []),
            properties=dict(payload.get("properties") or {}), state=dict(payload.get("state") or {}),
        )
        self.pm.upsert_relation(relation)
        return json_safe(relation)

    def cmd_ontology__delete_relation(self, payload):
        self.pm.delete_relation(payload["id"])
        return {"deleted": True}

    def cmd_research__session(self, _payload):
        session = self.ctx.research_agent_service.load_session()
        return self.public_data(session)

    def cmd_research__start(self, payload):
        return self.public_data(self.ctx.research_agent_service.start_session(str(payload.get("goal") or "")))

    def cmd_research__input(self, payload):
        self.ctx.research_agent_service.add_user_input(str(payload.get("text") or ""))
        return self.public_data(self.ctx.research_agent_service.load_session())

    def cmd_research__plan(self, _payload):
        self.ctx.research_agent_service.plan_next()
        return {"queued": True}

    def cmd_research__reset(self, _payload):
        self.ctx.research_agent_service.reset_session()
        return None

    def cmd_plugins__list(self, _payload):
        return []
