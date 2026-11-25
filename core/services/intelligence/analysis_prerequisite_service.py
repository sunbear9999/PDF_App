"""Shared source resolution and current-artifact readiness for analysis callers."""
from core.models.intelligence_models import ArtifactQuery, SourceScope
from core.models.analysis_requirements import normalize_requirements


def analysis_source_ids(project_manager, params):
    ids = params.get('allowed_source_ids') or []
    if not isinstance(ids, list) or any(not isinstance(value, str) for value in ids):
        raise ValueError('Analysis source IDs must be a list of strings.')
    if ids:
        return list(dict.fromkeys(value for value in ids if value))
    if params.get('scope') == 'project' and hasattr(project_manager, 'list_sources'):
        return [str(source['id']) for source in project_manager.list_sources(include_removed=False) if source.get('id')]
    if params.get('source_id'):
        return [str(params['source_id'])]
    if params.get('doc_path') and hasattr(project_manager, 'get_source_record_by_path'):
        source = project_manager.get_source_record_by_path(str(params['doc_path']))
        if source and source.get('id'):
            return [str(source['id'])]
    if hasattr(project_manager, 'list_sources'):
        return [str(source['id']) for source in project_manager.list_sources(include_removed=False) if source.get('id')]
    return []


class AnalysisPrerequisiteService:
    def __init__(self, project_manager, source_intelligence_service=None):
        self.project_manager = project_manager
        self.source_intelligence_service = source_intelligence_service

    def check(self, spec, params):
        db = getattr(self.project_manager, 'db_intelligence', None)
        if not db:
            return {'ready': False, 'missing': [{'kind': 'project', 'message': 'Open a project first.'}]}
        sources = analysis_source_ids(self.project_manager, params)
        groups = normalize_requirements(spec.artifact_requirements, spec.required_artifact_types)
        missing, found = [], {}
        if not sources and (groups or spec.required_stages):
            missing.append({'kind': 'sources', 'message': 'Choose at least one source.'})
        stages = self._stages(spec, sources)
        for identity, required in stages.items():
            statuses = db.get_stage_statuses(identity)
            missing.extend({'kind': 'stage', 'source_id': identity, 'stage': stage,
                            'status': (statuses.get(stage) or {}).get('status') or 'not_run'}
                           for stage in required if (statuses.get(stage) or {}).get('status') != 'complete')
        for group in groups:
            scopes = [[identity] for identity in sources] if group['scope'] == 'each_source' else [sources]
            for selected in scopes:
                matched = [kind for kind in group['artifact_types'] if self._has(db, kind, selected, found)]
                if not matched or (group['match'] == 'all' and len(matched) != len(group['artifact_types'])):
                    missing.append({'kind': 'artifact_types', **group, 'source_ids': selected,
                                    'missing_types': [kind for kind in group['artifact_types'] if kind not in matched]})
        return {'ready': not missing, 'missing': missing, 'source_ids': sources,
                'required_stages_by_source': stages, 'artifact_requirements': groups}

    def _stages(self, spec, source_ids):
        service = self.source_intelligence_service
        records = {str(row['id']): row for row in self.project_manager.list_sources(include_removed=False)} \
            if service and hasattr(self.project_manager, 'list_sources') else {}
        return {identity: service.resolve_analysis_stages(records[identity], spec.required_stages)
                if service and identity in records else list(spec.required_stages) for identity in source_ids}

    @staticmethod
    def _has(db, kind, sources, found):
        key = (kind, tuple(sources))
        if key not in found:
            found[key] = bool(sources and db.query_artifacts(ArtifactQuery(
                source_scope=SourceScope(source_ids=sources), artifact_types=[kind],
                statuses=['complete'], review_states=['unreviewed', 'verified', 'corrected'],
                current_only=True, limit=1))['items'])
        return found[key]
