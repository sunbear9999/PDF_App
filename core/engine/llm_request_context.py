"""Request text assembly shared by registered LLM steps, without model calls."""
from core.prompt_manager import PromptManager
from core.utils.context_budget import fit_text_request
from core.utils.structured_context_budget import pack_structured_context, supplied_context_items


def budget_notice(options, prompt_manager=None):
    template = (prompt_manager.get_prompt('Context Budget Notice') if prompt_manager else '')
    template = template or PromptManager.DEFAULT_PROMPTS['Context Budget Notice']
    return template.replace('{num_ctx}', str(int(options.get('num_ctx') or 8192)))


def fit_query_to_context(query, system, options, prompt_manager=None):
    limits = dict(context_limit=int(options.get('num_ctx') or 8192),
                  output_limit=int(options.get('num_predict') or 1024))
    clipped, changed = fit_text_request(query, system, **limits)
    if not changed:
        return clipped, ''
    notice = budget_notice(options, prompt_manager)
    clipped, _ = fit_text_request(query, system + '\n\n' + notice, **limits)
    return clipped, notice


def prepare_request_context(inputs, context, step, system, options):
    query = str(inputs.get('query', '') or '')
    key = inputs.get('context_items_key')
    if not key:
        query, notice = fit_query_to_context(query, system, options, context.prompt_manager)
        return query, notice, {}, {}
    items = context.state.get(key)
    query, notice, receipt = pack_structured_context(
        query, items, system, context_limit=int(options.get('num_ctx') or 8192),
        output_limit=int(options.get('num_predict') or 1024),
        notice=budget_notice(options, context.prompt_manager))
    receipt['context_key'] = key
    supplied = supplied_context_items(items, receipt)
    artifact_ids = list(dict.fromkeys(str(identity) for item in supplied
                                     for identity in item.get('artifact_input_ids') or [] if identity))
    receipts = dict(context.state.get('llm_context_receipts') or {})
    receipts[getattr(step, 'output_key', 'result')] = receipt
    return query, notice, {'llm_context_receipts': receipts}, {
        '_artifact_input_ids': artifact_ids, '_context_packing': receipt}
