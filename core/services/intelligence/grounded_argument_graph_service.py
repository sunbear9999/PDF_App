"""Build a useful argument graph from canonical source artifacts without an LLM."""
from __future__ import annotations

import hashlib
import re
from typing import Any, Dict, Iterable, List

from core.models.intelligence_models import GraphQualityContract
from core.models.analysis_focus import focus_rows, topical_terms
from core.models.analysis_roles import analysis_role, analysis_entity_type, analysis_workspace_type
from core.ontology.registry import OntologyRegistry
from core.services.intelligence.analysis_grounding import artifact_snapshot
from core.services.extraction.sentence_segmentation_service import (
    normalize_extracted_text,
)


_ARGUMENT_ROLES = {
    "claim", "methodology", "reasoning", "counterclaim", "limitation",
}
_BOILERPLATE_RE = re.compile(
    r"\b(copyright|all rights reserved|correspondence to|address correspondence|"
    r"published online|disclosure|received a scholarship|speaker honoraria|"
    r"conflicts? of interest|remaining authors|"
    r"continuing medical education|cme article|doi:)\b",
    re.I,
)
_TERMINAL_RE = re.compile(
    r"[.!?](?:[\"'’”)\]]|[0-9¹²³⁰⁴-⁹,;:–-])*$"
)


class GroundedArgumentGraphService:
    """Select, connect, and measure canonical argument units."""

    PRODUCER_VERSION = "3.0"

    def __init__(self, ontology_registry=None):
        self.ontology = ontology_registry or OntologyRegistry()

    def build(
        self,
        rows: Iterable[Dict[str, Any]],
        edges: Iterable[Dict[str, Any]],
        *,
        mode_id: str,
        contract: Dict[str, Any],
        focus: str = "",
    ) -> Dict[str, Any]:
        rows, edges = list(rows), list(edges)
        quality_contract = GraphQualityContract.from_value(contract)
        units = [item for item in self._argument_units(rows) if self._usable_unit(item)]
        units, focus_diagnostics = focus_rows(units, focus, edges)
        propositions = [item for item in units if self._role(item) in _ARGUMENT_ROLES]
        evidence = [item for item in units if self._role(item) == "evidence"]
        selected_props = self._select_propositions(
            propositions, quality_contract, focus
        )
        selected_evidence = self._select_evidence(
            evidence, quality_contract, focus
        )
        nodes = [self._proposition_node(item, mode_id) for item in selected_props
                 if self._role(item) in quality_contract.node_types]
        if 'evidence' in quality_contract.node_types:
            nodes.extend(self._evidence_node(item, mode_id) for item in selected_evidence)
        if focus and 'tracked_claim' in quality_contract.root_roles:
            nodes.insert(0, self._user_focus_node(focus, mode_id))
        self._apply_types(nodes, contract)
        relations = self._relations(
            selected_props, selected_evidence, edges, nodes,
            mode_id=mode_id, relation_types=set(quality_contract.relation_types),
            min_branches=quality_contract.min_branches,
            root_roles=set(quality_contract.root_roles),
        )
        metrics = self.measure(nodes, relations, quality_contract)
        metrics.update({
            "available_proposition_count": len(propositions),
            "available_evidence_count": len(evidence),
            "requested_density": quality_contract.density,
            "origin": "deterministic_baseline",
            "valid": bool(nodes),
            "limited_coverage": not metrics["meets_requested_contract"],
            "focus_eligibility": focus_diagnostics,
            "unmapped_proposition_count": sum(
                self._role(item) not in quality_contract.node_types for item in selected_props),
        })
        return {
            "entities": nodes,
            "relations": relations,
            "quality": metrics,
            "artifact_snapshots": self._snapshots(rows, nodes),
            "producer": "grounded_argument_graph",
            "producer_version": self.PRODUCER_VERSION,
        }

    @classmethod
    def measure(
        cls,
        nodes: List[Dict[str, Any]],
        relations: List[Dict[str, Any]],
        contract: GraphQualityContract,
    ) -> Dict[str, Any]:
        from core.services.intelligence.argument_graph_quality import (
            measure_argument_graph,
        )

        return measure_argument_graph(nodes, relations, contract)

    @staticmethod
    def _snapshots(rows, nodes):
        identities = {identity for node in nodes for identity in (
            node.get('argument_unit_id'), *(node.get('evidence_ids') or [])) if identity}
        return {row['artifact_id']: artifact_snapshot(row) for row in rows
                if row['artifact_id'] in identities}

    def _argument_units(self, rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        units = [row for row in rows if row.get("artifact_type") == "argument_unit"]
        if units:
            return units
        # Compatibility fallback for projects not yet lazily backfilled.
        result = []
        for row in rows:
            if row.get("artifact_type") not in {"sentence", "transcript_segment"}:
                continue
            metadata = row.get("metadata") or {}
            role = str(metadata.get("class_label") or "unknown")
            if role == "unknown":
                continue
            result.append({
                **row,
                "metadata": {
                    **metadata,
                    "argument_role": role,
                    "canonical_source_artifact_id": row.get("artifact_id"),
                },
            })
        return result

    def _select_propositions(
        self,
        rows: List[Dict[str, Any]],
        contract: GraphQualityContract,
        focus: str,
    ) -> List[Dict[str, Any]]:
        if not rows:
            return []
        focus_terms = self._terms(focus)
        ranked = sorted(
            rows,
            key=lambda row: self._score(row, focus_terms, proposition=True),
            reverse=True,
        )
        selected: List[Dict[str, Any]] = []
        selected_ids = set()
        target = self._target_proposition_count(contract, len(rows))

        def add(row: Dict[str, Any]) -> None:
            key = str(row.get("artifact_id") or "")
            if key and key not in selected_ids and len(selected) < target:
                selected.append(row)
                selected_ids.add(key)

        root = next(
            (row for row in ranked if self._role(row) in {"methodology", "claim"}),
            ranked[0],
        )
        add(root)
        for required_role, minimum in (
            ("reasoning", 4), ("claim", 4), ("methodology", 1),
            ("counterclaim", 1), ("limitation", 1),
        ):
            for row in (item for item in ranked if self._role(item) == required_role):
                if sum(self._role(item) == required_role for item in selected) >= minimum:
                    break
                add(row)
        by_section: Dict[str, List[Dict[str, Any]]] = {}
        for row in ranked:
            by_section.setdefault(self._section(row), []).append(row)
        while len(selected) < target:
            changed = False
            for section in sorted(by_section):
                candidate = next(
                    (row for row in by_section[section]
                     if str(row.get("artifact_id")) not in selected_ids),
                    None,
                )
                if candidate:
                    add(candidate)
                    changed = True
                if len(selected) >= target:
                    break
            if not changed:
                break
        ordered = sorted(selected, key=self._reading_order)
        return [root, *[item for item in ordered if item is not root]]

    def _select_evidence(
        self,
        rows: List[Dict[str, Any]],
        contract: GraphQualityContract,
        focus: str,
    ) -> List[Dict[str, Any]]:
        focus_terms = self._terms(focus)
        ranked = sorted(
            rows,
            key=lambda row: self._score(row, focus_terms, proposition=False),
            reverse=True,
        )
        selected = []
        target = self._target_evidence_count(contract, len(rows))
        section_counts: Dict[str, int] = {}
        for row in ranked:
            section = self._section(row)
            if section_counts.get(section, 0) >= 3:
                continue
            selected.append(row)
            section_counts[section] = section_counts.get(section, 0) + 1
            if len(selected) >= target:
                break
        return sorted(selected, key=self._reading_order)

    def _relations(
        self,
        propositions: List[Dict[str, Any]],
        evidence: List[Dict[str, Any]],
        stored_edges: List[Dict[str, Any]],
        nodes: List[Dict[str, Any]],
        *,
        mode_id: str,
        relation_types: set[str],
        min_branches: int,
        root_roles: set[str],
    ) -> List[Dict[str, Any]]:
        from core.services.intelligence.argument_graph_relations import (
            build_argument_relations,
        )

        return build_argument_relations(
            self, stored_edges, nodes, mode_id=mode_id,
            relation_types=relation_types, min_branches=min_branches,
            root_roles=root_roles,
        )

    def _proposition_node(
        self, row: Dict[str, Any], mode_id: str, *, role: str = ""
    ) -> Dict[str, Any]:
        role = role or self._role(row)
        text = normalize_extracted_text(str(row.get("exact_text") or ""))
        source_artifact_id = str(
            (row.get("metadata") or {}).get("canonical_source_artifact_id")
            or row.get("parent_artifact_id") or row.get("artifact_id") or ""
        )
        return self._node(
            row, mode_id, role, text, self._preview(text, 96), source_artifact_id,
            exact_text="",
        )

    def _user_focus_node(self, focus, mode_id):
        """A user question is separate from, and never impersonates, a source unit."""
        identity = hashlib.sha1(focus.encode()).hexdigest()[:14]
        node = self._node({'artifact_id': f'user-focus:{identity}'}, mode_id,
                          'tracked_claim', focus, focus[:120], '', exact_text='')
        node.update(evidence_ids=[], primary_evidence_id=None, grounding='user_input',
                    argument_unit_id=None)
        node['properties'].update(evidence_ids=[], primary_evidence_id=None)
        return node

    def _evidence_node(self, row: Dict[str, Any], mode_id: str) -> Dict[str, Any]:
        text = str(row.get("exact_text") or "")
        source_artifact_id = str(
            (row.get("metadata") or {}).get("canonical_source_artifact_id")
            or row.get("parent_artifact_id") or row.get("artifact_id") or ""
        )
        heading = self._section(row)
        preview = self._preview(text, 96)
        title = f"{heading}: “{preview}”"
        return self._node(
            row, mode_id, "evidence", text, title,
            source_artifact_id, exact_text=text,
        )

    def _node(
        self, row, mode_id, role, text, title, source_artifact_id, *, exact_text
    ) -> Dict[str, Any]:
        unit_id = str(row.get("artifact_id") or source_artifact_id)
        digest = hashlib.sha1(f"{mode_id}|{role}|{unit_id}".encode()).hexdigest()[:14]
        node_id = f"analysis-baseline:{mode_id}:{digest}"
        payload = {
            "id": node_id, "temp_id": node_id,
            "type": analysis_entity_type(role),
            "artifact_type": "analysis_node", "analysis_role": role,
            "title": title, "text": text, "exact_text": exact_text,
            "quote": exact_text, "argument_unit_id": unit_id,
            "evidence_ids": [source_artifact_id],
            "primary_evidence_id": source_artifact_id,
            "source_id": row.get("source_id"),
            "locator": row.get("locator") or {},
            "section_path": row.get("section_path") or [],
            "section_label": self._section(row),
            "confidence": row.get("confidence"),
            "review_state": row.get("review_state") or "unreviewed",
            "suggested": False, "grounding": "artifact", "baseline": True,
            "artifact_revision": row.get("revision") or 1,
            "artifact_payload_hash": row.get("payload_hash"),
        }
        payload["properties"] = {
            key: payload[key] for key in (
                "title", "text", "exact_text", "quote", "evidence_ids",
                "primary_evidence_id", "analysis_role", "source_id", "locator",
                "section_label",
            )
        }
        return payload

    @staticmethod
    def _role(row):
        metadata = row.get("metadata") or {}
        return str(metadata.get("argument_role") or metadata.get("class_label") or "unknown")

    @staticmethod
    def _section(row):
        path = [
            " ".join(str(value).split()) for value in row.get("section_path") or []
            if str(value).strip()
        ]
        useful = [
            value for value in path
            if len(value) <= 90 and value.lower() not in {"cme article", "abstract"}
            and not value.endswith("?")
        ]
        if useful:
            return useful[-1]
        page = (row.get("locator") or {}).get("page")
        return f"Page {page + 1}" if isinstance(page, int) else "Document"

    @staticmethod
    def _node_section(node):
        if node.get("section_label"):
            return str(node["section_label"])
        page = (node.get("locator") or {}).get("page")
        return f"Page {page + 1}" if isinstance(page, int) else "Document"

    @staticmethod
    def _reading_order(row):
        locator = row.get("locator") or {}
        return (
            str(row.get("source_id") or ""),
            locator.get("page") if isinstance(locator.get("page"), int) else 10**9,
            locator.get("char_start") if isinstance(locator.get("char_start"), int) else 10**9,
            str(row.get("artifact_id") or ""),
        )

    @classmethod
    def _score(cls, row, focus_terms, *, proposition):
        role = cls._role(row)
        text = normalize_extracted_text(str(row.get("exact_text") or ""))
        terms = cls._terms(text)
        word_count = len(text.split())
        confidence = float(row.get("confidence") or 0.0)
        role_score = {
            "claim": 2.2, "methodology": 2.3, "reasoning": 2.1,
            "counterclaim": 1.8, "limitation": 1.9, "evidence": 2.2,
        }.get(role, 0.0)
        return (
            cls._overlap(terms, focus_terms) * 4 + role_score + confidence,
            int(bool(row.get("section_path"))),
            -abs(word_count - 26) if proposition else confidence,
        )

    @staticmethod
    def _target_proposition_count(
        contract: GraphQualityContract, available: int
    ) -> int:
        preferred = {
            "focused": 16, "standard": 24, "exhaustive": 56,
        }.get(str(contract.density), contract.min_proposition_nodes)
        target = max(contract.min_proposition_nodes, preferred)
        return min(available, contract.max_proposition_nodes, target)

    @staticmethod
    def _target_evidence_count(
        contract: GraphQualityContract, available: int
    ) -> int:
        preferred = {
            "focused": 8, "standard": 12, "exhaustive": 24,
        }.get(str(contract.density), contract.min_evidence_nodes)
        target = max(contract.min_evidence_nodes, preferred)
        return min(available, contract.max_evidence_nodes, target)

    @classmethod
    def _usable_unit(cls, row: Dict[str, Any]) -> bool:
        metadata = row.get("metadata") or {}
        text = normalize_extracted_text(str(row.get("exact_text") or ""))
        words = re.findall(r"[A-Za-zÀ-ɏ]{2,}", text)
        section = " ".join(row.get("section_path") or []).lower()
        block_type = str(metadata.get("block_type") or "").lower()
        if metadata.get("is_fragment") or block_type in {
            "heading", "caption", "footnote", "bibliography",
        }:
            return False
        if len(words) < 6 or len(text) < 32 or _BOILERPLATE_RE.search(text):
            return False
        if re.match(r"^from\s+.{0,180}\b(hospital|university|institute)\b", text, re.I):
            return False
        if block_type == "list" and re.search(
            r"(^\d+[.)]\s|\bet\s+al\.?\b|https?://|\bdoi\b)", text, re.I
        ):
            return False
        if re.search(r"\b(references|bibliography|author affiliations?)\b", section):
            return False
        if text[:1].islower() or not _TERMINAL_RE.search(text.rstrip()):
            return False
        alpha = sum(char.isalpha() for char in text)
        return alpha / max(1, len(text)) >= 0.55

    @staticmethod
    def _preview(text: str, limit: int) -> str:
        text = str(text or "").strip()
        if len(text) <= limit:
            return text
        shortened = text[:limit].rsplit(" ", 1)[0].rstrip(" ,;:-")
        return (shortened or text[:limit].rstrip()) + "…"

    @staticmethod
    def _terms(text):
        return topical_terms(text)

    @staticmethod
    def _overlap(left, right):
        return len(left & right) if right else 0

    def _apply_types(self, nodes, contract):
        for node in nodes:
            role = node['analysis_role']
            entity = analysis_entity_type(role, contract.get('node_type_map'))
            if (entity not in self.ontology.entities or
                analysis_role(entity) != analysis_role(analysis_entity_type(role))):
                raise ValueError(f"Invalid compiled entity type for role '{role}'.")
            node['type'] = entity
            node['node_type_id'] = (contract.get('workspace_type_map') or {}).get(role) or analysis_workspace_type(entity)
