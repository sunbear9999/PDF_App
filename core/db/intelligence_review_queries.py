"""SQL issue predicates applied before pagination and counts."""


DEFAULT_REVIEW_CONFIDENCE = 0.6


def issue_clause(kind, alias="a"):
    from core.db.intelligence_citation_queries import effective_target
    meta = f"{alias}.metadata_json"
    role = f"COALESCE(json_extract({meta}, '$.argument_role'), json_extract({meta}, '$.class_label'), 'unknown')"
    predicates = {
        "low_confidence": f"({alias}.confidence<{DEFAULT_REVIEW_CONFIDENCE} OR json_extract({meta}, '$.disagreement')=1)",
        "unclassified": f"{alias}.artifact_type IN ('argument_unit','sentence','transcript_segment') AND {role}='unknown' AND ({alias}.artifact_type='argument_unit' OR json_extract({meta}, '$.argument_eligible')=1)",
        "citations": f"{alias}.artifact_type='in_text_citation' AND (json_extract({meta}, '$.unresolved')=1 OR json_extract({meta}, '$.ambiguous')=1 OR json_array_length(json_extract({meta}, '$.unmatched_numeric_keys'))>0 OR ({effective_target(alias)}) IS NULL)",
        "dates": f"{alias}.artifact_type='date_mention' AND (COALESCE(json_extract({meta}, '$.certainty'),'unknown') IN ('unknown','ambiguous') OR COALESCE(json_extract({meta}, '$.precision'),'unresolved')='unresolved')",
        "entities": f"{alias}.artifact_type IN ('entity','entity_mention') AND ({alias}.confidence<{DEFAULT_REVIEW_CONFIDENCE} OR COALESCE(json_extract({meta}, '$.entity_type'),'unknown') IN ('unknown','UNKNOWN') OR json_extract({meta}, '$.uncertain')=1)",
        "migration": f"json_extract({meta}, '$.migration_conflict') IS NOT NULL AND json_extract({meta}, '$.migration_conflict')!=0",
    }
    if kind not in predicates:
        raise ValueError("Unknown review issue filter.")
    return f"({predicates[kind]})"
