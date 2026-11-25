"""Typed personal entity-network perspective, independent of analysis templates."""
from dataclasses import dataclass, asdict


@dataclass(frozen=True)
class NetworkFilter:
    focus_entity_id: str = ''
    include_candidates: bool = True
    excluded_assertion_ids: tuple = ()


def coerce_network_filter(value=None):
    raw = asdict(value) if isinstance(value, NetworkFilter) else dict(value or {})
    if set(raw) - set(NetworkFilter.__dataclass_fields__):
        raise ValueError('Unknown network perspective setting.')
    result = NetworkFilter(**raw)
    if not isinstance(result.focus_entity_id, str) or len(result.focus_entity_id) > 512 or type(result.include_candidates) is not bool:
        raise ValueError('Choose a current focus entity and candidate visibility.')
    keys = result.excluded_assertion_ids
    if not isinstance(keys, (list, tuple)) or len(keys) > 5000 or any(not isinstance(key, str) or not 0 < len(key) <= 512 for key in keys):
        raise ValueError('Use a bounded list of hidden connection assertions.')
    return NetworkFilter(result.focus_entity_id, result.include_candidates, tuple(sorted(set(keys))))
