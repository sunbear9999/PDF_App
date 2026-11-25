"""Indexed evidence containment, including normalized multiblock sentences.

The multiblock branch names its partial index (created by ``ensure_indexes`` before
every use): left to itself the planner scanned every sentence of the source for each
date (2.9 s per 128 dates on a 424-page book; 0.6 s with the index)."""

ACTIVE="status='complete' AND review_state IN ('unreviewed','verified','corrected')"
CURRENT_C="c."+ACTIVE.replace(' AND review_state',' AND c.review_state')
CURRENT_P="p."+ACTIVE.replace(' AND review_state',' AND p.review_state')

CONTEXT_SQL=f"""SELECT d.artifact_id,COALESCE(
    (SELECT p.artifact_id FROM intelligence_artifacts p WHERE p.artifact_id=d.parent_artifact_id
     AND p.source_id=d.source_id AND p.artifact_type='transcript_segment' AND {CURRENT_P}
     AND (json_extract(d.locator_json,'$.block_id') IS NULL OR json_extract(d.locator_json,'$.block_id')=p.artifact_id)
     AND json_type(d.locator_json,'$.char_start')='integer' AND json_type(d.locator_json,'$.char_end')='integer'
     AND json_extract(d.locator_json,'$.char_start')>=0
     AND json_extract(d.locator_json,'$.char_end')>json_extract(d.locator_json,'$.char_start')
     AND json_extract(d.locator_json,'$.char_end')<=length(p.exact_text)),
    (SELECT CASE WHEN COUNT(*)=1 THEN MIN(artifact_id) END FROM (
      SELECT c.artifact_id FROM intelligence_artifacts c WHERE c.source_id=d.source_id
       AND c.artifact_type IN ('sentence','transcript_segment') AND {CURRENT_C}
       AND json_extract(c.locator_json,'$.block_id')=json_extract(d.locator_json,'$.block_id')
       AND json_type(d.locator_json,'$.char_start')='integer' AND json_type(d.locator_json,'$.char_end')='integer'
       AND json_type(c.locator_json,'$.char_start')='integer' AND json_type(c.locator_json,'$.char_end')='integer'
       AND json_extract(d.locator_json,'$.char_start')>=0
       AND json_extract(d.locator_json,'$.char_end')>json_extract(d.locator_json,'$.char_start')
       AND json_extract(c.locator_json,'$.char_start')<=json_extract(d.locator_json,'$.char_start')
       AND json_extract(c.locator_json,'$.char_end')>=json_extract(d.locator_json,'$.char_end')
      UNION
      SELECT c.artifact_id FROM intelligence_artifacts c INDEXED BY idx_event_merged_sentence
       WHERE c.source_id=d.source_id
       AND c.artifact_type='sentence' AND {CURRENT_C}
       AND json_array_length(c.locator_json,'$.source_spans')>1
       AND EXISTS(SELECT 1 FROM json_each(c.locator_json,'$.source_spans') span
         WHERE json_extract(span.value,'$.block_id')=json_extract(d.locator_json,'$.block_id')
         AND json_type(span.value,'$.char_start')='integer' AND json_type(span.value,'$.char_end')='integer'
         AND json_type(d.locator_json,'$.char_start')='integer' AND json_type(d.locator_json,'$.char_end')='integer'
         AND json_extract(d.locator_json,'$.char_start')>=0
         AND json_extract(d.locator_json,'$.char_end')>json_extract(d.locator_json,'$.char_start')
         AND json_extract(span.value,'$.char_start')<=json_extract(d.locator_json,'$.char_start')
         AND json_extract(span.value,'$.char_end')>=json_extract(d.locator_json,'$.char_end'))
    ))) FROM intelligence_artifacts d WHERE d.artifact_id IN (SELECT value FROM json_each(?))"""
