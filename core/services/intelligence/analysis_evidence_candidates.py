"""Current, source-scoped evidence reads shared by analysis packet preparation."""
from core.db.intelligence_artifact_queries import plan_query
from core.models.intelligence_models import RetrievalPlan
from core.services.intelligence.analysis_grounding import SOURCE_EVIDENCE_TYPES, scoped_focus_rows
from core.services.intelligence.artifact_eligibility import is_current_artifact


class AnalysisEvidenceCandidates:
    def __init__(self, db, retrieval=None):
        self.db = db
        self.retrieval = retrieval

    def load(self, source_ids, focus, *, max_sections, quotes_per_section, max_quotes):
        plan = RetrievalPlan(query=focus, allowed_source_ids=source_ids,
                             artifact_types=['sentence', 'transcript_segment', 'table', 'summary'])
        sentences, summaries = [], []
        for row in self.db.iter_artifacts(plan_query(plan, source_ids)):
            if not is_current_artifact(self.db, row):
                continue
            if row['artifact_type'] == 'summary':
                summaries.append(row)
            elif self.usable_text(row.get('exact_text')):
                sentences.append(row)
        retrieved, scores = self._retrieved(source_ids, focus, max_sections,
                                            quotes_per_section, max_quotes)
        existing = {row['artifact_id'] for row in sentences}
        for row in retrieved:
            if (row['artifact_id'] not in existing and row.get('artifact_type') in SOURCE_EVIDENCE_TYPES
                    and self.usable_text(row.get('exact_text'))):
                sentences.append(row)
                existing.add(row['artifact_id'])
        sentences, diagnostics = scoped_focus_rows(self.db, sentences, source_ids, focus)
        summaries, _ = scoped_focus_rows(self.db, summaries, source_ids, focus)
        return sentences, summaries, scores, diagnostics

    def _retrieved(self, source_ids, focus, max_sections, quotes_per_section, max_quotes):
        if not self.retrieval or not focus:
            return [], {}
        try:
            plan = self.retrieval.build_plan(
                focus, allowed_source_ids=source_ids,
                artifact_types=['argument_unit', 'sentence', 'transcript_segment', 'table'],
                argument_roles=['claim', 'methodology', 'reasoning', 'evidence', 'counterclaim', 'limitation'],
                artifact_families=['argument', 'structure', 'relation'],
                coverage_strategy='section_diverse', per_section_limit=quotes_per_section + 1,
                min_sections=max_sections, max_results=max_quotes * 4)
            results = self.retrieval.retrieve(plan)
            scores = {str(item.artifact_id): float(item.score) for item in results}
            rows = self.db.list_artifacts_by_ids(list(scores))
            identities = []
            for row in rows:
                identity = str(row.get('artifact_id') or '')
                canonical = (row.get('parent_artifact_id') if row.get('artifact_type') == 'argument_unit'
                             else identity)
                if canonical:
                    canonical = str(canonical)
                    identities.append(canonical)
                    scores[canonical] = max(scores.get(canonical, 0), scores.get(identity, 0))
            return self.db.list_artifacts_by_ids(identities), scores
        except Exception:
            # Retrieval is optional ranking assistance; canonical scoped reads remain authoritative.
            return [], {}

    @staticmethod
    def usable_text(text):
        return 5 <= len(str(text or '').split()) <= 220
