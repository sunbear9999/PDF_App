"""Bounded presentation patches preserve graph identity, evidence and reviewed truth."""
from copy import deepcopy

from core.models.analysis_focus import topical_terms
from core.models.analysis_patch_schema import MAX_TEXT_PATCHES
from core.models.intelligence_models import GraphQualityContract
from core.ontology.registry import OntologyRegistry
from core.services.intelligence.analysis_grounding import AnalysisEvidenceScope
from core.services.intelligence.analysis_relation_proposals import AnalysisRelationProposals
from core.services.intelligence.artifact_eligibility import is_current_artifact
from core.services.intelligence.grounded_argument_graph_service import GroundedArgumentGraphService


class AnalysisBaselinePatchService:
    """Validate the compact patch without rebuilding baseline nodes or edges."""

    def __init__(self, db, ontology_registry=None):
        self.db = db
        self.ontology = ontology_registry or OntologyRegistry()

    def apply(self, baseline, enrichment, *, mode_id, focus, contract,
              source_ids=(), snapshots=None, attempt='initial', allow_repair=True, aliases=None,
              allowed_node_ids=None):
        nodes, relations, stale_ids = self.current_baseline(baseline, source_ids, snapshots)
        accepted, rejected = self._text_patches(nodes, enrichment, allowed_node_ids)
        typed = GraphQualityContract.from_value(contract)
        supplied_nodes = nodes if allowed_node_ids is None else [node for node in nodes if node['id'] in allowed_node_ids]
        proposals, relation_errors = AnalysisRelationProposals(self.db, self.ontology).validate(
            enrichment.get('cross_links') or enrichment.get('x') or [], supplied_nodes, relations,
            mode_id=mode_id, contract=typed, aliases=aliases or {}, source_ids=source_ids,
            snapshots=snapshots)
        relations.extend(proposals)
        rejected.extend(relation_errors)
        changed = bool(accepted or proposals)
        quality = GroundedArgumentGraphService.measure(nodes, relations, typed)
        quality.update({
            'origin': ('repaired' if attempt == 'repair' else 'enhanced') if changed
                      else 'deterministic_baseline',
            'valid': bool(nodes), 'limited_coverage': not quality['meets_requested_contract'],
            'model_overlay_valid': changed, 'model_patch_valid': changed,
            'model_overlay_improves_baseline': False,
            'repair_attempted': attempt == 'repair',
            'focus_eligibility': (baseline.get('quality') or {}).get('focus_eligibility') or {},
        })
        needs_repair = bool(not changed and rejected and not stale_ids and attempt != 'repair'
                             and allow_repair and typed.allow_repair_call)
        quality.update(repair_required=needs_repair)
        return self._result(nodes, relations, quality, rejected, accepted, stale_ids,
                            mode_id, focus, contract, needs_repair, enrichment)

    def _text_patches(self, nodes, enrichment, allowed_node_ids):
        by_id = {node['id']: node for node in nodes}
        accepted, rejected, seen = [], [], set()
        updates = enrichment.get('updates', [])
        if not isinstance(updates, list):
            updates = []
            rejected.append({'reason': 'updates_must_be_a_list'})
        for patch in updates[:MAX_TEXT_PATCHES]:
            if not isinstance(patch, dict):
                rejected.append({'reason': 'patch_must_be_an_object'})
                continue
            identity = str(patch.get('id') or '')
            node = by_id.get(identity)
            reason = ('node_not_supplied_to_model' if allowed_node_ids is not None and identity not in allowed_node_ids
                      else self._patch_error(node, patch, seen))
            seen.add(identity)
            if reason:
                rejected.append({'id': identity, 'reason': reason})
                continue
            self._apply_text(node, patch)
            accepted.append(identity)
        if len(updates) > MAX_TEXT_PATCHES:
            rejected.append({'reason': 'patch_limit_exceeded', 'limit': MAX_TEXT_PATCHES})
        return accepted, rejected

    def current_baseline(self, baseline, source_ids=(), snapshots=None):
        nodes = deepcopy(baseline.get('entities') or [])
        evidence = {identity for node in nodes for identity in node.get('evidence_ids') or []}
        scope = AnalysisEvidenceScope(self.db, evidence, source_ids=source_ids,
                                      snapshots={**(baseline.get('artifact_snapshots') or {}),
                                                 **(snapshots or {})})
        current = [node for node in nodes if self._current_node(node, scope)]
        ids = {node['id'] for node in current}
        relations = [deepcopy(edge) for edge in baseline.get('relations') or []
                     if self._current_relation(edge, ids, scope)]
        return current, relations, [node['id'] for node in nodes if node['id'] not in ids]

    def _current_relation(self, edge, ids, scope):
        evidence = edge.get('evidence_ids') or []
        if (edge.get('source') not in ids or edge.get('target') not in ids
                or not evidence or not all(scope.resolve(identity) for identity in evidence)):
            return False
        if edge.get('grounding') != 'reviewed_assertion':
            return True
        edge_id, assertions = edge.get('source_edge_id'), edge.get('assertion_ids') or []
        if not edge_id or not assertions:
            return False
        current = {item['assertion_id'] for item in self.db.list_edge_assertions(edge_id)
                   if item.get('review_state') in {'verified', 'corrected'}
                   and scope.resolve(item.get('evidence_artifact_id'))}
        return set(assertions) <= current

    def _current_node(self, node, scope):
        if node.get('grounding') == 'user_input':
            return True
        evidence = node.get('evidence_ids') or []
        if not evidence or not all(scope.resolve(identity) for identity in evidence):
            return False
        unit_id = node.get('argument_unit_id')
        if not unit_id:
            return True
        unit = self.db.get_artifact(unit_id) if self.db else None
        return bool(is_current_artifact(self.db, unit)
                    and (unit.get('revision') or 1) == node.get('artifact_revision', 1)
                    and (not node.get('artifact_payload_hash')
                         or node['artifact_payload_hash'] == unit.get('payload_hash')))

    @staticmethod
    def _patch_error(node, patch, seen):
        if not node:
            return 'unknown_or_stale_baseline_node'
        if node['id'] in seen:
            return 'duplicate_patch'
        if (node.get('review_state') in {'verified', 'corrected'} or node.get('locked')
                or node.get('grounding') == 'user_input' or node.get('analysis_role') == 'evidence'):
            return 'node_is_not_model_editable'
        if patch.get('role') and patch['role'] != node.get('analysis_role'):
            return 'role_change_requires_semantic_validation'
        label, statement = patch.get('label') or '', patch.get('statement') or ''
        if not isinstance(label, str) or not isinstance(statement, str):
            return 'patch_text_must_be_a_string'
        if not label and not statement:
            return 'empty_patch'
        if label and (not 2 <= len(label.split()) <= 24 or len(label) > 160):
            return 'label_length_invalid'
        if statement:
            terms = topical_terms(statement)
            if not 5 <= len(statement.split()) <= 120 or len(statement) > 1500:
                return 'statement_length_invalid'
            if not terms or len(terms & topical_terms(node.get('text'))) < max(1, len(terms) / 2):
                return 'statement_lacks_source_overlap'
        return ''

    @staticmethod
    def _apply_text(node, patch):
        node['baseline_text'] = node.get('text')
        node['baseline_title'] = node.get('title')
        for field, key in (('title', 'label'), ('text', 'statement')):
            if patch.get(key):
                node[field] = ' '.join(str(patch[key]).split())
                node.setdefault('properties', {})[field] = node[field]
        node.update(suggested=True, model_patch=True)

    @staticmethod
    def _result(nodes, relations, quality, rejected, accepted, stale_ids,
                mode_id, focus, contract, needs_repair, enrichment):
        return {
            'template_title': str(enrichment.get('title') or mode_id),
            'mode_id': mode_id, 'focus': focus, 'entities': nodes, 'relations': relations,
            'quality': quality, 'graph_contract': contract,
            'rejected_proposals': rejected, 'suggestion_artifact_ids': [],
            'suggested_relations': [edge for edge in relations if edge.get('grounding') == 'model_proposal'],
            'deterministic_graph_preserved': not stale_ids,
            'diagnostics': {
                'fallback_to_deterministic_baseline': not quality['model_patch_valid'],
                'focused_deterministic_fallback': not quality['model_patch_valid'],
                'structured_model_output_valid': quality['model_patch_valid'],
                'model_overlay_improves_baseline': False,
                'accepted_patch_ids': accepted, 'stale_baseline_node_ids': stale_ids,
                'rejected_node_count': len(rejected), 'needs_repair': needs_repair,
                'repair_attempted': quality['repair_attempted'],
                'output_origin': quality['origin'], 'patch_only': True,
                'semantic_validation': 'typed evidence-backed proposals; entailment awaits review',
                'source_refresh_required': bool(stale_ids),
            },
        }
