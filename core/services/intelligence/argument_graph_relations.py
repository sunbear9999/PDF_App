"""Project reviewed relations; classifier roles and proximity cannot prove a link."""
from __future__ import annotations

import hashlib

from core.models.analysis_relations import directed_relation, semantic_relation


def build_argument_relations(owner, stored_edges, nodes, *, mode_id,
                             relation_types, min_branches, root_roles):
    """Keep direction, scoped evidence and review status of stored assertions.

    Density/root preferences select nodes elsewhere. They must never manufacture
    support, refutation, or a hierarchy between otherwise unrelated statements.
    The compatibility arguments remain for saved callers.
    """
    aliases = _node_aliases(nodes)
    node_types = {node['id']: node['type'] for node in nodes}
    relations = {}
    allowed = {directed_relation('', '', value)[2] for value in relation_types}
    for edge in stored_edges:
        evidence = list(dict.fromkeys(edge.get('evidence_ids') or []))
        if not edge.get('verified') or not evidence or not set(evidence) <= aliases.keys():
            continue
        source, target, role = directed_relation(
            edge.get('source_artifact_id') or edge.get('source'),
            edge.get('target_artifact_id') or edge.get('target'),
            edge.get('edge_type') or edge.get('type'))
        source, target = aliases.get(source), aliases.get(target)
        if not source or not target or source == target or (allowed and role not in allowed):
            continue
        if (f'relation.{role}' not in owner.ontology.relations or not owner.ontology.validate_relation(
                f'relation.{role}', node_types[source], node_types[target])):
            continue
        key = (source, target, role)
        if key in relations:
            relations[key]['evidence_ids'] = sorted(set(relations[key]['evidence_ids']) | set(evidence))
            continue
        digest = hashlib.sha1('|'.join(key).encode()).hexdigest()[:14]
        relations[key] = {
            'id': f'analysis-relation:{mode_id}:baseline:{digest}',
            'source': source, 'target': target, 'type': f'relation.{role}',
            'label': role.replace('_', ' '), 'evidence_ids': evidence,
            'suggested': False, 'verified': True, 'review_state': 'verified',
            'grounding': 'reviewed_assertion', 'semantic': semantic_relation({'type': role}),
            'assertion_ids': list(edge.get('assertion_ids') or []),
            'source_edge_id': edge.get('id') or edge.get('edge_id'),
        }
    return list(relations.values())


def _node_aliases(nodes):
    candidates = {}
    for node in nodes:
        for key in (node.get('id'), node.get('argument_unit_id'),
                    node.get('primary_evidence_id')):
            if key:
                candidates.setdefault(str(key), set()).add(str(node['id']))
    # Fail closed when multiple derived nodes share a source artifact.
    return {key: next(iter(ids)) for key, ids in candidates.items() if len(ids) == 1}
