"""Workflow steps for artifact-aware intelligence services."""
from __future__ import annotations

import json
import hashlib
import re
import uuid
from dataclasses import asdict, is_dataclass, replace
from typing import Any, List

from core.engine.steps.base_step import BaseStep
from core.models.intelligence_models import RetrievalPlan, RetrievalResult, SourceScope
from core.plugins.plugin_step_protocol import StepContext
from core.utils.state_resolver import StateResolver
from core.services.intelligence.retrieval_audit import (
    build_retrieval_audit, candidate_key, apply_selected_quotes,
)
from core.services.intelligence.analysis_model_context import build_analysis_context_items, supplied_analysis_scope


class BuildRetrievalPlanStep(BaseStep):
    step_type = "BUILD_RETRIEVAL_PLAN"
    label = "Build Retrieval Plan"
    category = "Intelligence"
    description = "Create an artifact-aware retrieval plan for a query."
    input_schema = {
        "query": {"type": "string", "label": "Query", "required": True},
        "allowed_source_ids": {"type": "array", "label": "Allowed Source IDs"},
        "artifact_types": {"type": "array", "label": "Artifact Types"},
        "max_results": {"type": "integer", "label": "Max Results"},
        "use_embeddings": {"type": "boolean", "label": "Use Vector Fallback"},
    }

    def execute(self, context: StepContext, inputs: dict):
        service = context.hybrid_retrieval_service
        query = str(inputs.get("query") or "")
        allowed = _list(inputs.get("allowed_source_ids"))
        artifact_types = _list(inputs.get("artifact_types"))
        send_limit = int(inputs.get("max_results") or 10)
        max_results = max(20, min(40, send_limit * 2))
        use_embeddings = bool(inputs.get("use_embeddings", True))
        if service and hasattr(service, "build_plan"):
            plan = service.build_plan(
                query,
                allowed_source_ids=allowed,
                artifact_types=artifact_types or None,
                max_results=max_results,
                use_embeddings=use_embeddings,
            )
        else:
            plan = RetrievalPlan(
                query=query,
                allowed_source_ids=allowed,
                artifact_types=artifact_types,
                use_embeddings=use_embeddings,
                max_results=max_results,
            )
        return self.build_result(
            json.dumps(asdict(plan), ensure_ascii=False),
            state_updates={"retrieval_send_limit": send_limit},
        )


class ArtifactRetrieveStep(BaseStep):
    step_type = "ARTIFACT_RETRIEVE"
    label = "Artifact Retrieve"
    category = "Intelligence"
    description = "Retrieve structured artifacts and vector fallback results."
    input_schema = {
        "plan": {"type": "json", "label": "Retrieval Plan"},
        "query": {"type": "string", "label": "Query"},
        "allowed_source_ids": {"type": "array", "label": "Allowed Source IDs"},
        "max_results": {"type": "integer", "label": "Max Results"},
    }

    def execute(self, context: StepContext, inputs: dict):
        service = context.hybrid_retrieval_service
        if not service:
            return self.build_result("[]")
        plan = _plan(inputs.get("plan"))
        state = context.state if isinstance(context.state, dict) else {}
        if plan is None:
            query = str(inputs.get("query") or "")
            if hasattr(service, "build_plan"):
                plan = service.build_plan(
                    query,
                    allowed_source_ids=_list(inputs.get("allowed_source_ids")),
                    max_results=int(inputs.get("max_results") or 10),
                )
            else:
                plan = RetrievalPlan(
                    query=query,
                    allowed_source_ids=_list(inputs.get("allowed_source_ids")),
                    max_results=int(inputs.get("max_results") or 10),
                )
        state_updates = {}
        if not state.get("retrieval_send_limit"):
            requested = max(1, int(plan.max_results or 10))
            plan = replace(
                plan, max_results=max(20, min(40, requested * 2))
            )
            state_updates["retrieval_send_limit"] = requested
        results = service.retrieve(plan)
        return self.build_result(
            json.dumps([_result_dict(item) for item in results], ensure_ascii=False),
            state_updates=state_updates,
        )


class PackArtifactContextStep(BaseStep):
    step_type = "PACK_ARTIFACT_CONTEXT"
    label = "Pack Artifact Context"
    category = "Intelligence"
    description = "Pack retrieval results into compact citation-ready context."
    input_schema = {
        "query": {"type": "string", "label": "Query", "required": True},
        "results": {"type": "json", "label": "Retrieval Results", "required": True},
    }

    def execute(self, context: StepContext, inputs: dict):
        packer = context.context_packing_service
        if not packer:
            return self.build_result("")
        results = [_retrieval_result(item) for item in _json_list(inputs.get("results"))]
        state = context.state if isinstance(context.state, dict) else {}
        try:
            send_limit = int(state.get("retrieval_send_limit") or 10)
        except (TypeError, ValueError):
            send_limit = 10
        send_pool = results[:max(1, send_limit)]
        from core.services.intelligence import context_sources
        known = dict(state.get(context_sources.STATE_KEY) or {})
        bundle = packer.pack(str(inputs.get("query") or ""), send_pool,
                             first_label=context_sources.next_number(known))
        known.update(bundle.metadata.get("sources") or {})
        audit = build_retrieval_audit(
            str(inputs.get("query") or ""),
            results,
            {candidate_key(item) for item in bundle.items},
            previous=state.get("retrieval_context_audit"),
        )
        return self.build_result(
            bundle.text,
            state_updates={
                "artifact_context_bundle": asdict(bundle),
                "retrieval_context_audit": audit,
                context_sources.STATE_KEY: known,
            },
        )


class SourceStageStep(BaseStep):
    step_type = "SOURCE_STAGE"
    label = "Source Stage"
    category = "Intelligence"
    description = "Request source intelligence processing through the scheduler."
    input_schema = {
        "source_id": {"type": "string", "label": "Source ID", "required": True},
        "source_ids": {"type": "array", "label": "Source IDs"},
        "stage": {"type": "string", "label": "Stage"},
        "stages": {"type": "array", "label": "Stages"},
        "action": {"type": "string", "label": "Action"},
        "mode": {"type": "string", "label": "Processing mode"},
        "expected_project": {"type": "array", "label": "Project identity"},
    }

    def execute(self, context: StepContext, inputs: dict):
        service = context.source_intelligence_service
        source_id = str(inputs.get("source_id") or "")
        action = str(inputs.get("action") or "run_stage")
        stage = str(inputs.get("stage") or "")
        if not service:
            return self.build_result({"error": "Source intelligence service is unavailable."})
        if action == "process_plan":
            return self._process_plan(service, source_id, context, inputs)
        if action == "ensure_analysis":
            source_ids = _list(inputs.get("source_ids"))
            if not source_ids and source_id:
                source_ids = [source_id]
            result = service.ensure_analysis_artifacts(
                source_ids,
                stages=_list(inputs.get("stages")) if 'stages' in inputs else None,
                cancel_check=context.is_aborted,
            )
            self._check_preparation(context, service)
            from core.events.domains.analysis_events import AnalysisEvent, AnalysisPayload
            return self.build_result(
                json.dumps(result, ensure_ascii=False),
                state_updates={"analysis_preparation": result},
                system_events=[self.bus_emit_event(
                    "analysis_result_changed", AnalysisEvent.PROGRESS,
                    AnalysisPayload(result={
                        "target_id": context.state.get("analysis_target_id"),
                        "message": (
                            f"Intelligence ready: {result.get('prepared_count', 0)} prepared, "
                            f"{result.get('reused_count', 0)} reused, "
                            f"{result.get('failed_count', 0)} failed. "
                            "Building the deterministic baseline…"
                        ),
                        "phase": "preparation",
                    }),
                )],
            )
        if action == "run_missing":
            jobs = service.run_missing(source_id)
        elif action == "rerun_stale":
            jobs = service.rerun_stale(source_id)
        elif action == "delete_derived":
            jobs = service.delete_derived(source_id, stage or None)
        else:
            jobs = service.run_stage(source_id, stage)
        return self.build_result(str(jobs))


    @staticmethod
    def _check_preparation(context, service):
        from types import SimpleNamespace
        from core.services.intelligence.analysis_prerequisite_service import AnalysisPrerequisiteService
        requirements = context.state.get('analysis_prerequisites')
        if not requirements:
            return  # Compatibility for workflows predating compiled prerequisites.
        spec = SimpleNamespace(**requirements, required_artifact_types=[])
        ready = AnalysisPrerequisiteService(context.project_manager, service).check(spec, context.state)
        if not ready['ready']:
            raise ValueError('Analysis prerequisites remain unavailable after preparation: '
                             + json.dumps(ready['missing'], ensure_ascii=False))

    def _process_plan(self, service, source_id, context, inputs):
        try:
            result = service.process_plan(source_id, mode=inputs.get("mode") or "missing",
                stages=_list(inputs.get("stages")), expected_project=_list(inputs.get("expected_project")) or None,
                cancel_check=context.is_aborted)
            return self.build_result(result, state_updates={"source_processing_result": result})
        except (ValueError, RuntimeError) as exc:
            result = {"status": "failed", "error": str(exc), "results": []}
            return self.build_result(result, state_updates={"source_processing_result": result})


class CitationValidateStep(BaseStep):
    step_type = "CITATION_VALIDATE"
    label = "Validate Citations"
    category = "Intelligence"
    description = "Deterministically match answer sentences to retrieved evidence."
    input_schema = {
        "answer": {"type": "string", "label": "Answer", "required": True},
        "context": {"type": "string", "label": "Evidence context", "required": True},
    }

    def execute(self, context: StepContext, inputs: dict):
        answer = str(inputs.get("answer") or "")
        source_context = str(inputs.get("context") or "")
        from core.services.intelligence import context_sources
        state = context.state if isinstance(context.state, dict) else {}
        blocks = context_sources.blocks(source_context, state.get(context_sources.STATE_KEY))
        citations = []
        unsupported = []
        for sentence in re.split(r"(?<=[.!?])\s+", answer):
            sentence = sentence.strip()
            if len(sentence) < 20:
                continue
            terms = {
                token.lower() for token in re.findall(r"[A-Za-z0-9]{4,}", sentence)
            }
            scored = []
            for block in blocks:
                haystack = block["text"].lower()
                overlap = len([term for term in terms if term in haystack])
                score = overlap / max(1, len(terms))
                scored.append((score, block))
            score, match = max(scored, key=lambda item: item[0], default=(0.0, None))
            if match and score >= 0.25:
                citations.append({
                    "artifact_id": match.get("artifact_id", ""),
                    "doc_name": match.get("doc_name", ""),
                    "source_id": match.get("source_id", ""),
                    "source_locator": match.get("locator", {}),
                    "quote": match.get("text", "")[:500],
                    "note": sentence,
                    "validation_score": round(score, 3),
                    "validation": "deterministic",
                })
            else:
                unsupported.append(sentence)
        payload = {
            "citations": citations,
            "unsupported_sentences": unsupported,
            "warning": (
                f"{len(unsupported)} answer sentence(s) were not matched to evidence."
                if unsupported else ""
            ),
        }
        audit = apply_selected_quotes(
            context.state.get("retrieval_context_audit"), citations
        )
        from core.engine.ui_payloads import build_ui_payloads
        ui_payloads = build_ui_payloads(
            "chat_widgets", json.dumps(payload, ensure_ascii=False),
            step=getattr(context, "_step_proxy", None),
            trace_id=context.trace_id,
        )
        if audit.get("candidates"):
            highlight_payload = {
                "type": "rag_context_highlights",
                "audit": audit,
                "citations": citations,
                "attach_to_previous": True,
            }
            hide_index = next(
                (
                    index for index, item in enumerate(ui_payloads)
                    if item.get("type") == "hide_status"
                ),
                len(ui_payloads),
            )
            ui_payloads.insert(hide_index, highlight_payload)
        return self.build_result(
            json.dumps(payload, ensure_ascii=False),
            state_updates={
                "citation_validation": payload,
                "retrieval_context_audit": audit,
            },
            ui_payloads=ui_payloads,
        )


class GraphEnrichmentValidateStep(BaseStep):
    step_type = "GRAPH_ENRICH_VALIDATE"
    label = "Validate Graph Enrichment"
    category = "Intelligence"
    description = "Hydrate a mode-specific graph while preserving evidence and typed nodes."
    input_schema = {
        "graph": {"type": "json", "label": "Deterministic graph (inline)"},
        "graph_state_key": {"type": "string", "label": "Deterministic graph (opaque state key)"},
        "enrichment": {"type": "json", "label": "Model proposals (inline)"},
        "enrichment_state_key": {"type": "string", "label": "Model proposals (opaque state key)"},
        "source_id": {"type": "string", "label": "Source ID"},
        "attempt": {"type": "string", "label": "Validation attempt"},
        "packing_receipt_key": {"type": "string", "label": "Model context receipt (output key)"},
    }

    def execute(self, context: StepContext, inputs: dict):
        graph = self._object(StateResolver.input_value(inputs, context.state, 'graph'))
        enrichment = self._expand_compact_enrichment(
            self._object(StateResolver.input_value(inputs, context.state, 'enrichment'))
        )
        output = graph.get("output") if isinstance(graph.get("output"), dict) else graph
        node_map = {
            node.get("id"): node for node in output.get("entities", [])
            if isinstance(node, dict) and node.get("id")
        }
        db = getattr(getattr(context, "project_manager", None), "db_intelligence", None)
        artifact_service = getattr(context, "artifact_service", None)
        from core.ontology.registry import OntologyRegistry
        ontology_registry = (
            getattr(context, "ontology_registry", None) or OntologyRegistry()
        )
        state = context.state if isinstance(context.state, dict) else {}
        evidence_aliases = {
            str(key): str(value)
            for key, value in (
                state.get("analysis_evidence_aliases") or {}
            ).items()
            if key and value
        }
        mode_id = str(state.get("analysis_mode_id") or "analysis")
        focus = str(state.get("analysis_focus") or "")
        contract = self._object(state.get("analysis_graph_contract"))
        allowed_node_types = {
            self._slug(value) for value in contract.get("node_types", [])
            if self._slug(value)
        }
        allowed_relation_types = {
            self._slug(value) for value in contract.get("relation_types", [])
            if self._slug(value)
        }
        max_nodes = max(3, min(80, int(contract.get("max_nodes") or 36)))
        max_relations = max(2, min(120, int(contract.get("max_relations") or 48)))
        baseline = output.get("argument_baseline") or {}
        if inputs.get('packing_receipt_key') and 'updates' not in enrichment:
            enrichment = {'updates': {}, 'cross_links': []}  # Invalid bounded patch; never use the legacy reader.
        if 'updates' in enrichment:
            return self._validate_baseline_patch(context, inputs, baseline, enrichment, contract, state)
        if baseline:
            from core.services.intelligence.analysis_baseline_patch_service import AnalysisBaselinePatchService
            from core.services.intelligence.grounded_argument_graph_service import GroundedArgumentGraphService
            from core.models.intelligence_models import GraphQualityContract
            current_nodes, current_relations, stale_ids = AnalysisBaselinePatchService(db).current_baseline(
                baseline, state.get('allowed_source_ids') or
                (graph.get('provenance') or {}).get('source_ids') or
                ([inputs['source_id']] if inputs.get('source_id') else []),
                state.get('analysis_evidence_snapshots'))
            quality = GroundedArgumentGraphService.measure(
                current_nodes, current_relations, GraphQualityContract.from_value(contract))
            baseline = {**baseline, 'entities': current_nodes, 'relations': current_relations,
                        'quality': {**(baseline.get('quality') or {}), **quality,
                                    'limited_coverage': not quality['meets_requested_contract'],
                                    'stale_baseline_node_ids': stale_ids}}

        from core.models.analysis_focus import AnalysisFocus
        from core.services.intelligence.analysis_grounding import AnalysisEvidenceScope
        supplied_ids = set(evidence_aliases.values()) | {
            identity for node in baseline.get('entities') or []
            for identity in node.get('evidence_ids') or []
        }
        supplied_ids.update(str(identity) for identity in node_map)
        scope = AnalysisEvidenceScope(db, supplied_ids,
            source_ids=state.get('allowed_source_ids') or (graph.get('provenance') or {}).get('source_ids')
                       or ([inputs['source_id']] if inputs.get('source_id') else []),
            snapshots={**(baseline.get('artifact_snapshots') or {}),
                       **(state.get('analysis_evidence_snapshots') or {})})
        focus_policy = AnalysisFocus(focus)

        def artifact(artifact_id: str):
            artifact_id = evidence_aliases.get(
                str(artifact_id), str(artifact_id)
            )
            item = scope.resolve(artifact_id)
            return item if item and (artifact_id in evidence_aliases.values()
                                      or focus_policy.matches(item)) else None

        def stored_artifact(artifact_id: str):
            return artifact(artifact_id)

        def evidence_ids(raw):
            result = []
            for value in raw if isinstance(raw, list) else []:
                item = artifact(str(value))
                artifact_type = str(
                    (item or {}).get("artifact_type") or ""
                ).lower()
                if not artifact_type:
                    stored = stored_artifact(str(value))
                    artifact_type = str(
                        (stored or {}).get("artifact_type") or ""
                    ).lower()
                # A graph grounding edge must end in source-bearing text/data,
                # never an entity name, date mention, or bibliography record.
                if artifact_type not in {
                    "sentence", "transcript_segment", "table",
                }:
                    continue
                value = str(
                    (item or {}).get("id")
                    or (item or {}).get("artifact_id")
                    or evidence_aliases.get(str(value), value) or ""
                )
                if value and item and value not in result:
                    result.append(value)
            return result

        analysis_nodes = []
        raw_to_hydrated = {}
        raw_node_proposals = []
        rejected_nodes = []
        proposed_nodes = enrichment.get("nodes") or enrichment.get("analysis_nodes") or []
        for index, proposal in enumerate(proposed_nodes[:max_nodes]):
            if not isinstance(proposal, dict):
                continue
            node_type = self._normalize_node_role(
                proposal.get("role") or proposal.get("type")
            )
            if allowed_node_types and node_type not in allowed_node_types:
                rejected_nodes.append({**proposal, "reason": "node_type_not_allowed"})
                continue
            raw_id = str(proposal.get("id") or f"node_{index + 1}")
            node_evidence = evidence_ids(
                proposal.get("evidence") or proposal.get("evidence_ids")
            )
            primary_raw = str(
                proposal.get("primary_evidence_id")
                or (node_evidence[0] if node_type in {
                    "evidence", "quotation", "quote", "source_evidence"
                } and node_evidence else "")
            )
            primary_item = stored_artifact(primary_raw) if primary_raw else None
            primary_id = str((primary_item or {}).get("id") or primary_raw)
            if primary_item and primary_item.get("artifact_id"):
                primary_id = str(primary_item["artifact_id"])
            primary = stored_artifact(primary_id) if primary_id else None
            is_evidence = node_type in {
                "evidence", "quotation", "quote", "source_evidence"
            }
            is_user_root = node_type == "tracked_claim" and focus
            if is_evidence and not primary:
                rejected_nodes.append({
                    **proposal, "reason": "evidence_node_requires_primary_evidence_id"
                })
                continue
            if primary_id and primary_id not in node_evidence:
                rejected_nodes.append({**proposal, 'reason': 'primary_evidence_not_in_supplied_evidence'})
                continue
            if not node_evidence and not is_user_root:
                rejected_nodes.append({
                    **proposal, "reason": "analysis_node_requires_resolvable_evidence"
                })
                continue
            digest = hashlib.sha1(
                f"{mode_id}|{raw_id}|{node_type}|"
                f"{proposal.get('statement') or proposal.get('text')}".encode(
                    "utf-8", "replace"
                )
            ).hexdigest()[:14]
            hydrated_id = f"analysis-node:{mode_id}:{digest}"
            if is_evidence:
                exact_text = str(
                    primary.get("exact_text") or primary.get("text")
                    or primary.get("title") or ""
                )
                text_value = exact_text
                title = self._evidence_title(primary, exact_text)
            elif is_user_root:
                exact_text = ""
                text_value = focus
                title = str(proposal.get("title") or "Tracked claim")
            else:
                exact_text = ""
                text_value = str(
                    proposal.get("statement") or proposal.get("text")
                    or proposal.get("label") or proposal.get("title") or ""
                ).strip()
                title = str(
                    proposal.get("label") or proposal.get("title")
                    or text_value[:120] or node_type
                )
            if not text_value:
                rejected_nodes.append({**proposal, "reason": "analysis_node_has_no_text"})
                continue
            grounding = (
                primary
                or (stored_artifact(node_evidence[0]) if node_evidence else None)
            )
            hydrated = {
                "id": hydrated_id,
                "temp_id": hydrated_id,
                "type": (contract.get("node_type_map") or {}).get(node_type) or self._workspace_node_type(node_type),
                "artifact_type": "analysis_node",
                "analysis_role": node_type or "claim",
                "title": title,
                "text": text_value,
                "exact_text": exact_text,
                "quote": exact_text,
                "evidence_ids": node_evidence,
                "primary_evidence_id": primary_id or None,
                "source_id": (grounding or {}).get("source_id"),
                "locator": (grounding or {}).get("locator") or {},
                "section_path": (grounding or {}).get("section_path") or [],
                "properties": {
                    "title": title,
                    "text": text_value,
                    "exact_text": exact_text,
                    "quote": exact_text,
                    "evidence_ids": node_evidence,
                    "primary_evidence_id": primary_id or None,
                    "analysis_role": node_type or "claim",
                    "source_id": (grounding or {}).get("source_id"),
                    "locator": (grounding or {}).get("locator") or {},
                    "section_path": (grounding or {}).get("section_path") or [],
                },
                "review_state": "unreviewed",
                "suggested": True,
                "grounding": "user_input" if is_user_root else "artifact",
            }
            analysis_nodes.append(hydrated)
            raw_to_hydrated[raw_id] = hydrated_id
            raw_node_proposals.append((raw_id, proposal, hydrated))

        # Existing artifact nodes are accepted only when the model explicitly
        # selects them.  They are never used as an all-graph fallback.
        selected_ids = {
            str(value) for value in enrichment.get("selected_node_ids", [])
            if str(value) in node_map
        }
        base_entities = [
            node for node_id, node in node_map.items() if node_id in selected_ids
        ]

        # New universal schema; accept the old relation key for saved/custom
        # modes while they migrate.
        raw_relations = list(
            enrichment.get("cross_links")
            or enrichment.get("analysis_relations")
            or enrichment.get("proposed_relations")
            or []
        )
        # The compact contract stores the primary hierarchy edge beside each
        # node. A valid prefix of a cut-off response therefore remains linked.
        for raw_id, proposal, hydrated in raw_node_proposals:
            parent = str(proposal.get("parent") or proposal.get("parent_id") or "")
            relation = str(
                proposal.get("relation") or proposal.get("parent_relation") or ""
            )
            if parent and relation:
                raw_relations.append({
                    "from": raw_id,
                    "to": parent,
                    "relation": relation,
                    "evidence": list(hydrated.get("evidence_ids") or []),
                    "explanation": "Mode hierarchy",
                })
        accepted_relations = []
        rejected_relations = []
        endpoint_ids = set(raw_to_hydrated) | selected_ids
        hydrated_node_by_id = {
            str(node.get("id")): node for node in analysis_nodes if node.get("id")
        }
        for index, proposal in enumerate(raw_relations[:max_relations]):
            if not isinstance(proposal, dict):
                continue
            raw_source = str(
                proposal.get("from") or proposal.get("source_id")
                or proposal.get("source") or ""
            )
            raw_target = str(
                proposal.get("to") or proposal.get("target_id")
                or proposal.get("target") or ""
            )
            source_id = raw_to_hydrated.get(raw_source, raw_source)
            target_id = raw_to_hydrated.get(raw_target, raw_target)
            from core.models.analysis_relations import directed_relation
            source_id, target_id, relation_type = directed_relation(source_id, target_id,
                proposal.get('relation') or proposal.get('type'))
            relation_evidence = evidence_ids(
                proposal.get("evidence") or proposal.get("evidence_ids")
            )
            endpoints_ok = raw_source in endpoint_ids and raw_target in endpoint_ids
            type_ok = (
                not allowed_relation_types
                or relation_type in allowed_relation_types
            )
            canonical_relation_type = self._workspace_relation_type(relation_type)
            source_node = hydrated_node_by_id.get(source_id)
            target_node = hydrated_node_by_id.get(target_id)
            ontology_ok = (
                not source_node or not target_node
                or ontology_registry.validate_relation(
                    canonical_relation_type,
                    str(source_node.get("type") or "entity.text"),
                    str(target_node.get("type") or "entity.text"),
                )
            )
            if (
                not endpoints_ok or not type_ok
                or not relation_evidence or not ontology_ok
            ):
                rejected_relations.append({
                    **proposal,
                    "reason": (
                        "unresolvable_endpoints" if not endpoints_ok
                        else "relation_type_not_allowed" if not type_ok
                        else "relation_endpoints_violate_ontology"
                        if not ontology_ok
                        else "relation_requires_resolvable_evidence"
                    ),
                })
                continue
            accepted_relations.append({
                "id": f"analysis-relation:{mode_id}:{index}:{source_id}|{target_id}",
                "source": source_id,
                "target": target_id,
                "type": canonical_relation_type,
                "label": (relation_type or "related_to").replace("_", " "),
                "evidence_ids": relation_evidence,
                "explanation": str(proposal.get("explanation") or ""),
                "suggested": True,
                "review_state": "unreviewed",
                "grounding": "model_proposal",
            })

        # Evidence is data, not prose-generation. Materialize one exact quote
        # node per cited artifact and ground every analytical node locally.
        evidence_node_by_artifact = {}
        for node in analysis_nodes:
            if node.get("analysis_role") == "evidence":
                primary_id = str(node.get("primary_evidence_id") or "")
                if primary_id:
                    evidence_node_by_artifact[primary_id] = node["id"]
        analytical_nodes = [
            node for node in analysis_nodes
            if node.get("analysis_role") != "evidence"
        ]
        for node in analytical_nodes:
            for evidence_id in list(node.get("evidence_ids") or [])[:2]:
                evidence_item = stored_artifact(evidence_id)
                if not evidence_item:
                    continue
                canonical_id = str(
                    evidence_item.get("id")
                    or evidence_item.get("artifact_id")
                    or evidence_id
                )
                evidence_node_id = evidence_node_by_artifact.get(canonical_id)
                if not evidence_node_id:
                    if len(analysis_nodes) >= max_nodes:
                        break
                    digest = hashlib.sha1(
                        f"{mode_id}|evidence|{canonical_id}".encode(
                            "utf-8", "replace"
                        )
                    ).hexdigest()[:14]
                    evidence_node_id = f"analysis-evidence:{mode_id}:{digest}"
                    exact_text = str(
                        evidence_item.get("exact_text")
                        or evidence_item.get("text")
                        or evidence_item.get("title")
                        or ""
                    ).strip()
                    if not exact_text:
                        continue
                    source_id = evidence_item.get("source_id")
                    locator = evidence_item.get("locator") or {}
                    evidence_title = self._evidence_title(
                        evidence_item, exact_text
                    )
                    analysis_nodes.append({
                        "id": evidence_node_id,
                        "temp_id": evidence_node_id,
                        "type": "entity.evidence",
                        "artifact_type": "analysis_node",
                        "analysis_role": "evidence",
                        "title": evidence_title,
                        "text": exact_text,
                        "exact_text": exact_text,
                        "quote": exact_text,
                        "evidence_ids": [canonical_id],
                        "primary_evidence_id": canonical_id,
                        "source_id": source_id,
                        "locator": locator,
                        "section_path": evidence_item.get("section_path") or [],
                        "properties": {
                            "title": evidence_title,
                            "text": exact_text,
                            "exact_text": exact_text,
                            "quote": exact_text,
                            "evidence_ids": [canonical_id],
                            "primary_evidence_id": canonical_id,
                            "analysis_role": "evidence",
                            "source_id": source_id,
                            "locator": locator,
                            "section_path": evidence_item.get("section_path") or [],
                        },
                        "review_state": "unreviewed",
                        "suggested": True,
                        "grounding": "artifact",
                    })
                    evidence_node_by_artifact[canonical_id] = evidence_node_id
                if not ontology_registry.validate_relation(
                    'relation.derived_from', node['type'], 'entity.evidence'
                ):
                    continue
                relation_key = f"{node['id']}|{evidence_node_id}|relation.derived_from"
                if not any(
                    f"{relation.get('source')}|{relation.get('target')}|"
                    f"{relation.get('type')}" == relation_key
                    for relation in accepted_relations
                ):
                    accepted_relations.append({
                        "id": (
                            f"analysis-relation:{mode_id}:ground:"
                            f"{len(accepted_relations)}"
                        ),
                        "source": node["id"],
                        "target": evidence_node_id,
                        "type": "relation.derived_from",
                        "label": "source quotation",
                        "semantic": False,
                        "grounding": "source_reference",
                        "evidence_ids": [canonical_id],
                        "explanation": "Source quotation; this link does not establish support or entailment.",
                        "suggested": True,
                        "review_state": "unreviewed",
                    })

        model_nodes, model_relations, pruned_node_count = (
            self._coherent_mode_projection(
                mode_id, analysis_nodes, accepted_relations, focus
            )
        )
        baseline_nodes = list(baseline.get("entities") or [])
        baseline_relations = list(baseline.get("relations") or [])
        baseline_quality = dict(baseline.get("quality") or {})
        quality_contract = self._effective_quality_contract(
            contract, baseline_quality
        )
        model_quality = self._graph_quality(
            model_nodes, model_relations, quality_contract
        )
        structured_output_valid = bool(model_quality.get("valid"))
        improves_baseline = (
            structured_output_valid
            and float(model_quality.get("quality_score") or 0.0)
            >= float(baseline_quality.get("quality_score") or 0.0)
        )
        if model_nodes and not structured_output_valid:
            rejected_nodes.append({
                "reason": "incomplete_mode_graph",
                "message": (
                    "The generated nodes did not form the minimum evidence-backed "
                    "structure requested by this analysis mode. The incomplete "
                    "model graph was discarded."
                ),
            })
        if structured_output_valid and not improves_baseline:
            rejected_nodes.append({
                "reason": "model_overlay_weaker_than_baseline",
                "message": "The grounded baseline scored higher than the model overlay.",
            })

        attempt = str(inputs.get("attempt") or "initial")
        use_model = structured_output_valid and improves_baseline
        final_nodes = model_nodes if use_model else baseline_nodes
        final_relations = model_relations if use_model else baseline_relations
        final_quality = dict(model_quality if use_model else baseline_quality)
        origin = (
            "repaired" if use_model and attempt == "repair"
            else "enhanced" if use_model
            else "deterministic_baseline"
        )
        repair_allowed = bool(
            contract.get("allow_repair_call", True)
            and state.get("analysis_allow_repair", True)
        )
        needs_repair = bool(
            not use_model and attempt != "repair" and repair_allowed
        )
        final_quality.update({
            "origin": origin,
            "valid": bool(final_nodes),
            "model_overlay_valid": structured_output_valid,
            "model_overlay_improves_baseline": improves_baseline,
            "repair_attempted": attempt == "repair",
            "repair_required": needs_repair,
        })

        # Persist only a validated overlay. Baseline nodes already reference
        # durable argument_unit/source artifacts and need no duplicate rows.
        suggestion_artifact_ids = []
        if use_model and artifact_service:
            for node in final_nodes:
                if node.get("analysis_role") == "evidence":
                    continue
                artifact_id = artifact_service.create_artifact(
                    source_id=str(node.get("source_id") or inputs.get("source_id") or "") or None,
                    artifact_type="analysis_node_suggestion",
                    storage_ref_type="graph_enrichment",
                    storage_ref_id=str(node.get("id") or ""),
                    exact_text=json.dumps(node, ensure_ascii=False),
                    metadata={
                        "stage": "analysis", "suggested": True,
                        "review_state": "unreviewed",
                        "mode_id": mode_id,
                        "analysis_role": node.get("analysis_role"),
                        "evidence_artifact_ids": list(node.get("evidence_ids") or []),
                    },
                    confidence=0.5,
                    created_by="model",
                    extractor_name="mode_graph",
                    model_role="main_answer_model",
                )
                if artifact_id:
                    suggestion_artifact_ids.append(artifact_id)

        result = {
            "template_title": str(
                enrichment.get("title")
                or state.get("analysis_mode_label")
                or "Analysis"
            ),
            "mode_id": mode_id,
            "focus": focus,
            "entities": final_nodes,
            "relations": final_relations,
            "themes": [],
            "suggested_relations": model_relations if use_model else [],
            "rejected_proposals": rejected_nodes + rejected_relations,
            "deterministic_graph_preserved": True,
            "complete_base_node_count": len(node_map),
            "complete_base_relation_count": len(output.get("relations", [])),
            "selected_node_count": len(final_nodes),
            "evidence_selection_artifact_ids": [],
            "evidence_role_artifact_ids": [],
            "suggestion_artifact_ids": suggestion_artifact_ids,
            "graph_contract": contract,
            "quality": final_quality,
            "diagnostics": {
                "invalid_selected_node_ids": [
                    value for value in enrichment.get("selected_node_ids", [])
                    if str(value) not in node_map
                ],
                "fallback_to_complete_graph": False,
                "fallback_to_deterministic_baseline": not use_model,
                "focused_deterministic_fallback": not use_model,
                "rejected_node_count": len(rejected_nodes),
                "rejected_relation_count": len(rejected_relations),
                "pruned_disconnected_node_count": pruned_node_count,
                "structured_model_output_valid": structured_output_valid,
                "model_overlay_improves_baseline": improves_baseline,
                "preserved_grounded_prefix": False,
                "needs_repair": needs_repair,
                "repair_attempted": attempt == "repair",
                "output_origin": origin,
                "model_quality": model_quality,
                "baseline_quality": baseline_quality,
                "evidence_preparation": (
                    state.get("analysis_evidence_diagnostics") or {}
                ),
            },
        }
        from core.events.domains.analysis_events import AnalysisEvent, AnalysisPayload
        progress_message = (
            "Model graph validated. Persisting the analysis…" if use_model
            else "Model graph failed validation. Running one targeted repair…"
            if needs_repair
            else "Using the validated deterministic baseline. Persisting the analysis…"
        )
        return self.build_result(
            json.dumps(result, ensure_ascii=False),
            state_updates={
                "validated_mode_graph": result,
                "analysis_graph_needs_repair": needs_repair,
                "analysis_graph_validation": result["diagnostics"],
            },
            system_events=[self.bus_emit_event(
                "analysis_result_changed", AnalysisEvent.PROGRESS,
                AnalysisPayload(result={
                    "target_id": state.get("analysis_target_id"),
                    "message": progress_message,
                    "phase": "repair" if needs_repair else "validation",
                }),
            )],
        )

    def _validate_baseline_patch(self, context, inputs, baseline, enrichment, contract, state):
        from core.services.intelligence.analysis_baseline_patch_service import AnalysisBaselinePatchService
        db = getattr(getattr(context, 'project_manager', None), 'db_intelligence', None)
        aliases, node_ids, packing = state.get('analysis_evidence_aliases') or {}, None, {}
        if inputs.get('packing_receipt_key'):
            aliases, node_ids, packing = supplied_analysis_scope(state, inputs['packing_receipt_key'])
        result = AnalysisBaselinePatchService(db, getattr(context, 'ontology_registry', None)).apply(
            baseline, enrichment, mode_id=str(state.get('analysis_mode_id') or 'analysis'),
            focus=str(state.get('analysis_focus') or ''), contract=contract,
            source_ids=state.get('allowed_source_ids') or
                       ([inputs['source_id']] if inputs.get('source_id') else []),
            snapshots=state.get('analysis_evidence_snapshots'),
            aliases=aliases, allowed_node_ids=node_ids,
            attempt=str(inputs.get('attempt') or 'initial'),
            allow_repair=state.get('analysis_allow_repair', True))
        if packing:
            result['diagnostics']['context_packing'] = packing
            result['quality']['context_packing'] = packing
        from core.events.domains.analysis_events import AnalysisEvent, AnalysisPayload
        return self.build_result(json.dumps(result, ensure_ascii=False), state_updates={
            'validated_mode_graph': result,
            'analysis_graph_needs_repair': result['diagnostics']['needs_repair'],
            'analysis_graph_validation': result['diagnostics'],
        }, system_events=[self.bus_emit_event('analysis_result_changed', AnalysisEvent.PROGRESS,
            AnalysisPayload(result={'target_id': state.get('analysis_target_id'),
                'phase': 'repair' if result['diagnostics']['needs_repair'] else 'validation',
                'message': 'Patch checked. Any new connections remain unreviewed suggestions.'}))])

    @classmethod
    def _expand_compact_enrichment(cls, value):
        """Normalize the short synthesis schema before validation.

        Short keys are intentional: they leave small local models enough output
        budget for a detailed graph.  Saved results using the earlier verbose
        schema remain accepted unchanged.
        """
        if not isinstance(value, dict):
            return {}
        if not isinstance(value.get("n"), list):
            if isinstance(value.get("u"), list):
                return {
                    **value,
                    "title": value.get("t") or value.get("title") or "Analysis",
                    "updates": [
                        {
                            "id": raw.get("i") or raw.get("id"),
                            "role": raw.get("r") or raw.get("role"),
                            "label": raw.get("l") or raw.get("label"),
                            "statement": raw.get("s") or raw.get("statement"),
                            "evidence": raw.get("q") or raw.get("evidence") or [],
                            "parent": raw.get("p") or raw.get("parent") or "",
                            "relation": raw.get("e") or raw.get("relation") or "",
                        } if isinstance(raw, dict) else raw
                        for raw in value.get("u") or []
                    ],
                    "cross_links": [
                        {
                            "from": raw.get("a") or raw.get("from"),
                            "to": raw.get("b") or raw.get("to"),
                            "relation": raw.get("r") or raw.get("relation"),
                            "evidence": raw.get("q") or raw.get("evidence") or [],
                            "explanation": raw.get('why') or raw.get('explanation') or '',
                        } if isinstance(raw, dict) else raw
                        for raw in value.get("x") or []
                    ],
                }
            return value
        nodes = []
        for index, raw in enumerate(value.get("n") or []):
            if not isinstance(raw, dict):
                continue
            nodes.append({
                "id": raw.get("i") or raw.get("id") or f"n{index + 1}",
                "role": raw.get("r") or raw.get("role") or raw.get("type"),
                "label": raw.get("l") or raw.get("label") or raw.get("title"),
                "statement": (
                    raw.get("s") or raw.get("statement") or raw.get("text")
                ),
                "evidence": (
                    raw.get("q") or raw.get("evidence")
                    or raw.get("evidence_ids") or []
                ),
                "parent": raw.get("p") or raw.get("parent") or "",
                "relation": raw.get("e") or raw.get("relation") or "",
            })
        cross_links = []
        for raw in value.get("x") or []:
            if not isinstance(raw, dict):
                continue
            cross_links.append({
                "from": raw.get("a") or raw.get("from") or raw.get("source"),
                "to": raw.get("b") or raw.get("to") or raw.get("target"),
                "relation": raw.get("r") or raw.get("relation"),
                "evidence": (
                    raw.get("q") or raw.get("evidence")
                    or raw.get("evidence_ids") or []
                ),
            })
        return {
            **value,
            "title": value.get("t") or value.get("title") or "Analysis",
            "nodes": nodes,
            "cross_links": cross_links,
        }

    @staticmethod
    def _object(value):
        if isinstance(value, dict):
            return value
        if isinstance(value, str):
            try:
                parsed = json.loads(value)
                return parsed if isinstance(parsed, dict) else {}
            except Exception:
                return {}
        return {}

    @staticmethod
    def _slug(value) -> str:
        return re.sub(r"[^a-z0-9]+", "_", str(value or "").lower()).strip("_")

    @staticmethod
    def _evidence_title(item, exact_text: str) -> str:
        item = item or {}
        section = " > ".join(item.get("section_path") or [])
        if not section:
            locator = item.get("locator") or {}
            page = locator.get("page")
            section = f"Page {page}" if page not in (None, "") else "Source passage"
        preview = " ".join(str(exact_text or "").split())
        if len(preview) > 88:
            preview = preview[:87].rstrip() + "…"
        return f"{section}: “{preview}”" if preview else section

    @staticmethod
    def _effective_quality_contract(contract, baseline_quality):
        from core.models.intelligence_models import GraphQualityContract
        # A short source is a partial result, not permission to lower the request.
        return GraphQualityContract.from_value(contract)

    @staticmethod
    def _graph_quality(nodes, relations, contract):
        from core.models.analysis_relations import semantic_relation
        from core.services.intelligence.grounded_argument_graph_service import (
            GroundedArgumentGraphService,
        )

        metrics = GroundedArgumentGraphService.measure(
            list(nodes), list(relations), contract
        )
        placeholders = []
        generic = {
            "claim", "reason", "reasoning", "evidence", "direct evidence",
            "support", "supporting evidence", "method", "methodology",
        }
        for node in nodes:
            text = " ".join(str(node.get("text") or "").lower().split())
            title = " ".join(str(node.get("title") or "").lower().split())
            if text in generic or (title in generic and len(text.split()) < 5):
                placeholders.append(str(node.get("id") or title or text))
        root_roles = set(contract.root_roles)
        proposition_ids = {
            str(node.get("id")) for node in nodes
            if node.get("analysis_role") != "evidence" and node.get("id")
        }
        analytical_sources = {
            str(relation.get("source")) for relation in relations
            if str(relation.get("source")) in proposition_ids
            and str(relation.get("target")) in proposition_ids
            and semantic_relation(relation)
        }
        roots = [
            node for node in nodes
            if str(node.get("id")) in proposition_ids - analytical_sources
        ]
        root_roles_valid = bool(roots) and all(
            str(node.get("analysis_role") or "") in root_roles for node in roots
        )
        valid = bool(
            nodes and metrics.get("meets_requested_contract")
            and metrics.get("proposition_count", 0) <= contract.max_proposition_nodes
            and metrics.get("evidence_count", 0) <= contract.max_evidence_nodes
            and len(nodes) <= contract.max_nodes
            and len(relations) <= contract.max_relations
            and not placeholders and len(roots) == 1 and root_roles_valid
        )
        metrics.update({
            "valid": valid,
            "limited_coverage": not metrics['meets_requested_contract'],
            "placeholder_node_ids": placeholders,
            "root_roles_valid": root_roles_valid,
        })
        return metrics

    @classmethod
    def _normalize_node_role(cls, value) -> str:
        from core.models.analysis_roles import analysis_role
        return analysis_role(value)

    @classmethod
    def _normalize_relation_role(cls, value) -> str:
        from core.models.analysis_relations import canonical_relation
        return canonical_relation(value)

    @classmethod
    def _workspace_node_type(cls, role: str) -> str:
        from core.models.analysis_roles import analysis_entity_type
        return analysis_entity_type(role or 'claim')

    @classmethod
    def _workspace_relation_type(cls, role: str) -> str:
        from core.models.analysis_relations import canonical_relation
        return f"relation.{canonical_relation(role)}"

    @staticmethod
    def _coherent_mode_projection(mode_id, nodes, relations, focus=""):
        """Keep one useful analytical component and its grounded evidence leaves.

        A repaired/truncated JSON object may contain a complete prefix plus an
        unfinished orphan. Importing every surviving object makes the result
        look noisy and falsely complete. The root-bearing connected component
        is the usable analysis; discarded objects remain visible in the prompt
        trace and diagnostics.
        """
        analytical_ids = {
            str(node.get("id")) for node in nodes
            if node.get("analysis_role") != "evidence" and node.get("id")
        }
        if len(analytical_ids) <= 1:
            return nodes, relations, 0
        node_by_id = {
            str(node.get("id")): node for node in nodes if node.get("id")
        }
        adjacency = {node_id: set() for node_id in analytical_ids}
        for relation in relations:
            source = str(relation.get("source") or "")
            target = str(relation.get("target") or "")
            if source in analytical_ids and target in analytical_ids:
                adjacency[source].add(target)
                adjacency[target].add(source)
        components = []
        unseen = set(analytical_ids)
        while unseen:
            start = unseen.pop()
            component = {start}
            pending = [start]
            while pending:
                current = pending.pop()
                for neighbor in adjacency.get(current, set()):
                    if neighbor in unseen:
                        unseen.remove(neighbor)
                        component.add(neighbor)
                        pending.append(neighbor)
            components.append(component)

        preferred_roots = (
            {"tracked_claim"} if mode_id == "claim_tracking"
            else {"methodology", "claim"} if mode_id in {
                "argument_map", "argument_map_enriched"
            }
            else {"tracked_claim", "methodology", "claim", "finding"}
        )

        focus_terms = {
            term for term in re.findall(r"[a-z0-9]{4,}", str(focus).lower())
            if term not in {"this", "that", "with", "from", "tracking"}
        }
        method_intent = bool(re.search(
            r"\b(how|track|tracking|measure|measuring|evaluate|evaluation|"
            r"implement|method|methodology|approach)\b",
            str(focus).lower(),
        ))
        solution_terms = {
            "approach", "framework", "method", "methodology", "measure",
            "measurement", "evaluate", "evaluation", "score", "pipeline",
        }

        def component_score(component):
            roles = {
                str(node_by_id[node_id].get("analysis_role") or "")
                for node_id in component
            }
            text = " ".join(
                str(
                    node_by_id[node_id].get("title")
                    or node_by_id[node_id].get("text") or ""
                ).lower()
                for node_id in component
            )
            edge_count = sum(
                1 for relation in relations
                if str(relation.get("source") or "") in component
                and str(relation.get("target") or "") in component
            )
            return (
                int(bool(roles & preferred_roots)),
                int(method_intent and any(term in text for term in solution_terms)),
                sum(1 for term in focus_terms if term in text),
                int("reasoning" in roles),
                edge_count,
                len(component),
            )

        keep_analytical = max(components, key=component_score)
        keep_ids = set(keep_analytical)
        # Retain evidence nodes only when they are linked to a kept analytical
        # node. Shared evidence can ground multiple branches without duplication.
        changed = True
        while changed:
            changed = False
            for relation in relations:
                source = str(relation.get("source") or "")
                target = str(relation.get("target") or "")
                if source in keep_ids and target in node_by_id and target not in keep_ids:
                    if node_by_id[target].get("analysis_role") == "evidence":
                        keep_ids.add(target)
                        changed = True
                if target in keep_ids and source in node_by_id and source not in keep_ids:
                    if node_by_id[source].get("analysis_role") == "evidence":
                        keep_ids.add(source)
                        changed = True
        kept_nodes = [
            node for node in nodes if str(node.get("id") or "") in keep_ids
        ]
        kept_relations = [
            relation for relation in relations
            if str(relation.get("source") or "") in keep_ids
            and str(relation.get("target") or "") in keep_ids
        ]
        return kept_nodes, kept_relations, len(nodes) - len(kept_nodes)

    @staticmethod
    def _mode_graph_is_structured(mode_id, nodes, relations, focus="") -> bool:
        if not nodes:
            return False
        roles = {str(node.get("analysis_role") or "") for node in nodes}
        analytical_ids = {
            str(node.get("id")) for node in nodes
            if node.get("analysis_role") != "evidence" and node.get("id")
        }
        analytical_links = [
            relation for relation in relations
            if str(relation.get("source") or "") in analytical_ids
            and str(relation.get("target") or "") in analytical_ids
        ]
        if mode_id in {"argument_map", "argument_map_enriched"}:
            node_by_id = {
                str(node.get("id")): node for node in nodes if node.get("id")
            }
            roots = analytical_ids - {
                str(relation.get("source") or "")
                for relation in analytical_links
            }
            root_nodes = [node_by_id[root_id] for root_id in roots]
            reasoning_count = sum(
                1 for node in nodes
                if node.get("analysis_role") == "reasoning"
            )
            evidence_ids = {
                str(node.get("id")) for node in nodes
                if node.get("analysis_role") == "evidence" and node.get("id")
            }
            grounded_targets = {
                str(relation.get("target") or "")
                for relation in relations
                if str(relation.get("source") or "") in evidence_ids
                and str(relation.get("target") or "") in analytical_ids
                and str(relation.get("type") or "") == "relation.supports"
            }
            method_intent = bool(re.search(
                r"\b(how|track|tracking|measure|measuring|evaluate|evaluation|"
                r"implement|method|methodology|approach)\b",
                str(focus).lower(),
            ))
            root_text = " ".join(
                str(node.get("title") or "") + " "
                + str(node.get("text") or "")
                for node in root_nodes
            ).lower()
            root_answers_method = (
                not method_intent
                or any(
                    node.get("analysis_role") == "methodology"
                    for node in root_nodes
                )
                or any(term in root_text for term in {
                    "approach", "framework", "method", "methodology",
                    "measure", "measurement", "evaluate", "score",
                })
            )
            return (
                bool(roles.intersection({"claim", "methodology"}))
                and len(roots) == 1
                and len(analytical_ids) >= 3
                and reasoning_count >= 2
                and "evidence" in roles
                and len(evidence_ids) >= 2
                and len(analytical_links) >= 2
                and analytical_ids.issubset(grounded_targets)
                and root_answers_method
            )
        if mode_id == "claim_tracking":
            return (
                "tracked_claim" in roles
                and bool(roles.intersection({
                    "supporting_claim", "contradicting_claim",
                    "qualification", "evidence",
                }))
                and bool(analytical_links)
            )
        return len(nodes) >= 2 and bool(relations) and (
            len(analytical_ids) <= 1 or bool(analytical_links)
        )

    @classmethod
    def _focused_fallback(cls, node_map, relations, focus: str, limit: int):
        """Return a small analysis-relevant projection, never the entity catalog."""
        focus_terms = {
            word for word in re.findall(r"[a-z0-9]{4,}", focus.lower())
            if word not in {"that", "this", "with", "from", "what", "when"}
        }
        allowed = {"claim", "reasoning", "evidence", "summary"}

        def role(node):
            return cls._slug(
                node.get("analysis_role") or node.get("type")
                or node.get("artifact_type")
            )

        def score(node):
            text = str(node.get("text") or node.get("title") or "").lower()
            overlap = sum(1 for term in focus_terms if term in text)
            role_rank = {
                "claim": 4, "reasoning": 3, "evidence": 3, "summary": 2
            }.get(role(node), 0)
            return (overlap, role_rank, float(node.get("centrality") or 0.0))

        candidates = [node for node in node_map.values() if role(node) in allowed]
        candidates.sort(key=score, reverse=True)
        selected = candidates[:max(3, min(limit, 24))]
        selected_ids = {node.get("id") for node in selected}
        kept_relations = [
            relation for relation in relations
            if isinstance(relation, dict)
            and relation.get("source") in selected_ids
            and relation.get("target") in selected_ids
            and relation.get("evidence_ids")
        ]
        return selected, kept_relations


class DeterministicGraphAnalysisStep(BaseStep):
    step_type = "DETERMINISTIC_GRAPH_ANALYSIS"
    label = "Deterministic Graph Analysis"
    category = "Intelligence"
    description = "Project a complete evidence-backed graph from canonical artifacts."
    input_schema = {
        "source_id": {"type": "string", "label": "Source ID"},
        "allowed_source_ids": {"type": "array", "label": "Source IDs"},
        "doc_path": {"type": "string", "label": "Document"},
        "mode_id": {"type": "string", "label": "Mode"},
        "analysis_focus": {"type": "string", "label": "Analysis focus"},
        'event_filters': {'type':'json','label':'Timeline event-query filters'},
        'event_cursor': {'type':'string','label':'Timeline continuation cursor'},
    }

    def execute(self, context: StepContext, inputs: dict):
        from core.services.intelligence.deterministic_graph_analysis_service import (
            DeterministicGraphAnalysisService,
        )
        source_ids = _list(inputs.get("allowed_source_ids"))
        source_id = str(inputs.get("source_id") or "")
        if not source_ids and source_id:
            source_ids = [source_id]
        if not source_ids and inputs.get("doc_path") and context.project_manager:
            source = context.project_manager.get_source_record_by_path(str(inputs["doc_path"]))
            if source:
                source_ids = [source.get("id")]
        state = context.state if isinstance(context.state, dict) else {}
        focus = str(
            inputs.get("analysis_focus")
            or state.get("analysis_focus")
            or state.get("claim")
            or ""
        )
        contract = state.get("analysis_graph_contract") or {}
        if isinstance(contract, str):
            try:
                contract = json.loads(contract)
            except Exception:
                contract = {}
        service = DeterministicGraphAnalysisService(
            project_manager=context.project_manager,
            artifact_service=context.artifact_service,
            ontology_registry=getattr(context, 'ontology_registry', None),
        )
        envelope = service.run(
            mode_id=str(inputs.get("mode_id") or "argument_map"),
            source_ids=[value for value in source_ids if value],
            params={
                **dict(inputs), "analysis_graph_contract": contract,
                "baseline_only": True,
            },
            persist=False,
        )
        envelope_dict = asdict(envelope)
        graph = envelope_dict.get("output") or {}
        if envelope.result_kind == 'timeline':
            return self.build_result(json.dumps(envelope_dict, ensure_ascii=False), state_updates={
                'deterministic_graph_output':graph, 'analysis_result_envelope':envelope_dict})
        from core.services.intelligence.analysis_evidence_preparation_service import (
            AnalysisEvidencePreparationService,
        )
        evidence = AnalysisEvidencePreparationService(
            project_manager=context.project_manager,
            artifact_service=context.artifact_service,
            hybrid_retrieval_service=context.hybrid_retrieval_service,
        )
        selection = (
            contract.get("evidence_selection") or {}
            if isinstance(contract, dict) else {}
        )
        evidence = evidence.prepare(
            mode_id=str(inputs.get("mode_id") or "argument_map"),
            focus=focus,
            source_ids=source_ids,
            graph_contract=contract if isinstance(contract, dict) else {},
            max_sections=int(selection.get("max_sections") or 10),
            quotes_per_section=int(selection.get("quotes_per_section") or 3),
            max_quotes=int(selection.get("max_quotes") or 24),
        )
        alias_by_artifact = {
            str(artifact_id): str(alias)
            for alias, artifact_id in evidence["alias_to_artifact_id"].items()
        }
        baseline = graph.get("argument_baseline") or {}
        baseline_context = {
            "quality": baseline.get("quality") or {},
            "nodes": [
                {
                    "id": node.get("id"),
                    "role": node.get("analysis_role"),
                    "statement": node.get("text"),
                    "q": [
                        alias_by_artifact[evidence_id]
                        for evidence_id in node.get("evidence_ids") or []
                        if evidence_id in alias_by_artifact
                    ],
                }
                for node in (baseline.get("entities") or [])
            ],
            "relations": [
                {
                    "source": relation.get("source"),
                    "target": relation.get("target"),
                    "type": relation.get("type"),
                }
                for relation in (baseline.get("relations") or [])
                if str(relation.get("source") or "").startswith("analysis-baseline")
                and str(relation.get("target") or "").startswith("analysis-baseline")
            ],
        }
        from core.events.domains.analysis_events import AnalysisEvent, AnalysisPayload
        return self.build_result(
            json.dumps(envelope_dict, ensure_ascii=False),
            state_updates={
                "analysis_result_envelope": envelope_dict,
                "deterministic_graph_output": graph,
                "deterministic_graph_context": evidence["context"],
                "deterministic_argument_baseline_context": json.dumps(
                    baseline_context, ensure_ascii=False, separators=(",", ":")
                ),
                "analysis_evidence_package": evidence["package"],
                "analysis_model_context_items": build_analysis_context_items(baseline, evidence),
                "analysis_evidence_aliases": evidence["alias_to_artifact_id"],
                "analysis_evidence_snapshots": evidence.get('artifact_snapshots') or {},
                "analysis_evidence_selection_aliases": (
                    evidence["alias_to_selection_artifact_id"]
                ),
                "analysis_evidence_selection_ids": (
                    evidence["selection_artifact_ids"]
                ),
                "analysis_evidence_focus_signature": evidence["focus_signature"],
                "analysis_evidence_diagnostics": {
                    "selected_count": evidence["selected_count"],
                    "section_count": evidence["section_count"],
                    "reused_count": evidence["reused_count"],
                    "focus_eligibility": evidence.get('focus_eligibility') or {},
                },
            },
            system_events=[self.bus_emit_event(
                "analysis_result_changed", AnalysisEvent.PROGRESS,
                AnalysisPayload(result={
                    "target_id": state.get("analysis_target_id"),
                    "message": "Grounded baseline prepared. Checking text and relation proposals with the selected model…",
                    "phase": "baseline",
                }),
            )],
        )


class AnalysisOutputWriteStep(BaseStep):
    """Persist a mode's visible result and reusable cache envelope."""

    step_type = "ANALYSIS_OUTPUT_WRITE"
    label = "Write Analysis Result"
    category = "Intelligence"
    description = "Persist an analysis result, evidence references, and model-use diagnostics."
    input_schema = {
        "mode": {"type": "string", "label": "Mode", "required": True},
        "result_kind": {"type": "string", "label": "Result kind"},
        "artifact_type": {"type": "string", "label": "Artifact type"},
        "output": {"type": "json", "label": "Analysis output (inline)"},
        "output_state_key": {"type": "string", "label": "Analysis output (opaque state key)"},
        "evidence": {"type": "json", "label": "Evidence"},
        "source_id": {"type": "string", "label": "Source"},
        "params_hash": {"type": "string", "label": "Parameters hash"},
        "doc_hash": {"type": "string", "label": "Document hash"},
        "model_role": {"type": "string", "label": "Model role"},
        "persist_artifact": {"type": "boolean", "label": "Persist artifact"},
    }

    def execute(self, context: StepContext, inputs: dict):
        service = getattr(context, "artifact_service", None)
        db = getattr(getattr(context, "project_manager", None), "db_intelligence", None)
        mode = _clean_placeholder(inputs.get("mode")) or "analysis"
        params_hash = _clean_placeholder(inputs.get("params_hash"))
        doc_hash = _clean_placeholder(inputs.get("doc_hash")) or "project"
        source_id = _clean_placeholder(inputs.get("source_id")) or None
        cache_parameters = context.state.get("analysis_cache_parameters")
        if isinstance(cache_parameters, dict):
            from core.services.intelligence.analysis_mode_service import AnalysisModeService

            params_hash = AnalysisModeService(
                project_manager=context.project_manager,
                prompt_manager=context.prompt_manager,
            )._params_hash(cache_parameters)
            context.state["params_hash"] = params_hash
        result_kind = _clean_placeholder(inputs.get("result_kind")) or "narrative"
        artifact_type = _clean_placeholder(inputs.get("artifact_type")) or "analysis_answer"
        model_role = _clean_placeholder(inputs.get("model_role")) or None
        output = _json_value(StateResolver.input_value(inputs, context.state, 'output'))
        evidence = _json_value(inputs.get("evidence"))
        output_quality = (
            dict(output.get("quality") or {}) if isinstance(output, dict) else {}
        )
        prompt_trace = context.state.get("llm_prompt_trace") or []
        model_call_count = (
            len(prompt_trace) if isinstance(prompt_trace, list)
            else (1 if model_role else 0)
        )
        exact_text = (
            output if isinstance(output, str)
            else json.dumps(output, ensure_ascii=False, default=str)
        )
        artifact_id = None
        persist_artifact = inputs.get("persist_artifact", True)
        if isinstance(persist_artifact, str):
            persist_artifact = persist_artifact.strip().lower() not in {"0", "false", "no"}
        if service and persist_artifact:
            artifact_id = service.create_artifact(
                source_id=source_id,
                artifact_type=artifact_type,
                storage_ref_type="analysis_mode",
                storage_ref_id=f"{mode}:{params_hash or doc_hash}",
                exact_text=str(exact_text or ""),
                metadata={
                    "stage": "analysis",
                    "mode_id": mode,
                    "result_kind": result_kind,
                    "evidence": evidence,
                    "quality": output_quality,
                    "prompt_version": "managed",
                    "model_call_count": model_call_count,
                },
                confidence=1.0 if output_quality.get("valid") else 0.7,
                created_by="validated_analysis",
                extractor_name=f"analysis_mode:{mode}",
                model_role=model_role,
                model_name=_clean_placeholder(context.state.get("selected_model")),
            )
        run_id = str(uuid.uuid4())
        envelope = {
            "run_id": run_id,
            "mode_id": mode,
            "result_kind": result_kind,
            "output_artifact_ids": [artifact_id] if artifact_id else [],
            "output": (
                output if isinstance(output, dict)
                else {"content": output, "evidence": evidence}
            ),
            "provenance": {
                "producer": f"analysis_mode:{mode}",
                "model_role": model_role,
                "model_name": _clean_placeholder(context.state.get("selected_model")),
            },
            "diagnostics": {
                "model_call_count": model_call_count,
                **(
                    dict(output.get("diagnostics") or {})
                    if isinstance(output, dict) else {}
                ),
            },
            "model_usage": {"call_count": model_call_count},
            "cache_status": "miss",
            "view_state": {},
            "quality": output_quality,
        }
        envelope["diagnostics"].update({
            "analysis_preparation": context.state.get("analysis_preparation"),
            "unsupported_sentences": (
                context.state.get("citation_validation", {})
                .get("unsupported_sentences", [])
                if isinstance(context.state.get("citation_validation"), dict)
                else []
            ),
        })
        if isinstance(envelope["output"], dict) and prompt_trace:
            envelope["output"]["prompt_trace"] = {
                "blueprint_id": context.blueprint_id,
                "calls": prompt_trace,
            }
        if db and mode and params_hash:
            db.save_analysis_run(mode, params_hash, doc_hash, envelope, source_id)
        visible_output = dict(envelope["output"])
        visible_output.update({
            "analysis_envelope": {
                key: value for key, value in envelope.items() if key != "output"
            },
            "target_id": context.state.get("analysis_target_id"),
        })
        from core.events.domains.analysis_events import AnalysisEvent, AnalysisPayload
        return self.build_result(
            json.dumps(envelope, ensure_ascii=False, default=str),
            state_updates={"analysis_result_envelope": envelope},
            system_events=[
                self.bus_emit_event(
                    "analysis_result_changed",
                    AnalysisEvent.RESULT_READY,
                    AnalysisPayload(
                        run_id=run_id,
                        template_id=mode,
                        result=visible_output,
                    ),
                ),
                self.bus_emit_event(
                    "analysis_result_changed",
                    AnalysisEvent.RUN_COMPLETED,
                    AnalysisPayload(
                        run_id=run_id,
                        template_id=mode,
                        result={
                            "target_id": context.state.get("analysis_target_id"),
                            "cached": False,
                        },
                    ),
                ),
            ],
        )


def _list(value: Any) -> List[str]:
    if value is None:
        return []
    if isinstance(value, list):
        return [str(item) for item in value if str(item).strip()]
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
            if isinstance(parsed, list):
                return _list(parsed)
        except Exception:
            pass
        return [part.strip() for part in value.split(",") if part.strip()]
    return [str(value)]


def _json_list(value: Any) -> List[Any]:
    if isinstance(value, list):
        return value
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
            return parsed if isinstance(parsed, list) else []
        except Exception:
            return []
    return []


def _json_value(value: Any) -> Any:
    if isinstance(value, str):
        try:
            return json.loads(value)
        except Exception:
            return value
    return value


def _clean_placeholder(value: Any) -> str:
    text = str(value or "").strip()
    return "" if text.startswith("{") and text.endswith("}") else text


def _plan(value: Any) -> RetrievalPlan | None:
    if isinstance(value, RetrievalPlan):
        return value
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except Exception:
            return None
    if isinstance(value, dict):
        fields = RetrievalPlan.__dataclass_fields__
        payload = {key: value.get(key) for key in fields if key in value}
        if isinstance(payload.get("source_scope"), dict):
            payload["source_scope"] = SourceScope(**payload["source_scope"])
        return RetrievalPlan(**payload)
    return None


def _retrieval_result(value: Any) -> RetrievalResult:
    if isinstance(value, RetrievalResult):
        return value
    if isinstance(value, dict):
        fields = RetrievalResult.__dataclass_fields__
        return RetrievalResult(**{key: value.get(key) for key in fields if key in value})
    return RetrievalResult(artifact_id="", artifact_type="", exact_text=str(value or ""))


def _result_dict(value: Any) -> dict:
    if is_dataclass(value):
        return asdict(value)
    if isinstance(value, dict):
        return value
    return {"text": str(value)}
