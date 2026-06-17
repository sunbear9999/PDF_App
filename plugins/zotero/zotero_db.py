"""
plugins/zotero/zotero_db.py

Offline, read-only access to the local Zotero SQLite database.
Does NOT require Zotero to be running — opens the DB in read-only WAL mode.
Falls back to a temp-file copy if a write-lock is detected.
"""
from __future__ import annotations

import os
import glob
import shutil
import sqlite3
import tempfile
from typing import List, Dict, Optional


_ZOTERO_ITEM_EXCLUDE_TYPES = {"attachment", "note"}


def _find_zotero_db() -> Optional[str]:
    """Return the path to zotero.sqlite, or None if not found."""
    # Env override
    env_path = os.environ.get("ZOTERO_DB_PATH")
    if env_path and os.path.isfile(env_path):
        return env_path

    home = os.path.expanduser("~")
    candidates = [
        os.path.join(home, "Zotero", "zotero.sqlite"),
        # Legacy Linux profile location
        *glob.glob(os.path.join(home, ".zotero", "zotero", "*", "zotero.sqlite")),
    ]
    for path in candidates:
        if os.path.isfile(path):
            return path
    return None


class ZoteroDB:
    """
    Read-only wrapper around the Zotero local SQLite database.

    Usage::

        db = ZoteroDB()
        if db.is_available():
            items = db.get_items()
    """

    def __init__(self, db_path: Optional[str] = None):
        self._db_path = db_path or _find_zotero_db()
        self._zotero_data_dir = (
            os.path.dirname(self._db_path) if self._db_path else None
        )

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def is_available(self) -> bool:
        return self._db_path is not None and os.path.isfile(self._db_path)

    def get_db_path(self) -> Optional[str]:
        return self._db_path

    def get_collections(self) -> List[Dict]:
        """Return all collections as {id, name, parent_id}."""
        sql = """
            SELECT collectionID, collectionName, parentCollectionID
            FROM collections
            ORDER BY collectionName
        """
        rows = self._query(sql)
        return [
            {"id": r[0], "name": r[1], "parent_id": r[2]}
            for r in rows
        ]

    def get_tags(self) -> List[str]:
        """Return sorted list of all tag names."""
        rows = self._query("SELECT name FROM tags ORDER BY name")
        return [r[0] for r in rows]

    def get_items(
        self,
        collection_id: Optional[int] = None,
        tag: Optional[str] = None,
        search: Optional[str] = None,
    ) -> List[Dict]:
        """
        Return items (excluding notes and attachments).

        Optionally filter by collection_id, tag name, or a search string
        (matched against title and author last names).
        """
        # Base query: items with type name, excluding internal types
        sql = """
            SELECT i.itemID, it.typeName, i.key
            FROM items i
            JOIN itemTypes it ON i.itemTypeID = it.itemTypeID
            WHERE it.typeName NOT IN ('attachment', 'note')
            AND i.itemID NOT IN (SELECT itemID FROM deletedItems)
        """
        params: list = []

        if collection_id is not None:
            sql += """
                AND i.itemID IN (
                    SELECT itemID FROM collectionItems WHERE collectionID = ?
                )
            """
            params.append(collection_id)

        if tag:
            sql += """
                AND i.itemID IN (
                    SELECT it2.itemID FROM itemTags it2
                    JOIN tags t ON it2.tagID = t.tagID
                    WHERE t.name = ?
                )
            """
            params.append(tag)

        rows = self._query(sql, params)
        items = []
        for item_id, type_name, key in rows:
            item = self._fetch_item_data(item_id, type_name, key)
            items.append(item)

        # Client-side search filter (SQLite FTS not guaranteed)
        if search:
            q = search.lower()
            items = [
                i for i in items
                if q in i.get("title", "").lower()
                or q in i.get("authors_display", "").lower()
                or q in str(i.get("year", ""))
            ]

        return items

    def get_item(self, item_id: int) -> Optional[Dict]:
        """Return full data for a single item, or None."""
        rows = self._query(
            """
            SELECT i.itemID, it.typeName, i.key
            FROM items i
            JOIN itemTypes it ON i.itemTypeID = it.itemTypeID
            WHERE i.itemID = ?
            """,
            [item_id],
        )
        if not rows:
            return None
        r = rows[0]
        return self._fetch_item_data(r[0], r[1], r[2])

    def get_item_by_doc_id(self, doc_id: str) -> Optional[Dict]:
        """Resolve a 'zotero:{item_id}' doc_id back to an item dict."""
        if not doc_id.startswith("zotero:"):
            return None
        try:
            item_id = int(doc_id[7:])
        except ValueError:
            return None
        return self.get_item(item_id)

    def get_attachments(self, item_id: int) -> List[Dict]:
        """
        Return PDF attachments for an item.
        Each dict has: {item_id, content_type, path, local_path, title}
        """
        rows = self._query(
            """
            SELECT ia.itemID, ia.contentType, ia.path, ia.linkMode
            FROM itemAttachments ia
            WHERE ia.parentItemID = ?
            AND (ia.contentType LIKE '%pdf%' OR ia.path LIKE '%.pdf')
            """,
            [item_id],
        )
        attachments = []
        for att_id, ct, path, link_mode in rows:
            local = self._resolve_attachment_path(att_id, path, link_mode)
            title_rows = self._query(
                """
                SELECT idv.value FROM itemData id
                JOIN fields f ON id.fieldID = f.fieldID
                JOIN itemDataValues idv ON id.valueID = idv.valueID
                WHERE id.itemID = ? AND f.fieldName = 'title'
                """,
                [att_id],
            )
            title = title_rows[0][0] if title_rows else os.path.basename(path or "")
            attachments.append(
                {
                    "item_id": att_id,
                    "content_type": ct,
                    "path": path,
                    "local_path": local,
                    "title": title,
                }
            )
        return attachments

    def get_notes(self, item_id: int) -> List[str]:
        """Return HTML note strings for an item."""
        rows = self._query(
            "SELECT note FROM itemNotes WHERE parentItemID = ?",
            [item_id],
        )
        return [r[0] for r in rows if r[0]]

    def get_item_tags(self, item_id: int) -> List[str]:
        """Return tag names for a single item."""
        rows = self._query(
            """
            SELECT t.name FROM itemTags it
            JOIN tags t ON it.tagID = t.tagID
            WHERE it.itemID = ?
            ORDER BY t.name
            """,
            [item_id],
        )
        return [r[0] for r in rows]

    def find_matching_pdf(self, pdf_path: str) -> Optional[Dict]:
        """
        Check if a project PDF path matches a Zotero attachment.
        Returns the parent item dict if found, else None.
        """
        basename = os.path.basename(pdf_path)
        rows = self._query(
            """
            SELECT ia.parentItemID FROM itemAttachments ia
            WHERE ia.path LIKE ? OR ia.path LIKE ?
            LIMIT 1
            """,
            [f"%{basename}", f"storage:{basename}"],
        )
        if not rows or rows[0][0] is None:
            return None
        return self.get_item(rows[0][0])

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _fetch_item_data(self, item_id: int, type_name: str, key: str) -> Dict:
        """Build a complete item dict from the database."""
        # Field values
        field_rows = self._query(
            """
            SELECT f.fieldName, idv.value
            FROM itemData id
            JOIN fields f ON id.fieldID = f.fieldID
            JOIN itemDataValues idv ON id.valueID = idv.valueID
            WHERE id.itemID = ?
            """,
            [item_id],
        )
        fields: Dict[str, str] = {r[0]: r[1] for r in field_rows}

        # Creators
        creator_rows = self._query(
            """
            SELECT c.firstName, c.lastName, ct.creatorType, ic.orderIndex
            FROM itemCreators ic
            JOIN creators c ON ic.creatorID = c.creatorID
            JOIN creatorTypes ct ON ic.creatorTypeID = ct.creatorTypeID
            WHERE ic.itemID = ?
            ORDER BY ic.orderIndex
            """,
            [item_id],
        )
        creators = [
            {
                "first": r[0] or "",
                "last": r[1] or "",
                "type": r[2],
                "order": r[3],
            }
            for r in creator_rows
        ]

        # Human-readable author string for display
        authors_display = "; ".join(
            f"{c['last']}, {c['first']}" if c["first"] else c["last"]
            for c in creators
            if c["type"] in ("author", "editor", "seriesEditor")
        )

        return {
            "item_id": item_id,
            "item_type": type_name,
            "key": key,
            "doc_id": f"zotero:{item_id}",
            "title": fields.get("title", ""),
            "date": fields.get("date", ""),
            "year": (fields.get("date", "") or "")[:4],
            "publicationTitle": fields.get("publicationTitle", ""),
            "publisher": fields.get("publisher", ""),
            "volume": fields.get("volume", ""),
            "issue": fields.get("issue", ""),
            "pages": fields.get("pages", ""),
            "DOI": fields.get("DOI", ""),
            "url": fields.get("url", ""),
            "ISBN": fields.get("ISBN", ""),
            "ISSN": fields.get("ISSN", ""),
            "abstractNote": fields.get("abstractNote", ""),
            "place": fields.get("place", ""),
            "edition": fields.get("edition", ""),
            "series": fields.get("series", ""),
            "language": fields.get("language", ""),
            "creators": creators,
            "authors_display": authors_display,
            # Extra fields map for UI display
            "fields": fields,
        }

    def _resolve_attachment_path(
        self, att_id: int, path: Optional[str], link_mode: Optional[int]
    ) -> Optional[str]:
        """Resolve a Zotero attachment path to a local filesystem path."""
        if not path:
            return None
        if os.path.isabs(path):
            return path if os.path.isfile(path) else None

        if path.startswith("storage:") and self._zotero_data_dir:
            filename = path[len("storage:"):]
            # Zotero stores files under storage/{parent_key}/
            key_rows = self._query(
                "SELECT key FROM items WHERE itemID = ?", [att_id]
            )
            if key_rows:
                att_key = key_rows[0][0]
                local = os.path.join(
                    self._zotero_data_dir, "storage", att_key, filename
                )
                if os.path.isfile(local):
                    return local

        if path.startswith("attachments:") and self._zotero_data_dir:
            rel = path[len("attachments:"):]
            local = os.path.join(self._zotero_data_dir, rel)
            if os.path.isfile(local):
                return local

        return None

    def _query(self, sql: str, params: list = None) -> list:
        """Execute a read-only query, returning all rows."""
        if not self._db_path:
            return []
        try:
            uri = f"file:{self._db_path}?mode=ro"
            conn = sqlite3.connect(uri, uri=True)
            conn.row_factory = None
            cur = conn.cursor()
            cur.execute(sql, params or [])
            rows = cur.fetchall()
            conn.close()
            return rows
        except sqlite3.OperationalError as exc:
            if "database is locked" in str(exc):
                return self._query_via_copy(sql, params)
            print(f"[ZoteroDB] Query error: {exc}")
            return []
        except Exception as exc:
            print(f"[ZoteroDB] Unexpected error: {exc}")
            return []

    def _query_via_copy(self, sql: str, params: list = None) -> list:
        """Copy the DB to a temp file and retry (handles write-lock from running Zotero)."""
        try:
            with tempfile.NamedTemporaryFile(suffix=".sqlite", delete=False) as tmp:
                tmp_path = tmp.name
            shutil.copy2(self._db_path, tmp_path)
            conn = sqlite3.connect(tmp_path)
            cur = conn.cursor()
            cur.execute(sql, params or [])
            rows = cur.fetchall()
            conn.close()
            os.unlink(tmp_path)
            return rows
        except Exception as exc:
            print(f"[ZoteroDB] Copy-query error: {exc}")
            return []
