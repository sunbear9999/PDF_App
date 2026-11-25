"""Rank eligible evidence and apply explicit section/source diversity bounds."""
from collections import defaultdict
import re

from core.models.analysis_focus import topical_terms
from core.services.intelligence.analysis_evidence_scoring import analysis_mode_terms, score_evidence_candidate

UNSECTIONED_SEGMENT_SIZE = 12
METHOD_INTENT = re.compile(r'\b(method|methodology|approach|framework|measure|measurement|metric|'
                          r'evaluate|evaluation|score|procedure|pipeline|experiment|dataset|'
                          r'we propose|we introduce|we develop|we use)\b', re.I)


def section_key(row):
    path = [str(value).strip() for value in row.get('section_path') or [] if str(value).strip()]
    if path:
        return ' > '.join(path)
    page = (row.get('locator') or {}).get('page')
    try:
        return f'Pages {((int(page) - 1) // 3) * 3 + 1}-{((int(page) - 1) // 3) * 3 + 3}'
    except (TypeError, ValueError):
        return 'Document'


def reading_order(row):
    locator = row.get('locator') or {}
    return tuple(locator[key] if isinstance(locator.get(key), int) else 10**9
                 for key in ('page', 'char_start'))


def global_reading_order(row):
    storage_id = str(row.get('storage_ref_id') or '')
    numeric = re.search(r'\d+', storage_id)
    return (str(row.get('source_id') or ''), *reading_order(row),
            int(numeric.group()) if numeric else 10**9, storage_id)


def section_order(value):
    match = re.search(r'\d+', value or '')
    return (int(match.group()) if match else 10**9, value or '')


def rank_sections(sentences, summaries, scores, *, focus, mode_id, contract):
    focus_terms = topical_terms(focus)
    contract_terms = topical_terms(' '.join([
        str(contract.get('purpose') or ''), str(contract.get('custom_instructions') or ''),
        ' '.join(map(str, contract.get('node_types', []))),
        ' '.join(map(str, contract.get('required_shape', [])))]))
    mode_terms = analysis_mode_terms(mode_id)
    method_intent = bool(METHOD_INTENT.search(focus)) or bool({
        'measure', 'measurement', 'method', 'methodology', 'evaluate', 'evaluation', 'track', 'tracking'
    } & focus_terms)
    section_summaries, documents = {}, []
    for row in summaries:
        if (row.get('metadata') or {}).get('scope') == 'document':
            documents.append(row)
        else:
            section_summaries[(str(row.get('source_id') or ''), section_key(row))] = row
    grouped = defaultdict(list)
    unsectioned = defaultdict(int)
    for row in sorted(sentences, key=global_reading_order):
        source_id, section = str(row.get('source_id') or ''), section_key(row)
        if section == 'Document':
            section = f'Document segment {unsectioned[source_id] // UNSECTIONED_SEGMENT_SIZE + 1}'
            unsectioned[source_id] += 1
        terms = topical_terms(row.get('exact_text') or '')
        score, role, reused = score_evidence_candidate(
            row, focus_overlap=len(terms & focus_terms), mode_overlap=len(terms & mode_terms),
            contract_overlap=len(terms & contract_terms), method_intent=method_intent)
        score += min(4.0, scores.get(str(row.get('artifact_id') or ''), 0.0) * 20.0)
        grouped[(source_id, section)].append((score, row, role, reused))
    ranked = []
    for key, candidates in grouped.items():
        candidates.sort(key=lambda item: (-item[0], reading_order(item[1]), str(item[1].get('artifact_id') or '')))
        summary = section_summaries.get(key)
        overlap = len(topical_terms((summary or {}).get('exact_text') or '') & focus_terms)
        ranked.append((sum(item[0] for item in candidates[:3]) + overlap * 3.0, key, candidates, summary))
    return sorted(ranked, key=lambda item: (-item[0], *item[1])), documents, method_intent


def choose_sections(ranked, source_ids, max_sections):
    chosen, seen = [], set()
    for source_id in source_ids:
        best = next((item for item in ranked if item[1][0] == source_id), None)
        if best and best[1] not in seen:
            chosen.append(best)
            seen.add(best[1])
        if len(chosen) >= max_sections:
            break
    for item in ranked:
        if len(chosen) >= max_sections:
            break
        if item[1] not in seen:
            chosen.append(item)
            seen.add(item[1])
    return sorted(chosen, key=lambda item: (item[1][0], section_order(item[1][1])))


def pick_section_quotes(candidates, quotes_per_section, remaining, method_intent):
    if remaining <= 0:
        return []
    candidates = sorted(candidates, key=lambda item: item[1].get('_analysis_focus_basis') != 'reviewed_context')
    limit = min(quotes_per_section, remaining)
    picks = list(candidates[:limit])
    if method_intent and candidates and not any(item[2] == 'methodology' for item in picks):
        method = next((item for item in candidates if item[2] == 'methodology'), None)
        if method and not (limit == 1 and picks[0][1].get('_analysis_focus_basis') == 'reviewed_context'):
            picks[-1] = method
    return picks


def select_quotes(ranked, source_ids, *, max_sections, quotes_per_section, max_quotes, method_intent):
    chosen = choose_sections(ranked, source_ids, max_sections)
    sections = []
    selected_ids = set()
    for _, key, candidates, summary in chosen:
        picks = pick_section_quotes(candidates, quotes_per_section,
                                    max_quotes - len(selected_ids), method_intent)
        picks = [item for item in picks if item[1]['artifact_id'] not in selected_ids]
        if picks:
            sections.append({'key': key, 'summary': summary, 'picks': picks})
            selected_ids.update(item[1]['artifact_id'] for item in picks)
    by_key = {item['key']: item for item in sections}
    # Preserve the actual group key, including generated unsectioned segments.
    remaining = [(candidate, key) for _, key, candidates, _ in ranked for candidate in candidates
                 if candidate[1]['artifact_id'] not in selected_ids]
    remaining.sort(key=lambda item: -item[0][0])
    for candidate, key in remaining:
        if len(selected_ids) >= max_quotes:
            break
        target = by_key.get(key)
        identity = candidate[1]['artifact_id']
        if target and len(target['picks']) < quotes_per_section + 1 and identity not in selected_ids:
            target['picks'].append(candidate)
            selected_ids.add(identity)
    return sections
