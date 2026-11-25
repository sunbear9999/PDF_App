"""Shared analysis-role identity; aliases change presentation, never stance."""
import re

ROLE_ENTITY_TYPES = {
    'methodology': 'entity.method', 'counterclaim': 'entity.counterargument',
    'limitation': 'entity.counterargument', 'tracked_claim': 'entity.claim',
    'supporting_claim': 'entity.claim', 'contradicting_claim': 'entity.counterargument',
    'qualification': 'entity.reasoning', 'uncertainty': 'entity.reasoning',
    'evidence': 'entity.evidence',
}
ROLE_ALIASES = {'method': 'methodology', 'quotation': 'evidence', 'quote': 'evidence',
                'source_evidence': 'evidence', 'main_claim': 'claim', 'thesis': 'claim',
                'main_thesis': 'claim', 'central_claim': 'claim', 'reason': 'reasoning',
                'rationale': 'reasoning', 'objection': 'counterclaim', 'counter_argument': 'counterclaim',
                'counterargument': 'counterclaim', 'constraint': 'limitation', 'result': 'finding'}


def analysis_role(value):
    raw = str(value or '').strip().removeprefix('entity.')
    raw = re.sub(r'[-\s]+', '_', raw)
    return ROLE_ALIASES.get(raw, raw)


def analysis_entity_type(role, mapping=None):
    if mapping and role in mapping:
        return mapping[role]
    if str(role).startswith('entity.'):
        return role
    role = analysis_role(role)
    return ROLE_ENTITY_TYPES.get(role, f'entity.{role}')


def analysis_workspace_type(entity_type):
    return 'workspace.node.quote' if entity_type in {'entity.quote', 'entity.evidence'} else 'workspace.node.text'
