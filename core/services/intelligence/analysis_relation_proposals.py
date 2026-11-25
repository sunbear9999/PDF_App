"""Typed, evidence-scoped relation proposals over stable baseline IDs."""
import hashlib

from core.models.analysis_relations import canonical_relation, directed_relation, semantic_relation
from core.models.analysis_patch_schema import MAX_RELATION_PROPOSALS
from core.services.intelligence.analysis_grounding import AnalysisEvidenceScope


class AnalysisRelationProposals:
    MAX_PROPOSALS = MAX_RELATION_PROPOSALS

    def __init__(self, db, ontology_registry):
        self.db = db
        self.ontology = ontology_registry

    def validate(self, proposals, nodes, existing, *, mode_id, contract,
                 aliases, source_ids=(), snapshots=None):
        by_id = {node['id']: node for node in nodes}
        scope = AnalysisEvidenceScope(self.db, aliases.values(), source_ids=source_ids, snapshots=snapshots)
        allowed = {canonical_relation(value) for value in contract.relation_types}
        seen = {(edge['source'], edge['target'], edge['type']) for edge in existing}
        accepted, rejected = [], []
        if not isinstance(proposals, list):
            return [], [{'reason': 'relations_must_be_a_list'}]
        for raw in proposals[:self.MAX_PROPOSALS]:
            edge, error = self._proposal(raw, by_id, allowed, scope, aliases, mode_id)
            if error:
                rejected.append({'reason': error})
                continue
            key = (edge['source'], edge['target'], edge['type'])
            if key in seen:
                rejected.append({'reason': 'relation_already_present'})
                continue
            if len(existing) + len(accepted) >= contract.max_relations:
                rejected.append({'reason': 'relation_limit_exceeded'})
                break
            seen.add(key)
            accepted.append(edge)
        if len(proposals) > self.MAX_PROPOSALS:
            rejected.append({'reason': 'relation_proposal_limit_exceeded'})
        return accepted, rejected

    def _proposal(self, raw, nodes, allowed, scope, aliases, mode_id):
        if not isinstance(raw, dict):
            return None, 'relation_must_be_an_object'
        source, target, role = directed_relation(
            raw.get('a') or raw.get('from'), raw.get('b') or raw.get('to'),
            raw.get('r') or raw.get('relation'))
        if (not isinstance(source, str) or not isinstance(target, str)
                or source not in nodes or target not in nodes or source == target):
            return None, 'unresolvable_endpoints'
        relation_type = f'relation.{role}'
        if role not in allowed or relation_type not in self.ontology.relations:
            return None, 'relation_type_not_allowed'
        if not self.ontology.validate_relation(relation_type, nodes[source]['type'], nodes[target]['type']):
            return None, 'relation_endpoints_violate_ontology'
        values = raw.get('q') or raw.get('evidence') or []
        if (not isinstance(values, list) or not 1 <= len(values) <= 4
                or any(not isinstance(value, str) or value not in aliases for value in values)):
            return None, 'relation_requires_supplied_evidence_aliases'
        evidence = list(dict.fromkeys(aliases[value] for value in values))
        if not all(scope.resolve(value) for value in evidence):
            return None, 'relation_evidence_is_stale_or_out_of_scope'
        explanation = str(raw.get('why') or raw.get('explanation') or '').strip()
        if not 5 <= len(explanation.split()) <= 120 or len(explanation) > 800:
            return None, 'relation_requires_grounding_explanation'
        digest = hashlib.sha1(f'{source}|{target}|{role}'.encode()).hexdigest()[:14]
        return {
            'id': f'analysis-relation:{mode_id}:proposal:{digest}',
            'source': source, 'target': target, 'type': relation_type,
            'label': role.replace('_', ' '), 'evidence_ids': evidence,
            'explanation': explanation, 'suggested': True, 'verified': False,
            'review_state': 'unreviewed', 'grounding': 'model_proposal',
            'semantic': semantic_relation({'type': relation_type}),
        }, ''
