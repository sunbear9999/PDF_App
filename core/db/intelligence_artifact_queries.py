"""Shared typed SQL queries with revision-checked, query-bound cursor pages.

Pages are not immutable snapshots: a changed knowledge revision invalidates the
cursor explicitly. Consumers must restart rather than combine different versions.
"""
from dataclasses import asdict, replace
import hashlib
import json
import sqlite3

from core.models.intelligence_models import ArtifactQuery, SourceScope


MAX_PAGE_SIZE = 1000
MAX_MENTION_PREVIEW = 50


class ArtifactSnapshotChanged(RuntimeError):
    """The caller must restart its read after an intervening intelligence edit."""


def coerce_query(query):
    if isinstance(query, ArtifactQuery):
        return query
    payload = dict(query)
    if isinstance(payload.get("source_scope"), dict):
        payload["source_scope"] = SourceScope(**payload["source_scope"])
    return ArtifactQuery(**payload)


def plan_query(plan, source_ids):
    """The same typed restrictions are used by every retrieval signal."""
    names = ("artifact_types", "review_states", "argument_roles", "section_paths",
             "artifact_families", "tag_filters", "tag_logic", "has_citation",
             "has_table", "block_types")
    return ArtifactQuery(
        source_scope=SourceScope(source_ids=source_ids),
        statuses=["complete", "stale"] if plan.include_stale else ["complete"],
        current_only=True,
        **{name: getattr(plan, name) for name in names},
    )


def compile_filters(query, families, alias="a"):
    clauses, params = [], []
    scope = query.source_scope
    if scope.unresolved_refs or (not scope.source_ids and (scope.source_paths or scope.display_names)):
        clauses.append("0")
    fields = {
        "source_id": scope.source_ids, "artifact_type": query.artifact_types,
        "status": query.statuses, "review_state": query.review_states,
        "created_by": query.created_by, "model_name": query.model_names,
        "parent_artifact_id": query.parent_artifact_ids,
        "json_extract(metadata_json, '$.entity_type')": query.entity_types,
        "COALESCE(json_extract(metadata_json, '$.argument_role'), json_extract(metadata_json, '$.class_label'))": query.argument_roles,
        "json_extract(metadata_json, '$.block_type')": query.block_types,
    }
    for expression, values in fields.items():
        if values:
            expression = _qualify(expression, alias)
            clauses.append(f"{expression} IN ({','.join('?' for _ in values)})")
            params.extend(values)
    if query.exclude_entity_types:
        clauses.append(f"COALESCE(json_extract({alias}.metadata_json, '$.entity_type'),'') NOT IN "
                       f"({','.join('?' for _ in query.exclude_entity_types)})")
        params.extend(query.exclude_entity_types)
    if query.source_tags:
        clauses.append(f"{alias}.source_id IN ({SOURCE_TAG_SQL})")
        params.append(json.dumps(list(query.source_tags)))
    if query.exclude_artifact_ids:
        clauses.append(f"{alias}.artifact_id NOT IN (SELECT value FROM json_each(?))")
        params.append(json.dumps(query.exclude_artifact_ids))
    if query.issue_kind:
        from core.db.intelligence_review_queries import issue_clause
        clauses.append(issue_clause(query.issue_kind, alias))
    if query.bibliography_ids:
        from core.db.intelligence_citation_queries import effective_target
        clauses.append(f"({effective_target(alias)}) IN (SELECT value FROM json_each(?))")
        params.append(json.dumps(query.bibliography_ids))
    _extra_filters(query, families, alias, clauses, params)
    if query.current_only:
        clauses.extend(_eligibility_clauses(alias))
    return clauses, params


def _eligibility_clauses(alias):
    active = "('unreviewed','verified','corrected')"
    metadata = f"{alias}.metadata_json"
    return [
        f"{alias}.review_state IN {active}",
        f"{alias}.status IN ('complete','stale')",
        f"({alias}.artifact_type!='argument_unit' OR EXISTS ("
        f"SELECT 1 FROM intelligence_artifacts parent WHERE parent.artifact_id={alias}.parent_artifact_id "
        f"AND parent.status='complete' AND parent.review_state IN {active} "
        f"AND COALESCE(parent.exact_text,'')=COALESCE({alias}.exact_text,'') "
        f"AND ({alias}.review_state IN ('corrected','verified') OR ("
        f"COALESCE(json_extract({metadata}, '$.argument_eligible'),1)!=0 "
        "AND COALESCE(json_extract(parent.metadata_json, '$.argument_eligible'),1)!=0 "
        f"AND (NULLIF(json_extract(parent.metadata_json, '$.class_label'),'') IS NULL OR "
        f"NULLIF(json_extract({metadata}, '$.argument_role'),'') IS NULL OR "
        f"json_extract(parent.metadata_json, '$.class_label')=json_extract({metadata}, '$.argument_role'))))))",
    ]


def _qualify(expression, alias):
    if "metadata_json" in expression:
        return expression.replace("metadata_json", f"{alias}.metadata_json")
    return f"{alias}.{expression}"


def _extra_filters(query, families, alias, clauses, params):
    if query.artifact_families:
        types = families(query.artifact_families)
        choices = [f"{alias}.artifact_type IN ({','.join('?' for _ in types)})"] if types else []
        params.extend(types)
        if "derived" in query.artifact_families:
            from core.models.artifact_families import ARTIFACT_FAMILIES
            known = sorted(set().union(*ARTIFACT_FAMILIES.values()))
            choices.append(f"{alias}.artifact_type NOT IN ({','.join('?' for _ in known)})")
            params.extend(known)
        clauses.append("(" + " OR ".join(choices) + ")" if choices else "0")
    if query.section_paths:
        clauses.append("(" + " OR ".join(f"json({alias}.section_path_json)=json(?)" for _ in query.section_paths) + ")")
        params.extend(json.dumps(path) for path in query.section_paths)
    for field, operator, value in (
        ("confidence", ">=", query.min_confidence), ("confidence", "<=", query.max_confidence),
        ("normalized_text", ">=", query.date_from or None), ("normalized_text", "<=", query.date_to or None),
    ):
        if value is not None:
            clauses.append(f"{alias}.{field}{operator}?")
            params.append(value)
    if query.stage:
        clauses.append(f"json_extract({alias}.metadata_json, '$.stage')=?")
        params.append(query.stage)
    for name in ("has_citation", "has_table"):
        if getattr(query, name) is not None:
            clauses.append(f"json_extract({alias}.metadata_json, '$.{name}')=?")
            params.append(int(getattr(query, name)))
    if query.timeline_only:
        clauses.append(f"{alias}.artifact_type='date_mention'")
        clauses.append(f"COALESCE(json_extract({alias}.metadata_json, '$.timeline_eligible'),1)=1")
    _text_and_tags(query, alias, clauses, params)


def _text_and_tags(query, alias, clauses, params):
    if query.text.strip():
        text = query.text.strip().replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        clauses.append("(" + " OR ".join(f"{alias}.{field} LIKE ? ESCAPE '\\'" for field in ("exact_text", "normalized_text", "metadata_json")) + ")")
        params.extend([f"%{text}%"] * 3)
    if query.tag_logic.upper() not in {"AND", "OR"}:
        raise ValueError("tag_logic must be AND or OR")
    tags = []
    for tag in query.tag_filters:
        tags.append(f"(json_extract({alias}.metadata_json, ?)=1 OR EXISTS (SELECT 1 FROM json_each({alias}.metadata_json, '$.tags') WHERE value=?))")
        params.extend(['$."tag_' + tag.replace('"', '\\"') + '"', tag])
    if tags:
        clauses.append("(" + f" {query.tag_logic.upper()} ".join(tags) + ")")


def revision_token(db):
    rows = db._conn.execute(
        "SELECT source_id, family, revision FROM intelligence_knowledge_revisions WHERE family!='embedding' ORDER BY source_id, family"
    ).fetchall()
    return hashlib.sha256(json.dumps([list(row) for row in rows]).encode()).hexdigest()


def fingerprint(query, mode):
    payload = asdict(replace(query, cursor="", limit=100))
    return hashlib.sha256(json.dumps([mode, payload], sort_keys=True).encode()).hexdigest()


def cursor_state(query, mode, revision):
    if not query.cursor:
        return None
    try:
        state = json.loads(bytes.fromhex(query.cursor).decode())
        if state["query"] == fingerprint(query, mode) and state["revision"] == revision:
            key = state["key"]
            if (isinstance(key, list) and len(key) == 2 and isinstance(key[1], str)
                    and isinstance(key[0], (str, int, float)) and not isinstance(key[0], bool)):
                return key
    except (ValueError, TypeError, KeyError, UnicodeDecodeError):
        pass
    raise ArtifactSnapshotChanged("Artifacts changed or the cursor does not match this query; restart from the first page.")


def page(db, query, columns, families, mode="artifacts", extra_filters=None):
    if not db._conn:
        return {"items": [], "next_cursor": ""}
    revision = revision_token(db)
    try:
        key = cursor_state(query, mode, revision)
    except ArtifactSnapshotChanged as exc:
        return {"items": [], "next_cursor": "", "invalidated": True, "reason": str(exc)}
    clauses, params = _entity_filters(query, families) if mode == "entities" else compile_filters(query, families)
    if extra_filters is not None:
        additional,values=extra_filters
        clauses.extend(additional); params.extend(values)
    order, expression, operator = _ordering(query)
    if key:
        clauses.append(f"({expression}{operator}? OR ({expression}=? AND a.artifact_id{operator}?))")
        params.extend([key[0], key[0], key[1]])
    limit = max(1, min(int(query.limit or 100), MAX_PAGE_SIZE))
    where = " AND ".join(clauses) or "1"
    qualified = ", ".join(f"a.{name.strip()}" for name in columns.split(","))
    rows = db._conn.execute(f"SELECT {qualified} FROM intelligence_artifacts a WHERE {where} ORDER BY {order} LIMIT ?", (*params, limit + 1)).fetchall()
    items = [db._artifact_row_to_dict(row) for row in rows[:limit]]
    if mode == "entities":
        _hydrate_mentions(db, query, items, columns, families)
        from core.services.intelligence.entity_citations import cited_entries
        cited = cited_entries(db, [item["artifact_id"] for item in items])
        for item in items:
            if item["artifact_id"] in cited:
                item["metadata"] = {**item["metadata"], "cited_entry": cited[item["artifact_id"]]}
        from core.services.intelligence.entity_hierarchy import hydrate
        hydrate(db, items, columns)
    if revision != revision_token(db):
        return {"items": [], "next_cursor": "", "invalidated": True, "reason": "Artifacts changed during the read; refresh."}
    cursor = ""
    if len(rows) > limit and items:
        last = items[-1]
        sort_value = ORDERINGS[query.order_by][2](last)
        state = {"query": fingerprint(query, mode), "revision": revision, "key": [sort_value, last["artifact_id"]]}
        cursor = json.dumps(state).encode().hex()
    return {"items": items, "next_cursor": cursor, "snapshot_revision": revision}


def _date_key(row):
    return (row.get("metadata") or {}).get("normalized_date") or row.get("normalized_text") or "9999"


def _mentions_key(row):
    try:
        return int((row.get("metadata") or {}).get("project_mention_count", (row.get("metadata") or {}).get("mention_count")) or 0)
    except (TypeError, ValueError):
        return 0


def _alpha_key(row):
    """Matches SQLite's LOWER(), which folds ASCII letters only."""
    return "".join(c.lower() if "A" <= c <= "Z" else c for c in (row.get("exact_text") or ""))


_DATE_SQL = "COALESCE(NULLIF(json_extract(a.metadata_json, '$.normalized_date'),''), NULLIF(a.normalized_text,''), '9999')"
_MENTIONS_SQL = "CAST(COALESCE(json_extract(a.metadata_json, '$.mention_count'),0) AS INTEGER)"
_ALPHA_SQL = "LOWER(COALESCE(a.exact_text,''))"
# order_by -> (sort expression, descending, the row's sort value for the cursor).
# Mention sorts use the project-wide count stored on the canonical entity, so the
# order is stable; the count shown next to each row is the one for the chosen scope.
ORDERINGS = {
    "recent": ("a.updated_at", True, lambda row: row["updated_at"]),
    "date": (_DATE_SQL, False, _date_key),
    "mentions_desc": (_MENTIONS_SQL, True, _mentions_key),
    "mentions_asc": (_MENTIONS_SQL, False, _mentions_key),
    "alpha": (_ALPHA_SQL, False, _alpha_key),
    "alpha_desc": (_ALPHA_SQL, True, _alpha_key),
    "confidence_desc": ("COALESCE(a.confidence,0)", True, lambda row: float(row.get("confidence") or 0)),
}


def _ordering(query):
    if query.order_by not in ORDERINGS:
        raise ValueError("order_by must be one of " + ", ".join(sorted(ORDERINGS)))
    expression, descending, _ = ORDERINGS[query.order_by]
    direction = "DESC" if descending else "ASC"
    return f"{expression} {direction}, a.artifact_id {direction}", expression, "<" if descending else ">"


def _mention_query(query, parent_ids=()):
    return ArtifactQuery(
        source_scope=query.source_scope, artifact_types=["entity_mention"],
        source_tags=list(query.source_tags),       # an entity counts where it is mentioned
        statuses=query.statuses or ["complete"],
        review_states=query.review_states or ["unreviewed", "verified", "corrected"],
        parent_artifact_ids=list(parent_ids),
    )


def _entity_filters(query, families):
    canonical = replace(query, source_scope=SourceScope(), source_tags=[], artifact_types=["entity"],
                        statuses=query.statuses or ["complete"],
                        review_states=query.review_states or ["unreviewed", "verified", "corrected"])
    clauses, params = compile_filters(canonical, families)
    mentions, mention_params = compile_filters(_mention_query(query), families, "m")
    clauses.append("EXISTS (SELECT 1 FROM intelligence_artifacts m WHERE m.parent_artifact_id=a.artifact_id AND " + " AND ".join(mentions) + ")")
    return clauses, params + mention_params


def _hydrate_mentions(db, query, items, columns, families):
    for entity in items:
        mention_query = _mention_query(query, [entity["artifact_id"]])
        clauses, params = compile_filters(mention_query, families)
        where = " AND ".join(clauses)
        counts = db._conn.execute(f"SELECT COUNT(*), COUNT(DISTINCT a.source_id) FROM intelligence_artifacts a WHERE {where}", params).fetchone()
        stored = entity["metadata"].get("mention_count")
        entity["metadata"] = {**entity["metadata"], "project_mention_count": stored,
                              "mention_count": counts[0], "source_count": counts[1]}
        limit = max(0, min(MAX_MENTION_PREVIEW, int(query.mention_limit)))
        rows = db._conn.execute(f"SELECT {columns} FROM intelligence_artifacts a WHERE {where} ORDER BY a.updated_at DESC, a.artifact_id DESC LIMIT ?", (*params, limit)).fetchall()
        entity["mentions"] = [db._artifact_row_to_dict(row) for row in rows]
        entity["mentions_truncated"] = counts[0] > len(rows)
    from core.db.mention_snippets import attach
    attach(db, [mention for entity in items for mention in entity["mentions"]])


FACET_FIELDS = {
    "entity_type": ("json_extract(a.metadata_json, '$.entity_type')",
                    ("entity_types", "exclude_entity_types")),
    "artifact_type": ("a.artifact_type", ("artifact_types",)),
    "review_state": ("a.review_state", ("review_states",)),
    "argument_role": ("COALESCE(json_extract(a.metadata_json, '$.argument_role'), "
                      "json_extract(a.metadata_json, '$.class_label'), '')", ("argument_roles",)),
}
MAX_FACETS = 200
# Sources carrying any of the given document tags (doc_tags is keyed by the source's path).
SOURCE_TAG_SQL = ("SELECT s.id FROM sources s JOIN doc_tags dt ON dt.doc_id=s.path JOIN tags t ON t.id=dt.tag_id "
                  "WHERE t.name IN (SELECT value FROM json_each(?))")
TAG_FACETS = {"tag": ("tag_filters",), "source_tag": ("source_tags",)}


def facets(db, query, field, families, entities=False):
    """Counts per value of ``field`` under every filter except the one on ``field`` itself,
    so a filter menu can show how many rows each choice would give."""
    if not db._conn or (field not in FACET_FIELDS and field not in TAG_FACETS):
        return {}
    if field in TAG_FACETS:
        return _tag_facets(db, query, field, families, entities)
    expression, own = FACET_FIELDS[field]
    base = replace(query, cursor="", **{name: [] for name in own})
    clauses, params = _entity_filters(base, families) if entities else compile_filters(base, families)
    if field == "entity_type":
        clauses.append(f"{expression} IS NOT NULL")  # rows without a type are not entities
    rows = db._conn.execute(
        f"SELECT {expression}, COUNT(*) FROM intelligence_artifacts a WHERE {' AND '.join(clauses) or '1'} "
        f"GROUP BY 1 ORDER BY 2 DESC LIMIT ?", (*params, MAX_FACETS)).fetchall()
    return {str(value or ""): int(count) for value, count in rows}


def _tag_facets(db, query, field, families, entities):
    """Rows per record tag, or per document tag of the row's source (entities: of a mention's source)."""
    base = replace(query, cursor="", **{name: [] for name in TAG_FACETS[field]})
    clauses, params = _entity_filters(base, families) if entities else compile_filters(base, families)
    where = " AND ".join(clauses) or "1"
    if field == "tag":
        sql = (f"SELECT tag.value, COUNT(DISTINCT a.artifact_id) FROM intelligence_artifacts a, "
               f"json_each(a.metadata_json, '$.tags') tag WHERE {where} GROUP BY 1 ORDER BY 2 DESC LIMIT ?")
    else:
        owner = ("JOIN intelligence_artifacts m ON m.parent_artifact_id=a.artifact_id AND m.artifact_type='entity_mention' "
                 "JOIN sources s ON s.id=m.source_id" if entities else "JOIN sources s ON s.id=a.source_id")
        sql = (f"SELECT t.name, COUNT(DISTINCT a.artifact_id) FROM intelligence_artifacts a {owner} "
               f"JOIN doc_tags dt ON dt.doc_id=s.path JOIN tags t ON t.id=dt.tag_id WHERE {where} "
               f"GROUP BY 1 ORDER BY 2 DESC LIMIT ?")
    try:
        rows = db._conn.execute(sql, (*params, MAX_FACETS)).fetchall()
    except sqlite3.OperationalError:
        return {}          # a database without document tag tables
    return {str(value or ""): int(count) for value, count in rows if value}


def iterate(db, query, *, entities=False):
    cursor = query.cursor
    while True:
        method = db.query_entities if entities else db.query_artifacts
        result = method(replace(query, cursor=cursor))
        if result.get("invalidated"):
            raise ArtifactSnapshotChanged(result["reason"])
        yield from result["items"]
        cursor = result["next_cursor"]
        if not cursor:
            return


def search(db, text, query, columns, families, fts_query, limit):
    """FTS ranking applies the full typed policy before the SQL limit."""
    clauses, params = compile_filters(query, families)
    qualified = ", ".join(f"a.{name.strip()}" for name in columns.split(","))
    where = " AND ".join(clauses) or "1"
    try:
        rows = db._conn.execute(
            f"SELECT {qualified} FROM intelligence_artifacts_fts f JOIN intelligence_artifacts a ON a.artifact_id=f.artifact_id "
            f"WHERE intelligence_artifacts_fts MATCH ? AND {where} "
            "ORDER BY bm25(intelligence_artifacts_fts), a.confidence DESC, a.artifact_id DESC LIMIT ?",
            (fts_query(text), *params, max(1, int(limit))),
        ).fetchall()
        if rows:
            return [db._artifact_row_to_dict(row) for row in rows]
    except sqlite3.OperationalError:
        pass  # Older packs may not have a working FTS index yet.
    fallback = replace(query, text=text)
    clauses, params = compile_filters(fallback, families)
    rows = db._conn.execute(
        f"SELECT {qualified} FROM intelligence_artifacts a WHERE {' AND '.join(clauses)} "
        "ORDER BY a.confidence DESC, a.updated_at DESC, a.artifact_id DESC LIMIT ?",
        (*params, max(1, int(limit))),
    ).fetchall()
    return [db._artifact_row_to_dict(row) for row in rows]


def entity_contexts(db, entity_ids, query, columns, families, limit):
    if not entity_ids:
        return []
    clauses, params = compile_filters(query, families)
    mentions, mention_params = compile_filters(_mention_query(query, entity_ids), families, "m")
    clauses.append("EXISTS (SELECT 1 FROM intelligence_artifacts m WHERE "
                   "json_extract(m.metadata_json, '$.evidence_artifact_id')=a.artifact_id AND "
                   + " AND ".join(mentions) + ")")
    qualified = ", ".join(f"a.{name.strip()}" for name in columns.split(","))
    rows = db._conn.execute(
        f"SELECT {qualified} FROM intelligence_artifacts a WHERE {' AND '.join(clauses)} "
        "ORDER BY a.confidence DESC, a.updated_at DESC, a.artifact_id DESC LIMIT ?",
        (*params, *mention_params, max(1, int(limit))),
    ).fetchall()
    return [db._artifact_row_to_dict(row) for row in rows]
