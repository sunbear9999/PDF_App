"""Batch evidence-window reads for projections; never extracts or modifies artifacts."""
import json
from core.models.event_projection import calendar_interval
from core.db.event_context_sql import ACTIVE,CONTEXT_SQL

MAX_CONTEXT_MENTIONS = 8192


def calendar_filters(filters):
    """Safe prefilter; final interval validation retains coarse year/month dates."""
    expression="COALESCE(NULLIF(json_extract(a.metadata_json,'$.normalized_date'),''),a.normalized_text,'')"
    clauses,values=[],[]
    end_expression=f"COALESCE(NULLIF(json_extract(a.metadata_json,'$.event_date_end'),''),{expression})"
    if filters.date_from:
        lower=calendar_interval(filters.date_from)[0]
        clauses.append(f'({end_expression}>=? OR {end_expression}=? OR {end_expression}=?)')
        values.extend([lower,lower[:4],lower[:7]])
    if filters.date_to:
        clauses.append(f'{expression}<=?')
        values.append(calendar_interval(filters.date_to)[1])
    entity_clause=entity_page_filter(filters)
    if entity_clause:
        clauses.append(entity_clause[0]); values.extend(entity_clause[1])
    return clauses,values


# A sentence can continue onto the next page, so a date counts as near a mention
# on the same page or an adjacent one. Matching is still decided per sentence later.
NEAR_PAGES=1


def entity_page_filter(filters):
    """Sound prefilter for sentence/word associations: skip dates on pages that mention
    none of the entities every match must name. Turns a full scan into a few pages."""
    from core.models.entity_expression import required_any
    if filters.association not in {'sentence','word_window'}:
        return None
    required=required_any(filters.expression)
    if not required:
        return None
    return (f"(json_extract(a.locator_json,'$.page') IS NULL OR EXISTS (SELECT 1 FROM intelligence_artifacts em WHERE em.artifact_type='entity_mention' "
            f"AND em.parent_artifact_id IN (SELECT value FROM json_each(?)) AND em.source_id=a.source_id "
            f"AND em.{ACTIVE.replace(' AND review_state',' AND em.review_state')} "
            f"AND (json_extract(em.locator_json,'$.page') IS NULL OR "   # no page: cannot rule the date out
            f"ABS(CAST(json_extract(em.locator_json,'$.page') AS INTEGER)-"
            f"CAST(json_extract(a.locator_json,'$.page') AS INTEGER))<=?)))",[json.dumps(sorted(required)),NEAR_PAGES])


def ensure_indexes(db):
    conn=db._conn
    conn.execute("CREATE INDEX IF NOT EXISTS idx_event_context_block ON intelligence_artifacts "
        "(source_id,artifact_type,json_extract(locator_json,'$.block_id'),json_extract(locator_json,'$.char_start'))")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_event_mention_evidence ON intelligence_artifacts "
        "(artifact_type,json_extract(metadata_json,'$.evidence_artifact_id'))")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_event_mention_sentence ON intelligence_artifacts "
        "(artifact_type,json_extract(locator_json,'$.sentence_id'))")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_event_merged_sentence ON intelligence_artifacts (source_id) "
        "WHERE artifact_type='sentence' AND json_array_length(locator_json,'$.source_spans')>1")


def contexts(db,dates):
    if not dates:
        return {},{},False,{}
    ensure_indexes(db)
    # Integer source offsets and same-source containment are mandatory. Repeated
    # prose is never resolved by quote search or a nearest-page heuristic.
    pairs=db._conn.execute(CONTEXT_SQL,(json.dumps([row['artifact_id'] for row in dates]),)).fetchall()
    records=read_artifacts(db,{pair[1] for pair in pairs if pair[1]})
    by_date={identity:records[context] for identity,context in pairs if context in records}
    manual = {row['artifact_id']: row.get('parent_artifact_id') for row in dates if row.get('metadata', {}).get('manual_event')}
    parents = read_artifacts(db, set(manual.values()) - {None, ''})
    for row in dates:
        parent = parents.get(manual.get(row['artifact_id']))
        if parent and parent['source_id'] == row['source_id'] and parent['status'] == 'complete' and parent['review_state'] in {'unreviewed', 'verified', 'corrected'}:
            records[parent['artifact_id']] = parent
            by_date[row['artifact_id']] = parent
    mentions,truncated=mentions_for_contexts(db,records)
    blocks=read_artifacts(db,{row.get('parent_artifact_id') for row in records.values()}-{None,''})
    return by_date,mentions,truncated,blocks


def mentions_for_contexts(db,records):
    if not records:
        return {},False
    keys=list(records)
    references=list({str(row.get('storage_ref_id') or '') for row in records.values()}-{''})
    rows=db._conn.execute(f"SELECT artifact_id FROM intelligence_artifacts WHERE {ACTIVE} "
        "AND artifact_type='entity_mention' AND (json_extract(metadata_json,'$.evidence_artifact_id') "
        "IN (SELECT value FROM json_each(?)) OR json_extract(locator_json,'$.sentence_id') "
        "IN (SELECT value FROM json_each(?))) ORDER BY artifact_id LIMIT ?",
        (json.dumps(keys),json.dumps(keys+references),MAX_CONTEXT_MENTIONS+1)).fetchall()
    mentions=read_artifacts(db,[row[0] for row in rows[:MAX_CONTEXT_MENTIONS]])
    entities=read_artifacts(db,{row.get('parent_artifact_id') for row in mentions.values()}-{None,''})
    by_reference={(row['source_id'],str(row.get('storage_ref_id') or '')):identity for identity,row in records.items()}
    output={identity:[] for identity in records}
    for mention in mentions.values():
        entity=entities.get(mention.get('parent_artifact_id'))
        if not entity or entity.get('artifact_type')!='entity' or entity.get('status')!='complete' or entity.get('review_state') not in {'unreviewed','verified','corrected'}:
            continue
        context=(mention.get('metadata') or {}).get('evidence_artifact_id')
        sentence=str((mention.get('locator') or {}).get('sentence_id') or '')
        if context not in records:
            context=sentence if sentence in records else by_reference.get((mention.get('source_id'),sentence))
        if context in records and records[context].get('source_id')==mention.get('source_id'):
            output[context].append({**mention,'entity':entity})
    return output,len(rows)>MAX_CONTEXT_MENTIONS


def read_artifacts(db,identities):
    return {row['artifact_id']:row for row in db.iter_artifacts_by_ids(identities)}


def resolve_entity(db,name,bindings):
    if name in bindings:
        try:
            identity=db.resolve_current_artifact_id(bindings[name])
        except ValueError:
            raise ValueError(f'The selected entity “{name}” needs grouping review; choose a current identity.') from None
        row=db.get_artifact(identity)
        if row and row['artifact_type']=='entity' and row['status']=='complete' and row['review_state'] in {'unreviewed','verified','corrected'}:
            return identity
        raise ValueError(f'The selected entity “{name}” is no longer current; choose it again.')
    rows=db._conn.execute(f"SELECT artifact_id,exact_text,json_extract(metadata_json,'$.entity_type') FROM intelligence_artifacts "
        f"WHERE {ACTIVE} AND artifact_type='entity' "
        "AND (exact_text=? COLLATE NOCASE OR normalized_key=? COLLATE NOCASE OR normalized_text=? COLLATE NOCASE) LIMIT ?",
        (name,name,name,MAX_NAME_CHOICES+1)).fetchall()
    if not rows:
        raise ValueError(f'No entity is named “{name}”. Check the spelling, or use Find entity to search by part of the name.')
    if len(rows)>1:
        from core.models.entity_categories import type_label
        choices=', '.join(f'{text} ({type_label(kind)})' for _,text,kind in rows[:MAX_NAME_CHOICES])
        raise ValueError(f'“{name}” names more than one entity: {choices}. Use Find entity to choose which one.')
    return rows[0][0]


MAX_NAME_CHOICES=5
