from __future__ import annotations

import hashlib
import json
import uuid
from typing import Any, Dict, Optional

from core.db.base_db import BaseDB


class MediaAssetDB(BaseDB):
    """Project-contained binary assets shared by core features and plugins."""

    def put(self, data: bytes, mime_type: str, metadata: Optional[Dict[str, Any]] = None) -> str:
        if not self._conn:
            raise RuntimeError("A project must be open before saving a media asset.")
        digest = hashlib.sha256(data).hexdigest()
        row = self._conn.execute("SELECT id FROM media_assets WHERE sha256 = ?", (digest,)).fetchone()
        if row:
            return str(row[0])
        asset_id = f"asset_{uuid.uuid4()}"
        self._conn.execute(
            "INSERT INTO media_assets (id, mime_type, sha256, data, metadata_json) VALUES (?, ?, ?, ?, ?)",
            (asset_id, mime_type or "application/octet-stream", digest, data, json.dumps(metadata or {}, ensure_ascii=False)),
        )
        self._conn.commit()
        return asset_id

    def get(self, asset_id: str) -> Optional[Dict[str, Any]]:
        if not self._conn or not asset_id:
            return None
        row = self._conn.execute(
            "SELECT id, mime_type, sha256, data, metadata_json FROM media_assets WHERE id = ?", (asset_id,)
        ).fetchone()
        if not row:
            return None
        try:
            metadata = json.loads(row[4] or "{}")
        except Exception:
            metadata = {}
        return {"asset_id": row[0], "mime_type": row[1], "sha256": row[2], "data": bytes(row[3]), "metadata": metadata}
