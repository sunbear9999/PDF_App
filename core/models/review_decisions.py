"""Typed vocabulary for explicit knowledge decisions; no GUI or database imports."""
from enum import Enum


class ReviewDecisionKind(str, Enum):
    RELATION_VERIFY = 'relation_verify'
    RELATION_REJECT = 'relation_reject'
    ENTITY_MERGE = 'entity_merge'
    ENTITY_SPLIT = 'entity_split'
    ENTITY_NEVER_MERGE = 'entity_never_merge'
    MIGRATION_RESOLVE = 'migration_resolve'


ENTITY_GROUP_ACTIONS = (('entity_merge','Merge into the target entity'), ('entity_never_merge','Keep these entities separate'),
                        ('entity_subgroup','Make it a subgroup of the target (both stay their own entity)'),
                        ('entity_connect','Not the same, but connected: save a connection'))
# Pair decisions done by direct, journaled service calls rather than the review-command path.
DIRECT_PAIR_ACTIONS = frozenset({'entity_subgroup', 'entity_connect'})
PAIR_ACTIONS = frozenset({'entity_merge', 'entity_never_merge'}) | DIRECT_PAIR_ACTIONS


def validate_payload(command, payload):
    if payload is None:
        return {}
    if not isinstance(payload, dict):
        raise ValueError('Decision values must be named typed fields.')
    allowed = {'entity_merge':{'target_id'},'entity_never_merge':{'target_id'},
               'entity_split':{'mention_ids','display_name'},
               'migration_resolve':{'target_id','correspondence_confirmed'}}.get(command,set())
    if set(payload) - allowed:
        raise ValueError('Unknown decision fields.')
    for key in ('target_id','display_name'):
        if key in payload and not isinstance(payload[key], str):
            raise ValueError(f'{key} must be text.')
    if 'mention_ids' in payload and (not isinstance(payload['mention_ids'],list) or not all(isinstance(item,str) for item in payload['mention_ids'])):
        raise ValueError('Mention choices must be artifact IDs.')
    if 'correspondence_confirmed' in payload and type(payload['correspondence_confirmed']) is not bool:
        raise ValueError('Source correspondence requires an explicit Yes or No.')
    return dict(payload)
