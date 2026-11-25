"""Direction and truth vocabulary shared by analysis, validation and workspace import."""
import re

STRUCTURAL_RELATIONS = frozenset({'part_of', 'references', 'derived_from', 'basic'})
SEMANTIC_RELATIONS = frozenset({'supports', 'reasons', 'refutes', 'contradicts',
                               'critiques', 'implies', 'similar_to'})
INVERSE_RELATIONS = {'supported_by': 'supports', 'reasoned_by': 'reasons',
                     'attacked_by': 'refutes', 'refuted_by': 'refutes',
                     'qualified_by': 'critiques', 'limited_by': 'critiques'}
RELATION_ALIASES = {'support': 'supports', 'reason_for': 'reasons', 'reasoning_for': 'reasons',
                    'attack': 'refutes', 'attacks': 'refutes', 'contradiction': 'contradicts',
                    'critique': 'critiques', 'limits': 'critiques', 'part': 'part_of',
                    'raises_uncertainty': 'critiques', 'repeats': 'similar_to',
                    'related_to': 'similar_to', 'explains': 'reasons', 'elaborates': 'reasons',
                    'depends_on': 'derived_from'}


def relation_role(value):
    raw = re.sub(r'[-\s]+', '_', str(value or 'basic').strip().casefold())
    return raw.removeprefix('relation.')


def canonical_relation(value):
    raw = relation_role(value)
    return INVERSE_RELATIONS.get(raw, RELATION_ALIASES.get(raw, raw))


def directed_relation(source, target, value):
    """An inverse spelling changes endpoints as well as its displayed type."""
    raw = relation_role(value)
    if raw in INVERSE_RELATIONS:
        source, target = target, source
    return source, target, canonical_relation(value)


def semantic_relation(edge):
    return (edge.get('semantic') is not False and edge.get('grounding') != 'structural_grouping'
            and canonical_relation(edge.get('type') or edge.get('edge_type')) in SEMANTIC_RELATIONS)


def normalize_analysis_relation(edge):
    """Normalize a legacy edge once at either workspace import boundary."""
    raw = edge.get('type') or edge.get('edge_type')
    source, target, role = directed_relation(
        edge.get('source') or edge.get('source_id'),
        edge.get('target') or edge.get('target_id'), raw)
    return {**edge, 'source': str(source or ''), 'target': str(target or ''),
            'type': f'relation.{role}',
            'label': role.replace('_', ' ') if relation_role(raw) in INVERSE_RELATIONS
                     else edge.get('label') or role.replace('_', ' ')}


def analysis_relation_properties(edge):
    return {**(edge.get('properties') or {}), 'explanation': edge.get('explanation') or '',
            'semantic': semantic_relation(edge), 'grounding': edge.get('grounding') or 'legacy',
            'assertion_ids': list(edge.get('assertion_ids') or []),
            'source_edge_id': edge.get('source_edge_id'),
            'review_state': edge.get('review_state') or 'unreviewed',
            'suggested': edge.get('suggested', True)}


def analysis_relation_state(edge):
    verified = edge.get('grounding') == 'reviewed_assertion' and edge.get('suggested') is False
    return {'is_verified': verified, 'origin': 'analysis_reviewed' if verified else 'analysis_ai'}
