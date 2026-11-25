"""Artifact-backed graph and timeline projections with zero generative calls."""
from __future__ import annotations

from dataclasses import asdict
import hashlib
import json
import re
import uuid
from typing import Any, Dict, Iterable, List, Optional

from core.models.intelligence_models import (
    AnalysisResultEnvelope,
    ArtifactQuery,
    SourceScope,
)
from core.services.intelligence.artifact_eligibility import (
    active_assertions, is_current_artifact,
)
from core.services.extraction.citation_extraction_service import InTextCitationExtractor
from core.services.extraction.date_context import legacy_timeline_eligible


class DeterministicGraphAnalysisService:
    """Materialize complete evidence-backed analysis views from artifacts.

    This service deliberately does not know about an LLM manager. Optional AI
    naming/enrichment is a separate, explicitly invoked blueprint.
    """

    PRODUCER_VERSION = "4.0"
    NODE_TYPES = {
        "sentence", "transcript_segment", "entity", "date_mention",
        "citation_entry", "in_text_citation", "table", "annotation", "summary",
        "argument_unit",
    }
    REQUIRED_STAGES = ("parsed_blocks", "sections", "claims")

    def __init__(self, project_manager=None, artifact_service=None, stage_registry=None,
                 citation_extractor=None, ontology_registry=None):
        self.project_manager = project_manager
        self.artifact_service = artifact_service
        self.stage_registry = stage_registry
        self._citations = citation_extractor or InTextCitationExtractor()
        self.ontology_registry = ontology_registry

    @property
    def db(self):
        return getattr(self.project_manager, "db_intelligence", None)

    def readiness(self, source_ids: Optional[List[str]] = None) -> Dict[str, Any]:
        db = self.db
        if not db:
            return {"ready": False, "missing": [{"reason": "artifact_database_unavailable"}]}
        selected = list(source_ids or [])
        if not selected and self.project_manager and hasattr(self.project_manager, "list_sources"):
            selected = [str(s.get("id")) for s in self.project_manager.list_sources() if s.get("id")]
        missing = []
        for source_id in selected:
            statuses = db.get_stage_statuses(source_id)
            for stage in self.REQUIRED_STAGES:
                status = (statuses.get(stage) or {}).get("status")
                if status != "complete":
                    missing.append({"source_id": source_id, "stage": stage, "status": status or "not_run"})
        return {"ready": not missing, "missing": missing, "source_ids": selected}

    def run(
        self,
        *,
        mode_id: str = "argument_map",
        source_ids: Optional[List[str]] = None,
        params: Optional[Dict[str, Any]] = None,
        persist: bool = True,
    ) -> AnalysisResultEnvelope:
        result_kind = "timeline" if mode_id == "timeline" else "graph"
        db = self.db
        if not db or not self.artifact_service:
            return AnalysisResultEnvelope(
                run_id=str(uuid.uuid4()), mode_id=mode_id, result_kind=result_kind,
                diagnostics={"error": "Artifact intelligence is unavailable."},
            )
        params = dict(params or {})
        baseline_only = bool(params.get("baseline_only"))
        source_ids = list(source_ids or params.get("allowed_source_ids") or [])
        if mode_id == 'timeline':
            from core.services.intelligence.timeline_analysis import run
            return run(self, source_ids, params, persist)
        ready = self.readiness(source_ids)
        rows = self._load_artifact_rows(
            source_ids, max(1, int(params.get("artifact_limit") or 100000))
        )
        if source_ids:
            from itertools import islice
            entities = self.db.iter_entities(ArtifactQuery(
                source_scope=SourceScope(source_ids=source_ids), statuses=["complete"],
                review_states=["unreviewed", "verified", "corrected"], limit=1000, mention_limit=0,
            ))
            entities = list(islice(entities, int(params.get("artifact_limit") or 100000)))
            rows = list({row["artifact_id"]: row for row in [*rows, *entities]}.values())
        row_index = {row.get("artifact_id"): row for row in rows}
        rows = [
            row for row in rows
            if is_current_artifact(db, row, parent_cache=row_index)
        ]

        has_argument_units = any(
            row.get("artifact_type") == "argument_unit" for row in rows
        )
        if baseline_only:
            nodes = []
            edges = self._edges({row['artifact_id'] for row in rows}, reviewed_only=True)
            timeline, claims = self._timeline(rows), []
        else:
            node_rows = [
                row for row in rows if self._include_node(row)
                and not (
                    has_argument_units
                    and row.get("artifact_type") in {"sentence", "transcript_segment"}
                )
            ]
            nodes = [self._node(row) for row in node_rows]
            node_ids = {node["id"] for node in nodes}
            edges = self._edges(node_ids, {row['artifact_id'] for row in rows})
            accepted_edges = [edge for edge in edges if edge.get("verified")]
            communities = self._communities(nodes, accepted_edges)
            centrality = self._centrality(nodes, accepted_edges)
            for node in nodes:
                node["community_id"] = communities.get(node["id"], "")
                node["centrality"] = centrality.get(node["id"], 0.0)
            nodes.sort(
                key=lambda node: (-node["centrality"], node["type"], node["id"])
            )
            timeline = self._timeline(rows)
            claims = self._claim_chains(nodes, accepted_edges)
        graph_contract = params.get("analysis_graph_contract") or params.get("graph_contract") or {}
        if isinstance(graph_contract, str):
            try:
                graph_contract = json.loads(graph_contract)
            except Exception:
                graph_contract = {}
        from core.registries.analysis_mode_registry import normalize_graph_contract
        from core.services.intelligence.grounded_argument_graph_service import (
            GroundedArgumentGraphService,
        )
        grounded_baseline = GroundedArgumentGraphService(self.ontology_registry).build(
            rows,
            [edge for edge in edges if edge.get("verified")],
            mode_id=mode_id,
            contract=normalize_graph_contract(graph_contract),
            focus=str(params.get("analysis_focus") or params.get("claim") or ""),
        )
        if baseline_only:
            # Surface the actual usable graph at the top level. This keeps step
            # diagnostics honest (rather than logging an apparently empty
            # projection) and gives validation direct access to baseline IDs.
            nodes = list(grounded_baseline.get("entities") or [])
            edges = list(grounded_baseline.get("relations") or [])
        result = {
            "entities": nodes,
            "relations": edges,
            "communities": [] if baseline_only else self._community_summaries(nodes),
            "timeline": timeline,
            "claim_chains": claims,
            "argument_baseline": grounded_baseline,
            "readiness": ready,
            "complete_node_count": len(nodes),
            "complete_relation_count": len(edges),
            "visible_node_ids": [node["id"] for node in nodes[:200]],
            "has_more_nodes": len(nodes) > 200,
        }
        run_id = str(uuid.uuid4())
        artifact_id = None
        if persist:
            artifact_id = self.artifact_service.create_artifact(
                source_id=source_ids[0] if len(source_ids) == 1 else None,
                artifact_type="analysis_result",
                storage_ref_type="deterministic_analysis",
                storage_ref_id=self._storage_key(mode_id, source_ids, params),
                exact_text=json.dumps(result, ensure_ascii=False),
                metadata={
                    "stage": "analysis",
                    "mode_id": mode_id,
                    "result_kind": result_kind,
                    "producer_version": self.PRODUCER_VERSION,
                    "source_ids": source_ids,
                    "model_call_count": 0,
                },
                confidence=1.0,
                created_by="deterministic_graph_analysis",
                extractor_name="deterministic_graph_projection",
            )
        return AnalysisResultEnvelope(
            run_id=run_id,
            mode_id=mode_id,
            result_kind=result_kind,
            output_artifact_ids=[artifact_id] if artifact_id else [],
            output=result,
            provenance={
                "producer": "deterministic_graph_projection",
                "producer_version": self.PRODUCER_VERSION,
                "source_ids": source_ids,
            },
            diagnostics={
                "readiness": ready,
                "model_call_count": 0,
                "unsupported_edge_count": 0,
                "baseline_quality": grounded_baseline.get("quality") or {},
            },
            model_usage={"call_count": 0},
            view_state={
                "initial_visible_limit": 200,
                "visible_node_ids": result["visible_node_ids"],
                "complete_node_count": len(nodes),
            },
            quality=grounded_baseline.get("quality") or {},
        )

    def _load_artifact_rows(self, source_ids: List[str], limit: int) -> List[Dict[str, Any]]:
        from itertools import islice
        from core.db.intelligence_artifact_queries import iterate
        query = ArtifactQuery(
            source_scope=SourceScope(source_ids=source_ids), artifact_types=sorted(self.NODE_TYPES),
            statuses=["complete"], review_states=["unreviewed", "verified", "corrected"], current_only=True,
        )
        return list(islice(iterate(self.artifact_service, query), limit))

    def project_graph(self, rows):
        """Bounded browsing projection; full analysis retains its separate builder."""
        from core.services.intelligence.artifact_eligibility import is_current_artifact
        eligible = [row for row in rows if self._include_node(row) and is_current_artifact(self.db, row)]
        nodes = [self._node(row) for row in eligible]
        return {"nodes": nodes, "edges": self._edges({node["id"] for node in nodes})}

    def _include_node(self, row: Dict[str, Any]) -> bool:
        if row.get("artifact_type") in {"sentence", "transcript_segment"}:
            return (row.get("metadata") or {}).get("class_label") in {
                "claim", "evidence", "reasoning",
            }
        if row.get("artifact_type") == "argument_unit":
            return (row.get("metadata") or {}).get("argument_role") != "unknown"
        return True

    @staticmethod
    def _node(row: Dict[str, Any]) -> Dict[str, Any]:
        meta = row.get("metadata") or {}
        return {
            "id": row.get("artifact_id"),
            "artifact_id": row.get("artifact_id"),
            "type": (
                meta.get("entity_type") or meta.get("argument_role")
                or meta.get("class_label") or row.get("artifact_type")
            ),
            "artifact_type": row.get("artifact_type"),
            "title": meta.get("canonical_label") or (row.get("exact_text") or "")[:120],
            "text": row.get("exact_text") or row.get("normalized_text") or "",
            "source_id": row.get("source_id"),
            "locator": row.get("locator") or {},
            "section_path": row.get("section_path") or [],
            "confidence": row.get("confidence"),
            "review_state": row.get("review_state") or "unreviewed",
            "provenance": {
                "created_by": row.get("created_by"),
                "extractor_name": row.get("extractor_name"),
                "extractor_version": row.get("extractor_version"),
                "revision": row.get("revision") or 1,
            },
        }

    def _edges(self, node_ids: set[str], evidence_scope=None, *, reviewed_only=False) -> List[Dict[str, Any]]:
        from core.services.intelligence.analysis_grounding import reviewed_relations
        return reviewed_relations(self.db, node_ids, evidence_scope, reviewed_only=reviewed_only)

    @staticmethod
    def _communities(nodes: List[Dict[str, Any]], edges: List[Dict[str, Any]]) -> Dict[str, str]:
        parent = {node["id"]: node["id"] for node in nodes}

        def find(value: str) -> str:
            while parent[value] != value:
                parent[value] = parent[parent[value]]
                value = parent[value]
            return value

        for edge in edges:
            left, right = find(edge["source"]), find(edge["target"])
            if left != right:
                parent[max(left, right)] = min(left, right)
        roots = {node_id: find(node_id) for node_id in parent}
        labels = {root: f"community:{index + 1}" for index, root in enumerate(sorted(set(roots.values())))}
        return {node_id: labels[root] for node_id, root in roots.items()}

    @staticmethod
    def _centrality(nodes: List[Dict[str, Any]], edges: List[Dict[str, Any]]) -> Dict[str, float]:
        degree = {node["id"]: 0 for node in nodes}
        for edge in edges:
            degree[edge["source"]] += 1
            degree[edge["target"]] += 1
        denominator = max(1, len(nodes) - 1)
        return {node_id: value / denominator for node_id, value in degree.items()}

    @staticmethod
    def _community_summaries(nodes: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        groups: Dict[str, List[Dict[str, Any]]] = {}
        for node in nodes:
            groups.setdefault(node.get("community_id") or "", []).append(node)
        return [
            {
                "id": community_id,
                "node_count": len(items),
                "central_node_ids": [item["id"] for item in sorted(
                    items, key=lambda item: (-item.get("centrality", 0.0), item["id"])
                )[:5]],
            }
            for community_id, items in sorted(groups.items())
        ]

    def _timeline(self, rows: Iterable[Dict[str, Any]]) -> List[Dict[str, Any]]:
        return self.project_timeline(rows, self._citations)

    def project_timeline(self, rows, citations=None):
        """Project a bounded page of dates using the same timeline eligibility."""
        rows = list(rows)
        row_map = {row.get("artifact_id"): row for row in rows}
        items = []
        for row in rows:
            if row.get("artifact_type") != "date_mention":
                continue
            if not legacy_timeline_eligible(row, citations or self._citations):
                continue
            meta = row.get("metadata") or {}
            normalized = meta.get("normalized_date") or row.get("normalized_text") or ""
            displayed_date = normalized or meta.get("exact_text") or ""
            precision = meta.get("precision") or (
                "day" if re.fullmatch(r"\d{4}-\d{2}-\d{2}", normalized)
                else "month" if re.fullmatch(r"\d{4}-\d{2}", normalized)
                else "year" if re.fullmatch(r"\d{4}", normalized)
                else "unresolved"
            )
            linked_ids = []
            for edge in self.db.list_edges(row.get("artifact_id")) if self.db else []:
                if not active_assertions(edge):
                    continue
                other = (
                    edge.get("target_artifact_id")
                    if edge.get("source_artifact_id") == row.get("artifact_id")
                    else edge.get("source_artifact_id")
                )
                if other and other in row_map:
                    linked_ids.append(other)
            items.append({
                "artifact_id": row.get("artifact_id"),
                "date": displayed_date,
                "precision": precision,
                "text": row.get("exact_text") or "",
                "source_id": row.get("source_id"),
                "locator": row.get("locator") or {},
                "certainty": meta.get("certainty") or "unknown",
                "context_role": meta.get("context_role") or "event",
                "review_state": row.get("review_state") or "unreviewed",
                "linked_artifact_ids": sorted(set(linked_ids)),
                "interpretations": meta.get("interpretations") or [displayed_date],
                "conflicting": len(set(meta.get("interpretations") or [displayed_date])) > 1,
            })
        return sorted(items, key=lambda item: (not bool(item["date"]), item["date"], item["artifact_id"]))

    @staticmethod
    def _claim_chains(nodes: List[Dict[str, Any]], edges: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        node_map = {node["id"]: node for node in nodes}
        claims = []
        for node in nodes:
            if node.get("type") != "claim":
                continue
            related = [
                edge for edge in edges
                if edge["source"] == node["id"] or edge["target"] == node["id"]
            ]
            claims.append({
                "artifact_id": node["id"],
                "claim_artifact_id": node["id"],
                "text": node["text"],
                "source_id": node.get("source_id"),
                "locator": node.get("locator") or {},
                "review_state": node.get("review_state") or "unreviewed",
                "relations": related,
                "source_diversity": len({
                    node_map.get(
                        edge["target"] if edge["source"] == node["id"] else edge["source"], {}
                    ).get("source_id")
                    for edge in related
                    if node_map.get(
                        edge["target"] if edge["source"] == node["id"] else edge["source"], {}
                    ).get("source_id")
                }),
                "unsupported": not any(edge["type"] == "supports" for edge in related),
            })
        return claims

    @staticmethod
    def _storage_key(mode_id: str, source_ids: List[str], params: Dict[str, Any]) -> str:
        raw = json.dumps(
            {"mode": mode_id, "sources": sorted(source_ids), "params": params},
            sort_keys=True, ensure_ascii=False, default=str,
        )
        return hashlib.sha256(raw.encode("utf-8", "replace")).hexdigest()
