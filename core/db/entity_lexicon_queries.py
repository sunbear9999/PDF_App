"""Project-wide read of directly evidenced entity names, for the project lexicon."""
import re

# Mentions another mention produced; a lexicon built on them would feed itself.
DERIVED_METHODS = ("propagated", "project_propagated", "nominal_coref")
_METHOD = "COALESCE(json_extract(m.metadata_json,'$.method'),'')"


def lexicon_rows(conn):
    """(surface, type, key, canonical id, source id, mentions, settled mentions) per direct name."""
    marks = ",".join("?" for _ in DERIVED_METHODS)
    return conn.execute(
        "SELECT m.exact_text, json_extract(m.metadata_json,'$.entity_type'), m.normalized_key, "
        "m.parent_artifact_id, m.source_id, COUNT(*), "
        "SUM(COALESCE(json_extract(m.metadata_json,'$.type_settled'),0)) "
        "FROM intelligence_artifacts m LEFT JOIN intelligence_artifacts c ON c.artifact_id=m.parent_artifact_id "
        "WHERE m.artifact_type='entity_mention' AND m.status='complete' "
        "AND m.review_state NOT IN ('rejected','superseded') "
        "AND COALESCE(c.review_state,'') NOT IN ('rejected','superseded') "
        f"AND {_METHOD} NOT IN ({marks}) GROUP BY 1,2,3,4,5", DERIVED_METHODS).fetchall()


_LOWER_WORD = re.compile(r"(?<![A-Za-z])[a-z]+(?![A-Za-z])")
_VOCABULARY: dict = {}       # connection id -> (sentence version, set of lowercase words)


def words_used_lowercase(conn, words):
    """Which of ``words`` (lowercase, letters only) any sentence in the project writes in lowercase.
    The project's lowercase vocabulary is read once and kept until sentences change: one scan
    per word took 0.3 s each on a 620-source project, minutes per linking run."""
    vocabulary = lowercase_vocabulary(conn)
    return {word for word in words if word.isalpha() and word in vocabulary}


def lowercase_vocabulary(conn):
    version = conn.execute("SELECT COUNT(*), MAX(rowid), SUM(revision), MAX(updated_at) FROM intelligence_artifacts "
                           "WHERE artifact_type='sentence' AND status='complete'").fetchone()
    cached = _VOCABULARY.get(id(conn))
    if cached and cached[0] == version:
        return cached[1]
    words = set()
    for (text,) in conn.execute("SELECT exact_text FROM intelligence_artifacts "
                                "WHERE artifact_type='sentence' AND status='complete'"):
        words.update(_LOWER_WORD.findall(text or ""))
    _VOCABULARY.clear()                      # one project at a time
    _VOCABULARY[id(conn)] = (version, words)
    return words


def lexicon_version(conn):
    """Changes whenever the lexicon can: a finished Entities run or a rejected entity."""
    runs = conn.execute("SELECT COUNT(*), MAX(finished_at) FROM intelligence_stage_runs "
                        "WHERE stage='entities' AND status='complete'").fetchone()
    rejected = conn.execute("SELECT COUNT(*) FROM intelligence_artifacts "
                            "WHERE artifact_type='entity' AND review_state='rejected'").fetchone()
    return (runs[0], runs[1], rejected[0])
