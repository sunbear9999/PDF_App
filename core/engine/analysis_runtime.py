from __future__ import annotations

import hashlib
import json
import os
import re
import uuid
from typing import Any, Dict, List, Optional, Tuple

from core.events.domains.analysis_events import AnalysisEvent, AnalysisPayload
from core.events.domains.ontology_events import EntityPayload, RelationPayload
from core.models.ontology_model import EntityIntent, EntityModel, RelationIntent
from core.ontology.registry import OntologyRegistry, RelationTrait
from core.utils.json_utils import extract_and_heal_json


class AnalysisRuntime:
    """Reusable MasterRunner support for registry-driven document graph analysis."""

    def __init__(self, project_manager, prompt_manager, registry: Optional[OntologyRegistry] = None, bus=None):
        self.pm = project_manager
        self.prompt_manager = prompt_manager
        self.registry = registry or OntologyRegistry()
        if bus is None:
            from core.events.event_bus import EventBus
            bus = EventBus.get_instance()
        self.bus = bus
        self._entity_aliases = self._build_entity_aliases()
        self._relation_aliases = self._build_relation_aliases()

    def analysis_limits(self, template: dict) -> dict:
        raw = template.get("limits") if isinstance(template.get("limits"), dict) else {}
        return {
            "chunk_pages": int(raw.get("chunk_pages", template.get("chunk_pages", 4)) or 4),
            "max_chunk_chars": int(raw.get("max_chunk_chars", template.get("max_chunk_chars", 14000)) or 14000),
            "max_master_chars": int(raw.get("max_master_chars", template.get("max_master_chars", 36000)) or 36000),
            "num_ctx": int(raw.get("num_ctx", template.get("num_ctx", 24576)) or 24576),
            # INCREASED: Give the LLM enough room to output complete JSON graphs
            "chunk_num_predict": int(raw.get("chunk_num_predict", template.get("chunk_num_predict", 1400)) or 1400),
            "synthesis_num_predict": int(raw.get("synthesis_num_predict", template.get("synthesis_num_predict", 3500)) or 3500),
            "master_num_predict": int(raw.get("master_num_predict", template.get("master_num_predict", 4000)) or 4000),
            "max_entities_per_chunk": int(raw.get("max_entities_per_chunk", template.get("max_entities_per_chunk", 6)) or 6),
            "max_relations_per_chunk": int(raw.get("max_relations_per_chunk", template.get("max_relations_per_chunk", 10)) or 10),
            "max_quotes_per_chunk": int(raw.get("max_quotes_per_chunk", template.get("max_quotes_per_chunk", 2)) or 2),
            "quote_words": int(raw.get("quote_words", template.get("quote_words", 10)) or 10),
            "max_quote_words": int(raw.get("max_quote_words", template.get("max_quote_words", 18)) or 18),
            "explanation_words": int(raw.get("explanation_words", template.get("explanation_words", 10)) or 10),
        }

    def send_to_workspace(self, result: dict, workspace_id: int = 1) -> dict:
        # REMOVED: self._ensure_source() - SQLite cannot be called from this QThread.
        # We rely on the origin_id payload to let the main thread build the source.
        result = self._bounded_workspace_result(result)
        
        id_map: Dict[str, str] = {}
        type_by_temp: Dict[str, str] = {}
        for idx, item in enumerate(result.get("entities", [])):
            temp_id = str(item.get("temp_id") or item.get("id") or f"n{idx}")
            entity_type = item.get("type") or self._fallback_text_entity_type()
            if not entity_type:
                continue
            type_by_temp[temp_id] = entity_type
            props = dict(item.get("properties") or {})
            for key in ("text", "title", "exact_text", "quote", "page", "page_num"):
                if item.get(key) not in (None, ""):
                    props.setdefault(key, item.get(key))
            if item.get("exact_text") or props.get("exact_text") or props.get("quote"):
                quote = item.get("exact_text") or item.get("quote") or item.get("text") or props.get("exact_text") or ""
                props.update({"exact_text": quote, "quote": quote, "text": quote, "note_text": item.get("note_text") or props.get("note_text", "")})
            
            # Map the PDF directly; the main thread's ontology manager will link it
            props.setdefault("pdf_path", result.get("doc_path"))
            if result.get("doc_path"):
                props.setdefault("doc_name", os.path.basename(result.get("doc_path")))
                
            entity_id = self._stable_entity_id(result, temp_id)
            id_map[temp_id] = entity_id
            self.bus.entity_action_requested.emit(
                EntityIntent.ADD,
                EntityPayload(
                    entity_id=entity_id,
                    entity_type=entity_type,
                    origin_id=result.get("doc_path"),
                    view_id=str(workspace_id),
                    data={
                        "properties": props,
                        "state": {"is_verified": False, "ai_generated": True, "origin": "analysis_ai"},
                        "view_meta": self._view_meta_for_index(idx),
                    },
                ),
            )

        emitted = 0
        allowed = {r.get("type") for r in result.get("relations_contract", []) if r.get("type")} or {bp.type_key for bp in self.registry.all_relations()}
        for idx, rel in enumerate(result.get("relations", [])):
            raw_src = str(rel.get("source") or rel.get("source_id") or "")
            raw_tgt = str(rel.get("target") or rel.get("target_id") or "")
            src, tgt = id_map.get(raw_src), id_map.get(raw_tgt)
            if not src or not tgt:
                continue
            rel_type = self._compatible_relation_type_for_types(rel.get("type"), type_by_temp.get(raw_src), type_by_temp.get(raw_tgt), allowed)
            if not rel_type:
                continue
            self.bus.relation_action_requested.emit(
                RelationIntent.ADD,
                RelationPayload(
                    relation_id=self._stable_relation_id(result, src, tgt, rel_type),
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
            emitted += 1
        return {"entity_count": len(id_map), "relation_count": emitted}

    def build_contract(self, template: dict) -> Dict[str, Any]:
        limits = self.analysis_limits(template)
        node_types = list(template.get("node_types") or [])
        if not node_types:
            node_types = [bp.type_key for bp in self.registry.all_entities()]
        if template.get("allow_text_nodes", False):
            fallback_type = self._fallback_text_entity_type()
            if fallback_type and fallback_type not in node_types:
                node_types.append(fallback_type)

        relation_types = list(template.get("relation_types") or [])
        if not relation_types:
            relation_types = [bp.type_key for bp in self.registry.all_relations()]

        allowed_entities = [self._entity_contract(type_key) for type_key in node_types]
        allowed_relations = [self._relation_contract(type_key) for type_key in relation_types]
        self._add_prompt_aliases(allowed_entities)
        self._add_prompt_aliases(allowed_relations)
        graph_plan = self._build_graph_plan(allowed_entities, allowed_relations)
        return {
            "allowed_entity_types": allowed_entities,
            "allowed_relation_types": allowed_relations,
            "limits": limits,
            "template_schema": self._parse_template_schema(template.get("schema")),
            "graph_plan": graph_plan,
            "prompt_contract": "",
        }

    def build_prompt_contract(self, contract: dict) -> str:
        lines = ["NODES"]
        for ent in contract.get("allowed_entity_types", []):
            fields = ",".join(field["key"] for field in ent.get("fields", []))
            exact = " exact_text=verbatim_required" if ent.get("requires_exact_quote") else ""
            lines.append(f"{ent['a']}={ent['type']} {ent['label']}; fields:{fields or '-'}; src:{int(bool(ent.get('requires_source')))};{exact}")
        lines.append("EDGES")
        for rel in contract.get("allowed_relation_types", []):
            fields = ",".join(field["key"] for field in rel.get("fields", []))
            src = ",".join(self._alias_for_type(t, contract.get("allowed_entity_types", [])) for t in rel.get("valid_source_types", []))
            tgt = ",".join(self._alias_for_type(t, contract.get("allowed_entity_types", [])) for t in rel.get("valid_target_types", []))
            lines.append(f"{rel['a']}={rel['type']} {rel['label']}; {src}->{tgt}; fields:{fields or '-'}")
        limits = contract.get("limits", {})
        lines.append(
            f"LIMITS nodes<={limits.get('max_entities_per_chunk')} edges<={limits.get('max_relations_per_chunk')} quotes<={limits.get('max_quotes_per_chunk')}"
        )
        schema = contract.get("template_schema") or {}
        if schema:
            lines.append("EXTRA_SCHEMA " + json.dumps(schema, separators=(",", ":"))[:700])
        plan = contract.get("graph_plan") or {}
        if plan:
            lines.append("GRAPH_PLAN " + self._graph_plan_text(plan))
        return "\n".join(lines)

    def chunk_document(self, doc_path: str, template_id: str, template: dict, contract: dict) -> list:
        from core.utils.doc_parser import DocumentParser
        limits = contract.get("limits") or self.analysis_limits(template)
        prompt_contract = contract.get("prompt_contract") or self.build_prompt_contract(contract)
        return DocumentParser.chunk_document_for_analysis(
            doc_path,
            template_id,
            template.get("instructions", ""),
            prompt_contract,
            chunk_size=limits["chunk_pages"],
            max_chars_per_chunk=limits["max_chunk_chars"],
        )

    def chunk_system_prompt(self, template: dict, contract: dict) -> str:
        prompt_key = template.get("chunk_prompt_key") or "Graph Analysis Chunk Observations System"
        prompt = self.prompt_manager.get_prompt(prompt_key)
        if prompt_key == "Graph Analysis Chunk System":
            return self._render_prompt(prompt, template, contract, master=False)
        return self._render_chunk_observation_prompt(prompt, template)

    def master_system_prompt(self, template: dict, contract: dict) -> str:
        prompt = self.prompt_manager.get_prompt(template.get("master_prompt_key") or "Graph Analysis Master System")
        return self._render_prompt(prompt, template, contract, master=True)

    def synthesis_system_prompt(self, template: dict, contract: dict) -> str:
        prompt = self.prompt_manager.get_prompt(template.get("synthesis_prompt_key") or "Graph Analysis Synthesis System")
        return self._render_synthesis_prompt(prompt, template)

    def chunk_query_prompt(self, template: dict) -> str:
        return self.prompt_manager.get_prompt(template.get("chunk_query_prompt_key") or "Graph Analysis Chunk Observations Query")

    def master_query_prompt(self, template: dict) -> str:
        return self.prompt_manager.get_prompt(template.get("master_query_prompt_key") or "Graph Analysis Master Query")

    def synthesis_query_prompt(self, template: dict) -> str:
        return self.prompt_manager.get_prompt(template.get("synthesis_query_prompt_key") or "Graph Analysis Synthesis Query")

    def compact_master_input(self, chunks: Any, max_chars: int, contract: Optional[dict] = None) -> str:
        if isinstance(chunks, str):
            success, parsed = extract_and_heal_json(chunks)
            chunks = parsed if success else []
        if not isinstance(chunks, list):
            chunks = [chunks]
        contract = contract or {}
        entity_alias = {item.get("type"): item.get("a") for item in contract.get("allowed_entity_types", [])}
        relation_alias = {item.get("type"): item.get("a") for item in contract.get("allowed_relation_types", [])}
        compact = []
        quote_counter = 1
        for chunk in chunks:
            if isinstance(chunk, str):
                success, chunk = extract_and_heal_json(chunk)
                if not success:
                    continue
            if not isinstance(chunk, dict):
                continue
            if self._is_chunk_observation(chunk):
                item = self._compact_chunk_observation(chunk)
                for quote in item.get("q", []):
                    quote["id"] = f"Q{quote_counter}"
                    quote_counter += 1
                compact.append(item)
                continue
            raw_nodes = chunk.get("entities") or chunk.get("nodes") or chunk.get("n") or []
            raw_edges = chunk.get("relations") or chunk.get("edges") or chunk.get("e") or []
            compact.append({
                "s": str(chunk.get("summary") or chunk.get("s") or "")[:260],
                "n": [
                    self._compact_master_node(e, entity_alias)
                    for e in raw_nodes[:36] if isinstance(e, dict)
                ],
                "e": [
                    self._compact_master_edge(r, relation_alias)
                    for r in raw_edges[:54] if isinstance(r, dict)
                ],
            })
        result = json.dumps(compact, separators=(",", ":"))
        return result[:max_chars]

    def normalize_result(self, doc_path: str, template_id: str, run_id: str, template: dict, contract: dict, chunks_raw: Any, master_raw: Any, synthesis: str = "") -> dict:
        chunks = chunks_raw if isinstance(chunks_raw, list) else self._parse_jsonish(chunks_raw)
        chunks = chunks if isinstance(chunks, list) else [chunks]
        normalized_chunks, all_entities, all_relations = [], [], []
        for idx, chunk in enumerate(chunks):
            chunk = self._parse_jsonish(chunk) if isinstance(chunk, str) else chunk
            if not isinstance(chunk, dict):
                continue
            if self._is_chunk_observation(chunk):
                normalized_chunks.append(self._normalize_chunk_observation(chunk, idx))
                continue
            norm = self.normalize_graph_object(chunk, f"chunk{idx}", contract)
            normalized_chunks.append(norm)

        master = self._parse_jsonish(master_raw) if isinstance(master_raw, str) else master_raw
        master_norm = self.normalize_graph_object(master, "master", contract) if isinstance(master, dict) else {"summary": "", "entities": [], "relations": []}
        if self._master_graph_is_usable(master_norm):
            all_entities = master_norm["entities"]
            all_relations = master_norm["relations"]
        else:
            all_entities, all_relations = self._synthesis_graph_from_chunks(normalized_chunks, str(synthesis or ""), contract)
            if not all_entities:
                all_entities, all_relations = self._observation_graph_from_chunks(normalized_chunks, contract)
        all_relations = self._ensure_argument_chains(all_entities, all_relations, {r["type"] for r in contract.get("allowed_relation_types", [])})
        all_entities, all_relations = self._dedupe_cross_type_repetitions(all_entities, all_relations, contract)
        all_relations = self._drop_confused_duplicate_relations(all_entities, all_relations, contract)
        all_entities, all_relations = self._ensure_graph_plan_roots(all_entities, all_relations, normalized_chunks, contract)
        all_relations = self._ensure_connected_master_graph(all_entities, all_relations, contract)
        all_entities = self._ensure_master_argument_node(all_entities, all_relations, normalized_chunks, contract)
        all_entities, all_relations = self._repair_graph_density(
            all_entities,
            all_relations,
            "master",
            contract,
            {r["type"] for r in contract.get("allowed_relation_types", [])},
        )
        return {
            "run_id": run_id,
            "doc_path": doc_path,
            "template_id": template_id,
            "template_title": template.get("title") or template.get("name") or "Analysis",
            "entities": all_entities,
            "relations": all_relations,
            "chunks": normalized_chunks,
            "master": master_norm,
            "relations_contract": contract.get("allowed_relation_types", []),
            "entities_contract": contract.get("allowed_entity_types", []),
        }

    def save_result(self, result: dict):
        if not self.pm:
            return
        doc_path, template_id = result.get("doc_path"), result.get("template_id")
        try:
            self.pm.clear_document_analyses(doc_path, template_id)
        except Exception:
            pass
        for idx, chunk in enumerate(result.get("chunks", [])):
            self.pm.save_document_analysis(doc_path, template_id, idx, json.dumps(chunk))
        self.pm.save_document_analysis(doc_path, template_id, 999999, json.dumps({"master": result}))

    
    def _is_chunk_observation(self, obj: dict) -> bool:
        return isinstance(obj, dict) and isinstance(obj.get("q") or obj.get("quotes") or obj.get("c") or obj.get("claims"), list)

    def _compact_chunk_observation(self, chunk: dict) -> dict:
        if isinstance(chunk.get("q") or chunk.get("quotes"), list):
            quotes = chunk.get("q") if isinstance(chunk.get("q"), list) else chunk.get("quotes", [])
            compact_quotes = []
            for idx, quote in enumerate(quotes[:5]):
                if not isinstance(quote, dict):
                    continue
                quote_text = str(quote.get("x") or quote.get("quote") or quote.get("exact_text") or "").strip()
                if not quote_text:
                    continue
                compact_quotes.append({
                    "id": str(quote.get("id") or f"q{idx + 1}"),
                    "x": self._short_quote_text(quote_text, 18),
                    "n": str(quote.get("n") or quote.get("note") or quote.get("relevance") or "")[:90],
                })
            return {"s": str(chunk.get("s") or chunk.get("summary") or "")[:180], "q": compact_quotes}

        claims = chunk.get("c") if isinstance(chunk.get("c"), list) else chunk.get("claims", [])
        compact_quotes = []
        for idx, claim in enumerate(claims[:8]):
            if not isinstance(claim, dict):
                continue
            quotes = claim.get("q") if isinstance(claim.get("q"), list) else claim.get("quotes", [])
            for q_idx, quote in enumerate(quotes[:2]):
                if not isinstance(quote, dict):
                    continue
                quote_text = str(quote.get("x") or quote.get("quote") or quote.get("exact_text") or "").strip()
                if not quote_text:
                    continue
                compact_quotes.append({
                    "id": str(quote.get("id") or f"q{idx + 1}_{q_idx + 1}"),
                    "x": self._short_quote_text(quote_text, 18),
                    "n": str(quote.get("n") or quote.get("note") or claim.get("x") or "")[:90],
                })
                if len(compact_quotes) >= 5:
                    break
            if len(compact_quotes) >= 5:
                break
        return {"s": str(chunk.get("s") or chunk.get("summary") or "")[:180], "q": compact_quotes}

    def _normalize_chunk_observation(self, chunk: dict, idx: int) -> dict:
        compact = self._compact_chunk_observation(chunk)
        return {
            "summary": compact.get("s", ""),
            "quotes": compact.get("q", []),
            "claims": [],
            "entities": [],
            "relations": [],
            "chunk_index": idx,
            "raw_observations": compact,
        }

    def _short_quote_text(self, text: str, max_words: int = 18) -> str:
        words = str(text or "").split()
        if len(words) <= max_words:
            return str(text or "").strip()
        return " ".join(words[:max_words]).strip()

    def _synthesis_graph_from_chunks(self, chunks: list, synthesis: str, contract: dict) -> tuple[list, list]:
        plan = contract.get("graph_plan") or {}
        chains = plan.get("preferred_chains") or []
        if not chunks or not synthesis.strip() or not chains:
            return [], []
        chain = max(chains, key=len)
        entity_contracts = contract.get("allowed_entity_types", [])
        allowed_relations = {item["type"] for item in contract.get("allowed_relation_types", [])}
        aliases = [chain[0]["s"]] + [step["g"] for step in chain]
        types = [self._type_for_alias(alias, entity_contracts) for alias in aliases]
        if not types or not types[0] or not types[-1]:
            return [], []

        source_type = types[0]
        root_type = types[-1]
        bridge_type = types[1] if len(types) > 2 and types[1] else None
        source_rel_type = self._type_for_alias(chain[0].get("t"), contract.get("allowed_relation_types", [])) if chain else None
        bridge_rel_type = self._type_for_alias(chain[1].get("t"), contract.get("allowed_relation_types", [])) if len(chain) > 1 else None
        quotes_by_id = self._numbered_quote_lookup(chunks)
        supports = self._parse_synthesis_supports(synthesis)
        if not supports:
            return [], []

        root_text = self._parse_synthesis_root(synthesis) or "Document synthesis"
        root = {
            "temp_id": "syn_root",
            "type": root_type,
            "title": root_text[:140],
            "text": root_text,
            "exact_text": "",
            "page": None,
            "properties": {"confidence": 0.72, "derived_from_synthesis": True},
        }
        entities = [root]
        relations = []
        seen_entities = {root["temp_id"]}
        seen_relations = set()

        def add_entity(entity):
            if entity["temp_id"] in seen_entities:
                return
            seen_entities.add(entity["temp_id"])
            entities.append(entity)

        def add_relation(source, target, rel_type, evidence_ids=None, confidence=0.72):
            if not rel_type or not self.registry.validate_relation(rel_type, source.get("type"), target.get("type")):
                rel_type = self._best_relation_between(
                    source.get("type"),
                    target.get("type"),
                    allowed_relations,
                    [RelationTrait.EVIDENTIARY, RelationTrait.HIERARCHICAL, RelationTrait.SEMANTIC],
                )
            if not rel_type:
                return
            key = (source["temp_id"], target["temp_id"], rel_type)
            if key in seen_relations:
                return
            seen_relations.add(key)
            relations.append({
                "temp_id": f"syn_rel_{len(relations)}",
                "source": source["temp_id"],
                "target": target["temp_id"],
                "type": rel_type,
                "properties": {"confidence": confidence, "derived_from_synthesis": True},
                "evidence_ids": list(evidence_ids or []),
            })

        for idx, support in enumerate(supports[:5]):
            quote_ids = [qid for qid in support.get("quote_ids", []) if qid in quotes_by_id]
            if not quote_ids:
                continue
            support_text = self._support_text(support)
            target = root
            if bridge_type:
                bridge = {
                    "temp_id": f"syn_support_{idx}",
                    "type": bridge_type,
                    "title": support_text[:140],
                    "text": support_text,
                    "exact_text": "",
                    "page": None,
                    "properties": {"confidence": 0.68, "derived_from_synthesis": True, "evidence_count": len(quote_ids)},
                }
                add_entity(bridge)
                add_relation(bridge, root, bridge_rel_type, [], 0.68)
                target = bridge
            for qid in quote_ids[:6]:
                quote = quotes_by_id[qid]
                quote_text = str(quote.get("x") or "").strip()
                if not quote_text:
                    continue
                source = {
                    "temp_id": f"syn_{qid.lower()}",
                    "type": source_type,
                    "title": str(quote.get("n") or quote_text)[:140],
                    "text": str(quote.get("n") or quote_text),
                    "exact_text": quote_text,
                    "page": None,
                    "properties": {
                        "exact_text": quote_text,
                        "quote": quote_text,
                        "note_text": str(quote.get("n") or ""),
                        "source_quote_id": qid,
                        "derived_from_synthesis": True,
                    },
                }
                add_entity(source)
                add_relation(source, target, source_rel_type, [source["temp_id"]], 0.78)
        return entities, relations

    def _numbered_quote_lookup(self, chunks: list) -> dict:
        lookup = {}
        counter = 1
        for chunk in chunks or []:
            quotes = chunk.get("quotes") or (chunk.get("raw_observations") or {}).get("q") or []
            for quote in quotes:
                if not isinstance(quote, dict):
                    continue
                lookup[f"Q{counter}"] = {
                    "x": str(quote.get("x") or quote.get("quote") or quote.get("exact_text") or "").strip(),
                    "n": str(quote.get("n") or quote.get("note") or "").strip(),
                }
                counter += 1
        return lookup

    def _parse_synthesis_root(self, synthesis: str) -> str:
        for line in str(synthesis or "").splitlines():
            text = line.strip().strip("* ")
            if not text:
                continue
            match = re.search(r"(?:central|main|top[- ]level).{0,30}?:\s*(.+)", text, re.I)
            if match:
                return self._clean_synthesis_text(match.group(1))
        for line in str(synthesis or "").splitlines():
            text = self._clean_synthesis_text(line)
            if text and not text.lower().startswith(("support", "evidence", "tension", "caveat")):
                return text
        return ""

    def _parse_synthesis_supports(self, synthesis: str) -> list:
        supports, by_key = [], {}
        for line in str(synthesis or "").splitlines():
            stripped = line.strip()
            if not stripped:
                continue
            quote_ids = [qid.upper() for qid in re.findall(r"\bQ\d+\b", stripped, flags=re.I)]
            numbered = re.match(r"^\s*\d+[\.\)]\s+(.*)$", stripped)
            content = numbered.group(1).strip() if numbered else stripped
            bold = re.search(r"\*\*(.+?)(?::)?\*\*\s*:?\s*(.*)", content)
            if numbered or (bold and quote_ids):
                if bold:
                    theme = self._clean_synthesis_text(bold.group(1))
                    detail = self._clean_synthesis_text(bold.group(2))
                else:
                    parts = content.split(":", 1)
                    theme = self._clean_synthesis_text(parts[0])
                    detail = self._clean_synthesis_text(parts[1] if len(parts) > 1 else "")
                if not theme or theme.lower() in {"evidence", "support themes", "supporting claims and evidence"}:
                    continue
                key = self._observation_claim_key(theme)
                item = by_key.setdefault(key, {"theme": theme, "detail": detail, "quote_ids": []})
                if detail and not item.get("detail"):
                    item["detail"] = detail
                for qid in quote_ids:
                    if qid not in item["quote_ids"]:
                        item["quote_ids"].append(qid)
                if item not in supports:
                    supports.append(item)
                continue
            if quote_ids and ":" in stripped:
                left, _right = stripped.split(":", 1)
                theme = self._clean_synthesis_text(left)
                key = self._observation_claim_key(theme)
                item = by_key.get(key)
                if not item:
                    for candidate_key, candidate in by_key.items():
                        if candidate_key and (candidate_key in key or key in candidate_key):
                            item = candidate
                            break
                if not item:
                    item = by_key.setdefault(key, {"theme": theme, "detail": "", "quote_ids": []})
                    supports.append(item)
                for qid in quote_ids:
                    if qid not in item["quote_ids"]:
                        item["quote_ids"].append(qid)
        return [item for item in supports if item.get("quote_ids")]

    def _clean_synthesis_text(self, text: str) -> str:
        text = re.sub(r"^[\-\*\s]+", "", str(text or "").strip())
        text = re.sub(r"\*\*", "", text)
        text = re.sub(r"\bQ\d+\b", "", text, flags=re.I)
        text = re.sub(r"[\s,;:]+$", "", text).strip()
        return text

    def _support_text(self, support: dict) -> str:
        theme = str(support.get("theme") or "").strip()
        detail = str(support.get("detail") or "").strip()
        if detail and detail.lower() not in theme.lower():
            return f"{theme}: {detail}"
        return theme or detail or "Evidence support"

    def _observation_graph_from_chunks(self, chunks: list, contract: dict) -> tuple[list, list]:
        plan = contract.get("graph_plan") or {}
        chains = plan.get("preferred_chains") or []
        if not chains:
            return [], []
        chain = max(chains, key=len)
        entity_contracts = contract.get("allowed_entity_types", [])
        allowed_relations = {item["type"] for item in contract.get("allowed_relation_types", [])}
        aliases = [chain[0]["s"]] + [step["g"] for step in chain]
        types = [self._type_for_alias(alias, entity_contracts) for alias in aliases]
        if not types or not types[0] or not types[-1]:
            return [], []

        source_type = types[0]
        root_type = types[-1]
        bridge_type = types[1] if len(types) > 2 and types[1] else None
        source_rel_type = self._type_for_alias(chain[0].get("t"), contract.get("allowed_relation_types", [])) if chain else None
        bridge_rel_type = self._type_for_alias(chain[1].get("t"), contract.get("allowed_relation_types", [])) if len(chain) > 1 else None

        entities, relations = [], []
        existing_entities = set()
        existing_relations = set()

        def add_entity(item):
            key = self._entity_dedupe_key(item)
            if key in existing_entities:
                return
            existing_entities.add(key)
            entities.append(item)

        def add_relation(source, target, rel_type, evidence_ids=None, idx_suffix=""):
            if not rel_type or not self.registry.validate_relation(rel_type, source.get("type"), target.get("type")):
                rel_type = self._best_relation_between(
                    source.get("type"),
                    target.get("type"),
                    allowed_relations,
                    [RelationTrait.EVIDENTIARY, RelationTrait.HIERARCHICAL, RelationTrait.SEMANTIC],
                )
            if not rel_type:
                return
            key = (source["temp_id"], target["temp_id"], rel_type)
            if key in existing_relations:
                return
            existing_relations.add(key)
            relations.append({
                "temp_id": f"obs_rel_{len(relations)}{idx_suffix}",
                "source": source["temp_id"],
                "target": target["temp_id"],
                "type": rel_type,
                "properties": {"confidence": 0.7, "derived_from_observation": True},
                "evidence_ids": list(evidence_ids or []),
            })

        groups = self._group_quote_evidence(chunks)
        limits = contract.get("limits") or {}
        chunk_max = max(1, int(limits.get("max_entities_per_chunk") or 6))
        max_claims = min(7, max(4, chunk_max // 2))
        max_quotes_each = min(3, max(1, int(limits.get("max_quotes_per_chunk") or 2)))
        for claim_idx, group in enumerate(groups[:max_claims]):
            claim_text = group["theme"]
            reason_text = group.get("reason", "")
            quotes = group.get("quotes", [])[:max_quotes_each]
            chunk_idx = group.get("chunk_index", 0)
            if not claim_text:
                continue
            root = {
                "temp_id": f"obs_c{claim_idx}",
                "type": root_type,
                "title": claim_text[:140],
                "text": claim_text,
                "exact_text": "",
                "page": None,
                "properties": {"confidence": 0.62, "derived_from_selected_quotes": True, "evidence_count": len(group.get("quotes", []))},
            }
            add_entity(root)
            bridge = None
            if bridge_type and reason_text:
                bridge = {
                    "temp_id": f"obs_r{claim_idx}",
                    "type": bridge_type,
                    "title": reason_text[:140],
                    "text": reason_text,
                    "exact_text": "",
                    "page": None,
                    "properties": {"confidence": 0.6, "derived_from_selected_quotes": True},
                }
                add_entity(bridge)
                add_relation(bridge, root, bridge_rel_type, [], f"_{claim_idx}_br")

            target = bridge or root
            for quote_idx, quote in enumerate(quotes):
                quote_text = str(quote.get("x") or "").strip()
                source = {
                    "temp_id": f"obs_q{claim_idx}_{quote_idx}",
                    "type": source_type,
                    "title": str(quote.get("n") or claim_text)[:140],
                    "text": str(quote.get("n") or claim_text),
                    "exact_text": quote_text,
                    "page": None,
                    "properties": {
                        "exact_text": quote_text,
                        "quote": quote_text,
                        "note_text": str(quote.get("n") or ""),
                        "derived_from_selected_quotes": True,
                        "source_chunk": chunk_idx,
                    },
                }
                add_entity(source)
                add_relation(source, target, source_rel_type, [source["temp_id"]], f"_{claim_idx}_{quote_idx}")
        return entities, relations

    def _group_quote_evidence(self, chunks: list) -> list:
        groups: dict[str, dict] = {}
        for chunk_idx, chunk in enumerate(chunks or []):
            summary = str(chunk.get("summary") or "").strip()
            quotes = chunk.get("quotes") or (chunk.get("raw_observations") or {}).get("q") or []
            for quote in quotes:
                if not isinstance(quote, dict):
                    continue
                quote_text = str(quote.get("x") or "").strip()
                note = str(quote.get("n") or "").strip()
                if not quote_text:
                    continue
                key = self._observation_claim_key(note or summary or quote_text)
                theme = self._theme_from_note(note, summary, quote_text)
                group = groups.setdefault(key, {
                    "theme": theme,
                    "reason": note or summary,
                    "quotes": [],
                    "quote_keys": set(),
                    "chunk_index": chunk_idx,
                })
                qkey = re.sub(r"\W+", "", quote_text.lower())[:180]
                if qkey in group["quote_keys"]:
                    continue
                group["quote_keys"].add(qkey)
                group["quotes"].append({"x": quote_text, "n": note, "chunk_index": chunk_idx})
        grouped = []
        for group in groups.values():
            group.pop("quote_keys", None)
            group["quotes"] = sorted(group["quotes"], key=lambda q: len(str(q.get("x") or "")), reverse=True)
            grouped.append(group)
        return sorted(grouped, key=lambda item: len(item.get("quotes") or []), reverse=True)

    def _theme_from_note(self, note: str, summary: str, quote_text: str) -> str:
        text = (note or summary or quote_text).strip()
        words = text.split()
        if len(words) > 14:
            text = " ".join(words[:14])
        return text or "Selected evidence theme"

    def _master_graph_is_usable(self, master_norm: dict) -> bool:
        entities = master_norm.get("entities") or []
        relations = master_norm.get("relations") or []
        if len(entities) < 3 or len(relations) < 2:
            return False
        source_backed = [e for e in entities if str(e.get("exact_text") or "").strip()]
        return bool(source_backed)

    def _dedupe_cross_type_repetitions(self, entities: list, relations: list, contract: dict) -> tuple[list, list]:
        source_aliases = set((contract.get("graph_plan") or {}).get("sources") or [])
        def is_source(item):
            return self._alias_for_entity(item.get("type"), contract) in source_aliases or bool(item.get("exact_text"))

        by_norm = {}
        replace = {}
        keep_entities = []
        for entity in entities:
            text = str(entity.get("exact_text") or entity.get("text") or entity.get("title") or "").strip()
            norm = re.sub(r"\W+", "", text.lower())[:160]
            if not norm:
                keep_entities.append(entity)
                continue
            existing = by_norm.get(norm)
            if not existing:
                by_norm[norm] = entity
                keep_entities.append(entity)
                continue
            existing_is_source = is_source(existing)
            entity_is_source = is_source(entity)
            if existing_is_source and not entity_is_source:
                replace[entity["temp_id"]] = existing["temp_id"]
            elif entity_is_source and not existing_is_source:
                replace[existing["temp_id"]] = entity["temp_id"]
                by_norm[norm] = entity
                keep_entities = [e for e in keep_entities if e.get("temp_id") != existing.get("temp_id")]
                keep_entities.append(entity)
            else:
                replace[entity["temp_id"]] = existing["temp_id"]

        cleaned = []
        seen = set()
        for rel in relations:
            rel = dict(rel)
            rel["source"] = replace.get(rel.get("source"), rel.get("source"))
            rel["target"] = replace.get(rel.get("target"), rel.get("target"))
            if not rel.get("source") or not rel.get("target") or rel["source"] == rel["target"]:
                continue
            key = (rel.get("source"), rel.get("target"), rel.get("type"))
            if key in seen:
                continue
            seen.add(key)
            cleaned.append(rel)
        return keep_entities, cleaned

    def _drop_confused_duplicate_relations(self, entities: list, relations: list, contract: dict) -> list:
        by_id = {item.get("temp_id"): item for item in entities}
        relation_contracts = {item.get("type"): item for item in contract.get("allowed_relation_types", [])}
        cleaned = []
        for rel in relations:
            source = by_id.get(rel.get("source"))
            target = by_id.get(rel.get("target"))
            if not source or not target:
                continue
            source_text = self._entity_semantic_text(source)
            target_text = self._entity_semantic_text(target)
            if source_text and target_text and source_text == target_text and self._relation_reads_as_opposition(rel.get("type"), relation_contracts):
                continue
            cleaned.append(rel)
        return cleaned

    def _entity_semantic_text(self, entity: dict) -> str:
        text = str(entity.get("exact_text") or entity.get("text") or entity.get("title") or "").strip()
        return re.sub(r"\W+", "", text.lower())[:180]

    def _relation_reads_as_opposition(self, rel_type: str, relation_contracts: dict) -> bool:
        contract = relation_contracts.get(rel_type) or {}
        haystack = " ".join([
            str(rel_type or ""),
            str(contract.get("a") or ""),
            str(contract.get("label") or ""),
            str(contract.get("description") or ""),
        ]).lower()
        return any(word in haystack for word in ("refute", "contradict", "critique", "criticize", "against", "negative"))

    def _ensure_connected_master_graph(self, entities: list, relations: list, contract: dict) -> list:
        if len(entities) < 2:
            return relations
        allowed = {item["type"] for item in contract.get("allowed_relation_types", [])}
        root = self._select_graph_root(entities, relations, contract, allowed)
        if not root:
            return relations
        connected = self._component_ids(root["temp_id"], relations)
        existing = {(r.get("source"), r.get("target"), r.get("type")) for r in relations}
        for entity in entities:
            entity_id = entity.get("temp_id")
            if not entity_id or entity_id in connected:
                continue
            rel_type = self._best_relation_between(
                entity.get("type"),
                root.get("type"),
                allowed,
                [RelationTrait.HIERARCHICAL, RelationTrait.SEMANTIC, RelationTrait.EVIDENTIARY],
            )
            if not rel_type:
                rel_type = self._best_relation_between(
                    root.get("type"),
                    entity.get("type"),
                    allowed,
                    [RelationTrait.HIERARCHICAL, RelationTrait.SEMANTIC],
                )
                source_id, target_id = root["temp_id"], entity_id
            else:
                source_id, target_id = entity_id, root["temp_id"]
            if not rel_type:
                continue
            key = (source_id, target_id, rel_type)
            if key in existing:
                continue
            relations.append({
                "temp_id": f"master_connect_{len(relations)}",
                "source": source_id,
                "target": target_id,
                "type": rel_type,
                "properties": {"confidence": 0.45, "auto_connected_component": True},
                "evidence_ids": [],
            })
            existing.add(key)
            connected |= self._component_ids(entity_id, relations)
        return relations

    def _select_graph_root(self, entities: list, relations: list, contract: dict, allowed: set[str]) -> Optional[dict]:
        root_aliases = set((contract.get("graph_plan") or {}).get("roots") or [])
        candidates = [e for e in entities if self._alias_for_entity(e.get("type"), contract) in root_aliases] or entities
        incoming = {}
        outgoing = {}
        for rel in relations:
            outgoing[rel.get("source")] = outgoing.get(rel.get("source"), 0) + 1
            incoming[rel.get("target")] = incoming.get(rel.get("target"), 0) + 1
        return max(candidates, key=lambda e: (incoming.get(e.get("temp_id"), 0), -outgoing.get(e.get("temp_id"), 0), len(str(e.get("text") or ""))))

    def _component_ids(self, start_id: str, relations: list) -> set[str]:
        seen = {start_id}
        changed = True
        while changed:
            changed = False
            for rel in relations:
                a, b = rel.get("source"), rel.get("target")
                if a in seen and b not in seen:
                    seen.add(b)
                    changed = True
                if b in seen and a not in seen:
                    seen.add(a)
                    changed = True
        return seen

    def _group_observed_claims(self, chunks: list) -> list:
        groups: dict[str, dict] = {}
        for chunk_idx, chunk in enumerate(chunks or []):
            claims = chunk.get("claims") or (chunk.get("raw_observations") or {}).get("c") or []
            for claim in claims:
                if not isinstance(claim, dict):
                    continue
                claim_text = str(claim.get("x") or "").strip()
                if not claim_text:
                    continue
                key = self._observation_claim_key(claim_text)
                group = groups.setdefault(key, {
                    "claim": claim_text,
                    "reason": str(claim.get("r") or "").strip(),
                    "quotes": [],
                    "quote_keys": set(),
                    "count": 0,
                    "chunk_index": chunk_idx,
                })
                group["count"] += 1
                if len(claim_text) > len(group["claim"]):
                    group["claim"] = claim_text
                if not group.get("reason") and claim.get("r"):
                    group["reason"] = str(claim.get("r") or "").strip()
                for quote in claim.get("q") or []:
                    if not isinstance(quote, dict):
                        continue
                    quote_text = str(quote.get("x") or "").strip()
                    quote_key = re.sub(r"\W+", "", quote_text.lower())[:180]
                    if not quote_text or quote_key in group["quote_keys"]:
                        continue
                    group["quote_keys"].add(quote_key)
                    group["quotes"].append({
                        "x": quote_text,
                        "n": str(quote.get("n") or "").strip(),
                        "chunk_index": chunk_idx,
                    })
        grouped = []
        for group in groups.values():
            group["quotes"] = sorted(
                group["quotes"],
                key=lambda q: (len(str(q.get("x") or "")) >= 45, -abs(len(str(q.get("x") or "")) - 120)),
                reverse=True,
            )
            group.pop("quote_keys", None)
            grouped.append(group)
        return sorted(
            grouped,
            key=lambda item: (
                min(4, len(item.get("quotes") or [])),
                min(3, item.get("count", 1)),
                len(item.get("claim", "")) >= 45,
            ),
            reverse=True,
        )

    def _observation_claim_key(self, text: str) -> str:
        words = [
            w for w in re.findall(r"[a-zA-Z0-9]{4,}", text.lower())
            if w not in {"this", "that", "with", "from", "have", "will", "which", "their", "there", "could", "would", "should"}
        ]
        return " ".join(words[:12]) or re.sub(r"\W+", "", text.lower())[:120]

    def _bounded_workspace_result(self, result: dict) -> dict:
        entities = list(result.get("entities") or [])
        relations = list(result.get("relations") or [])
        if len(entities) <= 48 and len(relations) <= 64:
            return result
        contract = {
            "limits": {"max_entities_per_chunk": 12, "max_relations_per_chunk": 16},
            "allowed_relation_types": result.get("relations_contract") or [],
        }
        allowed = {r.get("type") for r in result.get("relations_contract", []) if r.get("type")} or {bp.type_key for bp in self.registry.all_relations()}
        entities, relations = self._repair_graph_density(entities, relations, "master", contract, allowed)
        bounded = dict(result)
        bounded["entities"] = entities[:40]
        keep = {item.get("temp_id") for item in bounded["entities"]}
        bounded["relations"] = [
            rel for rel in relations
            if rel.get("source") in keep and rel.get("target") in keep
        ][:52]
        bounded["export_was_bounded"] = True
        bounded["original_entity_count"] = len(result.get("entities") or [])
        bounded["original_relation_count"] = len(result.get("relations") or [])
        return bounded

    def normalize_graph_object(self, obj: dict, prefix: str, contract: dict) -> dict:
        allowed_entities = {item["type"] for item in contract.get("allowed_entity_types", [])}
        allowed_relations = {item["type"] for item in contract.get("allowed_relation_types", [])}
        entities, temp_id_map = [], {}
        raw_entities = self._coerce_list(obj.get("entities") or obj.get("nodes") or obj.get("n") or [])
        for idx, item in enumerate(raw_entities):
            item = self._coerce_node_item(item)
            if not isinstance(item, dict):
                continue
            entity_type = self._resolve_entity_type(item.get("type") or item.get("t") or item.get("entity_type"), allowed_entities, contract)
            if entity_type not in allowed_entities:
                continue
            raw_temp_id = str(item.get("temp_id") or item.get("id") or f"n{idx}")
            temp_id = self._scoped_temp_id(prefix, raw_temp_id)
            temp_id_map[raw_temp_id] = temp_id
            props = item.get("properties") if isinstance(item.get("properties"), dict) else {}
            entity_contract = self._entity_contract_for_type(entity_type, contract)
            exact_text = item.get("exact_text") or item.get("q") or item.get("quote") or ""
            if not entity_contract.get("requires_exact_quote"):
                exact_text = ""
            page = None if entity_contract.get("requires_exact_quote") else self._extract_page(item)
            entities.append({
                "temp_id": temp_id,
                "type": entity_type,
                "title": item.get("title") or item.get("label") or item.get("x") or self.registry.get_entity_blueprint(entity_type).display_name,
                "text": item.get("text") or item.get("x") or "",
                "exact_text": exact_text,
                "page": page,
                "properties": props,
            })
        if not entities:
            entities.extend(self._fallback_entities_from_legacy_output(obj, prefix, allowed_entities))

        entity_ids = {item["temp_id"] for item in entities}
        relations = []
        raw_relations = self._raw_relation_items(obj)
        for idx, rel in enumerate(raw_relations):
            rel = self._coerce_relation_item(rel)
            if not isinstance(rel, dict):
                continue
            raw_src = str(rel.get("source") or rel.get("s") or rel.get("source_id") or "")
            raw_tgt = str(rel.get("target") or rel.get("g") or rel.get("target_id") or "")
            src = temp_id_map.get(raw_src, self._scoped_temp_id(prefix, raw_src) if raw_src else "")
            tgt = temp_id_map.get(raw_tgt, self._scoped_temp_id(prefix, raw_tgt) if raw_tgt else "")
            if src not in entity_ids or tgt not in entity_ids:
                continue
            rel_type = self._resolve_relation_type(rel.get("type") or rel.get("t") or rel.get("relation_type"), allowed_relations, contract)
            rel_type = self._compatible_relation_type(rel_type, src, tgt, entities, allowed_relations)
            if not rel_type:
                continue
            relations.append({
                "temp_id": self._scoped_temp_id(prefix, str(rel.get("temp_id") or rel.get("id") or f"r{idx}")),
                "source": src,
                "target": tgt,
                "type": rel_type,
                "properties": rel.get("properties") if isinstance(rel.get("properties"), dict) else {},
                "evidence_ids": [temp_id_map.get(str(e), self._scoped_temp_id(prefix, str(e))) for e in self._coerce_list(rel.get("evidence_ids") or rel.get("ev") or [])],
            })
        relations = self._ensure_argument_chains(entities, relations, allowed_relations)
        entities, relations = self._repair_graph_density(entities, relations, prefix, contract, allowed_relations)
        return {"summary": obj.get("summary") or obj.get("s") or "", "entities": entities, "relations": relations}

    def _compact_master_node(self, node: dict, entity_alias: dict) -> dict:
        raw_type = node.get("type") or node.get("t") or node.get("entity_type")
        normalized_type = self._normalize_entity_type(raw_type)
        return {
            "id": node.get("temp_id") or node.get("id"),
            "t": node.get("a") or entity_alias.get(raw_type) or entity_alias.get(normalized_type) or raw_type,
            "x": str(node.get("text") or node.get("x") or node.get("title") or "")[:300],
            "q": str(node.get("exact_text") or node.get("q") or node.get("quote") or "")[:420],
            "p": node.get("page") if node.get("page") is not None else node.get("p"),
        }

    def _compact_master_edge(self, edge: dict, relation_alias: dict) -> dict:
        raw_type = edge.get("type") or edge.get("t") or edge.get("relation_type")
        normalized_type = self._normalize_relation_type(raw_type)
        return {
            "s": edge.get("source") or edge.get("s"),
            "g": edge.get("target") or edge.get("g"),
            "t": edge.get("a") or relation_alias.get(raw_type) or relation_alias.get(normalized_type) or raw_type,
            "ev": edge.get("evidence_ids") or edge.get("ev") or [],
        }

    def _render_prompt(self, prompt: str, template: dict, contract: dict, master: bool) -> str:
        compact = contract.get("prompt_contract") or self.build_prompt_contract(contract)
        output_shape = self._compact_output_shape(contract)
        few_shot = self._schema_few_shot(contract)
        stage_rules = self._stage_rules(master)
        prompt = prompt or "{template_instructions}\n\n{template_schema}"
        prompt = (
            prompt.replace("{template_instructions}", template.get("instructions", ""))
            .replace("{analysis_contract}", compact)
            .replace("{template_schema}", compact)
            .replace("{schema_few_shot}", few_shot)
            .replace("{combined_text}", "the compacted chunk artifacts in the user query")
        )
        mode = "MASTER PASS" if master else "CHUNK PASS"
        output_contract = self.prompt_manager.get_prompt("Graph Analysis Compact Output Contract")
        output_contract = (
            output_contract.replace("{analysis_stage}", mode)
            .replace("{compact_output_shape}", output_shape)
            .replace("{analysis_stage_rules}", stage_rules)
            .replace("{schema_few_shot}", few_shot)
            .replace("{analysis_contract}", compact)
            .replace("{template_schema}", compact)
        )
        return "\n\n".join(part for part in [prompt, output_contract, self._argument_map_directive(contract)] if part)

    def _render_chunk_observation_prompt(self, prompt: str, template: dict) -> str:
        prompt = prompt or "{template_instructions}"
        prompt = (
            prompt.replace("{template_instructions}", template.get("instructions", ""))
            .replace("{analysis_contract}", "")
            .replace("{template_schema}", "")
            .replace("{schema_few_shot}", "")
        )
        return "\n\n".join([
            prompt,
            (
                "OUTPUT RULES:\n"
                "Return ONLY valid minified JSON. No markdown. No commentary.\n"
                "Use shape {\"s\":\"section topic\",\"q\":[{\"id\":\"q1\",\"x\":\"exact short quote\",\"n\":\"short relevance note\"}]}.\n"
                "Select only quotes that would help build the requested final analysis, not background, section framing, method, or framework lines unless the analysis goal asks for them. "
                "For argument-style goals, prefer substantive positions, concrete data/evidence, causal supports, limitations, or counterpoints. "
                "Every q[].x must be exact text copied from the chunk, about 10 words and never more than 18 words. "
                "Every q[].n must be about 10 words explaining why the quote matters for the analysis mode. "
                "Do not include claims, graph node types, relation types, aliases, confidence scores, page guesses, or schema fields."
            ),
        ])

    def _render_synthesis_prompt(self, prompt: str, template: dict) -> str:
        prompt = prompt or "{template_instructions}"
        return (
            prompt.replace("{template_instructions}", template.get("instructions", ""))
            .replace("{analysis_contract}", "")
            .replace("{template_schema}", "")
            .replace("{schema_few_shot}", "")
        )

    def _compact_output_shape(self, contract: dict) -> str:
        entities = contract.get("allowed_entity_types", []) or []
        relations = contract.get("allowed_relation_types", []) or []
        plan_chain = ((contract.get("graph_plan") or {}).get("preferred_chains") or [[]])[0]
        if plan_chain:
            aliases = [plan_chain[0]["s"]] + [step["g"] for step in plan_chain]
            nodes = [
                {"id": f"n{idx + 1}", "t": alias, "x": "", "q": "", "p": None, "properties": {}}
                for idx, alias in enumerate(aliases[:3])
            ]
            edges = [
                {"s": f"n{idx + 1}", "g": f"n{idx + 2}", "t": step["t"], "ev": [], "properties": {}}
                for idx, step in enumerate(plan_chain[:2])
            ]
            return json.dumps({"m": "", "s": "", "n": nodes, "e": edges}, separators=(",", ":"))

        rel = self._example_relation(contract)
        by_type = {item.get("type"): item for item in entities}
        src_type = self._example_endpoint(rel.get("valid_source_types", []), entities) if rel else None
        tgt_type = self._example_endpoint(rel.get("valid_target_types", []), entities) if rel else None
        first_alias = by_type.get(src_type, entities[0] if entities else {}).get("a", "n")
        second_alias = by_type.get(tgt_type, entities[1] if len(entities) > 1 else (entities[0] if entities else {})).get("a", "n")
        rel_alias = rel.get("a") if rel else (relations[0].get("a", "r") if relations else "r")
        obj = {
            "m": "",
            "s": "",
            "n": [
                {"id": "n1", "t": first_alias, "x": "", "q": "", "p": None, "properties": {}},
                {"id": "n2", "t": second_alias, "x": "", "q": "", "p": None, "properties": {}},
            ],
            "e": [{"s": "n1", "g": "n2", "t": rel_alias, "ev": [], "properties": {}}],
        }
        return json.dumps(obj, separators=(",", ":"))

    def _schema_few_shot(self, contract: dict) -> str:
        entities = contract.get("allowed_entity_types", []) or []
        relations = contract.get("allowed_relation_types", []) or []
        if not entities or not relations:
            return "No example available; follow CONTRACT aliases exactly and fill content from the user input."
        plan_chain = ((contract.get("graph_plan") or {}).get("preferred_chains") or [[]])[0]
        if plan_chain:
            return self._schema_plan_few_shot(contract, plan_chain)

        rel = self._example_relation(contract)
        if not rel:
            return "No valid example edge available; include only edges whose source and target types match CONTRACT directions."

        src_type = self._example_endpoint(rel.get("valid_source_types", []), entities)
        tgt_type = self._example_endpoint(rel.get("valid_target_types", []), entities)
        if not src_type or not tgt_type:
            return "No valid example edge available; include only edges whose source and target types match CONTRACT directions."

        by_type = {item.get("type"): item for item in entities}
        src = by_type[src_type]
        tgt = by_type[tgt_type]
        source_requires_quote = bool(src.get("requires_exact_quote"))
        target_requires_quote = bool(tgt.get("requires_exact_quote"))
        example_text = "SOURCE TEXT: The document says, \"Alpha improves beta when conditions are stable.\" The paragraph then explains that stable conditions make the improvement repeatable."
        example = {
            "s": "alpha and beta relationship",
            "n": [
                {
                    "id": "n1",
                    "t": src.get("a"),
                    "x": "Alpha improves beta under stable conditions",
                    "q": "Alpha improves beta when conditions are stable" if source_requires_quote else "",
                    "p": None,
                    "properties": {},
                },
                {
                    "id": "n2",
                    "t": tgt.get("a"),
                    "x": "Stable conditions make the improvement repeatable",
                    "q": "stable conditions make the improvement repeatable" if target_requires_quote else "",
                    "p": None,
                    "properties": {},
                },
            ],
            "e": [{"s": "n1", "g": "n2", "t": rel.get("a"), "ev": ["n1"] if source_requires_quote else [], "properties": {}}],
        }
        return (
            "This is format training only; do not copy its content. "
            "It shows that aliases come from CONTRACT while x/q come from source text.\n"
            f"{example_text}\nOUTPUT:{json.dumps(example, separators=(',', ':'))}"
        )

    def _schema_plan_few_shot(self, contract: dict, chain: list) -> str:
        by_alias = {item.get("a"): item for item in contract.get("allowed_entity_types", [])}
        aliases = [chain[0]["s"]] + [step["g"] for step in chain]
        nodes = []
        for idx, alias in enumerate(aliases[:3]):
            requires_quote = bool((by_alias.get(alias) or {}).get("requires_exact_quote"))
            nodes.append({
                "id": f"n{idx + 1}",
                "t": alias,
                "x": [
                    "Alpha improves beta under stable conditions",
                    "Stable conditions make the improvement repeatable",
                    "The document treats repeatable improvement as the main conclusion",
                ][idx],
                "q": "Alpha improves beta when conditions are stable" if requires_quote else "",
                "p": None,
                "properties": {},
            })
        edges = [
            {"s": f"n{idx + 1}", "g": f"n{idx + 2}", "t": step["t"], "ev": ["n1"] if idx == 0 else [], "properties": {}}
            for idx, step in enumerate(chain[:2])
        ]
        example = {"s": "alpha and beta relationship", "n": nodes, "e": edges}
        return (
            "This is format training only; do not copy its content. "
            "It shows a readable chain from source-like aliases toward root-like aliases using GRAPH_PLAN.\n"
            "SOURCE TEXT: The document says, \"Alpha improves beta when conditions are stable.\" The paragraph then explains that stable conditions make the improvement repeatable.\n"
            f"OUTPUT:{json.dumps(example, separators=(',', ':'))}"
        )

    def _example_relation(self, contract: dict) -> Optional[dict]:
        entities = {item.get("type") for item in contract.get("allowed_entity_types", [])}
        for rel in contract.get("allowed_relation_types", []):
            sources = rel.get("valid_source_types", [])
            targets = rel.get("valid_target_types", [])
            if self._example_endpoint(sources, contract.get("allowed_entity_types", [])) and self._example_endpoint(targets, contract.get("allowed_entity_types", [])):
                return rel
            if "*" in sources and entities and self._example_endpoint(targets, contract.get("allowed_entity_types", [])):
                return rel
            if "*" in targets and entities and self._example_endpoint(sources, contract.get("allowed_entity_types", [])):
                return rel
        return None

    def _example_endpoint(self, type_keys: list, entities: list) -> Optional[str]:
        available = [item.get("type") for item in entities]
        if "*" in type_keys:
            return available[0] if available else None
        for type_key in type_keys:
            if type_key in available:
                return type_key
        return None

    def _stage_rules(self, master: bool) -> str:
        if master:
            return (
                "MASTER PASS:\n"
                "Input is already compact JSON from chunks. Deduplicate by matching exact snippets first, then near-identical x values. "
                "Use GRAPH_PLAN to choose source, bridge, and root roles. Prefer one clear parent per node. Keep only the strongest edges needed to make the hierarchy readable. "
                "Root/bridge nodes must synthesize; short facts, data points, examples, and exact snippets should stay source-like whenever source aliases exist. "
                "Do not create new quoted snippets that are absent from chunk q fields."
            )
        return (
            "CHUNK PASS:\n"
            "Input is raw document text. Extract only the strongest local chains that follow GRAPH_PLAN when possible. "
            "If source text is required, q must be copied from the raw text in this chunk."
        )

    def _argument_map_directive(self, contract: dict) -> str:
        entity_types = {item["type"] for item in contract.get("allowed_entity_types", [])}
        relation_types = {item["type"] for item in contract.get("allowed_relation_types", [])}
        has_exact_quote = any(item.get("requires_exact_quote") for item in contract.get("allowed_entity_types", []))
        has_evidentiary = any(self._relation_has_trait(type_key, RelationTrait.EVIDENTIARY) for type_key in relation_types)
        has_hierarchical = any(self._relation_has_trait(type_key, RelationTrait.HIERARCHICAL) for type_key in relation_types)
        if not (has_exact_quote and has_evidentiary and has_hierarchical):
            return ""
        return self.prompt_manager.get_prompt("Graph Analysis Argument Chain Directive")

    def _entity_contract(self, type_key: str) -> dict:
        bp = self.registry.get_entity_blueprint(type_key)
        field_keys = {field.key for field in bp.fields}
        return {
            "type": bp.type_key,
            "label": bp.display_name,
            "description": bp.description,
            "requires_source": bp.requires_source,
            "fields": [self._field_contract(field) for field in bp.fields],
            "requires_exact_quote": bool({"exact_text", "quote"} & field_keys) or bp.requires_source,
        }

    def _relation_contract(self, type_key: str) -> dict:
        bp = self.registry.get_relation_blueprint(type_key)
        return {
            "type": bp.type_key,
            "label": bp.display_name,
            "description": bp.description,
            "valid_source_types": list(bp.valid_source_types),
            "valid_target_types": list(bp.valid_target_types),
            "fields": [self._field_contract(field) for field in bp.fields],
        }

    def _field_contract(self, field) -> dict:
        return {"key": field.key, "type": field.value_type, "default": field.default, "choices": list(field.choices or [])}

    def _entity_contract_for_type(self, type_key: str, contract: dict) -> dict:
        for item in contract.get("allowed_entity_types", []):
            if item.get("type") == type_key:
                return item
        return {}

    def _build_graph_plan(self, entities: list, relations: list) -> dict:
        if not entities or not relations:
            return {}
        by_type = {item.get("type"): item for item in entities}
        entity_types = set(by_type)
        edges = self._valid_plan_edges(entities, relations)
        if not edges:
            return {}

        source_types = {item["type"] for item in entities if item.get("requires_exact_quote") or item.get("requires_source")}
        incoming = {type_key: 0 for type_key in entity_types}
        outgoing = {type_key: 0 for type_key in entity_types}
        source_incoming = {type_key: 0 for type_key in entity_types}
        non_source_outgoing = {type_key: 0 for type_key in entity_types}
        for edge in edges:
            outgoing[edge["source"]] += 1
            incoming[edge["target"]] += 1
            if edge["source"] in source_types:
                source_incoming[edge["target"]] += 1
            if edge["source"] not in source_types:
                non_source_outgoing[edge["source"]] += 1

        non_sources = [type_key for type_key in entity_types if type_key not in source_types]
        root_types = sorted(
            non_sources,
            key=lambda t: (incoming[t] - outgoing[t], incoming[t], -outgoing[t], by_type[t].get("a", "")),
            reverse=True,
        )[:3]
        bridge_types = [
            t for t in sorted(
                non_sources,
                key=lambda t: (source_incoming[t], non_source_outgoing[t], incoming[t] + outgoing[t], by_type[t].get("a", "")),
                reverse=True,
            )
            if t not in root_types[:1] and outgoing[t] > 0
        ][:3]
        root_types = [t for t in root_types if t not in bridge_types] or root_types[:1]

        chains = self._preferred_plan_chains(edges, source_types, bridge_types, root_types, by_type)
        preferred_edges = {(step["s"], step["g"], step["t"]) for chain in chains for step in chain}
        if not chains:
            best = sorted(edges, key=lambda e: self._plan_edge_score(e, source_types, root_types), reverse=True)[:3]
            chains = [[{"s": by_type[e["source"]]["a"], "g": by_type[e["target"]]["a"], "t": e["relation_alias"]}] for e in best]
            preferred_edges = {(step["s"], step["g"], step["t"]) for chain in chains for step in chain}

        return {
            "sources": [by_type[t]["a"] for t in source_types if t in by_type],
            "bridges": [by_type[t]["a"] for t in bridge_types],
            "roots": [by_type[t]["a"] for t in root_types],
            "preferred_chains": chains[:4],
            "preferred_edges": sorted(preferred_edges),
        }

    def _valid_plan_edges(self, entities: list, relations: list) -> list:
        entity_types = [item.get("type") for item in entities]
        edges = []
        for rel in relations:
            for source_type in self._expand_plan_types(rel.get("valid_source_types", []), entity_types):
                for target_type in self._expand_plan_types(rel.get("valid_target_types", []), entity_types):
                    if source_type == target_type:
                        continue
                    if source_type in entity_types and target_type in entity_types:
                        edges.append({
                            "source": source_type,
                            "target": target_type,
                            "relation": rel.get("type"),
                            "relation_alias": rel.get("a"),
                            "traits": set(self.registry.get_relation_blueprint(rel.get("type")).traits or []),
                        })
        return edges

    def _expand_plan_types(self, type_keys: list, entity_types: list) -> list:
        return list(entity_types) if "*" in (type_keys or []) else list(type_keys or [])

    def _preferred_plan_chains(self, edges: list, source_types: set, bridge_types: list, root_types: list, by_type: dict) -> list:
        chains = []
        for source_type in source_types:
            for bridge_type in bridge_types:
                first_edges = [e for e in edges if e["source"] == source_type and e["target"] == bridge_type]
                if not first_edges:
                    continue
                for root_type in root_types:
                    if root_type == bridge_type:
                        continue
                    second_edges = [e for e in edges if e["source"] == bridge_type and e["target"] == root_type]
                    if not second_edges:
                        continue
                    first = max(first_edges, key=lambda e: self._plan_edge_score(e, source_types, root_types))
                    second = max(second_edges, key=lambda e: self._plan_edge_score(e, source_types, root_types))
                    chains.append([
                        {"s": by_type[source_type]["a"], "g": by_type[bridge_type]["a"], "t": first["relation_alias"]},
                        {"s": by_type[bridge_type]["a"], "g": by_type[root_type]["a"], "t": second["relation_alias"]},
                    ])
        if chains:
            return chains
        for source_type in source_types:
            for root_type in root_types:
                direct = [e for e in edges if e["source"] == source_type and e["target"] == root_type]
                if direct:
                    edge = max(direct, key=lambda e: self._plan_edge_score(e, source_types, root_types))
                    chains.append([{"s": by_type[source_type]["a"], "g": by_type[root_type]["a"], "t": edge["relation_alias"]}])
        return chains

    def _plan_edge_score(self, edge: dict, source_types: set, root_types: list) -> int:
        traits = edge.get("traits") or set()
        score = 0
        if edge.get("source") in source_types:
            score += 30
        if edge.get("target") in root_types[:1]:
            score += 25
        if RelationTrait.EVIDENTIARY in traits:
            score += 12
        if RelationTrait.HIERARCHICAL in traits:
            score += 8
        if RelationTrait.SEMANTIC in traits:
            score += 3
        return score

    def _graph_plan_text(self, plan: dict) -> str:
        parts = []
        if plan.get("sources"):
            parts.append("source_aliases=" + ",".join(plan["sources"]))
        if plan.get("bridges"):
            parts.append("bridge_aliases=" + ",".join(plan["bridges"]))
        if plan.get("roots"):
            parts.append("root_aliases=" + ",".join(plan["roots"]))
        if plan.get("preferred_chains"):
            chain_text = []
            for chain in plan["preferred_chains"]:
                chain_text.append("|".join(f"{step['s']}-{step['t']}->{step['g']}" for step in chain))
            parts.append("preferred_chains=" + ";".join(chain_text))
        parts.append("rule=build small trees from source_aliases toward root_aliases through bridge_aliases when possible")
        return " ".join(parts)

    def _build_entity_aliases(self) -> dict:
        aliases = {}
        for bp in self.registry.all_entities():
            self._add_type_aliases(aliases, bp.type_key, bp.display_name)
        return aliases

    def _build_relation_aliases(self) -> dict:
        aliases = {}
        for bp in self.registry.all_relations():
            self._add_type_aliases(aliases, bp.type_key, bp.display_name)
        return aliases

    def _add_type_aliases(self, aliases: dict, type_key: str, display_name: str = ""):
        short = self._short_type(type_key)
        names = {type_key, short, short.replace("_", ""), str(display_name or "").strip().lower()}
        names.add(short[0] if short else "")
        words = [w for w in re.split(r"[\s_\-.]+", short) if w]
        if words:
            names.add("".join(w[0] for w in words))
        for name in names:
            normalized = self._alias_key(name)
            if normalized:
                aliases.setdefault(normalized, type_key)

    def _add_prompt_aliases(self, items: list):
        used = set()
        for item in items:
            alias = self._registry_prompt_alias(item["type"])
            if alias in used:
                alias = item["type"]
            item["a"] = alias
            used.add(alias)

    def _registry_prompt_alias(self, type_key: str) -> str:
        short = self._short_type(type_key)
        words = [w for w in re.split(r"[\s_\-.]+", short) if w]
        return "".join(w[0] for w in words) if len(words) > 1 else short[:3]

    def _alias_for_type(self, type_key: str, entity_contracts: list) -> str:
        if type_key == "*":
            return "*"
        for item in entity_contracts:
            if item.get("type") == type_key:
                return item.get("a", self._short_type(type_key))
        return self._short_type(type_key)

    def _normalize_entity_type(self, key: str) -> str:
        key = str(key or "").strip()
        return self._entity_aliases.get(self._alias_key(key), key)

    def _resolve_entity_type(self, key: str, allowed: set[str], contract: Optional[dict] = None) -> str:
        contract_type = self._type_for_alias(str(key or ""), (contract or {}).get("allowed_entity_types", []))
        if contract_type in allowed:
            return contract_type
        normalized = self._normalize_entity_type(key)
        if normalized in allowed:
            return normalized
        return self._fallback_entity_type_for_alias(key, allowed)

    def _resolve_relation_type(self, key: str, allowed: set[str], contract: Optional[dict] = None) -> str:
        contract_type = self._type_for_alias(str(key or ""), (contract or {}).get("allowed_relation_types", []))
        if contract_type in allowed:
            return contract_type
        normalized = self._normalize_relation_type(key)
        if normalized in allowed:
            return normalized
        return normalized

    def _normalize_relation_type(self, key: str) -> str:
        key = str(key or "").strip()
        return self._relation_aliases.get(self._alias_key(key), key)

    def _alias_key(self, value: str) -> str:
        return re.sub(r"[^a-z0-9]+", "", str(value or "").lower())

    def _short_type(self, type_key: str) -> str:
        return "*" if type_key == "*" else str(type_key or "").split(".", 1)[-1]

    def _compatible_relation_type(self, relation_type: str, source_id: str, target_id: str, entities: list, allowed: set[str]) -> Optional[str]:
        types = {entity["temp_id"]: entity["type"] for entity in entities}
        return self._compatible_relation_type_for_types(relation_type, types.get(source_id), types.get(target_id), allowed)

    def _compatible_relation_type_for_types(self, relation_type: str, source_type: Optional[str], target_type: Optional[str], allowed: set[str]) -> Optional[str]:
        if relation_type in allowed and source_type and target_type and self.registry.validate_relation(relation_type, source_type, target_type):
            return relation_type
        for candidate in self._relation_candidates(allowed, source_type, target_type):
            if candidate in allowed and source_type and target_type and self.registry.validate_relation(candidate, source_type, target_type):
                return candidate
        return None

    def _ensure_argument_chains(self, entities: list, relations: list, allowed: set[str]) -> list:
        # STOP SPIDERWEBBING: The LLM is smart enough to build the tree. 
        # We only filter out invalid schema relations here.
        types = {entity["temp_id"]: entity["type"] for entity in entities}
        cleaned = []
        for rel in relations:
            rel_type = self._compatible_relation_type_for_types(
                rel.get("type"),
                types.get(rel.get("source")),
                types.get(rel.get("target")),
                allowed,
            )
            if not rel_type:
                continue
            rel = dict(rel)
            rel["type"] = rel_type
            cleaned.append(rel)
        return cleaned

    def _synthesize_relations(self, entities: list, allowed: set[str]) -> list:
        # STOP SPIDERWEBBING: Do not auto-generate edges Cartesian-style. 
        # Trust the LLM's JSON graph to dictate the structure.
        return []

    def _repair_graph_density(self, entities: list, relations: list, prefix: str, contract: dict, allowed: set[str]) -> tuple[list, list]:
        if not entities:
            return entities, []
        limits = contract.get("limits") or {}
        chunk_max = max(1, int(limits.get("max_entities_per_chunk") or 6))
        if prefix == "master":
            max_entities = min(24, max(12, chunk_max * 2))
            max_relations = min(30, max(max_entities - 1, int(limits.get("max_relations_per_chunk") or 10) * 2))
        else:
            max_entities = chunk_max
            max_relations = max(max_entities - 1, int(limits.get("max_relations_per_chunk") or 10))

        if len(entities) > max_entities:
            entities = self._prioritize_entities_for_graph(entities, relations)[:max_entities]
        keep = {entity["temp_id"] for entity in entities}
        relations = [r for r in relations if r.get("source") in keep and r.get("target") in keep]

        existing = {(r.get("source"), r.get("target"), r.get("type")) for r in relations}
        for rel in self._synthesize_relations(entities, allowed):
            if len(relations) >= max_relations:
                break
            key = (rel.get("source"), rel.get("target"), rel.get("type"))
            pair = (rel.get("source"), rel.get("target"))
            if key not in existing and pair not in {(r.get("source"), r.get("target")) for r in relations}:
                relations.append(rel)
                existing.add(key)
        return entities, relations[:max_relations]

    def _prioritize_entities_for_graph(self, entities: list, relations: list) -> list:
        linked = {r.get("source") for r in relations} | {r.get("target") for r in relations}
        return sorted(
            entities,
            key=lambda e: (
                0 if e.get("temp_id") in linked else 1,
                self._entity_graph_rank(e.get("type")),
                -len(str(e.get("exact_text") or e.get("text") or e.get("title") or "")),
            ),
        )

    def _ensure_master_argument_node(self, entities: list, relations: list, chunks: list, contract: dict) -> list:
        return entities

    def _ensure_graph_plan_roots(self, entities: list, relations: list, chunks: list, contract: dict) -> tuple[list, list]:
        plan = contract.get("graph_plan") or {}
        root_aliases = set(plan.get("roots") or [])
        if not root_aliases or any(self._alias_for_entity(e.get("type"), contract) in root_aliases for e in entities):
            return entities, relations
        root_type = self._type_for_alias(next(iter(root_aliases)), contract.get("allowed_entity_types", []))
        if not root_type:
            return entities, relations
        bridge_aliases = set(plan.get("bridges") or [])
        bridge_entities = [e for e in entities if self._alias_for_entity(e.get("type"), contract) in bridge_aliases]
        if not bridge_entities:
            return entities, relations
        root = {
            "temp_id": "analysis_root_summary",
            "type": root_type,
            "title": "Document synthesis",
            "text": self._master_summary_text(chunks, bridge_entities),
            "exact_text": "",
            "page": None,
            "properties": {"role": "analysis_root", "auto_inferred": True, "confidence": 0.35},
        }
        allowed_relations = {item["type"] for item in contract.get("allowed_relation_types", [])}
        entities = [root] + entities
        existing = {(r.get("source"), r.get("target"), r.get("type")) for r in relations}
        for idx, bridge in enumerate(bridge_entities[:6]):
            rel_type = self._best_relation_between(bridge.get("type"), root_type, allowed_relations, [RelationTrait.HIERARCHICAL, RelationTrait.EVIDENTIARY, RelationTrait.SEMANTIC])
            key = (bridge["temp_id"], root["temp_id"], rel_type)
            if rel_type and key not in existing:
                relations.append({
                    "temp_id": f"analysis_root_link_{idx}",
                    "source": bridge["temp_id"],
                    "target": root["temp_id"],
                    "type": rel_type,
                    "properties": {"confidence": 0.35, "auto_inferred": True},
                    "evidence_ids": [],
                })
                existing.add(key)
        return entities, relations

    def _alias_for_entity(self, type_key: str, contract: dict) -> str:
        for item in contract.get("allowed_entity_types", []):
            if item.get("type") == type_key:
                return item.get("a", "")
        return ""

    def _type_for_alias(self, alias: str, contracts: list) -> Optional[str]:
        alias_key = self._alias_key(alias)
        for item in contracts:
            if self._alias_key(item.get("a")) == alias_key or self._alias_key(item.get("type")) == alias_key:
                return item.get("type")
        return None

    def _nearest_text_match(self, anchor: dict, candidates: list) -> Optional[dict]:
        if not candidates:
            return None
        words = self._content_words(anchor)
        if not words:
            return candidates[0]
        return max(candidates, key=lambda c: len(words & self._content_words(c)))

    def _content_words(self, item: dict) -> set[str]:
        text = " ".join(str(item.get(k) or "") for k in ("title", "text", "exact_text"))
        return {w for w in re.findall(r"[a-zA-Z]{4,}", text.lower()) if w not in {"this", "that", "with", "from", "have", "will", "which", "their", "there"}}

    def _coerce_node_item(self, item):
        if isinstance(item, dict):
            if "t" in item and "type" not in item:
                item = dict(item)
                item["type"] = item.get("t")
                item["text"] = item.get("x", item.get("text", ""))
                item["exact_text"] = item.get("q", item.get("exact_text", ""))
                item["page"] = item.get("p", item.get("page"))
            return item
        if isinstance(item, list) and len(item) >= 3:
            props = item[3] if len(item) > 3 and isinstance(item[3], dict) else {}
            return {"temp_id": item[0], "type": item[1], "text": item[2], "exact_text": props.get("exact_text") or props.get("quote") or "", "page": props.get("page"), "properties": props}
        return item

    def _coerce_relation_item(self, item):
        if isinstance(item, dict):
            if "g" in item and "target" not in item:
                item = dict(item)
                item["source"] = item.get("s")
                item["target"] = item.get("g")
                item["type"] = item.get("t")
                item["evidence_ids"] = item.get("ev", item.get("evidence_ids", []))
            return item
        if isinstance(item, list) and len(item) >= 4:
            props = item[4] if len(item) > 4 and isinstance(item[4], dict) else {}
            return {"type": item[1], "source": item[2], "target": item[3], "properties": props, "evidence_ids": props.get("evidence_ids", [])}
        return item

    def _fallback_entities_from_legacy_output(self, obj: dict, prefix: str, allowed: set[str]) -> list:
        preferred = self._preferred_master_entity_type(allowed) or next(iter(allowed), None)
        if not preferred:
            return []
        text = obj.get("summary") or obj.get("document_summary") or obj.get("text")
        return [{"temp_id": f"{prefix}_summary", "type": preferred, "title": "Summary", "text": str(text)[:900], "exact_text": "", "page": self._extract_page(obj), "properties": {"legacy_shape": True}}] if text else []

    def _raw_relation_items(self, obj: dict) -> list:
        raw = []
        for key in ("relations", "edges", "e"):
            raw.extend(self._coerce_list(obj.get(key) or []))
        for key, value in obj.items():
            if key in {"relations", "edges", "e", "entities", "nodes", "n"}:
                continue
            if str(key).lower().startswith(("e", "edge", "relation")):
                raw.extend(self._coerce_list(value))
        return raw

    def _fallback_entity_type_for_alias(self, alias: str, allowed: set[str]) -> str:
        alias_key = self._alias_key(alias)
        scored = []
        for type_key in allowed:
            bp = self.registry.get_entity_blueprint(type_key)
            searchable = self._alias_key(" ".join([bp.display_name, bp.description, self._short_type(type_key)]))
            score = 0
            if alias_key and alias_key in searchable:
                score += 5
            if not bp.requires_source:
                score += 2
            if bp.requires_source:
                score -= 1
            scored.append((score, type_key))
        scored.sort(reverse=True)
        return scored[0][1] if scored else str(alias or "")

    def _relation_candidates(self, allowed: set[str], source_type: Optional[str] = None, target_type: Optional[str] = None, preferred_traits: Optional[list] = None) -> list[str]:
        preferred_traits = preferred_traits or []
        candidates = []
        for type_key in allowed:
            try:
                bp = self.registry.get_relation_blueprint(type_key)
            except Exception:
                continue
            if source_type and target_type and not self.registry.validate_relation(type_key, source_type, target_type):
                continue
            candidates.append((self._relation_score(bp, preferred_traits), type_key))
        candidates.sort(key=lambda item: item[0], reverse=True)
        return [type_key for _, type_key in candidates]

    def _relation_score(self, bp, preferred_traits: list) -> int:
        traits = set(getattr(bp, "traits", []) or [])
        score = 0
        for idx, trait in enumerate(preferred_traits):
            if trait in traits:
                score += 30 - idx
        score += 5 * len(traits)
        if getattr(bp, "valid_source_types", None) == ["*"]:
            score -= 3
        if getattr(bp, "valid_target_types", None) == ["*"]:
            score -= 3
        return score

    def _best_relation_between(self, source_type: Optional[str], target_type: Optional[str], allowed: set[str], preferred_traits: Optional[list] = None) -> Optional[str]:
        for candidate in self._relation_candidates(allowed, source_type, target_type, preferred_traits):
            return candidate
        return None

    def _relation_has_trait(self, type_key: str, trait: RelationTrait) -> bool:
        try:
            return trait in set(self.registry.get_relation_blueprint(type_key).traits or [])
        except Exception:
            return False

    def _most_targetable_entity(self, entities: list, allowed_relations: set[str]) -> Optional[dict]:
        if not entities:
            return None
        return max(
            entities,
            key=lambda target: sum(
                1 for source in entities
                if source is not target and self._best_relation_between(source.get("type"), target.get("type"), allowed_relations)
            ),
        )

    def _entity_graph_rank(self, type_key: str) -> int:
        try:
            bp = self.registry.get_entity_blueprint(type_key)
        except Exception:
            return 20
        if bp.requires_source:
            return 10
        incoming = sum(1 for rel in self.registry.all_relations() if type_key in rel.valid_target_types or "*" in rel.valid_target_types)
        outgoing = sum(1 for rel in self.registry.all_relations() if type_key in rel.valid_source_types or "*" in rel.valid_source_types)
        return max(0, 20 - incoming * 2 - outgoing)

    def _preferred_master_entity_type(self, allowed_entities: set[str]) -> Optional[str]:
        candidates = []
        for type_key in allowed_entities:
            try:
                bp = self.registry.get_entity_blueprint(type_key)
            except Exception:
                continue
            if bp.requires_source:
                continue
            targetable = sum(1 for rel in self.registry.all_relations() if type_key in rel.valid_target_types or "*" in rel.valid_target_types)
            candidates.append((targetable, -self._entity_graph_rank(type_key), type_key))
        candidates.sort(reverse=True)
        return candidates[0][2] if candidates else None

    def _fallback_text_entity_type(self) -> Optional[str]:
        candidates = []
        for bp in self.registry.all_entities():
            if bp.requires_source:
                continue
            fields = {field.key for field in bp.fields}
            score = int("text" in fields) + int("title" in fields)
            candidates.append((score, -len(bp.fields), bp.type_key))
        candidates.sort(reverse=True)
        return candidates[0][2] if candidates else None

    def _merge_entities(self, base: list, incoming: list) -> list:
        seen = {self._entity_dedupe_key(item): item for item in base}
        for item in incoming:
            seen.setdefault(self._entity_dedupe_key(item), item)
        return list(seen.values())

    def _merge_relations(self, base: list, incoming: list) -> list:
        seen = {(r.get("source"), r.get("target"), r.get("type")): r for r in base}
        for rel in incoming:
            seen.setdefault((rel.get("source"), rel.get("target"), rel.get("type")), rel)
        return list(seen.values())

    def _entity_dedupe_key(self, item: dict) -> Tuple[str, str]:
        text = item.get("exact_text") or item.get("text") or item.get("title") or item.get("temp_id")
        return item.get("type"), re.sub(r"\W+", "", str(text).lower())[:160]

    def _master_summary_text(self, chunks: list, nodes: list) -> str:
        summaries = [str(c.get("summary", "")).strip() for c in chunks if isinstance(c, dict) and c.get("summary")]
        return (" ".join(summaries) or "; ".join(str(c.get("title") or c.get("text") or "") for c in nodes[:5]))[:900]

    def _parse_template_schema(self, raw) -> dict:
        if isinstance(raw, dict):
            return raw
        if not raw:
            return {}
        try:
            parsed = json.loads(raw)
            return parsed if isinstance(parsed, dict) else {}
        except Exception:
            return {"notes": str(raw)}

    def _parse_jsonish(self, value):
        if not isinstance(value, str):
            return value
        success, parsed = extract_and_heal_json(value)
        return parsed if success else {}

    def _extract_page(self, item: dict):
        page = item.get("page") if item.get("page") is not None else item.get("page_num") or item.get("p")
        try:
            return int(page)
        except Exception:
            return None

    def _coerce_list(self, value) -> list:
        if value is None:
            return []
        return value if isinstance(value, list) else [value]

    def _scoped_temp_id(self, prefix: str, temp_id: str) -> str:
        temp_id = str(temp_id or "").strip()
        if not temp_id:
            return prefix
        return temp_id if temp_id.startswith(f"{prefix}_") or temp_id.startswith("master_") else f"{prefix}_{temp_id}"

    def _stable_entity_id(self, result: dict, temp_id: str) -> str:
        return "analysis:" + str(uuid.uuid5(uuid.NAMESPACE_URL, f"{result.get('run_id')}:{temp_id}"))

    def _stable_relation_id(self, result: dict, src: str, tgt: str, rel_type: str) -> str:
        return "analysis-rel:" + str(uuid.uuid5(uuid.NAMESPACE_URL, f"{result.get('run_id')}:{src}:{tgt}:{rel_type}"))

    def run_id(self, doc_path: str, template_id: str) -> str:
        raw = f"{doc_path}:{template_id}:{os.path.getmtime(doc_path) if os.path.exists(doc_path) else ''}"
        return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:16]

    

    def _view_meta_for_index(self, index: int) -> dict:
        col, row = index % 4, index // 4
        return {"x": 80 + col * 270, "y": 80 + row * 190, "properties": {"width": 220, "height": 125}}
