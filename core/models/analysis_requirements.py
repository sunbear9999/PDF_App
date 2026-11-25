"""Explicit all/any artifact requirements, with a documented legacy adapter."""

DEFAULT_GRAPH_STAGES = ('parsed_blocks', 'sections', 'claims', 'summaries')


def normalize_requirements(groups=None, legacy_types=()):
    if not isinstance(legacy_types, (list, tuple)) or any(not isinstance(kind, str) for kind in legacy_types):
        raise ValueError('Legacy artifact types must be a list of names.')
    if groups is None:
        groups = ([{'artifact_types': list(legacy_types), 'match': 'any', 'scope': 'selection'}]
                  if legacy_types else [])
    if not isinstance(groups, list):
        raise ValueError('Artifact requirements must be a list of groups.')
    result = []
    for group in groups:
        if not isinstance(group, dict) or set(group) - {'artifact_types', 'match', 'scope'}:
            raise ValueError('Each artifact requirement needs artifact_types, match and scope only.')
        types = group.get('artifact_types')
        if not isinstance(types, list) or not types or any(not isinstance(t, str) or not t.strip() for t in types):
            raise ValueError('Artifact requirements need a non-empty list of artifact types.')
        match, scope = group.get('match'), group.get('scope')
        if (not isinstance(match, str) or not isinstance(scope, str)
                or match not in {'all', 'any'} or scope not in {'selection', 'each_source'}):
            raise ValueError('Artifact requirements must declare match all/any and scope selection/each_source.')
        result.append({'artifact_types': list(dict.fromkeys(types)), 'match': match, 'scope': scope})
    return result


def template_prerequisites(template):
    """Missing groups use the legacy adapter; explicit [] means no artifact gate."""
    return {'required_stages': template.get('required_stages', list(DEFAULT_GRAPH_STAGES)),
            'artifact_requirements': normalize_requirements(
                template.get('artifact_requirements'), template.get('required_artifact_types', ['argument_unit']))}


def describe_missing(missing):
    messages = []
    for item in missing:
        if item.get('kind') == 'stage':
            message = f"Run {item['stage'].replace('_', ' ')} ({item.get('status', 'not run')})."
        elif item.get('kind') == 'artifact_types':
            types = ', '.join(kind.replace('_', ' ') for kind in item['artifact_types'])
            scope = 'each selected source' if item['scope'] == 'each_source' else 'the selected sources'
            message = f"Need {item['match']} of: {types}, in {scope}."
        else:
            message = item.get('message') or 'Analysis inputs are unavailable.'
        if message not in messages:
            messages.append(message)
    return ' '.join(messages)
