"""Graph Seeds stage: grounded relation *suggestions* between argument units.

Discourse-marker scores propose a relation; an installed NLI model may
re-score uncertain pairs. Edges carry the scorer that produced them and stay
suggestions until reviewed.
"""
from __future__ import annotations

from bisect import bisect_left
import re
from typing import Any, Dict, List

from core.models.intelligence_models import ProcessingStage, StageStatus
from core.models.producer_contract import ProducerContext, ProducerResult
from core.models.producer_models import MethodKind, ProducerIdentity
from core.services.intelligence.artifact_eligibility import is_current_artifact
from core.services.intelligence.argument_citation_evidence import citation_evidence
from core.services.intelligence.discourse_relation_scorer import (
    SCORER_ID, score_relation,
)

_ROLES = ("claim", "methodology", "evidence", "reasoning", "counterclaim", "limitation")
_HIGH = 0.65
_SECOND_CHOICE = 0.78
_NLI_ACCEPT = 0.72
_MAX_NLI_PAIRS = 256
_ROLE_RELATION = {"counterclaim": "attacks", "limitation": "critiques"}


class GraphSeedsProducer:
    producer_id = "graph_seeds"

    def __init__(self, project_manager: Any, artifact_service: Any, nli_scorer: Any) -> None:
        self._pm = project_manager
        self._artifacts = artifact_service
        self._nli = nli_scorer

    def identity(self, context: ProducerContext) -> ProducerIdentity:
        if self._nli and self._nli.is_installed():
            marker = self._nli.model_marker()
            return ProducerIdentity(
                producer_id=self.producer_id, producer_version=context.producer_version,
                method_kind=MethodKind.LEARNED.value, backend="discourse+onnx_nli",
                model_id=str(marker.get("repository") or "nli-model"),
                model_version=str(marker.get("revision") or ""),
            )
        return ProducerIdentity(
            producer_id=self.producer_id, producer_version=context.producer_version,
            method_kind=MethodKind.HEURISTIC.value, backend=SCORER_ID,
        )

    def run(self, context: ProducerContext) -> ProducerResult:
        db = getattr(self._pm, "db_intelligence", None)
        if db is None or not self._artifacts:
            return ProducerResult()
        units = self._artifacts.get_all_artifacts(context.source_id, {
            "artifact_type": "argument_unit", "status": StageStatus.COMPLETE.value,
        })
        units = [unit for unit in units if is_current_artifact(db, unit)]
        candidates = _candidates(_by_section(units))
        citations = citation_evidence(self._artifacts, context.source_id, units, context.cancelled)
        context.raise_if_cancelled()
        self._rescore_uncertain(candidates)
        identity = self.identity(context)
        written: List[str] = []
        for candidate in candidates:
            context.raise_if_cancelled()
            candidate["citation_evidence"] = citations.get(candidate["supporter"]["artifact_id"], [])
            if candidate["confidence"] >= _HIGH:
                edge_id = self._publish(candidate, identity)
                if edge_id:
                    written.append(edge_id)
        return ProducerResult(output_ids=written, identity=identity)

    def _rescore_uncertain(self, candidates: List[Dict[str, Any]]) -> None:
        uncertain = [c for c in candidates if c["confidence"] < _HIGH][:_MAX_NLI_PAIRS]
        if not uncertain or not self._nli or not self._nli.is_available():
            return
        # (premise, hypothesis): does the supporting unit entail the claim?
        scores = self._nli.score_pairs([
            (c["supporter"].get("exact_text", ""), c["claim"].get("exact_text", ""))
            for c in uncertain
        ])
        for candidate, score in zip(uncertain, scores):
            label = str(score.get("label") or "")
            confidence = float(score.get("confidence") or 0.0)
            if label in {"entailment", "contradiction"} and confidence >= _NLI_ACCEPT:
                candidate.update(
                    relation="supports" if label == "entailment" else "attacks",
                    confidence=confidence, scorer="onnx_nli",
                )

    def _publish(self, candidate: Dict[str, Any], identity: ProducerIdentity) -> str:
        supporter, claim = candidate["supporter"], candidate["claim"]
        return self._artifacts.create_edge(
            supporter["artifact_id"], claim["artifact_id"], candidate["relation"],
            confidence=candidate["confidence"], created_by="graph_seeds",
            metadata={
                "scorer": candidate["scorer"], "stage": ProcessingStage.GRAPH_SEEDS.value,
                "evidence_artifact_ids": [supporter["artifact_id"], claim["artifact_id"],
                    str(supporter.get("parent_artifact_id") or supporter["artifact_id"]),
                    *candidate.get("citation_evidence", [])],
                "citation_evidence": candidate.get("citation_evidence", []), "suggested": True,
                "producer_version": identity.producer_version, "producer": identity.to_metadata(),
            },
        )


def _by_section(units) -> Dict[str, Dict[str, List[Dict[str, Any]]]]:
    groups: Dict[str, Dict[str, List[Dict[str, Any]]]] = {}
    for unit in units:
        role = (unit.get("metadata") or {}).get("argument_role")
        if role in _ROLES:
            key = " > ".join(unit.get("section_path") or []) or "doc"
            groups.setdefault(key, {name: [] for name in _ROLES})[role].append(unit)
    return groups


def _candidates(groups) -> List[Dict[str, Any]]:
    candidates: List[Dict[str, Any]] = []
    for group in groups.values():
        claims = sorted(group["claim"] + group["methodology"], key=_position)
        positions = [_position(claim) for claim in claims]
        supporters = (group["evidence"] + group["reasoning"]
                      + group["counterclaim"] + group["limitation"])
        for supporter in supporters:
            role = str((supporter.get("metadata") or {}).get("argument_role") or "unknown")
            ranked = []
            index = bisect_left(positions, _position(supporter))
            for claim in claims[max(0, index-4):index+4]:
                relation, confidence = score_relation(
                    supporter.get("exact_text", ""), claim.get("exact_text", ""), role,
                    (claim.get("metadata") or {}).get("argument_role") or "claim",
                )
                if role == "reasoning" and relation == "elaborates":
                    relation = "reasons"
                relation = _ROLE_RELATION.get(role, relation)
                ranked.append((confidence, relation, claim))
            ranked.sort(key=lambda item: item[0], reverse=True)
            for index, (confidence, relation, claim) in enumerate(ranked[:2]):
                if index and confidence < _SECOND_CHOICE:
                    continue
                candidates.append({
                    "supporter": supporter, "claim": claim, "relation": relation,
                    "confidence": confidence, "scorer": "discourse_markers_contextual",
                })
    return candidates


def _position(unit):
    loc = unit.get("locator") or {}
    ordinal = re.search(r"_b(\d+)$", loc.get("block_id") or "")
    return (loc.get("page") if loc.get("page") is not None else -1,
            int(ordinal[1]) if ordinal else 0, loc.get("char_start") or 0)
