"""Prepare run-local, source-backed evidence without classifying or writing artifacts."""
from __future__ import annotations

import hashlib
import json

from core.services.intelligence.analysis_evidence_candidates import AnalysisEvidenceCandidates
from core.services.intelligence.analysis_evidence_scoring import empty_evidence_package
from core.services.intelligence.analysis_evidence_selection import rank_sections, select_quotes
from core.services.intelligence.analysis_grounding import artifact_snapshot

MAX_SECTIONS = 12
MAX_QUOTES_PER_SECTION = 4
MAX_QUOTES = 28


class AnalysisEvidencePreparationService:
    PRODUCER_VERSION = "3.0"

    def __init__(self, project_manager=None, artifact_service=None, hybrid_retrieval_service=None):
        self.project_manager = project_manager
        self.artifact_service = artifact_service
        self.hybrid_retrieval_service = hybrid_retrieval_service

    @property
    def db(self):
        return getattr(self.project_manager, "db_intelligence", None)

    def prepare(self, *, mode_id, focus, source_ids, graph_contract=None,
                max_sections=7, quotes_per_section=2, max_quotes=16):
        source_ids = list(dict.fromkeys(str(value) for value in source_ids if value))
        limits = dict(max_sections=max(1, min(MAX_SECTIONS, int(max_sections))),
                      quotes_per_section=max(1, min(MAX_QUOTES_PER_SECTION, int(quotes_per_section))),
                      max_quotes=max(1, min(MAX_QUOTES, int(max_quotes))))
        contract, focus = dict(graph_contract or {}), str(focus or "").strip()
        signature = hashlib.sha1(f"{mode_id}|{focus.lower()}|{json.dumps(contract, sort_keys=True, default=str)}"
                                 .encode("utf-8", "replace")).hexdigest()[:16]
        if not self.db or not source_ids:
            return empty_evidence_package(signature)
        candidates = AnalysisEvidenceCandidates(self.db, self.hybrid_retrieval_service)
        sentences, summaries, scores, diagnostics = candidates.load(source_ids, focus, **limits)
        if not sentences:
            return {**empty_evidence_package(signature), 'focus_eligibility': diagnostics}
        ranked, documents, method_intent = rank_sections(
            sentences, summaries, scores, focus=focus, mode_id=mode_id, contract=contract)
        sections = select_quotes(ranked, source_ids, method_intent=method_intent, **limits)
        return self._package(sections, documents, mode_id, focus, signature, diagnostics)

    @classmethod
    def _package(cls, sections, documents, mode_id, focus, signature, diagnostics):
        selected, packets, snapshots = [], [], {}
        for section in sections:
            source_id, heading = section['key']
            quotes = []
            for score, row, role, reused in section['picks']:
                alias, identity = f"q{len(selected) + 1}", row['artifact_id']
                item = dict(alias=alias, artifact_id=identity, selection_artifact_id=None,
                            source_id=source_id, section=heading, suggested_role=role,
                            score=round(float(score), 4), exact_text=str(row.get('exact_text') or ''),
                            locator=row.get('locator') or {}, section_path=row.get('section_path') or [],
                            reused=reused, focus_basis=row.get('_analysis_focus_basis'))
                selected.append(item)
                snapshots[identity] = artifact_snapshot(row)
                # Source passages are atomic verbatim evidence, never clipped snippets.
                quotes.append({'q': alias, 'r': role, 'x': item['exact_text']})
            packets.append(dict(id=f"s{len(packets) + 1}", source_id=source_id,
                                heading=heading, summary=cls._clip(
                                    str((section['summary'] or {}).get('exact_text') or ''), 170), quotes=quotes))
        overview = [{'source_id': row.get('source_id'), 'text': cls._clip(str(row.get('exact_text') or ''), 200)}
                    for row in sorted(documents, key=lambda row: str(row.get('source_id') or ''))[:2]]
        package = dict(focus=focus, mode=mode_id, overview_read_only=overview, sections=packets)
        return dict(context=json.dumps(package, ensure_ascii=False, separators=(',', ':')),
                    package=package, selected=selected,
                    alias_to_artifact_id={item['alias']: item['artifact_id'] for item in selected},
                    alias_to_selection_artifact_id={}, selection_artifact_ids=[],
                    focus_signature=signature, reused_count=sum(int(item['reused']) for item in selected),
                    selected_count=len(selected), section_count=len(packets),
                    focus_eligibility=diagnostics, artifact_snapshots=snapshots)

    @staticmethod
    def _clip(text, limit):
        text = " ".join(str(text or "").split())
        return text if len(text) <= limit else text[:limit - 1].rstrip() + "…"
