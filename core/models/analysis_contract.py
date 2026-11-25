"""Structural validation of graph contracts, independent of live registries."""
from core.models.intelligence_models import GraphQualityContract

COUNT_FIELDS = ('min_branches', 'min_sections', 'min_evidence_per_branch',
                'min_proposition_nodes', 'max_proposition_nodes', 'min_evidence_nodes',
                'max_evidence_nodes', 'max_nodes', 'max_relations')
BOOL_FIELDS = ('requires_exact_quotes', 'require_connected', 'allow_repair_call')
EXTENSION_FIELDS = {'purpose', 'custom_instructions', 'required_shape', 'evidence_selection',
                    'node_type_map', 'workspace_type_map', 'projection_strategy', 'compiler_version',
                    'extensions'}


def normalize_graph_contract(value):
    if value is not None and not isinstance(value, dict):
        raise ValueError('Graph contract must be an object.')
    raw = dict(value or {})
    unknown = set(raw) - set(GraphQualityContract.__dataclass_fields__) - EXTENSION_FIELDS
    if unknown:
        raise ValueError(f"Unknown graph contract fields: {', '.join(sorted(unknown))}.")
    if not isinstance(raw.get('density', 'standard'), str) or raw.get('density', 'standard') not in {'focused', 'standard', 'exhaustive'}:
        raise ValueError('Graph density must be focused, standard or exhaustive.')
    contract = {**GraphQualityContract.from_value(raw).to_dict(), **raw}
    for key in ('node_types', 'relation_types', 'root_roles', 'required_roles'):
        values = contract.get(key)
        if (not isinstance(values, list) or (not values and key in {'node_types', 'relation_types'})
                or any(not isinstance(item, str) or not item.strip() for item in values)):
            raise ValueError(f"Graph contract '{key}' must be a {'non-empty ' if key in {'node_types', 'relation_types'} else ''}list of names.")
        contract[key] = list(dict.fromkeys(item.strip() for item in values))
    for key in COUNT_FIELDS:
        value = contract[key]
        if type(value) is not int or value < 0 or value > 10000:
            raise ValueError(f"Graph contract '{key}' must be an integer from 0 to 10000.")
    for key in BOOL_FIELDS:
        if type(contract[key]) is not bool:
            raise ValueError(f"Graph contract '{key}' must be true or false.")
    _check_bounds(contract)
    _check_options(contract)
    return contract


def _check_bounds(contract):
    for kind in ('proposition', 'evidence'):
        if contract[f'min_{kind}_nodes'] > contract[f'max_{kind}_nodes']:
            raise ValueError(f'Graph contract {kind} minimum exceeds its maximum.')
    if contract['max_nodes'] < contract['min_proposition_nodes'] + contract['min_evidence_nodes']:
        raise ValueError('Graph contract max_nodes is smaller than the requested proposition/evidence minimum.')
    if contract['max_nodes'] < 1:
        raise ValueError('Graph contract max_nodes must be positive.')


def _check_options(contract):
    if type(contract['schema_version']) is not int or contract['schema_version'] != 1:
        raise ValueError('Unsupported graph contract schema_version; expected 1.')
    for key in ('purpose', 'custom_instructions'):
        if key in contract and not isinstance(contract[key], str):
            raise ValueError(f"Graph '{key}' must be text.")
    selection = contract.get('evidence_selection', {})
    bounds = {'quotes_per_section': (1, 4), 'max_sections': (1, 12), 'max_quotes': (1, 28)}
    if not isinstance(selection, dict) or set(selection) - set(bounds):
        raise ValueError('Invalid evidence_selection fields.')
    for key, value in selection.items():
        if type(value) is not int or not bounds[key][0] <= value <= bounds[key][1]:
            raise ValueError(f"Invalid evidence_selection '{key}' limit.")
