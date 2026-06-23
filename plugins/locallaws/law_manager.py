"""
plugins/locallaws/law_manager.py
"""
from __future__ import annotations

import json
import os
import re
import threading
import uuid
import time
import chromadb
from chromadb.config import Settings
import pandas as pd
import requests

from .law_tags_db import LocalLawsTagDB


# ---------------------------------------------------------------------------
# Module-level constants
# ---------------------------------------------------------------------------

# Maximum records returned by local_search.  Keeps the list widget snappy
# for large DBs; users narrow results by typing a query.
_SEARCH_LIMIT = 2_000

# PyArrow batch size for streaming bulk ingestion (records flushed at once)
_BULK_BATCH = 500


# ---------------------------------------------------------------------------
# Canonical-key helpers (deterministic, file-id-independent)
# ---------------------------------------------------------------------------

def _canonical_key_caselaw(cluster_id) -> str:
    """Stable dedup key for a CourtListener opinion cluster."""
    return f"cl:{cluster_id}"


def _canonical_key_municipal(state: str, city: str, header: str) -> str:
    """Stable dedup key for a LOCUS municipal code section."""
    header_clean = re.sub(r"^\s*#+\s*", "", str(header or "")).strip().lower()
    return f"mun:{str(state or '').strip().lower()}:{str(city or '').strip().lower()}:{header_clean}"


class LocalLawManager:
    def __init__(self, api):
        self.api = api
        self.plugin_dir = os.path.dirname(os.path.abspath(__file__))
        self.download_dir = os.path.join(self.plugin_dir, "dbs", "downloads")
        os.makedirs(self.download_dir, exist_ok=True)

        self.db_path = os.path.join(self.plugin_dir, "dbs")
        self.client = chromadb.PersistentClient(
            path=self.db_path,
            settings=Settings(anonymized_telemetry=False),
        )
        self._records_cache = {}
        self._db_lock = threading.RLock()

        tag_db_path = os.path.join(self.plugin_dir, "dbs", "law_tags.db")
        self.tag_db = LocalLawsTagDB(tag_db_path)
        # LocalLawsTagDB is internally thread-safe (WAL + per-call connections + RLock).
        # No additional wrapper needed here.

    # ------------------------------------------------------------------
    # Tag convenience API (delegate to LocalLawsTagDB)
    # ------------------------------------------------------------------

    def auto_tag_db(self, file_id: str, *tag_names: str) -> None:
        """Ensure *tag_names* exist and assign them to *file_id*."""
        for name in tag_names:
            name = name.strip()
            if not name:
                continue
            tid = self.tag_db.ensure_tag(name)
            if tid is not None:
                self.tag_db.assign_tag_to_db(file_id, tid)

    def get_tags_for_db(self, file_id: str) -> list:
        return self.tag_db.get_tags_for_db(file_id)

    def get_all_tags(self) -> list:
        return self.tag_db.get_all_tags()

    def filter_dbs_by_tags(self, active_dbs: list, tag_names: list, logic: str = "AND") -> list:
        """Return the subset of *active_dbs* that match the tag filter."""
        if not tag_names:
            return active_dbs
        matching = set(self.tag_db.get_dbs_for_tags(tag_names, logic))
        return [db for db in active_dbs if db in matching]

    # ------------------------------------------------------------------
    # Static / class helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _clean_value(value, default=""):
        if value is None or pd.isna(value):
            return default
        text = str(value).strip()
        return text if text and text.lower() != "nan" else default

    @classmethod
    def row_to_record(cls, row, file_id: str = "", row_index: int = -1):
        if "type" in row and row["type"] == "caselaw":
            return {
                "is_caselaw": True,
                "label": f"{row.get('case_name', 'Unknown')} ({row.get('date_filed', '')})",
                "court": row.get("court", ""),
                "case_name": row.get("case_name", ""),
                "text": row.get("text", ""),
                "url": row.get("url", ""),
                "_file_id": file_id,
                "_row_index": row_index,
                "_cluster_id": row.get("cluster_id", ""),
            }

        by_name = {str(key).lower(): key for key in row.index}

        def first(*names):
            for name in names:
                key = by_name.get(name)
                if key is not None:
                    value = cls._clean_value(row[key])
                    if value:
                        return value
            return ""

        header = re.sub(r"^\s*#+\s*", "", first("header", "heading", "name", "caption"))
        text = first("content", "text", "body", "law_text", "section_text")
        title = first("title")
        chapter = first("chapter")
        section = first("section", "section_number", "code")
        state = first("state")
        city = first("city")
        if header:
            section_match = re.match(r"([\w.-]+(?:-[\w.-]+)+)\s*:\s*", header)
            if section_match and not section:
                section = section_match.group(1)
        parts = [
            f"Title {title}" if title else "",
            f"Chapter {chapter}" if chapter else "",
            f"Section {section}" if section else "",
        ]
        label = header or ", ".join(part for part in parts if part) or "Municipal Code Provision"
        return {
            "is_caselaw": False,
            "label": label,
            "header": header,
            "title": title,
            "chapter": chapter,
            "section": section,
            "text": text,
            "state": state,
            "city": city,
            "_file_id": file_id,
            "_row_index": row_index,
            "_cluster_id": "",
        }

    def is_available(self):
        return self.client is not None

    @staticmethod
    def _mark_index_complete(parquet_path: str) -> None:
        try:
            open(parquet_path + ".done", "w").close()
        except Exception:
            pass

    # ------------------------------------------------------------------
    # DB listing
    # ------------------------------------------------------------------

    def get_installed_dbs(self):
        dbs = []
        if not os.path.exists(self.download_dir):
            return dbs

        for file in os.listdir(self.download_dir):
            if not file.endswith(".parquet"):
                continue
            path = os.path.join(self.download_dir, file)
            size_mb = os.path.getsize(path) / (1024 * 1024)
            index_complete = os.path.exists(path + ".done")

            if file.startswith("caselaw_"):
                parts = file.replace(".parquet", "").split("_", 2)
                court_id = parts[1]
                query_slug = parts[2] if len(parts) > 2 else "all"
                file_id = file.replace(".parquet", "")
                dbs.append({
                    "type": "caselaw",
                    "label": f"{court_id.upper()} (Q: {query_slug})",
                    "city": court_id.upper(),
                    "state": "CASELAW",
                    "size_mb": round(size_mb, 2),
                    "file_id": file_id,
                    "filename": file,
                    "index_complete": index_complete,
                    "tags": self.tag_db.get_tags_for_db(file_id),
                })
            else:
                stem = file.replace(".parquet", "")
                if "_subj_" in stem:
                    parts = stem.split("_subj_", 1)
                    subject_slug = parts[0].replace("_", " ").title()
                    state_part = parts[1].upper() if len(parts) > 1 else "?"
                    dbs.append({
                        "type": "municipal",
                        "city": f"[Subject: {subject_slug}]",
                        "state": state_part,
                        "label": f"Subject: {subject_slug} ({state_part})",
                        "size_mb": round(size_mb, 2),
                        "file_id": stem,
                        "filename": file,
                        "index_complete": index_complete,
                        "tags": self.tag_db.get_tags_for_db(stem),
                    })
                else:
                    name_parts = stem.rsplit("_", 1)
                    if len(name_parts) == 2:
                        city, state = name_parts[0], name_parts[1]
                        file_id = f"{city.title()}_{state.upper()}"
                        dbs.append({
                            "type": "municipal",
                            "city": city.title(),
                            "state": state.upper(),
                            "label": f"{city.title()}, {state.upper()}",
                            "size_mb": round(size_mb, 2),
                            "file_id": file_id,
                            "filename": file,
                            "index_complete": index_complete,
                            "tags": self.tag_db.get_tags_for_db(file_id),
                        })
        return dbs

    # ------------------------------------------------------------------
    # DuckDB local search  (NEW — called from background SearchWorker)
    # ------------------------------------------------------------------

    def local_search(
        self,
        query: str,
        db_ids: list | None = None,
        limit: int = _SEARCH_LIMIT,
    ) -> list[dict]:
        """
        DuckDB boolean-SQL search over local Parquet files.

        Runs entirely in a background thread — zero PySide6 contact.
        Returns up to *limit* record dicts.  An empty *query* returns
        the first *limit* rows (browse mode).
        """
        import duckdb

        installed = self.get_installed_dbs()
        if db_ids is not None:
            dbs = [d for d in installed if d["file_id"] in db_ids]
        else:
            dbs = installed

        if not dbs:
            return []

        query = (query or "").strip()
        results: list[dict] = []

        for db_info in dbs:
            if len(results) >= limit:
                break

            filename = db_info.get("filename", "")
            path = os.path.join(self.download_dir, filename)
            if not os.path.exists(path):
                continue

            remaining = limit - len(results)
            file_id = db_info["file_id"]
            source_label = db_info.get("label", file_id)

            try:
                with duckdb.connect() as con:
                    # Discover which text columns are present
                    schema_df = con.execute(
                        f"DESCRIBE SELECT * FROM read_parquet('{path.replace(chr(39), chr(39)*2)}') LIMIT 0"
                    ).df()
                    cols = [c.lower() for c in schema_df["column_name"].tolist()]
                    text_cols = [
                        c for c in cols
                        if c in {
                            "content", "text", "body", "law_text",
                            "header", "heading", "case_name", "section_text",
                        }
                    ]

                    where_sql = (
                        self._build_subject_sql(query, text_cols)
                        if query and text_cols
                        else ""
                    )

                    safe = path.replace("'", "''")
                    if where_sql:
                        sql = (
                            f"SELECT * FROM read_parquet('{safe}') "
                            f"WHERE {where_sql} LIMIT {remaining}"
                        )
                    else:
                        sql = f"SELECT * FROM read_parquet('{safe}') LIMIT {remaining}"

                    df = con.execute(sql).df()

                for idx, row in df.iterrows():
                    rec = self.row_to_record(row, file_id=file_id, row_index=int(idx))
                    rec["_source_label"] = source_label
                    results.append(rec)

            except Exception:
                pass  # tolerate corrupt / schema-incompatible parquet

        return results

    # ------------------------------------------------------------------
    # Effective records (own + cross-referenced)
    # ------------------------------------------------------------------

    def get_effective_records(self, file_id: str, db_info: dict) -> list:
        """Return all records for *file_id*, including cross-referenced ones."""
        records = []
        filename = db_info.get("filename", "")
        file_path = os.path.join(self.download_dir, filename)
        if os.path.exists(file_path):
            try:
                df = pd.read_parquet(file_path)
                for idx, row in df.iterrows():
                    rec = self.row_to_record(row, file_id=file_id, row_index=int(idx))
                    records.append(rec)
            except Exception:
                pass

        # Append cross-referenced records from other DBs
        for xref in self.tag_db.get_cross_refs(file_id):
            src_fid = xref["source_file_id"]
            src_row = xref["source_row_index"]
            src_file = os.path.join(self.download_dir, f"{src_fid}.parquet")
            if not os.path.exists(src_file):
                found = next(
                    (f for f in os.listdir(self.download_dir)
                     if f.endswith(".parquet") and f.replace(".parquet", "") == src_fid),
                    None,
                )
                if found:
                    src_file = os.path.join(self.download_dir, found)
                else:
                    continue
            try:
                src_df = pd.read_parquet(src_file)
                if src_row >= 0 and src_row < len(src_df):
                    row = src_df.iloc[src_row]
                else:
                    continue
                rec = self.row_to_record(row, file_id=src_fid, row_index=src_row)
                rec["_is_borrowed"] = True
                rec["_borrowed_from"] = src_fid
                records.append(rec)
            except Exception:
                pass

        return records

    # ------------------------------------------------------------------
    # Shared ChromaDB embedding step (used by all ingesters)
    # ------------------------------------------------------------------

    def _index_parquet_to_chroma(
        self,
        file_id: str,
        parquet_path: str,
        _progress,
        cancel_check,
        start_pct: float = 0.5,
    ) -> dict:
        """
        Embed every record in *parquet_path* into ChromaDB.
        Extracted so bulk and API ingesters share identical logic.
        """
        try:
            df = pd.read_parquet(parquet_path)
            total = len(df)
            if total == 0:
                return {"success": False, "error": "Empty parquet file."}

            is_caselaw = file_id.startswith("caselaw_")
            collection_name = "us_case_laws" if is_caselaw else "us_local_laws"

            with self._db_lock:
                collection = self.client.get_or_create_collection(name=collection_name)

            batch_size = 10 if is_caselaw else 25
            newly_indexed = 0

            for i in range(0, total, batch_size):
                if cancel_check():
                    return {"success": False, "error": "Cancelled."}

                batch = df.iloc[i:i + batch_size]
                batch_texts, metadatas, ids = [], [], []

                for idx, (_, row) in enumerate(batch.iterrows()):
                    row_index = i + idx
                    if is_caselaw:
                        raw_text = str(row.get("text", ""))
                        clean_text = re.sub(r"<[^>]+>", " ", raw_text)[:4_000]
                        case_name = str(row.get("case_name", "Unknown"))
                        date_filed = str(row.get("date_filed", ""))
                        court = str(row.get("court", ""))
                        cluster_id = str(row.get("cluster_id", ""))
                        full_text = f"Case: {case_name} ({date_filed})\n{clean_text}"
                        stable_key = f"{file_id}:{case_name}:{clean_text[:60]}"
                        batch_texts.append(full_text)
                        metadatas.append({
                            "doc_name": f"{case_name} ({court})",
                            "case_name": case_name,
                            "date_filed": date_filed,
                            "source_type": "plugin.caselaw",
                            "source_id": file_id,
                            "url": str(row.get("url", "")),
                            "cluster_id": cluster_id,
                        })
                        ids.append(f"caselaw_{uuid.uuid5(uuid.NAMESPACE_URL, stable_key).hex}")
                    else:
                        record = self.row_to_record(row, file_id=file_id, row_index=row_index)
                        clean_text = (record["text"] or "No text found.")[:4_000]
                        full_text = f"[{record['label']}] {clean_text}"
                        stable_key = f"{file_id}:{record['label']}:{clean_text}"
                        batch_texts.append(full_text)
                        metadatas.append({
                            "doc_name": f"{file_id} — {record['label']}",
                            "state": record.get("state") or "",
                            "city": record.get("city") or "",
                            "section": record.get("section") or "",
                            "header": record.get("header") or "",
                            "source_type": "plugin.locallaws",
                            "source_id": file_id,
                        })
                        ids.append(f"law_{uuid.uuid5(uuid.NAMESPACE_URL, stable_key).hex}")

                with self._db_lock:
                    existing = set((collection.get(ids=ids) or {}).get("ids") or [])
                missing_idx = [j for j, id_ in enumerate(ids) if id_ not in existing]
                if not missing_idx:
                    pct = start_pct + (i + batch_size) / total * (1.0 - start_pct)
                    _progress(
                        f"Skipped {min(i + batch_size, total)}/{total} (already indexed)…",
                        min(pct, 0.97),
                    )
                    continue

                texts_m = [batch_texts[j] for j in missing_idx]
                metas_m = [metadatas[j] for j in missing_idx]
                ids_m = [ids[j] for j in missing_idx]

                embeddings = []
                for text in texts_m:
                    if cancel_check():
                        return {"success": False, "error": "Cancelled during embedding."}
                    emb = self.api.llm.get_embedding(text)
                    embeddings.append(emb or [0] * 768)

                with self._db_lock:
                    collection.upsert(
                        documents=texts_m,
                        embeddings=embeddings,
                        metadatas=metas_m,
                        ids=ids_m,
                    )

                newly_indexed += len(ids_m)
                pct = start_pct + (i + batch_size) / total * (1.0 - start_pct)
                _progress(
                    f"Indexed {min(i + batch_size, total)}/{total} records…",
                    min(pct, 0.97),
                )

            _progress("Embedding complete.", 1.0)
            self._mark_index_complete(parquet_path)
            self._records_cache.pop(file_id, None)
            return {"success": True, "label": file_id, "newly_indexed": newly_indexed}

        except Exception as e:
            return {"success": False, "error": str(e)}

    # ------------------------------------------------------------------
    # Indexing: resume (partial re-index)
    # ------------------------------------------------------------------

    def resume_indexing(self, file_id: str, progress_callback=None, cancel_check=None) -> dict:
        cancel_check = cancel_check or (lambda: False)
        _progress = progress_callback or (lambda msg, pct: None)

        parquet_path = os.path.join(self.download_dir, f"{file_id}.parquet")
        if not os.path.exists(parquet_path):
            return {"success": False, "error": "Parquet file not found."}

        try:
            df = pd.read_parquet(parquet_path)
            total = len(df)
            if total == 0:
                return {"success": False, "error": "Database file is empty."}

            is_caselaw = file_id.startswith("caselaw_")
            _progress(f"Scanning {total} records for un-indexed entries…", 0.02)

            with self._db_lock:
                if is_caselaw:
                    collection = self.client.get_or_create_collection(name="us_case_laws")
                else:
                    collection = self.client.get_or_create_collection(name="us_local_laws")

            city_display = file_id
            state_display = "?"
            if "_subj_" in file_id:
                parts = file_id.split("_subj_", 1)
                state_display = parts[1].upper() if len(parts) > 1 else "ALL"
                city_display = f"[{state_display}] {parts[0].replace('_', ' ')}"
            elif not is_caselaw and "_" in file_id:
                city_part, st = file_id.rsplit("_", 1)
                state_display = st.upper()
                city_display = city_part.title()

            batch_size = 10 if is_caselaw else 25
            newly_indexed = 0

            for i in range(0, total, batch_size):
                if cancel_check():
                    return {"success": False, "error": "Cancelled."}

                batch = df.iloc[i:i + batch_size]
                batch_texts, metadatas, ids = [], [], []

                for idx, (_, row) in enumerate(batch.iterrows()):
                    row_index = i + idx
                    if is_caselaw:
                        raw_text = str(row.get("text", ""))
                        clean_text = re.sub(r"<[^>]+>", " ", raw_text)
                        if len(clean_text) > 4000:
                            clean_text = clean_text[:4000] + "... [Truncated]"
                        case_name = str(row.get("case_name", "Unknown"))
                        date_filed = str(row.get("date_filed", ""))
                        court = str(row.get("court", ""))
                        cluster_id = str(row.get("cluster_id", ""))
                        full_text = f"Case: {case_name} ({date_filed})\n{clean_text}"
                        stable_key = f"{file_id}:{case_name}:{clean_text[:60]}"
                        batch_texts.append(full_text)
                        metadatas.append({
                            "doc_name": f"{case_name} ({court})",
                            "case_name": case_name,
                            "date_filed": date_filed,
                            "source_type": "plugin.caselaw",
                            "source_id": file_id,
                            "url": str(row.get("url", "")),
                            "cluster_id": cluster_id,
                        })
                        ids.append(f"caselaw_{uuid.uuid5(uuid.NAMESPACE_URL, stable_key).hex}")
                    else:
                        record = self.row_to_record(row, file_id=file_id, row_index=row_index)
                        clean_text = record["text"] or "No text found."
                        if len(clean_text) > 4000:
                            clean_text = clean_text[:4000] + "... [Truncated]"
                        full_text = f"[{record['label']}] {clean_text}"
                        stable_key = f"{file_id}:{record['label']}:{clean_text}"
                        batch_texts.append(full_text)
                        metadatas.append({
                            "doc_name": f"{city_display} — {record['label']}",
                            "state": state_display,
                            "city": city_display,
                            "section": record.get("section") or "",
                            "header": record.get("header") or "",
                            "source_type": "plugin.locallaws",
                            "source_id": file_id,
                        })
                        ids.append(f"law_{uuid.uuid5(uuid.NAMESPACE_URL, stable_key).hex}")

                with self._db_lock:
                    existing = set((collection.get(ids=ids) or {}).get("ids") or [])
                missing_idx = [j for j, id_ in enumerate(ids) if id_ not in existing]
                if not missing_idx:
                    pct = 0.02 + (i + batch_size) / total * 0.96
                    _progress(
                        f"Skipped {min(i + batch_size, total)}/{total} (already indexed)…",
                        min(pct, 0.97),
                    )
                    continue

                texts_m = [batch_texts[j] for j in missing_idx]
                metas_m = [metadatas[j] for j in missing_idx]
                ids_m = [ids[j] for j in missing_idx]

                embeddings = []
                for text in texts_m:
                    if cancel_check():
                        return {"success": False, "error": "Cancelled."}
                    emb = self.api.llm.get_embedding(text)
                    embeddings.append(emb or [0] * 768)

                with self._db_lock:
                    collection.upsert(
                        documents=texts_m,
                        embeddings=embeddings,
                        metadatas=metas_m,
                        ids=ids_m,
                    )

                newly_indexed += len(ids_m)
                pct = 0.02 + (i + batch_size) / total * 0.96
                _progress(
                    f"Indexed {min(i + batch_size, total)}/{total} records…",
                    min(pct, 0.97),
                )

            _progress(f"Complete — {newly_indexed} new records embedded.", 1.0)
            self._mark_index_complete(parquet_path)
            return {"success": True, "newly_indexed": newly_indexed}
        except Exception as e:
            return {"success": False, "error": str(e)}

    # ------------------------------------------------------------------
    # Remove DB
    # ------------------------------------------------------------------

    def remove_db(self, file_id: str):
        if file_id.startswith("caselaw_"):
            try:
                with self._db_lock:
                    col = self.client.get_collection(name="us_case_laws")
                    col.delete(where={"source_id": file_id})
            except Exception:
                pass
            file_path = os.path.join(self.download_dir, f"{file_id}.parquet")
        else:
            city, state = file_id.rsplit("_", 1)
            try:
                with self._db_lock:
                    col = self.client.get_collection(name="us_local_laws")
                    col.delete(where={"$and": [{"city": city}, {"state": state}]})
            except Exception:
                pass
            file_path = os.path.join(
                self.download_dir, f"{city.lower()}_{state.lower()}.parquet"
            )

        if os.path.exists(file_path):
            os.remove(file_path)
        done_path = file_path + ".done"
        if os.path.exists(done_path):
            os.remove(done_path)

        self.tag_db.remove_cross_refs_for_db(file_id)

    # ------------------------------------------------------------------
    # RAG query
    # ------------------------------------------------------------------

    def _merge_results(self, base_res, new_res):
        if not new_res or not new_res.get("ids") or not new_res["ids"][0]:
            return
        base_res["ids"][0].extend(new_res["ids"][0])
        base_res["distances"][0].extend(new_res["distances"][0])
        base_res["documents"][0].extend(new_res["documents"][0])
        base_res["metadatas"][0].extend(new_res["metadatas"][0])

    def query_laws(
        self,
        embedding_vector,
        n_results: int,
        active_dbs: list,
        tag_filters: list = None,
        tag_logic: str = "AND",
    ):
        """Query ChromaDB across *active_dbs*, optionally pre-filtered by tag."""
        effective_dbs = self.filter_dbs_by_tags(active_dbs, tag_filters or [], tag_logic)
        if not effective_dbs:
            return None

        mun_dbs = [db for db in effective_dbs if not db.startswith("caselaw_")]
        case_dbs = [db for db in effective_dbs if db.startswith("caselaw_")]

        all_results = {
            "ids": [[]],
            "distances": [[]],
            "documents": [[]],
            "metadatas": [[]],
        }

        try:
            if mun_dbs:
                with self._db_lock:
                    try:
                        col_mun = self.client.get_collection(name="us_local_laws")
                        where_conditions = []
                        for db_id in mun_dbs:
                            if "_" in db_id:
                                city, state = db_id.rsplit("_", 1)
                                where_conditions.append(
                                    {"$and": [{"city": city}, {"state": state}]}
                                )
                        if where_conditions:
                            where_clause = (
                                where_conditions[0]
                                if len(where_conditions) == 1
                                else {"$or": where_conditions}
                            )
                            res = col_mun.query(
                                query_embeddings=[embedding_vector],
                                n_results=n_results,
                                where=where_clause,
                            )
                            self._merge_results(
                                all_results, self._enrich_municipal_results(res)
                            )
                    except ValueError:
                        pass

            if case_dbs:
                with self._db_lock:
                    try:
                        col_case = self.client.get_collection(name="us_case_laws")
                        where_conditions = [{"source_id": db} for db in case_dbs]
                        where_clause = (
                            where_conditions[0]
                            if len(where_conditions) == 1
                            else {"$or": where_conditions}
                        )
                        res = col_case.query(
                            query_embeddings=[embedding_vector],
                            n_results=n_results,
                            where=where_clause,
                        )
                        self._merge_results(all_results, res)
                    except ValueError:
                        pass

            if all_results["ids"][0]:
                combined = list(
                    zip(
                        all_results["distances"][0],
                        all_results["ids"][0],
                        all_results["documents"][0],
                        all_results["metadatas"][0],
                    )
                )
                combined.sort(key=lambda x: x[0])
                combined = combined[:n_results]
                return {
                    "distances": [[x[0] for x in combined]],
                    "ids": [[x[1] for x in combined]],
                    "documents": [[x[2] for x in combined]],
                    "metadatas": [[x[3] for x in combined]],
                }
        except Exception as e:
            print(f"[Local Laws] Search Error: {e}")

        return None

    # ------------------------------------------------------------------
    # Municipal record helpers
    # ------------------------------------------------------------------

    def _records_for_jurisdiction(self, city, state):
        file_id = f"{city.title()}_{state.upper()}"
        if file_id not in self._records_cache:
            path = os.path.join(
                self.download_dir, f"{city.lower()}_{state.lower()}.parquet"
            )
            if not os.path.exists(path):
                self._records_cache[file_id] = []
            else:
                frame = pd.read_parquet(path)
                self._records_cache[file_id] = [
                    self.row_to_record(row) for _, row in frame.iterrows()
                ]
        return file_id, self._records_cache[file_id]

    def _enrich_municipal_results(self, results):
        if not results or not results.get("ids"):
            return results
        documents = (results.get("documents") or [[]])[0]
        metadatas = (results.get("metadatas") or [[]])[0]
        for index, metadata in enumerate(metadatas):
            meta = dict(metadata or {})
            city = self._clean_value(meta.get("city"))
            state = self._clean_value(meta.get("state"))
            file_id, records = self._records_for_jurisdiction(city, state)
            document = documents[index] if index < len(documents) else ""
            normalized_document = " ".join(str(document).lower().split())
            match = next(
                (
                    record
                    for record in records
                    if not record.get("is_caselaw")
                    and record["text"]
                    and " ".join(record["text"].lower().split())[:180] in normalized_document
                ),
                None,
            )
            if match:
                meta["doc_name"] = (
                    f"{city.title()} Municipal Code — {match['label']}"
                )
                meta["section"] = match["section"]
                meta["header"] = match["header"]
            meta["source_type"] = "plugin.locallaws"
            meta["source_id"] = file_id
            meta["source_locator"] = {
                "jurisdiction_id": file_id,
                "header": (match or {}).get("header", meta.get("header", "")),
                "section": (match or {}).get("section", meta.get("section", "")),
            }
            metadatas[index] = meta
        results["metadatas"] = [metadatas]
        return results

    # ------------------------------------------------------------------
    # Boolean SQL builder (LOCUS subject queries + local_search)
    # ------------------------------------------------------------------

    @staticmethod
    def _build_subject_sql(subject: str, text_cols: list) -> str:
        import re as _re

        if not text_cols or not subject.strip():
            return ""

        def _col_contains(term: str) -> str:
            safe = term.replace("'", "''").lower()
            return "(" + " OR ".join(f"LOWER({c}) LIKE '%{safe}%'" for c in text_cols) + ")"

        phrases = _re.findall(r'"([^"]+)"', subject)
        remainder = _re.sub(r'"[^"]+"', "", subject)
        tokens = _re.split(r"\s+", remainder.strip())

        required_parts = [_col_contains(p) for p in phrases]
        excluded_parts = []
        or_parts = []
        expect_not = False
        or_mode = False

        for tok in tokens:
            t = tok.strip()
            if not t:
                continue
            if t.upper() == "AND":
                or_mode = False
                continue
            if t.upper() == "OR":
                or_mode = True
                continue
            if t.upper() == "NOT":
                expect_not = True
                continue
            if t.startswith("-"):
                excluded_parts.append(_col_contains(t[1:]))
                continue
            if expect_not:
                excluded_parts.append(_col_contains(t))
                expect_not = False
            elif or_mode:
                or_parts.append(_col_contains(t))
                or_mode = False
            else:
                required_parts.append(_col_contains(t))

        clauses = list(required_parts)
        if or_parts:
            clauses.append("(" + " OR ".join(or_parts) + ")")
        for ex in excluded_parts:
            clauses.append(f"NOT {ex}")

        return " AND ".join(clauses) if clauses else ""

    # ------------------------------------------------------------------
    # CourtListener bulk archive ingestion  (NEW — streaming, memory-safe)
    # ------------------------------------------------------------------

    def stream_bulk_caselaw(
        self,
        court_id: str,
        api_key: str,
        progress_callback=None,
        cancel_check=None,
    ) -> dict:
        """
        Stream-download the CourtListener bulk opinion archive for *court_id*,
        parse JSON in _BULK_BATCH-record batches, flush incrementally to a
        Parquet file via PyArrow, then embed into ChromaDB.

        Memory ceiling: O(_BULK_BATCH * avg_record_size) — safe for the
        multi-GB 9th-Circuit archive.

        Bulk archive URLs:
          clusters: https://storage.courtlistener.com/bulk-data/clusters/{court_id}.tar.gz
          opinions: https://storage.courtlistener.com/bulk-data/opinions/{court_id}.tar.gz
        """
        try:
            import tarfile
            import pyarrow as pa
            import pyarrow.parquet as pq
        except ImportError as e:
            return {"success": False, "error": f"Missing dependency: {e}. Run: pip install pyarrow"}

        cancel_check = cancel_check or (lambda: False)
        _progress = progress_callback or (lambda msg, pct: None)
        court_id = court_id.strip().lower()

        file_id = f"caselaw_{court_id}_bulk"
        local_cache_file = os.path.join(self.download_dir, f"{file_id}.parquet")

        if os.path.exists(local_cache_file):
            _progress(f"Cached archive found. Loading {court_id.upper()} for embedding…", 0.05)
            return self._index_parquet_to_chroma(
                file_id, local_cache_file, _progress, cancel_check, start_pct=0.05
            )

        BASE = "https://storage.courtlistener.com/bulk-data"
        session = requests.Session()
        session.headers.update({"Authorization": f"Token {api_key}"})

        # ── Step 1: stream clusters archive for case_name / date_filed metadata ──
        _progress(f"Fetching cluster metadata for {court_id.upper()}…", 0.01)
        cluster_map: dict[str, dict] = {}

        try:
            resp = session.get(f"{BASE}/clusters/{court_id}.tar.gz", stream=True, timeout=60)
            if resp.ok:
                resp.raw.decode_content = True
                with tarfile.open(fileobj=resp.raw, mode="r|gz") as tar:
                    for member in tar:
                        if cancel_check():
                            session.close()
                            return {"success": False, "error": "Cancelled."}
                        if not member.isfile():
                            continue
                        f = tar.extractfile(member)
                        if f is None:
                            continue
                        try:
                            obj = json.loads(f.read().decode("utf-8", errors="replace"))
                            cid = str(obj.get("id") or "")
                            if cid:
                                cluster_map[cid] = {
                                    "case_name": str(obj.get("case_name") or "Unknown"),
                                    "date_filed": str(obj.get("date_filed") or ""),
                                    "url": (
                                        "https://www.courtlistener.com"
                                        + str(obj.get("absolute_url") or "")
                                    ),
                                }
                        except Exception:
                            pass
                _progress(f"Loaded {len(cluster_map)} cluster metadata records.", 0.22)
            else:
                _progress(
                    f"Cluster metadata unavailable (HTTP {resp.status_code}). "
                    f"Case names will be derived from opinion IDs.",
                    0.10,
                )
        except Exception as exc:
            _progress(f"Cluster metadata skipped ({exc}). Continuing…", 0.10)

        if cancel_check():
            session.close()
            return {"success": False, "error": "Cancelled."}

        # ── Step 2: stream opinions archive → incremental Parquet write ──────────
        _progress(f"Streaming opinion archive for {court_id.upper()}…", 0.23)

        parquet_schema = pa.schema([
            pa.field("type", pa.string()),
            pa.field("case_name", pa.string()),
            pa.field("date_filed", pa.string()),
            pa.field("court", pa.string()),
            pa.field("text", pa.string()),
            pa.field("url", pa.string()),
            pa.field("cluster_id", pa.string()),
        ])

        partial_file = local_cache_file + ".part"
        writer = None
        batch: list[dict] = []
        total_written = 0

        try:
            resp = session.get(f"{BASE}/opinions/{court_id}.tar.gz", stream=True, timeout=60)
            if not resp.ok:
                session.close()
                return {
                    "success": False,
                    "error": f"HTTP {resp.status_code}: cannot download opinions for {court_id.upper()}",
                }

            resp.raw.decode_content = True
            writer = pq.ParquetWriter(partial_file, parquet_schema, compression="snappy")

            with tarfile.open(fileobj=resp.raw, mode="r|gz") as tar:
                for member in tar:
                    if cancel_check():
                        raise InterruptedError("Cancelled by user")
                    if not member.isfile():
                        continue
                    f = tar.extractfile(member)
                    if f is None:
                        continue

                    try:
                        obj = json.loads(f.read().decode("utf-8", errors="replace"))
                    except Exception:
                        continue

                    # Derive cluster_id from the 'cluster' URL field
                    cluster_ref = str(obj.get("cluster") or "")
                    m = re.search(r"/clusters/(\d+)", cluster_ref)
                    cluster_id = m.group(1) if m else str(obj.get("id") or "")

                    meta = cluster_map.get(cluster_id, {})

                    # Prefer richest available text representation
                    raw_text = (
                        obj.get("plain_text")
                        or obj.get("html_with_citations")
                        or obj.get("html")
                        or obj.get("html_lawbox")
                        or obj.get("xml_harvard")
                        or ""
                    )
                    text = re.sub(r"<[^>]+>", " ", str(raw_text))

                    batch.append({
                        "type": "caselaw",
                        "case_name": meta.get("case_name") or f"Opinion {cluster_id}",
                        "date_filed": meta.get("date_filed") or str(obj.get("date_created", ""))[:10],
                        "court": court_id.upper(),
                        "text": text[:50_000],
                        "url": meta.get("url") or "",
                        "cluster_id": cluster_id,
                    })

                    if len(batch) >= _BULK_BATCH:
                        writer.write_table(
                            pa.Table.from_pylist(batch, schema=parquet_schema)
                        )
                        total_written += len(batch)
                        batch.clear()
                        pct = 0.23 + min(total_written / 100_000, 1.0) * 0.25
                        _progress(f"Extracted {total_written:,} opinions…", min(pct, 0.48))

            # Flush remainder
            if batch:
                writer.write_table(pa.Table.from_pylist(batch, schema=parquet_schema))
                total_written += len(batch)

            writer.close()
            writer = None

        except InterruptedError:
            if writer:
                try:
                    writer.close()
                except Exception:
                    pass
            if os.path.exists(partial_file):
                os.remove(partial_file)
            session.close()
            return {"success": False, "error": "Cancelled."}

        except Exception as exc:
            if writer:
                try:
                    writer.close()
                except Exception:
                    pass
            if os.path.exists(partial_file):
                try:
                    os.remove(partial_file)
                except Exception:
                    pass
            session.close()
            return {"success": False, "error": str(exc)}

        session.close()

        if total_written == 0:
            if os.path.exists(partial_file):
                os.remove(partial_file)
            return {"success": False, "error": "No opinions found in bulk archive."}

        os.replace(partial_file, local_cache_file)
        _progress(
            f"Extracted {total_written:,} opinions. Generating embeddings…",
            0.50,
        )
        self.api.notify(
            f"Stored {total_written:,} opinions for {court_id.upper()}. Now embedding…",
            level="info",
        )

        return self._index_parquet_to_chroma(
            file_id, local_cache_file, _progress, cancel_check, start_pct=0.50
        )

    # ------------------------------------------------------------------
    # CourtListener case law indexing — Search API path (targeted queries)
    # ------------------------------------------------------------------

    def index_courtlistener_caselaw(
        self,
        court_id: str,
        query: str,
        max_results: int,
        api_key: str,
        date_from: str = "",
        date_to: str = "",
        progress_callback=None,
        cancel_check=None,
    ):
        cancel_check = cancel_check or (lambda: False)
        _progress = progress_callback or (lambda msg, pct: None)
        court_id = court_id.strip().lower()
        query = query.strip()
        date_from = (date_from or "").strip()
        date_to = (date_to or "").strip()

        date_slug = ""
        if date_from:
            date_slug += f"_from{date_from.replace('-', '')}"
        if date_to:
            date_slug += f"_to{date_to.replace('-', '')}"
        query_slug = "".join(c if c.isalnum() else "_" for c in query)[:20] or "all"
        file_id = f"caselaw_{court_id}_{query_slug}{date_slug}"
        local_cache_file = os.path.join(self.download_dir, f"{file_id}.parquet")

        session = requests.Session()
        session.headers.update({"Authorization": f"Token {api_key}"})

        def _get_with_retry(url, params=None, timeout=20, max_retries=3):
            for attempt in range(max_retries):
                try:
                    resp = session.get(url, params=params, timeout=timeout)
                    if resp.status_code == 429:
                        wait = 2 ** (attempt + 1)
                        _progress(f"Rate limited — retrying in {wait}s…", -1)
                        time.sleep(wait)
                        continue
                    return resp
                except requests.exceptions.Timeout:
                    if attempt == max_retries - 1:
                        raise
                    time.sleep(1)
                except requests.exceptions.ConnectionError:
                    if attempt == max_retries - 1:
                        raise
                    time.sleep(2)
            return None

        if not os.path.exists(local_cache_file):
            _progress(f"Querying CourtListener for {court_id.upper()}…", 0.02)
            self.api.notify(f"Querying CourtListener for {court_id.upper()}...", level="info")
            raw_results = []

            params = {"type": "o"}
            if court_id:
                params["court"] = court_id
            if query:
                params["q"] = query
            if date_from:
                params["filed_after"] = date_from
            if date_to:
                params["filed_before"] = date_to

            next_url = "https://www.courtlistener.com/api/rest/v4/search/"
            while next_url and len(raw_results) < max_results:
                if cancel_check():
                    session.close()
                    return {"success": False, "error": "Inbound call aborted."}
                try:
                    resp = _get_with_retry(
                        next_url,
                        params=params if "search/" in next_url else None,
                        timeout=30,
                    )
                    if resp is None or not resp.ok:
                        err = f"HTTP {resp.status_code}" if resp is not None else "No response"
                        session.close()
                        return {"success": False, "error": f"CourtListener API error: {err}"}
                    data = resp.json()
                    page_items = data.get("results", [])
                    if not page_items:
                        break
                    raw_results.extend(page_items)
                    next_url = data.get("next")
                    _progress(
                        f"Found {len(raw_results)} results…",
                        min(0.1, len(raw_results) / max(max_results, 1) * 0.1),
                    )
                except Exception as e:
                    session.close()
                    return {"success": False, "error": f"CourtListener search failed: {e}"}

            raw_results = raw_results[:max_results]
            total_records = len(raw_results)
            if total_records == 0:
                session.close()
                return {"success": False, "error": "No records match those search parameters."}

            records = []
            cross_ref_count = 0
            skipped_count = 0

            for i, item in enumerate(raw_results):
                if cancel_check():
                    session.close()
                    return {"success": False, "error": "Download cancelled mid-stream."}

                cluster_id = item.get("id") or ""
                case_name = item.get("caseName")
                if not case_name:
                    cites = item.get("citation")
                    case_name = (
                        cites[0]
                        if isinstance(cites, list) and cites
                        else (cites if isinstance(cites, str) else "Unknown Case")
                    )

                canonical_key = _canonical_key_caselaw(cluster_id) if cluster_id else ""
                if canonical_key:
                    existing = self.tag_db.lookup_canonical(canonical_key)
                    if existing and existing["file_id"] != file_id:
                        self.tag_db.add_cross_ref(
                            borrower_file_id=file_id,
                            canonical_key=canonical_key,
                            source_file_id=existing["file_id"],
                            source_row_index=existing["row_index"],
                        )
                        cross_ref_count += 1
                        _progress(
                            f"Linked duplicate ({i+1}/{total_records}): {str(case_name)[:40]}…",
                            0.1 + (i / max(total_records, 1)) * 0.4,
                        )
                        continue

                _progress(
                    f"Fetching ({i+1}/{total_records}): {str(case_name)[:45]}…",
                    0.1 + (i / max(total_records, 1)) * 0.4,
                )

                opinion_ids = []
                if "opinions" in item and isinstance(item["opinions"], list):
                    for op in item["opinions"]:
                        if isinstance(op, dict) and op.get("id"):
                            opinion_ids.append(op["id"])

                if not opinion_ids and cluster_id:
                    try:
                        time.sleep(0.2)
                        cluster_resp = _get_with_retry(
                            f"https://www.courtlistener.com/api/rest/v4/clusters/{cluster_id}/",
                            timeout=15,
                        )
                        if cluster_resp and cluster_resp.status_code == 200:
                            for op_url in cluster_resp.json().get("opinions", []):
                                m = re.search(r"/opinions/(\d+)", op_url)
                                if m:
                                    opinion_ids.append(m.group(1))
                    except Exception:
                        pass

                text = ""
                for op_id in opinion_ids[:3]:
                    if cancel_check():
                        session.close()
                        return {"success": False, "error": "Download cancelled."}
                    try:
                        time.sleep(0.2)
                        op_resp = _get_with_retry(
                            f"https://www.courtlistener.com/api/rest/v4/opinions/{op_id}/",
                            timeout=15,
                        )
                        if op_resp and op_resp.status_code == 200:
                            op_data = op_resp.json()
                            text = (
                                op_data.get("html_with_citations")
                                or op_data.get("plain_text")
                                or op_data.get("html")
                                or op_data.get("html_lawbox")
                                or op_data.get("xml_harvard")
                                or ""
                            )
                            if text and str(text).strip():
                                break
                    except Exception:
                        pass

                if not text or not str(text).strip():
                    text = f"[No opinion text available for cluster {cluster_id}]"
                    skipped_count += 1

                row_index = len(records)
                records.append({
                    "type": "caselaw",
                    "case_name": case_name,
                    "date_filed": item.get("dateFiled", "Undated"),
                    "court": item.get("court", court_id).upper(),
                    "text": text,
                    "url": f"https://www.courtlistener.com{item.get('absolute_url', '')}",
                    "cluster_id": str(cluster_id),
                })

                if canonical_key:
                    self.tag_db.register_canonical(canonical_key, file_id, row_index)

            session.close()
            if not records:
                return {"success": False, "error": "All cases were duplicates or unreachable."}

            df = pd.DataFrame(records)
            df.to_parquet(local_cache_file)
            note = f" ({skipped_count} without text)" if skipped_count else ""
            _progress(
                f"Saved {len(records)} cases{note} ({cross_ref_count} linked). Generating embeddings…",
                0.5,
            )
            self.api.notify(f"Stored {len(records)} cases. Generating embeddings...", level="info")
        else:
            df = pd.read_parquet(local_cache_file)
            total_records = len(df)
            _progress(f"Loaded {total_records} cached cases. Generating embeddings…", 0.5)

        result = self._index_parquet_to_chroma(
            file_id, local_cache_file, _progress, cancel_check, start_pct=0.5
        )
        if result.get("success"):
            result["label"] = file_id
        return result

    # ------------------------------------------------------------------
    # LOCUS municipal indexing (by city) — with cross-DB deduplication
    # ------------------------------------------------------------------

    def index_real_jurisdiction(
        self,
        state: str,
        city: str,
        progress_callback=None,
        cancel_check=None,
    ):
        import duckdb

        cancel_check = cancel_check or (lambda: False)
        _progress = progress_callback or (lambda msg, pct: None)
        search_state = state.lower().strip()
        search_city = city.lower().strip()

        try:
            local_cache_file = os.path.join(
                self.download_dir, f"{search_city}_{search_state}.parquet"
            )
            if (
                os.path.exists(local_cache_file)
                and os.path.getsize(local_cache_file) < 10000
            ):
                os.remove(local_cache_file)

            df = None
            if os.path.exists(local_cache_file):
                try:
                    df = pd.read_parquet(local_cache_file)
                except Exception:
                    os.remove(local_cache_file)

            if df is None:
                if cancel_check():
                    return {"success": False}
                _progress(
                    f"Scanning LOCUS for {search_city.title()}, {search_state.upper()}…",
                    0.05,
                )
                self.api.notify(
                    f"Scanning LOCUS for {search_city}, {search_state}...", level="info"
                )
                partial_file = local_cache_file + ".part"
                if os.path.exists(partial_file):
                    os.remove(partial_file)
                with duckdb.connect() as con:
                    con.execute("INSTALL httpfs; LOAD httpfs; SET http_timeout=600;")
                    schema_df = con.execute(
                        "DESCRIBE SELECT * FROM read_parquet('hf://datasets/LocalLaws/LOCUS-v1/**/*.parquet') LIMIT 0"
                    ).df()
                    cols = [c.lower() for c in schema_df["column_name"].tolist()]
                    state_col = "state" if "state" in cols else cols[0]
                    city_col = next(
                        (
                            c
                            for c in ["city", "locality", "municipality", "jurisdiction"]
                            if c in cols
                        ),
                        None,
                    )
                    query = f"""
                        COPY (
                            SELECT * FROM read_parquet('hf://datasets/LocalLaws/LOCUS-v1/**/*.parquet')
                            WHERE LOWER({state_col}) = '{search_state}'
                              AND LOWER({city_col}) LIKE '%{search_city}%'
                        ) TO '{partial_file}' (FORMAT PARQUET);
                    """
                    con.execute(query)
                os.replace(partial_file, local_cache_file)
                if cancel_check():
                    return {"success": False}
                df = pd.read_parquet(local_cache_file)

            total_records = len(df)
            if df.empty:
                if os.path.exists(local_cache_file):
                    os.remove(local_cache_file)
                return {
                    "success": False,
                    "error": f"No laws found for '{city.title()}', '{state.upper()}'.",
                }

            _progress(f"Downloaded {total_records} laws. Generating embeddings…", 0.4)
            file_id = f"{city.title()}_{state.upper()}"
            with self._db_lock:
                collection = self.client.get_or_create_collection(name="us_local_laws")

            batch_size = 25
            cross_ref_count = 0

            for i in range(0, total_records, batch_size):
                if cancel_check():
                    return {"success": False}

                batch = df.iloc[i:i + batch_size]
                batch_texts, metadatas, ids = [], [], []
                row_offsets = []

                for idx, (_, row) in enumerate(batch.iterrows()):
                    record = self.row_to_record(row, file_id=file_id, row_index=i + idx)
                    clean_text = record["text"] or "No text found."
                    if len(clean_text) > 4000:
                        clean_text = clean_text[:4000] + "... [Truncated]"

                    r_state = record.get("state") or search_state
                    r_city = record.get("city") or search_city
                    canonical_key = _canonical_key_municipal(
                        r_state, r_city, record.get("header", "")
                    )
                    if canonical_key:
                        existing = self.tag_db.lookup_canonical(canonical_key)
                        if existing and existing["file_id"] != file_id:
                            self.tag_db.add_cross_ref(
                                borrower_file_id=file_id,
                                canonical_key=canonical_key,
                                source_file_id=existing["file_id"],
                                source_row_index=existing["row_index"],
                            )
                            cross_ref_count += 1
                            continue

                    full_text = f"[{record['label']}] {clean_text}"
                    batch_texts.append(full_text)
                    metadatas.append({
                        "doc_name": f"{city.title()} Municipal Code — {record['label']}",
                        "state": search_state.upper(),
                        "city": city.title(),
                        "section": record["section"],
                        "header": record["header"],
                        "source_type": "plugin.locallaws",
                        "source_id": file_id,
                    })
                    stable_key = f"{file_id}:{record['label']}:{clean_text}"
                    ids.append(f"law_{uuid.uuid5(uuid.NAMESPACE_URL, stable_key).hex}")
                    row_offsets.append(i + idx)

                    if canonical_key:
                        self.tag_db.register_canonical(canonical_key, file_id, i + idx)

                if not ids:
                    continue

                with self._db_lock:
                    existing_ids = set(
                        (collection.get(ids=ids) or {}).get("ids") or []
                    )
                missing = [
                    index for index, item_id in enumerate(ids) if item_id not in existing_ids
                ]
                if not missing:
                    continue

                batch_texts = [batch_texts[index] for index in missing]
                metadatas = [metadatas[index] for index in missing]
                ids = [ids[index] for index in missing]

                batch_embeddings = []
                for text in batch_texts:
                    if cancel_check():
                        return {"success": False}
                    emb = self.api.llm.get_embedding(text)
                    batch_embeddings.append(emb or [0] * 768)

                with self._db_lock:
                    collection.upsert(
                        documents=batch_texts,
                        embeddings=batch_embeddings,
                        metadatas=metadatas,
                        ids=ids,
                    )
                pct = 0.4 + ((i + batch_size) / max(total_records, 1)) * 0.58
                _progress(
                    f"Indexed {min(i + batch_size, total_records)}/{total_records} laws…",
                    min(pct, 0.98),
                )

            _progress("Municipal code indexing complete.", 1.0)
            self._records_cache.pop(file_id, None)
            self._mark_index_complete(local_cache_file)
            return {"success": True, "city": city, "cross_refs": cross_ref_count}
        except Exception as e:
            return {"success": False, "error": str(e)}

    # ------------------------------------------------------------------
    # LOCUS subject indexing (with cross-DB deduplication)
    # ------------------------------------------------------------------

    def index_by_subject(
        self,
        states_input: str,
        subject: str,
        progress_callback=None,
        cancel_check=None,
    ):
        cancel_check = cancel_check or (lambda: False)
        _progress = progress_callback or (lambda msg, pct: None)

        raw = states_input.replace(",", " ").split()
        raw_states = [s.strip().lower() for s in raw if s.strip()]
        use_all = not raw_states or raw_states == ["all"]

        if use_all:
            return self._index_subject_single(None, subject, _progress, cancel_check)
        elif len(raw_states) == 1:
            return self._index_subject_single(raw_states[0], subject, _progress, cancel_check)
        else:
            n = len(raw_states)
            any_success = False
            for i, state in enumerate(raw_states):
                if cancel_check():
                    return {"success": False, "error": "Cancelled."}

                def _sub(msg, pct, _i=i, _n=n, _s=state):
                    _progress(f"[{_s.upper()} {_i + 1}/{_n}] {msg}", (_i + pct) / _n)

                result = self._index_subject_single(state, subject, _sub, cancel_check)
                if isinstance(result, dict) and result.get("success"):
                    any_success = True

            if not any_success:
                return {
                    "success": False,
                    "error": "No matching laws found in any of the requested states.",
                }
            _progress(f"Completed all {n} states.", 1.0)
            return {"success": True}

    def _index_subject_single(self, state_or_none, subject: str, _progress, cancel_check):
        import duckdb

        subject_clean = subject.strip()
        subject_slug = (
            "".join(c if c.isalnum() else "_" for c in subject_clean.lower())[:24] or "all"
        )

        if state_or_none is None:
            search_state = None
            state_label = "ALL STATES"
            file_label = f"{subject_slug}_subj_all"
        else:
            search_state = state_or_none.lower().strip()
            state_label = search_state.upper()
            file_label = f"{subject_slug}_subj_{search_state}"

        try:
            local_cache_file = os.path.join(self.download_dir, f"{file_label}.parquet")
            if (
                os.path.exists(local_cache_file)
                and os.path.getsize(local_cache_file) < 10000
            ):
                os.remove(local_cache_file)

            df = None
            if os.path.exists(local_cache_file):
                try:
                    df = pd.read_parquet(local_cache_file)
                except Exception:
                    os.remove(local_cache_file)

            if df is None:
                if cancel_check():
                    return {"success": False}
                _progress(
                    f"Scanning LOCUS for '{subject_clean}' in {state_label}…", 0.05
                )
                self.api.notify(
                    f"Scanning LOCUS: '{subject_clean}' in {state_label}", level="info"
                )
                partial_file = local_cache_file + ".part"
                if os.path.exists(partial_file):
                    os.remove(partial_file)

                with duckdb.connect() as con:
                    con.execute("INSTALL httpfs; LOAD httpfs; SET http_timeout=600;")
                    schema_df = con.execute(
                        "DESCRIBE SELECT * FROM read_parquet('hf://datasets/LocalLaws/LOCUS-v1/**/*.parquet') LIMIT 0"
                    ).df()
                    cols = [c.lower() for c in schema_df["column_name"].tolist()]
                    state_col = "state" if "state" in cols else cols[0]
                    text_cols = [
                        c
                        for c in cols
                        if c in ("content", "text", "body", "law_text", "header", "heading")
                    ]

                    subject_sql = self._build_subject_sql(subject_clean, text_cols)

                    if search_state:
                        safe_state = search_state.replace("'", "''")
                        state_filter = f"LOWER({state_col}) = '{safe_state}'"
                        where_clause = f"{state_filter}{' AND (' + subject_sql + ')' if subject_sql else ''}"
                    else:
                        where_clause = f"({subject_sql})" if subject_sql else "1=1"

                    con.execute(
                        f"COPY (SELECT * FROM read_parquet('hf://datasets/LocalLaws/LOCUS-v1/**/*.parquet') "
                        f"WHERE {where_clause}) TO '{partial_file}' (FORMAT PARQUET);"
                    )
                os.replace(partial_file, local_cache_file)
                if cancel_check():
                    return {"success": False}
                df = pd.read_parquet(local_cache_file)

            total_records = len(df)
            if df.empty:
                if os.path.exists(local_cache_file):
                    os.remove(local_cache_file)
                return {
                    "success": False,
                    "error": f"No laws found matching '{subject_clean}' in {state_label}.",
                }

            _progress(f"Downloaded {total_records} laws. Generating embeddings…", 0.4)
            with self._db_lock:
                collection = self.client.get_or_create_collection(name="us_local_laws")

            city_display = f"[{state_label}] {subject_clean[:20]}"
            batch_size = 25
            cross_ref_count = 0

            for i in range(0, total_records, batch_size):
                if cancel_check():
                    return {"success": False}
                batch = df.iloc[i:i + batch_size]
                batch_texts, metadatas, ids = [], [], []

                for idx, (_, row) in enumerate(batch.iterrows()):
                    record = self.row_to_record(
                        row, file_id=file_label, row_index=i + idx
                    )
                    clean_text = record["text"] or "No text found."
                    if len(clean_text) > 4000:
                        clean_text = clean_text[:4000] + "... [Truncated]"

                    r_state = record.get("state") or (search_state or "all")
                    r_city = record.get("city") or city_display
                    canonical_key = _canonical_key_municipal(
                        r_state, r_city, record.get("header", "")
                    )
                    if canonical_key:
                        existing = self.tag_db.lookup_canonical(canonical_key)
                        if existing and existing["file_id"] != file_label:
                            self.tag_db.add_cross_ref(
                                borrower_file_id=file_label,
                                canonical_key=canonical_key,
                                source_file_id=existing["file_id"],
                                source_row_index=existing["row_index"],
                            )
                            cross_ref_count += 1
                            continue

                    full_text = f"[{record['label']}] {clean_text}"
                    batch_texts.append(full_text)
                    metadatas.append({
                        "doc_name": f"{city_display} — {record['label']}",
                        "state": (search_state or "ALL").upper(),
                        "city": city_display,
                        "section": record["section"],
                        "header": record["header"],
                        "source_type": "plugin.locallaws",
                        "source_id": file_label,
                    })
                    stable_key = f"{file_label}:{record['label']}:{clean_text}"
                    ids.append(f"law_{uuid.uuid5(uuid.NAMESPACE_URL, stable_key).hex}")

                    if canonical_key:
                        self.tag_db.register_canonical(canonical_key, file_label, i + idx)

                if not ids:
                    continue

                with self._db_lock:
                    existing_ids = set(
                        (collection.get(ids=ids) or {}).get("ids") or []
                    )
                missing = [idx for idx, item_id in enumerate(ids) if item_id not in existing_ids]
                if not missing:
                    continue

                batch_texts = [batch_texts[idx] for idx in missing]
                metadatas = [metadatas[idx] for idx in missing]
                ids = [ids[idx] for idx in missing]

                batch_embeddings = []
                for text in batch_texts:
                    if cancel_check():
                        return {"success": False}
                    emb = self.api.llm.get_embedding(text)
                    batch_embeddings.append(emb or [0] * 768)

                with self._db_lock:
                    collection.upsert(
                        documents=batch_texts,
                        embeddings=batch_embeddings,
                        metadatas=metadatas,
                        ids=ids,
                    )
                pct = 0.4 + ((i + batch_size) / max(total_records, 1)) * 0.58
                _progress(
                    f"Indexed {min(i + batch_size, total_records)}/{total_records} laws…",
                    min(pct, 0.98),
                )

            _progress(f"Indexed {state_label} — subject complete.", 1.0)
            self._records_cache.pop(file_label, None)
            self._mark_index_complete(local_cache_file)
            return {"success": True, "file_id": file_label, "cross_refs": cross_ref_count}
        except Exception as e:
            return {"success": False, "error": str(e)}
