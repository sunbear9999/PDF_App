"""Conservative topical eligibility, independent of rank, polarity and classifier role."""
from dataclasses import dataclass
import math
import re

from core.models.analysis_relations import semantic_relation

MAX_FOCUS_CHARS = 4096
FOCUS_COVERAGE = .5
_STOP = frozenset('a an and are as at be been being by can could did do does for from had has have how '
    'if in into is it its may more most not of on or our should than that the their there these they '
    'this those to was we were what when where whether which while who why will with would '
    'analyse analyze analysis argument arguments claim claims central document documents evidence '
    'explain find give main map maps paper please research show source sources specific support '
    'supports supporting oppose opposing track tracking view'.split())


def topical_terms(text: str) -> frozenset[str]:
    tokens = re.findall(r'[^\W_]+(?:[-’\'][^\W_]+)*', str(text or '').casefold(), re.UNICODE)
    return frozenset(token[:-1] if token.endswith('s') and len(token) > 4 else token
                     for token in tokens if len(token) >= 2 and token not in _STOP)


@dataclass(frozen=True)
class AnalysisFocus:
    text: str = ''

    def __post_init__(self):
        if not isinstance(self.text, str) or len(self.text) > MAX_FOCUS_CHARS:
            raise ValueError('Use a research focus of at most 4096 characters.')

    @property
    def terms(self):
        return topical_terms(self.text)

    @property
    def active(self):
        return bool(self.terms)

    def matches(self, row):
        if not self.active:
            return True
        # A section heading, confidence or retrieval rank cannot confer relevance.
        terms = topical_terms(row.get('exact_text') or row.get('text') or '')
        return len(self.terms & terms) >= max(1, math.ceil(len(self.terms) * FOCUS_COVERAGE))


def focus_rows(rows, focus, edges=()):
    """Allow direct topical matches and one hop of explicitly reviewed semantic proof."""
    policy = AnalysisFocus(focus)
    rows = list(rows)
    selected = {row['artifact_id'] for row in rows if policy.matches(row)}
    direct = set(selected)
    aliases = {}
    for row in rows:
        for key in (row['artifact_id'], row.get('parent_artifact_id'),
                    (row.get('metadata') or {}).get('canonical_source_artifact_id')):
            if key:
                aliases.setdefault(key, set()).add(row['artifact_id'])
    for edge in edges if policy.active else ():
        if not edge.get('verified') or not semantic_relation(edge) or not edge.get('evidence_ids'):
            continue
        if any(value not in aliases for value in edge['evidence_ids']):
            continue
        source = aliases.get(edge.get('source_artifact_id') or edge.get('source'), set())
        target = aliases.get(edge.get('target_artifact_id') or edge.get('target'), set())
        if len(source) == len(target) == 1 and (source & direct or target & direct):
            selected.update(source | target)
    result = [{**row, '_analysis_focus_basis': 'direct' if row['artifact_id'] in direct else 'reviewed_context'}
              for row in rows if row['artifact_id'] in selected]
    return result, {'active': policy.active, 'direct_count': len(direct),
                    'reviewed_context_count': len(selected - direct), 'excluded_count': len(rows) - len(result),
                    'policy': 'topical_terms_or_one_reviewed_semantic_hop', 'focus': focus}
