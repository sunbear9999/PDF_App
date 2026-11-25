"""Atomic JSON context packing under the existing conservative text estimate.

This does not count provider tokens, templates or images. It prevents the text
estimator from corrupting structured evidence while exact provider accounting
is being qualified separately.
"""
from copy import deepcopy
import hashlib
import json

from core.utils.context_budget import fit_text_request


def context_digest(items):
    data = json.dumps(items, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False)
    return hashlib.sha256(data.encode('utf-8', 'replace')).hexdigest()


def _render(query, items):
    return query + '\n\n' + json.dumps({'context_items': items}, ensure_ascii=False,
                                     separators=(',', ':'), allow_nan=False)


def _fits(query, system, context_limit, output_limit):
    _, changed = fit_text_request(query, system, context_limit=context_limit, output_limit=output_limit)
    return not changed


def pack_structured_context(query, items, system, *, context_limit, output_limit, notice=''):
    """Keep fixed instructions and whole ordered items, without renumbering IDs."""
    if not isinstance(items, list) or any(not isinstance(item, dict) for item in items):
        raise ValueError('Structured context must be a list of JSON objects.')
    complete = _render(query, items)
    if _fits(complete, system, context_limit, output_limit):
        return complete, '', _receipt(items, list(range(len(items))))
    budgeted_system = system + ('\n\n' + notice if notice else '')
    if not _fits(_render(query, []), budgeted_system, context_limit, output_limit):
        raise ValueError('The fixed instructions, schema and output reserve do not fit. '
                         'Shorten the instructions or choose a larger context; no evidence was truncated.')
    kept, indices = [], []
    for index, item in enumerate(items):
        candidate = _render(query, [*kept, item])
        if _fits(candidate, budgeted_system, context_limit, output_limit):
            kept.append(item)
            indices.append(index)
    return _render(query, kept), notice, _receipt(items, indices)


def _receipt(items, indices):
    return {'context_digest': context_digest(items), 'selected_indices': indices,
            'selected_count': len(indices), 'total_count': len(items),
            'omitted_count': len(items) - len(indices), 'method': 'utf8_estimate_atomic_v1'}


def supplied_context_items(items, receipt):
    """Resolve an immutable packing receipt; malformed/mismatched receipts fail closed."""
    if not valid_context_receipt(items, receipt):
        return []
    return [deepcopy(items[index]) for index in receipt['selected_indices']]


def valid_context_receipt(items, receipt):
    if not isinstance(items, list) or not isinstance(receipt, dict):
        return False
    try:
        digest = context_digest(items)
    except (TypeError, ValueError):
        return False
    if receipt.get('context_digest') != digest:
        return False
    indices = receipt.get('selected_indices')
    if (not isinstance(indices, list) or any(type(index) is not int or not 0 <= index < len(items)
                                           for index in indices)
            or len(set(indices)) != len(indices)):
        return False
    return True
