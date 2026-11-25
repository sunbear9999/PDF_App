"""Source-backed temporal interpretations; extraction never owns these records."""
from contextlib import nullcontext
import uuid

from core.db.intelligence_decision_journal import transaction
from core.models.event_projection import calendar_interval
from core.models.intelligence_models import ArtifactRecord
from core.models.producer_models import human
from core.services.intelligence.reference_note_service import digest

PARTICIPANT_ROLES = ('co-mentioned', 'participant', 'organizer', 'subject', 'location', 'witness')
EVENT_STATES = ('candidate', 'accepted', 'dismissed')
MAX_EVENT_SUMMARY = 4000


def notify(service, row):
    from core.events.domains.intelligence_events import IntelligenceEvent, ArtifactReviewPayload
    signal=getattr(getattr(service.project_manager,'bus',None),'intelligence_state_changed',None)
    if signal is not None:
        signal.emit(IntelligenceEvent.ARTIFACT_REVIEWED, ArtifactReviewPayload(row['artifact_id'],row['source_id'],row['revision']))


def validate(values):
    if not isinstance(values, dict) or set(values) != {'summary', 'date', 'date_end', 'certainty', 'decision', 'roles'}:
        raise ValueError('Supply the typed event fields, without changing source evidence.')
    summary = values['summary']
    if not isinstance(summary, str) or not 1 <= len(summary.strip()) <= MAX_EVENT_SUMMARY:
        raise ValueError('Give the event a summary between 1 and 4000 characters.')
    start, end = calendar_interval(values['date']), calendar_interval(values['date_end'])
    if not start or (end and end[1] < start[0]):
        raise ValueError('Choose a start date and an end on or after it.')
    if not isinstance(values['certainty'], str) or not isinstance(values['decision'], str) or values['certainty'] not in {'confirmed', 'likely', 'ambiguous', 'unknown'} or values['decision'] not in EVENT_STATES:
        raise ValueError('Choose a valid certainty and candidate decision.')
    roles = values['roles']
    if not isinstance(roles, dict) or len(roles) > 32 or any(
            not isinstance(key, str) or not 0 < len(key) <= 512 or not isinstance(role, str) or role not in PARTICIPANT_ROLES for key, role in roles.items()):
        raise ValueError('Choose recorded entities and valid participant roles (maximum 32).')
    return {'event_summary': summary.strip(), 'normalized_date': values['date'],
            'event_date_end': values['date_end'], 'certainty': values['certainty'],
            'event_decision': values['decision'], 'participant_roles': dict(roles),
            'precision': 'range' if end else {4: 'year', 7: 'month', 10: 'day'}[len(values['date'])]}


def snapshot(service, artifact_id):
    from core.db.event_projection_queries import contexts
    from core.models.event_projection import coerce_filter
    from core.services.intelligence.event_associations import participants
    from core.services.intelligence.artifact_eligibility import is_current_artifact
    if service.db is None:
        raise ValueError('Open a project before reviewing events.')
    row = service.db.get_artifact(artifact_id)
    if not row or row['artifact_type'] != 'date_mention' or not is_current_artifact(service.db, row):
        raise ValueError('Choose a current date/event candidate before editing.')
    if row['metadata'].get('migration_conflict'):
        raise ValueError('Resolve the source correspondence before reviewing this event.')
    windows, mentions, truncated, blocks = contexts(service.db, [row])
    context = windows.get(artifact_id) or {}
    people, _, _ = participants(row, context, mentions.get(context.get('artifact_id'), []), blocks, coerce_filter(), truncated)
    return {'row': row, 'participants': people[:32], 'participants_truncated': len(people) > 32 or truncated,
            'project_identity': service.project_identity}


def save(service, reference, values, *, expected_token, expected_project, request_id):
    """Reuse the verified note-source preview; publish one atomic canonical event."""
    patch = validate(values)
    identity = 'manual_event:' + str(uuid.UUID(request_id))
    db, pm = service.db, service.project_manager
    if db is None:
        raise ValueError('Open a project before authoring events.')
    with db._write_lock, getattr(pm, 'db_lock', nullcontext()):
        if tuple(expected_project) != service.project_identity:
            raise ValueError('Project changed; reload the source before saving.')
        prepared = service.prepare(reference)
        if prepared['token'] != expected_token:
            raise ValueError('The source changed; reload its preview before saving.')
        if values['roles']:
            raise ValueError('Create the event first, then assign roles to recorded co-mentions.')
        request_hash = digest([prepared['reference'], values])
        prior = db.get_artifact(identity)
        if prior:
            if prior['metadata'].get('save_request_hash') != request_hash:
                raise ValueError('This authoring request was already used; start a new event.')
            return {'row': prior, 'reused': True}
        parent = db.get_artifact(prepared['current_artifact_id'])
        if parent['artifact_type'] not in {'sentence', 'transcript_segment'}:
            raise ValueError('Create an event from a sentence or transcript segment with recorded evidence.')
        metadata = {**patch, 'manual_event': True, 'timeline_eligible': True, 'context_role': 'event',
            'stage': 'user_event', 'saved_anchor': prepared['anchor'], 'saved_source_hash': prepared['source_hash'],
            'artifact_ref': prepared['reference'], 'save_request_hash': request_hash}
        with transaction(db):
            db.upsert_artifact(human().stamp(ArtifactRecord(artifact_id=identity, source_id=parent['source_id'],
                source_path=parent.get('source_path'), source_type=parent.get('source_type'),
                artifact_type='date_mention', storage_ref_type='user_event', storage_ref_id=identity,
                parent_artifact_id=parent['artifact_id'], exact_text=prepared['reference']['quote'],
                normalized_text=values['date'], locator=parent['locator'], created_by='user', metadata=metadata)))
    row=db.get_artifact(identity)
    notify(service,row)
    return {'row': row, 'reused': False}


def update(service, artifact_id, values, *, expected_revision, expected_project):
    patch = validate(values)
    db, pm = service.db, service.project_manager
    if db is None:
        raise ValueError('Open a project before updating events.')
    with db._write_lock, getattr(pm, 'db_lock', nullcontext()):
        state = snapshot(service, artifact_id)
        row = state['row']
        if tuple(expected_project) != state['project_identity'] or expected_revision != row['revision']:
            raise ValueError('The event or project changed. Reload before saving; your draft is retained.')
        allowed = {person['entity_id'] for person in state['participants']}
        if set(values['roles']) - allowed:
            raise ValueError('Assign roles only to current recorded co-mentions in this evidence window.')
        # Preserve roles beyond the bounded preview, without treating them as editable choices.
        roles = {**row['metadata'].get('participant_roles', {}), **patch['participant_roles']}
        patch['participant_roles'] = roles
        from core.db import intelligence_decision_journal as journal
        with transaction(db):
            db.save_artifact_revision(artifact_id, field_changes={'metadata': {**row['metadata'], **patch}}, created_by='user')
            updated = db.get_artifact(artifact_id)
            receipt = journal.save(db, artifact_id, 'artifact', {'artifact_id': artifact_id, 'before': row,
                'after_revision': updated['revision'], 'citation_changed': False}, updated['revision'])
    notify(service,updated)
    return {'row': updated, 'receipt': receipt}
