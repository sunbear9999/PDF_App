from __future__ import annotations

import hashlib
import json
import os
import re
import uuid
from typing import Any, Callable, Dict, Iterable, List, Optional, Tuple

from PySide6.QtCore import QObject

from core.engine.action_model import ActionStep, AIActionBlueprint
from core.events.domains.analysis_events import AnalysisEvent, AnalysisIntent, AnalysisPayload
from core.events.domains.ontology_events import EntityPayload, RelationPayload
from core.events.event_bus import EventBus
from core.models.ontology_model import EntityIntent, EntityModel, EntityType, RelationIntent, RelationType
from core.ontology.registry import OntologyRegistry
from core.utils.doc_parser import DocumentParser
from core.utils.json_utils import extract_and_heal_json


class AnalysisAppService(QObject):
    """Graph-aware document analysis orchestrator.

    The GUI emits intents and renders result payloads. This service owns prompt
    contracts, template interpretation, graph validation, and graph writes.
    """

    def __init__(
        self,
        project_manager,
        prompt_manager,
        registry: Optional[OntologyRegistry] = None,
        event_bus: Optional[EventBus] = None,
        workflow_executor: Optional[Callable[[AIActionBlueprint, dict], Any]] = None,
        runner_starter: Optional[Callable[[Any], Any]] = None,
        model_provider: Optional[Callable[[], str]] = None,
        parent=None,
    ):
        super().__init__(parent)
        self.pm = project_manager
        self.prompt_manager = prompt_manager
        self.registry = registry or OntologyRegistry()
        self.bus = event_bus or EventBus.get_instance()
        self.workflow_executor = workflow_executor
        self.runner_starter = runner_starter
        self.model_provider = model_provider
        self._results: Dict[str, Dict[str, Any]] = {}
        self._failed_runs: set[str] = set()
        self.bus.analysis_action_requested.connect(self.handle_intent)
        self._entity_aliases = self._build_type_aliases(EntityType)
        self._relation_aliases = self._build_type_aliases(RelationType)

    def handle_intent(self, intent, payload):
        if isinstance(payload, dict):
            payload = AnalysisPayload(**payload)
        if intent == AnalysisIntent.RUN:
            self.run_analysis(payload)
        elif intent == AnalysisIntent.SEND_TO_WORKSPACE:
            self.send_to_workspace(payload)

    def run_analysis(self, payload: AnalysisPayload):
        if not self.workflow_executor or not self.runner_starter:
            self._emit(AnalysisEvent.RUN_FAILED, payload, ["Analysis runner is not configured."])
            return
        template = self._resolve_template(payload.template_id, payload.template)
        template_id = template.get("id") or payload.template_id
        if not payload.doc_path or not template_id:
            self._emit(AnalysisEvent.RUN_FAILED, payload, ["Missing document or template."])
            return

        run_id = payload.run_id or self._run_id(payload.doc_path, template_id)
        payload.run_id = run_id
        self._failed_runs.discard(run_id)
        limits = self._analysis_limits(template)
        contract = self._build_contract(template, limits)
        chunks = DocumentParser.chunk_document_for_analysis(
            payload.doc_path,
            template_id,
            template.get("instructions", ""),
            json.dumps(contract["chunk_schema"], indent=2),
            chunk_size=limits["chunk_pages"],
            max_chars_per_chunk=limits["max_chunk_chars"],
        )
        if not chunks:
            self._emit(AnalysisEvent.RUN_FAILED, payload, ["Could not parse document into analysis chunks."])
            return

        try:
            self.pm.clear_document_analyses(payload.doc_path, template_id)
        except Exception:
            pass

        blueprint = self._build_blueprint(template, chunks, contract, limits)
        state = {
            "selected_model": payload.selected_model or (self.model_provider() if self.model_provider else ""),
            "analysis_run_id": run_id,
            "analysis_doc_path": payload.doc_path,
            "analysis_template_id": template_id,
            "analysis_contract": json.dumps(contract, indent=2),
            "template_instructions": template.get("instructions", ""),
            "template_schema": self._prompt_contract(contract),
        }
        self._emit(AnalysisEvent.RUN_STARTED, AnalysisPayload(doc_path=payload.doc_path, template_id=template_id, run_id=run_id, template=template))
        runner = self.workflow_executor(blueprint, state)
        runner.step_started.connect(lambda step_id, p=payload, total=len(chunks): self._handle_step_started(p, step_id, total))
        runner.step_complete.connect(lambda step_id, result, snapshot, p=payload, c=contract, total=len(chunks): self._handle_step_complete_progress(p, step_id, result, c, total))
        runner.action_complete.connect(lambda final_state, p=payload, t=template, c=contract, rid=run_id: self._handle_runner_complete(p, t, c, rid, final_state))
        runner.error.connect(lambda msg, p=payload: self._handle_runner_error(p, msg))
        self.runner_starter(runner)

    def send_to_workspace(self, payload: AnalysisPayload):
        result = payload.result or self._results.get(payload.run_id or "")
        if not result:
            self._emit(AnalysisEvent.RUN_FAILED, payload, ["No analysis result is available to send."])
            return
        workspace_id = payload.workspace_id or 1
        source = self._ensure_source(result.get("doc_path"))
        id_map: Dict[str, str] = {}
        entity_types_by_temp: Dict[str, str] = {}

        for item in result.get("entities", []):
            temp_id = item.get("temp_id") or item.get("id")
            entity_type = item.get("type") or EntityType.TEXT.value
            entity_types_by_temp[str(temp_id or "")] = entity_type
            properties = dict(item.get("properties") or {})
            if item.get("text") and "text" not in properties:
                properties["text"] = item.get("text")
            if item.get("title") and "title" not in properties:
                properties["title"] = item.get("title")
            if entity_type == EntityType.QUOTE.value:
                quote = item.get("exact_text") or item.get("quote") or item.get("text") or properties.get("exact_text") or ""
                properties.update({
                    "exact_text": quote,
                    "quote": quote,
                    "text": quote,
                    "note_text": item.get("note_text") or properties.get("note_text", ""),
                })
            if source:
                properties.setdefault("source_id", source.id)
                properties.setdefault("pdf_path", result.get("doc_path"))
            if item.get("page") is not None:
                properties.setdefault("page", item.get("page"))
                properties.setdefault("page_num", item.get("page"))
            entity_id = self._stable_entity_id(result, temp_id or json.dumps(item, sort_keys=True))
            id_map[str(temp_id or entity_id)] = entity_id
            self.bus.entity_action_requested.emit(
                EntityIntent.ADD,
                EntityPayload(
                    entity_id=entity_id,
                    entity_type=entity_type,
                    origin_id=result.get("doc_path"),
                    view_id=str(workspace_id),
                    data={
                        "properties": properties,
                        "state": {"is_verified": False, "ai_generated": True, "origin": "analysis_ai"},
                        "view_meta": self._view_meta_for_index(len(id_map) - 1, item),
                    },
                ),
            )

        emitted_relations = 0
        for rel in result.get("relations", []):
            raw_src = str(rel.get("source") or rel.get("source_id") or "")
            raw_tgt = str(rel.get("target") or rel.get("target_id") or "")
            src = id_map.get(raw_src)
            tgt = id_map.get(raw_tgt)
            if not src or not tgt:
                continue
            rel_type = self._compatible_relation_type_for_types(
                rel.get("type") or RelationType.BASIC.value,
                entity_types_by_temp.get(raw_src),
                entity_types_by_temp.get(raw_tgt),
                {item.get("type") for item in result.get("relations_contract", []) if item.get("type")} or self._all_relation_types(),
            )
            if not rel_type:
                continue
            relation_id = self._stable_relation_id(result, src, tgt, rel_type)
            self.bus.relation_action_requested.emit(
                RelationIntent.ADD,
                RelationPayload(
                    relation_id=relation_id,
                    relation_type=rel_type,
                    source_id=src,
                    target_id=tgt,
                    view_id=str(workspace_id),
                    data={
                        "properties": rel.get("properties") or {},
                        "evidence_ids": [id_map.get(str(e), str(e)) for e in rel.get("evidence_ids", [])],
                        "state": {"is_verified": False, "origin": "analysis_ai"},
                    },
                ),
            )
            emitted_relations += 1

        sent_payload = AnalysisPayload(
            doc_path=result.get("doc_path"),
            template_id=result.get("template_id"),
            run_id=result.get("run_id"),
            workspace_id=workspace_id,
            result={"entity_count": len(id_map), "relation_count": emitted_relations},
        )
        self._emit(AnalysisEvent.SENT_TO_WORKSPACE, sent_payload)
        self.bus.workspace_changed.emit("analysis.sent_to_workspace", sent_payload)

    def _handle_runner_complete(self, payload: AnalysisPayload, template: dict, contract: dict, run_id: str, final_state: dict):
        if run_id in self._failed_runs:
            return
        template_id = template.get("id") or payload.template_id
        chunk_raw = final_state.get("final_analysis", "[]")
        master_raw = final_state.get("master_diagram", "{}")
        chunks = self._parse_jsonish(chunk_raw)
        master = self._parse_jsonish(master_raw)
        result = self._normalize_result(payload.doc_path, template_id, run_id, template, contract, chunks, master)
        self._results[run_id] = result
        try:
            for idx, chunk in enumerate(result.get("chunks", [])):
                self.pm.save_document_analysis(payload.doc_path, template_id, idx, json.dumps(chunk))
            self.pm.save_document_analysis(payload.doc_path, template_id, 999999, json.dumps({"master": result}))
        except Exception as e:
            print(f"[AnalysisAppService] Save failed: {e}")
        self._emit(AnalysisEvent.RESULT_READY, AnalysisPayload(doc_path=payload.doc_path, template_id=template_id, run_id=run_id, template=template, result=result))
        self._emit(AnalysisEvent.RUN_COMPLETED, AnalysisPayload(doc_path=payload.doc_path, template_id=template_id, run_id=run_id, template=template, result=result))

    def _handle_step_started(self, payload: AnalysisPayload, step_id: str, total_chunks: int):
        labels = {
            "analyze_chunk_graph": f"Analyzing chunks with bounded context ({total_chunks} total)...",
            "compact_master_input": "Compacting chunk artifacts for final pass...",
            "master_diagram_pass": "Building master graph from compacted artifacts...",
        }
        self._emit_progress(payload, labels.get(step_id, f"Running {step_id}..."))

    def _handle_step_complete_progress(self, payload: AnalysisPayload, step_id: str, result: str, contract: dict, total_chunks: int):
        if "Generation Error" in str(result):
            self._handle_runner_error(payload, str(result).strip())
            return
        if step_id.startswith("process_all_chunks_item_"):
            try:
                idx = int(step_id.rsplit("_", 1)[-1]) + 1
                self._emit_progress(payload, f"Completed chunk {idx}/{total_chunks}.")
                self._emit_chunk_result(payload, idx, total_chunks, result, contract)
            except Exception:
                self._emit_progress(payload, "Completed a chunk.")
        elif step_id == "compact_master_input":
            self._emit_progress(payload, "Compacted artifacts; starting final pass.")

    def _emit_progress(self, payload: AnalysisPayload, message: str):
        self._emit(
            AnalysisEvent.PROGRESS,
            AnalysisPayload(
                doc_path=payload.doc_path,
                template_id=payload.template_id,
                run_id=payload.run_id,
                result={"message": message},
            ),
        )

    def _emit_chunk_result(self, payload: AnalysisPayload, chunk_number: int, total_chunks: int, result: str, contract: dict):
        parsed = self._parse_jsonish(result)
        if not isinstance(parsed, dict):
            return
        chunk = self._normalize_graph_object(parsed, f"chunk{chunk_number - 1}", contract)
        self._emit(
            AnalysisEvent.CHUNK_RESULT,
            AnalysisPayload(
                doc_path=payload.doc_path,
                template_id=payload.template_id,
                run_id=payload.run_id,
                result={
                    "chunk_number": chunk_number,
                    "total_chunks": total_chunks,
                    "chunk": chunk,
                },
            ),
        )

    def _handle_runner_error(self, payload: AnalysisPayload, message: str):
        if payload.run_id:
            self._failed_runs.add(payload.run_id)
        clean = str(message or "Unknown analysis error").strip()
        if "took too long" in clean.lower() or "hardware limits" in clean.lower():
            clean += " Try a smaller model, fewer selected node/relation types, or lower analysis limits."
        self._emit(AnalysisEvent.RUN_FAILED, payload, [clean])

    def _analysis_limits(self, template: dict) -> dict:
        raw = template.get("limits") if isinstance(template.get("limits"), dict) else {}
        return {
            "chunk_pages": int(raw.get("chunk_pages", template.get("chunk_pages", 1)) or 1),
            "max_chunk_chars": int(raw.get("max_chunk_chars", template.get("max_chunk_chars", 7000)) or 7000),
            "max_master_chars": int(raw.get("max_master_chars", template.get("max_master_chars", 18000)) or 18000),
            "num_ctx": int(raw.get("num_ctx", template.get("num_ctx", 8192)) or 8192),
            "chunk_num_predict": int(raw.get("chunk_num_predict", template.get("chunk_num_predict", 1000)) or 1000),
            "master_num_predict": int(raw.get("master_num_predict", template.get("master_num_predict", 1200)) or 1200),
            "max_entities_per_chunk": int(raw.get("max_entities_per_chunk", template.get("max_entities_per_chunk", 6)) or 6),
            "max_relations_per_chunk": int(raw.get("max_relations_per_chunk", template.get("max_relations_per_chunk", 10)) or 10),
            "max_quotes_per_chunk": int(raw.get("max_quotes_per_chunk", template.get("max_quotes_per_chunk", 3)) or 3),
        }

    def _master_compactor_script(self, max_chars: int) -> str:
        return f"""
import json
raw = state.get('final_analysis', '[]')
try:
    data = json.loads(raw) if isinstance(raw, str) else raw
except Exception:
    data = raw
if not isinstance(data, list):
    data = [data]
compact = []
for chunk in data:
    if isinstance(chunk, str):
        try:
            chunk = json.loads(chunk)
        except Exception:
            start = chunk.find('{{')
            end = chunk.rfind('}}')
            if start >= 0 and end > start:
                try:
                    chunk = json.loads(chunk[start:end + 1])
                except Exception:
                    continue
            else:
                continue
    if not isinstance(chunk, dict):
        continue
    entities = chunk.get('entities') or chunk.get('nodes') or []
    relations = chunk.get('relations') or chunk.get('edges') or []
    compact.append({{
        'summary': chunk.get('summary', ''),
        'entities': [
            {{
                'temp_id': e.get('temp_id') or e.get('id'),
                'type': e.get('type') or e.get('entity_type'),
                'title': e.get('title') or e.get('label'),
                'text': str(e.get('text') or e.get('claim') or e.get('reasoning') or '')[:360],
                'exact_text': str(e.get('exact_text') or e.get('quote') or '')[:500],
                'page': e.get('page') or e.get('page_num'),
                'properties': e.get('properties') if isinstance(e.get('properties'), dict) else {{}},
            }}
            for e in entities[:30] if isinstance(e, dict)
        ],
        'relations': [
            {{
                'source': r.get('source') or r.get('source_id'),
                'target': r.get('target') or r.get('target_id'),
                'type': r.get('type') or r.get('relation_type'),
                'properties': r.get('properties') if isinstance(r.get('properties'), dict) else {{}},
                'evidence_ids': r.get('evidence_ids') or [],
            }}
            for r in relations[:45] if isinstance(r, dict)
        ],
    }})
result = json.dumps(compact)
if len(result) > {int(max_chars)}:
    result = result[:{int(max_chars)}] + "\\n...TRUNCATED_FOR_CONTEXT_LIMIT..."
"""

    def _build_blueprint(self, template: dict, chunks: list, contract: dict, limits: dict) -> AIActionBlueprint:
        chunk_prompt = self._chunk_system_prompt(template, contract)
        master_prompt = self._master_system_prompt(template, contract)
        sub_blueprint = AIActionBlueprint(name="Analyze Chunk Graph", description="", steps=[
            ActionStep(
                step_id="analyze_chunk_graph",
                step_type="LLM_QUERY",
                inputs={"query": "Analyze pages {item.page_range}. Return ONLY this JSON object shape: {\"summary\":\"\",\"entities\":[],\"relations\":[]}.\n\nTEXT:\n{item.text}"},
                system_prompt=chunk_prompt,
                llm_options={
                    "json_mode": True,
                    "num_predict": limits["chunk_num_predict"],
                    "temperature": 0.15,
                    "num_ctx": limits["num_ctx"],
                },
                ui_format="silent",
                ui_target="analysis_tab",
                output_key="chunk_json",
            )
        ])
        return AIActionBlueprint(name="Graph Document Analysis", description="Graph-aware document analysis.", steps=[
            ActionStep("process_all_chunks", "FOREACH", inputs={"list": chunks, "sub_blueprint": sub_blueprint}, output_key="final_analysis", ui_format="silent"),
            ActionStep(
                "compact_master_input",
                "PYTHON_SCRIPT",
                inputs={"script": self._master_compactor_script(limits["max_master_chars"])},
                output_key="master_input",
                ui_format="silent",
            ),
            ActionStep(
                "master_diagram_pass",
                "LLM_QUERY",
                inputs={"query": "Compacted chunk graph artifacts:\n{master_input}"},
                system_prompt=master_prompt,
                llm_options={
                    "json_mode": True,
                    "num_predict": limits["master_num_predict"],
                    "temperature": 0.1,
                    "num_ctx": limits["num_ctx"],
                },
                ui_format="silent",
                ui_target="analysis_tab",
                output_key="master_diagram",
            ),
        ])

    def _build_contract(self, template: dict, limits: Optional[dict] = None) -> Dict[str, Any]:
        limits = limits or self._analysis_limits(template)
        node_types = list(template.get("node_types") or [])
        if template.get("allow_text_nodes", True) and EntityType.TEXT.value not in node_types:
            node_types.append(EntityType.TEXT.value)
        if not node_types:
            node_types = [EntityType.CLAIM.value, EntityType.REASONING.value, EntityType.QUOTE.value]
        relation_types = list(template.get("relation_types") or [])
        if not relation_types:
            relation_types = [RelationType.SUPPORTS.value, RelationType.CONTRADICTS.value, RelationType.REASONS.value, RelationType.DERIVED_FROM.value]
        allowed_entities = [self._entity_contract(t) for t in node_types]
        allowed_relations = [self._relation_contract(t) for t in relation_types]
        self._add_prompt_aliases(allowed_entities, self._entity_aliases)
        self._add_prompt_aliases(allowed_relations, self._relation_aliases)
        return {
            "allowed_entity_types": allowed_entities,
            "allowed_relation_types": allowed_relations,
            "extraction_limits": {
                "max_entities_per_chunk": limits["max_entities_per_chunk"],
                "max_relations_per_chunk": limits["max_relations_per_chunk"],
                "max_quotes_per_chunk": limits["max_quotes_per_chunk"],
                "instruction": "Prefer the strongest, most workspace-useful artifacts over exhaustive extraction.",
            },
            "user_output_schema": self._parse_template_schema(template.get("schema")),
            "chunk_schema": {
                "summary": "brief chunk summary",
                "entities": [{
                    "temp_id": "stable local id",
                    "type": [e["prompt_type"] for e in allowed_entities],
                    "title": "short label shown on the workspace node",
                    "text": "editable node note/body, or claim/reasoning text",
                    "exact_text": "required only for quote nodes; exact copied source text",
                    "page": "source page number if known",
                    "properties": "object containing relevant registry field keys and custom template outputs",
                }],
                "relations": [{
                    "source": "source entity temp_id",
                    "target": "target entity temp_id",
                    "type": [r["prompt_type"] for r in allowed_relations],
                    "properties": "object containing relevant registry relation fields such as confidence/strength/reasoning_note",
                    "evidence_ids": "optional list of quote/evidence temp_ids supporting this relation",
                }],
            },
        }

    def _entity_contract(self, type_key: str) -> dict:
        bp = self.registry.get_entity_blueprint(type_key)
        return {
            "type": bp.type_key,
            "label": bp.display_name,
            "description": bp.description,
            "requires_source": bp.requires_source,
            "default_properties": dict(bp.default_properties),
            "fields": [self._field_contract(field) for field in bp.fields],
            "requires_exact_quote": bp.type_key == EntityType.QUOTE.value,
            "extraction_hints": dict(getattr(bp, "extraction_hints", {}) or {}),
        }

    def _relation_contract(self, type_key: str) -> dict:
        bp = self.registry.get_relation_blueprint(type_key)
        return {
            "type": bp.type_key,
            "label": bp.display_name,
            "description": bp.description,
            "traits": [getattr(trait, "value", trait) for trait in bp.traits],
            "valid_source_types": list(bp.valid_source_types),
            "valid_target_types": list(bp.valid_target_types),
            "default_properties": dict(bp.default_properties),
            "fields": [self._field_contract(field) for field in bp.fields],
        }

    def _chunk_system_prompt(self, template: dict, contract: dict) -> str:
        prompt_key = template.get("chunk_prompt_key") or "Graph Analysis Chunk System"
        prompt = self.prompt_manager.get_prompt(prompt_key) if self.prompt_manager else ""
        return self._render_analysis_prompt(prompt, template, contract)

    def _master_system_prompt(self, template: dict, contract: dict) -> str:
        prompt_key = template.get("master_prompt_key") or "Graph Analysis Master System"
        prompt = self.prompt_manager.get_prompt(prompt_key) if self.prompt_manager else ""
        return self._render_analysis_prompt(prompt, template, contract)

    def _render_analysis_prompt(self, prompt: str, template: dict, contract: dict) -> str:
        if not prompt:
            prompt = "{template_instructions}\n\nREGISTRY CONTRACT:\n{analysis_contract}"
        compact_contract = self._prompt_contract(contract)
        return (
            prompt
            .replace("{template_instructions}", template.get("instructions", ""))
            .replace("{analysis_contract}", compact_contract)
            .replace("{template_schema}", compact_contract)
            .replace("{item.page_range}", "the pages in the user query")
            .replace("{item.text}", "the TEXT in the user query")
            .replace("{combined_text}", "the compacted chunk artifacts in the user query")
            + "\n\nMANDATORY OUTPUT SHAPE:\n"
            + '{"summary":"brief summary","entities":[{"temp_id":"c1","type":"claim","title":"short label","text":"claim/reasoning text","exact_text":"","page":1,"properties":{}}],"relations":[{"source":"q1","target":"r1","type":"supports","properties":{"confidence":0.7},"evidence_ids":["q1"]}]}'
            + "\nUse compact type aliases in output, e.g. claim, reasoning, quote, supports, contradicts, reasons, derived_from. Do not output entity.claim or relation.supports unless a custom prompt already requires it."
            + self._argument_map_directive(contract)
            + "\nNever use keys like document_summary, extracted_entities, page_number, or details as the top-level result."
        )

    def _prompt_contract(self, contract: dict) -> str:
        lines = ["Allowed node aliases:"]
        for ent in contract.get("allowed_entity_types", []):
            fields = ", ".join(field["key"] for field in ent.get("fields", [])) or "properties allowed"
            full_type = ent["type"]
            lines.append(f"- {ent['prompt_type']} = {full_type} ({ent['label']}): {ent.get('description', '')} Fields: {fields}.")
        lines.append("Allowed relation aliases:")
        for rel in contract.get("allowed_relation_types", []):
            fields = ", ".join(field["key"] for field in rel.get("fields", [])) or "properties allowed"
            src = ", ".join(self._short_type(t) for t in rel.get("valid_source_types", []))
            tgt = ", ".join(self._short_type(t) for t in rel.get("valid_target_types", []))
            lines.append(f"- {rel['prompt_type']} = {rel['type']} ({rel['label']}): source [{src}] -> target [{tgt}]. Fields: {fields}.")
        limits = contract.get("extraction_limits", {})
        lines.append(
            "Limits: "
            f"max entities {limits.get('max_entities_per_chunk')}, "
            f"max relations {limits.get('max_relations_per_chunk')}, "
            f"max quotes {limits.get('max_quotes_per_chunk')} per chunk."
        )
        schema = contract.get("user_output_schema") or {}
        if schema:
            lines.append(f"Extra properties requested by template: {json.dumps(schema, separators=(',', ':'))[:900]}")
        return "\n".join(lines)

    def _argument_map_directive(self, contract: dict) -> str:
        entity_types = {item["type"] for item in contract.get("allowed_entity_types", [])}
        relation_types = {item["type"] for item in contract.get("allowed_relation_types", [])}
        if not {EntityType.CLAIM.value, EntityType.REASONING.value, EntityType.QUOTE.value}.issubset(entity_types):
            return ""
        if not {RelationType.SUPPORTS.value, RelationType.REASONS.value}.issubset(relation_types):
            return ""
        return (
            "\nArgument-map rule: do not emit isolated claim-only output. For each strong claim, include at least one reasoning node and one quote node when the text contains evidence. "
            "Connect quote -> reasoning with supports or contradicts; connect reasoning -> claim with reasons. "
            "Quote nodes must put the verbatim passage in exact_text and may repeat it in text only if needed. "
            "Use evidence_ids on reasoning->claim links to name the quote ids."
        )

    def _build_type_aliases(self, enum_cls) -> dict:
        aliases = {}
        for item in enum_cls:
            value = item.value
            short = self._short_type(value)
            aliases[value] = value
            aliases[short] = value
            aliases[short.replace("_", "-")] = value
        aliases["quoted_text"] = EntityType.QUOTE.value
        aliases["citation"] = EntityType.QUOTE.value
        aliases["reason"] = EntityType.REASONING.value
        aliases["premise"] = EntityType.REASONING.value
        aliases["evidence_quote"] = EntityType.QUOTE.value
        aliases["support"] = RelationType.SUPPORTS.value
        aliases["contradict"] = RelationType.CONTRADICTS.value
        aliases["reason"] = aliases.get("reason", RelationType.REASONS.value)
        aliases["reason_for"] = RelationType.REASONS.value
        aliases["derived"] = RelationType.DERIVED_FROM.value
        return aliases

    def _add_prompt_aliases(self, items: list, aliases: dict):
        used = set()
        for item in items:
            short = self._short_type(item["type"])
            prompt_type = short if short not in used else item["type"]
            item["prompt_type"] = prompt_type
            used.add(prompt_type)

    def _short_type(self, type_key: str) -> str:
        if type_key == "*":
            return "*"
        return str(type_key or "").split(".", 1)[-1]

    def _normalize_entity_type(self, type_key: str) -> str:
        key = str(type_key or "").strip()
        return self._entity_aliases.get(key, self._entity_aliases.get(key.lower(), key))

    def _normalize_relation_type(self, type_key: str) -> str:
        key = str(type_key or "").strip()
        return self._relation_aliases.get(key, self._relation_aliases.get(key.lower(), key))

    def _coerce_node_item(self, item):
        if isinstance(item, dict):
            return item
        if isinstance(item, list) and len(item) >= 3:
            props = item[3] if len(item) > 3 and isinstance(item[3], dict) else {}
            node_type = item[1]
            exact_text = props.get("exact_text") or props.get("quote") or ""
            if self._normalize_entity_type(node_type) == EntityType.QUOTE.value:
                exact_text = exact_text or str(item[2] or "")
            return {
                "temp_id": item[0],
                "type": node_type,
                "text": item[2],
                "exact_text": exact_text,
                "title": props.get("title") or props.get("label"),
                "page": props.get("page") or props.get("page_num"),
                "properties": {k: v for k, v in props.items() if k not in {"title", "label", "page", "page_num", "exact_text", "quote"}},
            }
        return item

    def _coerce_relation_item(self, item):
        if isinstance(item, dict):
            return item
        if isinstance(item, list) and len(item) >= 4:
            props = item[4] if len(item) > 4 and isinstance(item[4], dict) else {}
            return {
                "temp_id": item[0],
                "type": item[1],
                "source": item[2],
                "target": item[3],
                "properties": {k: v for k, v in props.items() if k != "evidence_ids"},
                "evidence_ids": props.get("evidence_ids") or props.get("evidence") or [],
            }
        return item

    def _field_contract(self, field) -> dict:
        return {
            "key": field.key,
            "label": field.label,
            "value_type": field.value_type,
            "default": field.default,
            "editable": field.editable,
            "choices": list(field.choices or []),
            "minimum": field.minimum,
            "maximum": field.maximum,
        }

    def _parse_template_schema(self, raw_schema) -> dict:
        if isinstance(raw_schema, dict):
            return raw_schema
        if not raw_schema:
            return {}
        try:
            parsed = json.loads(raw_schema)
            return parsed if isinstance(parsed, dict) else {}
        except Exception:
            return {"notes": str(raw_schema)}

    def _normalize_result(self, doc_path, template_id, run_id, template, contract, chunks_raw, master_raw) -> dict:
        chunks = chunks_raw if isinstance(chunks_raw, list) else [chunks_raw]
        normalized_chunks = []
        all_entities, all_relations = [], []
        for idx, chunk in enumerate(chunks):
            if isinstance(chunk, str):
                chunk = self._parse_jsonish(chunk)
            if not isinstance(chunk, dict):
                continue
            chunk_norm = self._normalize_graph_object(chunk, f"chunk{idx}", contract)
            normalized_chunks.append(chunk_norm)
            all_entities.extend(chunk_norm["entities"])
            all_relations.extend(chunk_norm["relations"])

        master_norm = self._normalize_graph_object(master_raw, "master", contract) if isinstance(master_raw, dict) else {"entities": [], "relations": [], "summary": ""}
        if master_norm["entities"] or master_norm["relations"]:
            if len(master_norm["entities"]) >= max(2, int(len(all_entities) * 0.5)):
                all_entities = self._merge_entities(all_entities, master_norm["entities"])
                all_relations = self._merge_relations(all_relations, master_norm["relations"])
            else:
                all_relations = self._merge_relations(all_relations, master_norm["relations"])
        elif master_norm.get("summary"):
            for chunk in normalized_chunks:
                if not chunk.get("summary"):
                    chunk["summary"] = master_norm["summary"]
        return {
            "run_id": run_id,
            "doc_path": doc_path,
            "template_id": template_id,
            "template_title": template.get("title") or template.get("name") or "Analysis",
            "entities": self._ensure_master_argument_node(all_entities, all_relations, normalized_chunks, contract),
            "relations": all_relations,
            "chunks": normalized_chunks,
            "master": master_norm,
            "relations_contract": contract.get("allowed_relation_types", []),
        }

    def _normalize_graph_object(self, obj: dict, prefix: str, contract: dict) -> dict:
        allowed_entities = {item["type"] for item in contract["allowed_entity_types"]}
        allowed_relations = {item["type"] for item in contract["allowed_relation_types"]}
        entities = []
        temp_id_map = {}
        for idx, item in enumerate(self._coerce_list(obj.get("entities") or obj.get("nodes") or [])):
            item = self._coerce_node_item(item)
            if not isinstance(item, dict):
                continue
            entity_type = self._normalize_entity_type(item.get("type") or item.get("entity_type") or EntityType.TEXT.value)
            if entity_type not in allowed_entities:
                continue
            raw_temp_id = str(item.get("temp_id") or item.get("id") or f"e{idx}")
            temp_id = self._scoped_temp_id(prefix, raw_temp_id)
            temp_id_map[raw_temp_id] = temp_id
            entities.append({
                "temp_id": temp_id,
                "type": entity_type,
                "title": item.get("title") or item.get("label") or self.registry.get_entity_blueprint(entity_type).display_name,
                "text": item.get("text") or item.get("note") or item.get("claim") or item.get("reasoning") or "",
                "exact_text": item.get("exact_text") or item.get("quote") or "",
                "page": self._extract_page(item),
                "properties": item.get("properties") if isinstance(item.get("properties"), dict) else {},
            })
        if not entities:
            entities.extend(self._fallback_entities_from_legacy_output(obj, prefix, allowed_entities))
        entity_ids = {item["temp_id"] for item in entities}
        relations = []
        for idx, rel in enumerate(self._coerce_list(obj.get("relations") or obj.get("edges") or [])):
            rel = self._coerce_relation_item(rel)
            if not isinstance(rel, dict):
                continue
            rel_type = self._normalize_relation_type(rel.get("type") or rel.get("relation_type") or RelationType.BASIC.value)
            raw_src = str(rel.get("source") or rel.get("source_id") or "")
            raw_tgt = str(rel.get("target") or rel.get("target_id") or "")
            src = temp_id_map.get(raw_src, self._scoped_temp_id(prefix, raw_src) if raw_src else "")
            tgt = temp_id_map.get(raw_tgt, self._scoped_temp_id(prefix, raw_tgt) if raw_tgt else "")
            if src not in entity_ids or tgt not in entity_ids:
                continue
            rel_type = self._compatible_relation_type(rel_type, src, tgt, entities, allowed_relations)
            if not rel_type:
                continue
            relations.append({
                "temp_id": self._scoped_temp_id(prefix, str(rel.get("temp_id") or rel.get("id") or f"r{idx}")),
                "source": src,
                "target": tgt,
                "type": rel_type,
                "properties": rel.get("properties") if isinstance(rel.get("properties"), dict) else {k: v for k, v in rel.items() if k not in {"source", "target", "source_id", "target_id", "type", "relation_type", "evidence_ids"}},
                "evidence_ids": [
                    temp_id_map.get(str(eid), self._scoped_temp_id(prefix, str(eid)))
                    for eid in self._coerce_list(rel.get("evidence_ids") or [])
                    if eid is not None
                ],
            })
        if not relations and len(entities) > 1:
            relations = self._synthesize_relations(entities, allowed_relations)
        relations = self._ensure_argument_chains(entities, relations, allowed_relations)
        return {"summary": obj.get("summary", ""), "entities": entities, "relations": relations}

    def _scoped_temp_id(self, prefix: str, temp_id: str) -> str:
        temp_id = str(temp_id or "").strip()
        if not temp_id:
            return prefix
        if temp_id.startswith(f"{prefix}_") or temp_id.startswith("master_"):
            return temp_id
        return f"{prefix}_{temp_id}"

    def _compatible_relation_type(self, relation_type: str, source_id: str, target_id: str, entities: list, allowed_relations: set[str]) -> Optional[str]:
        entity_types = {entity["temp_id"]: entity["type"] for entity in entities}
        return self._compatible_relation_type_for_types(relation_type, entity_types.get(source_id), entity_types.get(target_id), allowed_relations)

    def _compatible_relation_type_for_types(self, relation_type: str, source_type: Optional[str], target_type: Optional[str], allowed_relations: set[str]) -> Optional[str]:
        if relation_type in allowed_relations and source_type and target_type and self.registry.validate_relation(relation_type, source_type, target_type):
            return relation_type
        for candidate in [
            RelationType.SUPPORTS.value,
            RelationType.CONTRADICTS.value,
            RelationType.REASONS.value,
            RelationType.DERIVED_FROM.value,
            RelationType.PART_OF.value,
            RelationType.SIMILAR_TO.value,
            RelationType.BASIC.value,
        ]:
            if candidate in allowed_relations and source_type and target_type and self.registry.validate_relation(candidate, source_type, target_type):
                return candidate
        return None

    def _synthesize_relations(self, entities: list, allowed_relations: set[str]) -> list:
        root = entities[0]["temp_id"]
        relations = []
        for idx, entity in enumerate(entities[1:]):
            if entity["temp_id"] == root:
                continue
            relation_type = self._compatible_relation_type(
                self._preferred_relation_type(allowed_relations) or "",
                entity["temp_id"],
                root,
                entities,
                allowed_relations,
            )
            if not relation_type:
                continue
            relations.append({
                "temp_id": f"auto_rel_{idx}",
                "source": entity["temp_id"],
                "target": root,
                "type": relation_type,
                "properties": {
                    "confidence": 0.35,
                    "reasoning_note": "Automatically inferred because the model emitted related nodes without explicit relations.",
                    "auto_inferred": True,
                },
                "evidence_ids": [],
            })
        return relations

    def _ensure_argument_chains(self, entities: list, relations: list, allowed_relations: set[str]) -> list:
        entity_types = {entity["temp_id"]: entity["type"] for entity in entities}
        if not {
            EntityType.CLAIM.value,
            EntityType.REASONING.value,
            EntityType.QUOTE.value,
        }.issubset(set(entity_types.values())):
            return relations
        if not {
            RelationType.SUPPORTS.value,
            RelationType.REASONS.value,
        }.issubset(allowed_relations):
            return relations

        claims = [entity for entity in entities if entity["type"] == EntityType.CLAIM.value]
        reasoning = [entity for entity in entities if entity["type"] == EntityType.REASONING.value]
        quotes = [entity for entity in entities if entity["type"] == EntityType.QUOTE.value]
        if not claims or not reasoning or not quotes:
            return relations

        existing = {(rel.get("source"), rel.get("target"), rel.get("type")) for rel in relations}
        quote_ids = {quote["temp_id"] for quote in quotes}
        claim_ids = {claim["temp_id"] for claim in claims}
        reasoning_ids = {reason["temp_id"] for reason in reasoning}

        for idx, reason in enumerate(reasoning):
            reason_id = reason["temp_id"]
            has_quote_link = any(
                rel.get("target") == reason_id
                and rel.get("source") in quote_ids
                and rel.get("type") in {RelationType.SUPPORTS.value, RelationType.CONTRADICTS.value}
                for rel in relations
            )
            has_claim_link = any(
                rel.get("source") == reason_id
                and rel.get("target") in claim_ids
                and rel.get("type") == RelationType.REASONS.value
                for rel in relations
            )

            quote = self._nearest_text_match(reason, quotes) or quotes[min(idx, len(quotes) - 1)]
            claim = self._nearest_text_match(reason, claims) or claims[min(idx, len(claims) - 1)]

            if not has_quote_link:
                rel_key = (quote["temp_id"], reason_id, RelationType.SUPPORTS.value)
                if rel_key not in existing:
                    relations.append({
                        "temp_id": f"auto_quote_reason_{idx}",
                        "source": quote["temp_id"],
                        "target": reason_id,
                        "type": RelationType.SUPPORTS.value,
                        "properties": {
                            "confidence": 0.45,
                            "strength": 0.45,
                            "auto_inferred": True,
                            "reasoning_note": "Linked quote evidence to nearby reasoning because the extracted argument chain was incomplete.",
                        },
                        "evidence_ids": [quote["temp_id"]],
                    })
                    existing.add(rel_key)

            if not has_claim_link:
                rel_key = (reason_id, claim["temp_id"], RelationType.REASONS.value)
                if rel_key not in existing:
                    evidence_ids = [
                        rel.get("source")
                        for rel in relations
                        if rel.get("target") == reason_id and rel.get("source") in quote_ids
                    ] or [quote["temp_id"]]
                    relations.append({
                        "temp_id": f"auto_reason_claim_{idx}",
                        "source": reason_id,
                        "target": claim["temp_id"],
                        "type": RelationType.REASONS.value,
                        "properties": {
                            "confidence": 0.45,
                            "auto_inferred": True,
                            "reasoning_note": "Linked reasoning into a claim because the extracted argument chain was incomplete.",
                        },
                        "evidence_ids": evidence_ids,
                    })
                    existing.add(rel_key)

        return [
            rel for rel in relations
            if self._compatible_relation_type_for_types(
                rel.get("type"),
                entity_types.get(rel.get("source")),
                entity_types.get(rel.get("target")),
                allowed_relations,
            )
        ]

    def _nearest_text_match(self, anchor: dict, candidates: list) -> Optional[dict]:
        anchor_words = self._content_words(anchor)
        if not anchor_words:
            return candidates[0] if candidates else None
        best = None
        best_score = 0
        for candidate in candidates:
            words = self._content_words(candidate)
            score = len(anchor_words.intersection(words))
            if score > best_score:
                best = candidate
                best_score = score
        return best

    def _content_words(self, item: dict) -> set[str]:
        text = " ".join(str(item.get(key) or "") for key in ("title", "text", "exact_text"))
        return {word for word in re.findall(r"[a-zA-Z]{4,}", text.lower()) if word not in {"this", "that", "with", "from", "have", "will", "which", "their", "there"}}

    def _preferred_relation_type(self, allowed_relations: set[str]) -> Optional[str]:
        for relation_type in [
            RelationType.SUPPORTS.value,
            RelationType.REASONS.value,
            RelationType.DERIVED_FROM.value,
            RelationType.PART_OF.value,
            RelationType.SIMILAR_TO.value,
            RelationType.BASIC.value,
        ]:
            if relation_type in allowed_relations:
                return relation_type
        return next(iter(allowed_relations), None)

    def _fallback_entities_from_legacy_output(self, obj: dict, prefix: str, allowed_entities: set[str]) -> list:
        preferred_type = self._preferred_non_quote_type(allowed_entities)
        if not preferred_type:
            return []
        entities = []
        summary = obj.get("summary") or obj.get("document_summary") or obj.get("text")
        if summary:
            entities.append({
                "temp_id": f"{prefix}_summary",
                "type": preferred_type,
                "title": "Summary",
                "text": str(summary)[:900],
                "exact_text": "",
                "page": self._extract_page(obj),
                "properties": {"legacy_shape": True},
            })
        for idx, item in enumerate(self._coerce_list(obj.get("extracted_entities") or obj.get("items") or [])):
            if not isinstance(item, dict):
                continue
            text = item.get("details") or item.get("text") or item.get("entity") or item.get("name")
            if not text:
                continue
            entities.append({
                "temp_id": f"{prefix}_legacy_{idx}",
                "type": preferred_type,
                "title": str(item.get("entity") or item.get("name") or item.get("type") or "Extracted item")[:80],
                "text": str(text)[:900],
                "exact_text": "",
                "page": self._extract_page(item) or self._extract_page(obj),
                "properties": {"legacy_shape": True, "legacy_type": item.get("type", "")},
            })
        return entities

    def _preferred_non_quote_type(self, allowed_entities: set[str]) -> Optional[str]:
        for type_key in [EntityType.CLAIM.value, EntityType.REASONING.value, EntityType.FINDING.value, EntityType.TEXT.value]:
            if type_key in allowed_entities:
                return type_key
        return next((type_key for type_key in allowed_entities if type_key != EntityType.QUOTE.value), None)

    def _merge_entities(self, base: list, incoming: list) -> list:
        seen = {self._entity_dedupe_key(item): item for item in base}
        for item in incoming:
            seen.setdefault(self._entity_dedupe_key(item), item)
        return list(seen.values())

    def _ensure_master_argument_node(self, entities: list, relations: list, chunks: list, contract: dict) -> list:
        allowed_entities = {item["type"] for item in contract.get("allowed_entity_types", [])}
        allowed_relations = {item["type"] for item in contract.get("allowed_relation_types", [])}
        if EntityType.CLAIM.value not in allowed_entities or RelationType.SUPPORTS.value not in allowed_relations:
            return entities
        claims = [entity for entity in entities if entity.get("type") == EntityType.CLAIM.value]
        if len(claims) < 2:
            return entities
        existing_master = next((entity for entity in entities if entity.get("properties", {}).get("role") == "master_claim"), None)
        root = existing_master or {
            "temp_id": "master_argument_claim",
            "type": EntityType.CLAIM.value,
            "title": "Document argument",
            "text": self._master_summary_text(chunks, claims),
            "exact_text": "",
            "page": None,
            "properties": {"role": "master_claim", "auto_inferred": True, "confidence": 0.35},
        }
        if not existing_master:
            entities = [root] + entities
        linked_to_root = {
            rel.get("source")
            for rel in relations
            if rel.get("target") == root["temp_id"] and rel.get("type") == RelationType.SUPPORTS.value
        }
        for idx, claim in enumerate(claims):
            if claim["temp_id"] == root["temp_id"] or claim["temp_id"] in linked_to_root:
                continue
            relations.append({
                "temp_id": f"master_support_{idx}",
                "source": claim["temp_id"],
                "target": root["temp_id"],
                "type": RelationType.SUPPORTS.value,
                "properties": {
                    "confidence": 0.4,
                    "strength": 0.4,
                    "auto_inferred": True,
                    "reasoning_note": "Linked into the generated document-level argument outline.",
                },
                "evidence_ids": [],
            })
        return entities

    def _master_summary_text(self, chunks: list, claims: list) -> str:
        summaries = [str(chunk.get("summary", "")).strip() for chunk in chunks if isinstance(chunk, dict) and chunk.get("summary")]
        if summaries:
            return " ".join(summaries)[:900]
        return "Document-level argument synthesized from extracted claims: " + "; ".join(str(c.get("title") or c.get("text") or "") for c in claims[:5])[:700]

    def _merge_relations(self, base: list, incoming: list) -> list:
        seen = {(r.get("source"), r.get("target"), r.get("type")): r for r in base}
        for rel in incoming:
            seen.setdefault((rel.get("source"), rel.get("target"), rel.get("type")), rel)
        return list(seen.values())

    def _entity_dedupe_key(self, item: dict) -> Tuple[str, str]:
        text = item.get("exact_text") or item.get("text") or item.get("title") or item.get("temp_id")
        return item.get("type"), re.sub(r"\W+", "", str(text).lower())[:160]

    def _parse_jsonish(self, value):
        if not isinstance(value, str):
            return value
        success, parsed = extract_and_heal_json(value)
        return parsed if success else {}

    def _all_relation_types(self) -> set[str]:
        return {bp.type_key for bp in self.registry.all_relations()}

    def _resolve_template(self, template_id: Optional[str], template: dict) -> dict:
        if template:
            return dict(template)
        for item in self.pm.get_analysis_templates():
            if item.get("id") == template_id:
                return dict(item)
        return {}

    def _ensure_source(self, doc_path: Optional[str]) -> Optional[EntityModel]:
        if not doc_path or not getattr(self.pm, "db_graph", None):
            return None
        return self.pm.db_graph.ensure_source_entity(doc_path)

    def _view_meta_for_index(self, index: int, item: dict) -> dict:
        col = index % 4
        row = index // 4
        return {"x": 80 + col * 260, "y": 80 + row * 180, "properties": {"width": 210, "height": 120}}

    def _stable_entity_id(self, result: dict, temp_id: str) -> str:
        return "analysis:" + str(uuid.uuid5(uuid.NAMESPACE_URL, f"{result.get('run_id')}:{temp_id}"))

    def _stable_relation_id(self, result: dict, src: str, tgt: str, rel_type: str) -> str:
        return "analysis-rel:" + str(uuid.uuid5(uuid.NAMESPACE_URL, f"{result.get('run_id')}:{src}:{tgt}:{rel_type}"))

    def _run_id(self, doc_path: str, template_id: str) -> str:
        raw = f"{doc_path}:{template_id}:{os.path.getmtime(doc_path) if os.path.exists(doc_path) else ''}"
        return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:16]

    def _extract_page(self, item: dict):
        page = item.get("page") if item.get("page") is not None else item.get("page_num")
        try:
            return int(page)
        except Exception:
            return None

    def _coerce_list(self, value) -> list:
        if value is None:
            return []
        if isinstance(value, list):
            return value
        return [value]

    def _emit(self, event, payload: AnalysisPayload, errors: Optional[List[str]] = None):
        if errors:
            payload.errors = errors
        self.bus.analysis_result_changed.emit(event, payload)
