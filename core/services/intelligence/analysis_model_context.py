"""Dependency-complete evidence groups for a bounded analysis patch request."""
from copy import deepcopy

from core.utils.structured_context_budget import supplied_context_items, valid_context_receipt

REVIEWED_CONTEXT_PRIORITY = 100


def build_analysis_context_items(baseline, evidence):
    selected = evidence.get('selected') or []
    by_alias = {row['alias']: row for row in selected}
    by_artifact = {row['artifact_id']: row['alias'] for row in selected}
    parents = {alias: alias for alias in by_alias}

    def root(alias):
        while parents[alias] != alias:
            alias = parents[alias]
        return alias

    eligible, independent = [], []
    for node in baseline.get('entities') or []:
        identities = node.get('evidence_ids') or []
        if node.get('grounding') == 'user_input':
            independent.append(node)
        elif identities and all(identity in by_artifact for identity in identities):
            aliases = list(dict.fromkeys(by_artifact[identity] for identity in identities))
            for alias in aliases[1:]:
                parents[root(alias)] = root(aliases[0])
            eligible.append((node, aliases))
    groups = {}
    for alias, row in by_alias.items():
        groups.setdefault(root(alias), {'rows': [], 'nodes': []})['rows'].append(row)
    for node, aliases in eligible:
        groups[root(aliases[0])]['nodes'].append(_node(node, aliases))
    items = [_item(group, baseline) for group in groups.values()]
    items.sort(key=lambda item: (-item['_priority'], item['id']))
    for item in items:
        item.pop('_priority')
    # The independent focus is user input, not a source proposition or proof.
    if independent:
        items.insert(0, dict(id='user_focus', nodes=[_node(node, []) for node in independent],
                             sections=[], relations=[], artifact_input_ids=[]))
    return items


def _node(node, aliases):
    return {'id': node['id'], 'role': node.get('analysis_role'),
            'statement': node.get('text'), 'q': aliases,
            'grounding': node.get('grounding'),
            'editable': not (node.get('locked') or node.get('review_state') in {'verified', 'corrected'}
                             or node.get('grounding') == 'user_input' or node.get('analysis_role') == 'evidence')}


def _item(group, baseline):
    sections = {}
    for row in group['rows']:
        key = (row['source_id'], row['section'])
        section = sections.setdefault(key, dict(source_id=key[0], heading=key[1], quotes=[]))
        section['quotes'].append({'q': row['alias'], 'r': row['suggested_role'], 'x': row['exact_text']})
    ids = {node['id'] for node in group['nodes']}
    return dict(id=group['rows'][0]['alias'], nodes=group['nodes'], sections=list(sections.values()),
                relations=[{key: relation.get(key) for key in ('source', 'target', 'type')}
                           for relation in baseline.get('relations') or []
                           if relation.get('source') in ids and relation.get('target') in ids],
                artifact_input_ids=[row['artifact_id'] for row in group['rows']],
                _priority=max(row.get('score', 0) + (REVIEWED_CONTEXT_PRIORITY
                              if row.get('focus_basis') == 'reviewed_context' else 0)
                              for row in group['rows']))


def supplied_analysis_scope(state, receipt_key):
    """Return only endpoints/aliases present in the exact request for this attempt."""
    receipt = (state.get('llm_context_receipts') or {}).get(receipt_key) or {}
    items = state.get('analysis_model_context_items') or []
    supplied = (supplied_context_items(items, receipt)
                if receipt.get('context_key') == 'analysis_model_context_items' else [])
    available = state.get('analysis_evidence_aliases') or {}
    aliases = {quote['q']: available[quote['q']] for item in supplied
               for section in item.get('sections') or [] for quote in section.get('quotes') or []
               if quote.get('q') in available}
    nodes = {node['id'] for item in supplied for node in item.get('nodes') or []}
    diagnostics = {key: deepcopy(receipt[key]) for key in
                   ('selected_count', 'total_count', 'omitted_count', 'method') if key in receipt}
    diagnostics['receipt_valid'] = bool(valid_context_receipt(items, receipt)
                                      and receipt.get('context_key') == 'analysis_model_context_items')
    return aliases, nodes, diagnostics
