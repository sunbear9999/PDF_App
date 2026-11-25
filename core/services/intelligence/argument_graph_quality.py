"""Pure quality metrics for grounded analysis graphs."""
from __future__ import annotations

from typing import Any, Dict, List

from core.models.intelligence_models import GraphQualityContract
from core.models.analysis_relations import canonical_relation, semantic_relation


def measure_argument_graph(
    nodes: List[Dict[str, Any]],
    relations: List[Dict[str, Any]],
    contract: GraphQualityContract,
) -> Dict[str, Any]:
    proposition_ids, evidence_nodes, analytical_links = _typed_graph_parts(
        nodes, relations
    )
    targets = {str(item.get("target")) for item in analytical_links}
    roots = proposition_ids - {
        str(item.get("source")) for item in analytical_links
    }
    branches = {
        str(item.get("source")) for item in analytical_links
        if str(item.get("target")) in roots
    }
    branch_counts = _branch_evidence_counts(
        evidence_nodes, analytical_links, relations, branches, roots
    )
    section_keys = _evidence_sections(evidence_nodes)
    roles, role_counts = _role_metrics(nodes)
    connected = _connected(nodes, relations)
    exact_quotes = sum(
        bool(str(node.get("exact_text") or "").strip()) for node in evidence_nodes
    )
    reviewed_relations = [
        relation for relation in relations
        if relation.get("grounding") == "reviewed_assertion"
        and relation.get("suggested") is False
    ]
    meets = _meets_contract(
        contract, proposition_ids, evidence_nodes, branches, branch_counts,
        section_keys, roles, exact_quotes, connected,
    )
    return {
        "proposition_count": len(proposition_ids),
        "evidence_count": len(evidence_nodes),
        "exact_quote_count": exact_quotes,
        "relation_count": len(relations),
        "reviewed_relation_count": len(reviewed_relations),
        "suggested_relation_count": len(relations) - len(reviewed_relations),
        "analytical_relation_count": len(analytical_links),
        "semantic_relation_count": sum(semantic_relation(item) for item in relations),
        "structural_relation_count": sum(not semantic_relation(item) for item in relations),
        "root_count": len(roots),
        "branch_count": len(branches),
        "evidence_branch_count": sum(
            count >= contract.min_evidence_per_branch
            for count in branch_counts.values()
        ),
        "section_count": len(section_keys),
        "roles": sorted(roles), "role_counts": role_counts,
        "connected": connected, "meets_requested_contract": meets,
        "quality_score": (
            len(proposition_ids) * 2 + len(evidence_nodes) * 2
            + len(branches) * 3 + len(section_keys) + len(targets)
        ),
    }


def _typed_graph_parts(nodes, relations):
    proposition_ids = {
        str(node.get("id")) for node in nodes
        if node.get("analysis_role") != "evidence" and node.get("id")
    }
    evidence_nodes = [
        node for node in nodes if node.get("analysis_role") == "evidence"
    ]
    analytical_links = [
        item for item in relations
        if str(item.get("source")) in proposition_ids
        and str(item.get("target")) in proposition_ids
        and semantic_relation(item)
    ]
    return proposition_ids, evidence_nodes, analytical_links


def _branch_evidence_counts(
    evidence_nodes, analytical_links, relations, branches, roots
):
    parents = {}
    for item in analytical_links:
        parents.setdefault(str(item.get("source")), set()).add(str(item.get("target")))
    evidence_node_ids = {str(node.get("id")) for node in evidence_nodes}
    branch_evidence = {branch: set() for branch in branches}
    for item in relations:
        source, target = str(item.get("source")), str(item.get("target"))
        if canonical_relation(item.get('type')) in {'references', 'derived_from'} and target in evidence_node_ids:
            source, target = target, source
        if source not in evidence_node_ids or not (
            semantic_relation(item) or canonical_relation(item.get('type')) in {'references', 'derived_from'}
        ):
            continue
        visited, pending = set(), [target]
        while pending:
            current = pending.pop()
            if current in visited:
                continue
            visited.add(current)
            if current in branches:
                branch_evidence[current].add(source)
            pending.extend(parents.get(current, set()) - visited)
    return {branch: len(ids) for branch, ids in branch_evidence.items()}


def _evidence_sections(evidence_nodes):
    return {
        str(node.get("section_label") or "")
        or " > ".join(node.get("section_path") or [])
        or _page_key(node)
        for node in evidence_nodes
    }


def _role_metrics(nodes):
    roles = {str(node.get("analysis_role") or "") for node in nodes}
    role_counts = {}
    for node in nodes:
        role = str(node.get("analysis_role") or "unknown")
        role_counts[role] = role_counts.get(role, 0) + 1
    return roles, role_counts


def _meets_contract(
    contract, proposition_ids, evidence_nodes, branches, branch_counts,
    section_keys, roles, exact_quotes, connected,
):
    return (
        len(proposition_ids) >= contract.min_proposition_nodes
        and len(evidence_nodes) >= contract.min_evidence_nodes
        and len(branches) >= contract.min_branches
        and len(section_keys) >= contract.min_sections
        and all(
            count >= contract.min_evidence_per_branch
            for count in branch_counts.values()
        )
        and set(contract.required_roles).issubset(roles)
        and (not contract.requires_exact_quotes or exact_quotes == len(evidence_nodes))
        and (not contract.require_connected or connected)
    )


def _connected(nodes, relations) -> bool:
    ids = {str(node.get("id")) for node in nodes if node.get("id")}
    if not ids:
        return False
    adjacency = {node_id: set() for node_id in ids}
    for relation in relations:
        source, target = str(relation.get("source")), str(relation.get("target"))
        if source in ids and target in ids:
            adjacency[source].add(target)
            adjacency[target].add(source)
    seen = set()
    pending = [next(iter(ids))]
    while pending:
        current = pending.pop()
        if current in seen:
            continue
        seen.add(current)
        pending.extend(adjacency[current] - seen)
    return seen == ids


def _page_key(node: Dict[str, Any]) -> str:
    page = (node.get("locator") or {}).get("page")
    return f"Page {page + 1}" if isinstance(page, int) else "Document"
