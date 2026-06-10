import sqlite3
import json
import uuid
from core.db.base_db import BaseDB

class DatabaseSchema(BaseDB):
    def init_database(self):
        """Initializes the SQLite database and handles all schema creation and migrations."""
        if not self.manager.project_filepath:
            return
            
        try:
            if self.manager._conn:
                self.manager._conn.close()
            
            self.manager._conn = sqlite3.connect(self.manager.project_filepath)
            self.manager._conn.execute("PRAGMA foreign_keys = ON")
            cursor = self.manager._conn.cursor()
            
            cursor.execute('''CREATE TABLE IF NOT EXISTS metadata (key TEXT PRIMARY KEY, value TEXT)''')
            cursor.execute('''CREATE TABLE IF NOT EXISTS pdfs (path TEXT PRIMARY KEY)''')
            cursor.execute('''CREATE TABLE IF NOT EXISTS highlights (
                id TEXT PRIMARY KEY, doc_id TEXT, page_num INTEGER,
                rect_coords TEXT, text_content TEXT, note_content TEXT, color TEXT
            )''')
            cursor.execute("PRAGMA table_info(highlights)")
            highlight_columns = [col[1] for col in cursor.fetchall()]
            if "note_content" not in highlight_columns:
                try: cursor.execute("ALTER TABLE highlights ADD COLUMN note_content TEXT")
                except sqlite3.OperationalError: pass
            
            self._ensure_nodes_table(cursor)
            
            cursor.execute('''CREATE TABLE IF NOT EXISTS workspaces (
                id INTEGER PRIMARY KEY, name TEXT NOT NULL
            )''')
            cursor.execute("INSERT OR IGNORE INTO workspaces (id, name) VALUES (1, 'Main Board')")
            self._migrate_workspace_id_to_integer(cursor)
            
            cursor.execute('''CREATE TABLE IF NOT EXISTS edges (
                edge_id TEXT PRIMARY KEY, source_id TEXT, target_id TEXT, label TEXT, color TEXT
            )''')
            
            # Migration: add weight and workspace_id columns if missing
            cursor.execute("PRAGMA table_info(edges)")
            edge_columns = [col[1] for col in cursor.fetchall()]
            if "weight" not in edge_columns:
                try: cursor.execute("ALTER TABLE edges ADD COLUMN weight INTEGER DEFAULT 2")
                except sqlite3.OperationalError: pass
            if "workspace_id" not in edge_columns:
                try: cursor.execute("ALTER TABLE edges ADD COLUMN workspace_id INTEGER DEFAULT 1")
                except sqlite3.OperationalError: pass
                
            # Migration: add embedding_vector to nodes
            cursor.execute("PRAGMA table_info(nodes)")
            node_columns = [col[1] for col in cursor.fetchall()]
            if "embedding_vector" not in node_columns:
                try: cursor.execute("ALTER TABLE nodes ADD COLUMN embedding_vector TEXT")
                except sqlite3.OperationalError: pass
            if "node_type_id" not in node_columns:
                try: cursor.execute("ALTER TABLE nodes ADD COLUMN node_type_id TEXT")
                except sqlite3.OperationalError: pass

            # Tags and Relations
            cursor.execute('''CREATE TABLE IF NOT EXISTS tags (
                id INTEGER PRIMARY KEY, name TEXT UNIQUE, color TEXT
            )''')
            cursor.execute('''CREATE TABLE IF NOT EXISTS doc_tags (
                doc_id TEXT, tag_id INTEGER,
                PRIMARY KEY (doc_id, tag_id),
                FOREIGN KEY(tag_id) REFERENCES tags(id) ON DELETE CASCADE
            )''')
            cursor.execute('''CREATE TABLE IF NOT EXISTS node_tags (
                node_id TEXT, tag_id INTEGER,
                PRIMARY KEY (node_id, tag_id),
                FOREIGN KEY(tag_id) REFERENCES tags(id) ON DELETE CASCADE
            )''')
            
            # Research Components
            cursor.execute('''CREATE TABLE IF NOT EXISTS essays (
                id TEXT PRIMARY KEY, title TEXT, content TEXT,
                last_edited DATETIME DEFAULT CURRENT_TIMESTAMP
            )''')
            cursor.execute('''CREATE TABLE IF NOT EXISTS citations (
                doc_id TEXT PRIMARY KEY, title TEXT, authors TEXT,
                year TEXT, journal TEXT, doi TEXT
            )''')
            
            # Analysis
            cursor.execute('''CREATE TABLE IF NOT EXISTS analysis_templates (
                id INTEGER PRIMARY KEY AUTOINCREMENT, title TEXT UNIQUE,
                prompt_instructions TEXT, json_schema TEXT
            )''')
            cursor.execute('''CREATE TABLE IF NOT EXISTS document_analyses (
                doc_path TEXT, template_id TEXT, chunk_index INTEGER, json_data TEXT
            )''')

            self._ensure_graph_tables(cursor)
            self._migrate_legacy_workspace_to_graph(cursor)

            self.manager._conn.commit()

            # Tracking and Auditing migrations
            cursor.execute("PRAGMA table_info(nodes)")
            node_columns = [col[1] for col in cursor.fetchall()]
            
            if "node_origin" not in node_columns:
                try: cursor.execute("ALTER TABLE nodes ADD COLUMN node_origin TEXT DEFAULT 'human'")
                except sqlite3.OperationalError: pass
            if "is_verified" not in node_columns:
                try: cursor.execute("ALTER TABLE nodes ADD COLUMN is_verified INTEGER DEFAULT 0")
                except sqlite3.OperationalError: pass
            if "original_text" not in node_columns:
                try: cursor.execute("ALTER TABLE nodes ADD COLUMN original_text TEXT")
                except sqlite3.OperationalError: pass

            cursor.execute('''CREATE TABLE IF NOT EXISTS ai_audit_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT, timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
                prompt TEXT, response TEXT, model_used TEXT
            )''')
            
            cursor.execute('''CREATE TABLE IF NOT EXISTS chat_messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT, tab_name TEXT, role TEXT, content TEXT,
                ui_format TEXT DEFAULT 'live_stream', timestamp DATETIME DEFAULT CURRENT_TIMESTAMP
            )''')
            
            self.manager._conn.commit()
            
        except sqlite3.Error as e:
            print(f"Database initialization error: {e}")

    def _ensure_graph_tables(self, cursor):
        cursor.execute('''CREATE TABLE IF NOT EXISTS entities (
            id TEXT PRIMARY KEY,
            entity_type TEXT NOT NULL,
            origin_id TEXT,
            properties TEXT NOT NULL DEFAULT '{}',
            state TEXT NOT NULL DEFAULT '{}',
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
        )''')
        cursor.execute('''CREATE TABLE IF NOT EXISTS relations (
            id TEXT PRIMARY KEY,
            relation_type TEXT NOT NULL,
            source_id TEXT NOT NULL,
            target_id TEXT NOT NULL,
            evidence_ids TEXT NOT NULL DEFAULT '[]',
            properties TEXT NOT NULL DEFAULT '{}',
            state TEXT NOT NULL DEFAULT '{}',
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            updated_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY(source_id) REFERENCES entities(id) ON DELETE CASCADE,
            FOREIGN KEY(target_id) REFERENCES entities(id) ON DELETE CASCADE
        )''')
        cursor.execute('''CREATE TABLE IF NOT EXISTS views (
            id TEXT PRIMARY KEY,
            view_type TEXT NOT NULL DEFAULT 'view.graph',
            name TEXT NOT NULL,
            properties TEXT NOT NULL DEFAULT '{}',
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
        )''')
        cursor.execute('''CREATE TABLE IF NOT EXISTS view_entity_meta (
            view_id TEXT NOT NULL,
            entity_id TEXT NOT NULL,
            x REAL DEFAULT 0,
            y REAL DEFAULT 0,
            color TEXT,
            is_collapsed INTEGER DEFAULT 0,
            properties TEXT NOT NULL DEFAULT '{}',
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            updated_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY(view_id, entity_id),
            FOREIGN KEY(view_id) REFERENCES views(id) ON DELETE CASCADE,
            FOREIGN KEY(entity_id) REFERENCES entities(id) ON DELETE CASCADE
        )''')
        self._ensure_columns(cursor, "entities", {
            "created_at": "DATETIME DEFAULT CURRENT_TIMESTAMP",
            "updated_at": "DATETIME DEFAULT CURRENT_TIMESTAMP",
        })
        self._ensure_columns(cursor, "relations", {
            "created_at": "DATETIME DEFAULT CURRENT_TIMESTAMP",
            "updated_at": "DATETIME DEFAULT CURRENT_TIMESTAMP",
        })
        self._ensure_columns(cursor, "views", {
            "created_at": "DATETIME DEFAULT CURRENT_TIMESTAMP",
            "updated_at": "DATETIME DEFAULT CURRENT_TIMESTAMP",
        })
        self._ensure_columns(cursor, "view_entity_meta", {
            "created_at": "DATETIME DEFAULT CURRENT_TIMESTAMP",
            "updated_at": "DATETIME DEFAULT CURRENT_TIMESTAMP",
        })
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_entities_type ON entities(entity_type)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_entities_origin ON entities(origin_id)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_relations_type ON relations(relation_type)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_relations_source ON relations(source_id)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_relations_target ON relations(target_id)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_view_entity_meta_view ON view_entity_meta(view_id)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_view_entity_meta_entity ON view_entity_meta(entity_id)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_entities_unverified ON entities(json_extract(state, '$.is_verified'))")

    def _migrate_legacy_workspace_to_graph(self, cursor):
        cursor.execute("INSERT OR IGNORE INTO views (id, view_type, name, properties) VALUES ('1', 'view.graph', 'Main Board', '{}')")

        try:
            cursor.execute("SELECT id, name FROM workspaces")
            for workspace_id, name in cursor.fetchall():
                cursor.execute(
                    "INSERT OR IGNORE INTO views (id, view_type, name, properties) VALUES (?, 'view.graph', ?, ?)",
                    (str(workspace_id), name or f"Board {workspace_id}", json.dumps({"legacy_workspace_id": workspace_id})),
                )
        except sqlite3.Error:
            pass

        try:
            cursor.execute("SELECT path FROM pdfs")
            for (pdf_path,) in cursor.fetchall():
                source_id = self._source_entity_id(pdf_path)
                cursor.execute(
                    """INSERT OR IGNORE INTO entities (id, entity_type, origin_id, properties, state)
                       VALUES (?, 'entity.source', ?, ?, ?)""",
                    (
                        source_id,
                        pdf_path,
                        json.dumps({"path": pdf_path, "title": pdf_path.split("/")[-1] if pdf_path else ""}),
                        json.dumps({"is_verified": True, "ai_generated": False, "origin": "human"}),
                    ),
                )
        except sqlite3.Error:
            pass

        if not self._table_exists(cursor, "nodes"):
            return

        cursor.execute("PRAGMA table_info(nodes)")
        node_cols = [col[1] for col in cursor.fetchall()]
        select_cols = [
            "id", "highlight_id", "workspace_id", "quote", "note_text", "color",
            "is_custom", "pdf_path", "page_num", "manual_font_size", "x", "y",
            "width", "height", "node_origin", "is_verified", "original_text",
            "embedding_vector", "node_type_id",
        ]
        sql_select = ", ".join(col if col in node_cols else self._legacy_default_expr(col) for col in select_cols)
        cursor.execute(f"SELECT {sql_select} FROM nodes")
        for row in cursor.fetchall():
            data = dict(zip(select_cols, row))
            workspace_id = self._normalize_view_id(data.get("workspace_id"))
            entity_type = self._legacy_entity_type(data)
            quote = data.get("quote") or ""
            note_text = data.get("note_text") or ""
            source_backed = entity_type in {"entity.quote", "entity.evidence"}
            properties = {
                "quote": quote,
                "exact_text": quote,
                "text": quote if source_backed else note_text,
                "note_text": "" if source_backed and note_text.strip() == quote.strip() else note_text,
                "color": data.get("color"),
                "is_custom": bool(data.get("is_custom")),
                "pdf_path": data.get("pdf_path"),
                "page_num": data.get("page_num"),
                "highlight_id": data.get("highlight_id"),
                "manual_font_size": data.get("manual_font_size"),
                "original_text": data.get("original_text") or data.get("note_text") or "",
                "embedding_vector": data.get("embedding_vector"),
                "node_type_id": data.get("node_type_id") or "",
            }
            if data.get("pdf_path"):
                properties["source_id"] = self._source_entity_id(data["pdf_path"])
            state = {
                "is_verified": bool(data.get("is_verified")),
                "ai_generated": data.get("node_origin") == "ai",
                "origin": data.get("node_origin") or "human",
            }
            origin_id = data.get("highlight_id") or data.get("pdf_path")
            cursor.execute(
                """INSERT OR IGNORE INTO entities (id, entity_type, origin_id, properties, state)
                   VALUES (?, ?, ?, ?, ?)""",
                (data["id"], entity_type, origin_id, json.dumps(properties), json.dumps(state)),
            )
            cursor.execute(
                """INSERT OR REPLACE INTO view_entity_meta
                   (view_id, entity_id, x, y, color, is_collapsed, properties)
                   VALUES (?, ?, ?, ?, ?, 0, ?)""",
                (
                    workspace_id,
                    data["id"],
                    data.get("x") or 0,
                    data.get("y") or 0,
                    data.get("color"),
                    json.dumps({
                        "width": data.get("width") or 150,
                        "height": data.get("height") or 80,
                        "legacy_workspace_id": workspace_id,
                    }),
                ),
            )

        if self._table_exists(cursor, "edges"):
            cursor.execute("PRAGMA table_info(edges)")
            edge_cols = [col[1] for col in cursor.fetchall()]
            edge_select = ["edge_id", "source_id", "target_id", "label", "color", "weight", "workspace_id"]
            sql_select = ", ".join(col if col in edge_cols else self._legacy_default_expr(col) for col in edge_select)
            cursor.execute(f"SELECT {sql_select} FROM edges")
            for row in cursor.fetchall():
                edge = dict(zip(edge_select, row))
                view_id = self._normalize_view_id(edge.get("workspace_id"))
                props = {
                    "label": edge.get("label") or "",
                    "color": edge.get("color") or "#888888",
                    "weight": edge.get("weight") if edge.get("weight") is not None else 2,
                    "view_ids": [view_id],
                    "legacy_workspace_id": view_id,
                }
                cursor.execute("SELECT 1 FROM entities WHERE id = ?", (edge.get("source_id"),))
                if not cursor.fetchone():
                    continue
                cursor.execute("SELECT 1 FROM entities WHERE id = ?", (edge.get("target_id"),))
                if not cursor.fetchone():
                    continue
                cursor.execute(
                    """INSERT OR IGNORE INTO relations
                       (id, relation_type, source_id, target_id, evidence_ids, properties, state)
                       VALUES (?, 'relation.basic', ?, ?, '[]', ?, ?)""",
                    (
                        edge.get("edge_id"),
                        edge.get("source_id"),
                        edge.get("target_id"),
                        json.dumps(props),
                        json.dumps({"is_verified": True, "origin": "legacy"}),
                    ),
                )

        cursor.execute("INSERT OR REPLACE INTO metadata (key, value) VALUES ('graph_phase1_migrated', '1')")

    def _table_exists(self, cursor, name):
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name = ?", (name,))
        return cursor.fetchone() is not None

    def _ensure_columns(self, cursor, table_name, columns):
        cursor.execute(f"PRAGMA table_info({table_name})")
        existing = {col[1] for col in cursor.fetchall()}
        for col_name, col_type in columns.items():
            if col_name not in existing:
                try:
                    cursor.execute(f"ALTER TABLE {table_name} ADD COLUMN {col_name} {col_type}")
                except sqlite3.OperationalError:
                    if "CURRENT_TIMESTAMP" in col_type:
                        try:
                            cursor.execute(f"ALTER TABLE {table_name} ADD COLUMN {col_name} DATETIME")
                        except sqlite3.OperationalError:
                            pass

    def _legacy_default_expr(self, col):
        defaults = {
            "workspace_id": "1",
            "is_custom": "0",
            "manual_font_size": "NULL",
            "x": "0",
            "y": "0",
            "width": "150",
            "height": "80",
            "node_origin": "'human'",
            "is_verified": "0",
            "embedding_vector": "NULL",
            "node_type_id": "''",
            "weight": "2",
            "color": "'#888888'",
        }
        return f"{defaults.get(col, 'NULL')} AS {col}"

    def _normalize_view_id(self, workspace_id):
        if workspace_id is None or workspace_id == "default":
            return "1"
        return str(workspace_id)

    def _source_entity_id(self, pdf_path):
        return f"source:{uuid.uuid5(uuid.NAMESPACE_URL, pdf_path or '')}"

    def _legacy_entity_type(self, data):
        node_type_id = data.get("node_type_id") or ""
        if node_type_id.startswith("entity."):
            return node_type_id
        if data.get("pdf_path") or data.get("highlight_id") or data.get("quote"):
            return "entity.quote"
        return "entity.text"

    def _ensure_nodes_table(self, cursor):
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='nodes'")
        has_nodes_table = cursor.fetchone() is not None

        if not has_nodes_table:
            cursor.execute('''CREATE TABLE nodes (
                id TEXT PRIMARY KEY, highlight_id TEXT, workspace_id TEXT,
                quote TEXT, note_text TEXT, color TEXT, is_custom INTEGER,
                pdf_path TEXT, page_num INTEGER, manual_font_size INTEGER,
                x REAL, y REAL, width REAL, height REAL
            )''')
            return

        # Migration block for v2
        cursor.execute("PRAGMA table_info(nodes)")
        columns = [col[1] for col in cursor.fetchall()]
        required_columns = {
            "id", "highlight_id", "workspace_id", "quote", "note_text", "color",
            "is_custom", "pdf_path", "page_num", "manual_font_size", "x", "y",
            "width", "height"
        }
        if required_columns.issubset(set(columns)):
            return

        cursor.execute('''CREATE TABLE nodes_v2 (
            id TEXT PRIMARY KEY, highlight_id TEXT, workspace_id TEXT,
            quote TEXT, note_text TEXT, color TEXT, is_custom INTEGER,
            pdf_path TEXT, page_num INTEGER, manual_font_size INTEGER,
            x REAL, y REAL, width REAL, height REAL
        )''')

        if columns:
            cursor.execute("SELECT node_id, quote, note, color, is_custom, pdf_path, page_num, manual_font_size, x, y, width, height FROM nodes")
            migrated_rows = []
            migrated_highlights = []
            for row in cursor.fetchall():
                node_id, quote, note, color, is_custom, pdf_path, page_num, manual_font_size, x, y, width, height = row
                highlight_id = node_id if not is_custom else None
                migrated_rows.append((
                    node_id, highlight_id, "default", quote, note, color, is_custom,
                    pdf_path, page_num, manual_font_size, x, y, width, height,
                ))
                if highlight_id:
                    migrated_highlights.append((highlight_id, pdf_path, page_num, None, quote, note, color))

            cursor.executemany(
                """
                INSERT INTO nodes_v2 (
                    id, highlight_id, workspace_id, quote, note_text, color,
                    is_custom, pdf_path, page_num, manual_font_size, x, y, width, height
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, migrated_rows
            )
            cursor.executemany(
                """
                INSERT OR IGNORE INTO highlights (id, doc_id, page_num, rect_coords, text_content, note_content, color)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """, migrated_highlights
            )

        cursor.execute("DROP TABLE nodes")
        cursor.execute("ALTER TABLE nodes_v2 RENAME TO nodes")

    def _migrate_workspace_id_to_integer(self, cursor):
        cursor.execute("PRAGMA table_info(nodes)")
        cols_info = {col[1]: col[2].upper() for col in cursor.fetchall()}
        if cols_info.get("workspace_id") == "INTEGER":
            return
            
        cursor.execute('''CREATE TABLE IF NOT EXISTS nodes_wsid (
            id TEXT PRIMARY KEY, highlight_id TEXT, workspace_id INTEGER DEFAULT 1,
            quote TEXT, note_text TEXT, color TEXT, is_custom INTEGER,
            pdf_path TEXT, page_num INTEGER, manual_font_size INTEGER,
            x REAL, y REAL, width REAL, height REAL,
            node_origin TEXT DEFAULT 'human', is_verified INTEGER DEFAULT 0,
            original_text TEXT, embedding_vector TEXT, node_type_id TEXT
        )''')
        node_origin_expr = "node_origin" if "node_origin" in cols_info else "'human'"
        is_verified_expr = "is_verified" if "is_verified" in cols_info else "0"
        original_text_expr = "original_text" if "original_text" in cols_info else "note_text"
        embedding_expr = "embedding_vector" if "embedding_vector" in cols_info else "NULL"
        node_type_expr = "node_type_id" if "node_type_id" in cols_info else "CASE WHEN (quote IS NOT NULL AND quote != '') OR highlight_id IS NOT NULL OR pdf_path IS NOT NULL THEN 'workspace.node.quote' ELSE 'workspace.node.text' END"
        cursor.execute("""
            INSERT OR IGNORE INTO nodes_wsid (
                id, highlight_id, workspace_id, quote, note_text, color, is_custom,
                pdf_path, page_num, manual_font_size, x, y, width, height,
                node_origin, is_verified, original_text, embedding_vector, node_type_id
            )
            SELECT id, highlight_id,
                   CASE WHEN workspace_id IS NULL OR workspace_id = 'default' THEN 1
                        ELSE CAST(workspace_id AS INTEGER) END,
                   quote, note_text, color, is_custom,
                   pdf_path, page_num, manual_font_size, x, y, width, height,
                   {node_origin_expr}, {is_verified_expr}, {original_text_expr}, {embedding_expr}, {node_type_expr}
            FROM nodes
        """.format(
            node_origin_expr=node_origin_expr,
            is_verified_expr=is_verified_expr,
            original_text_expr=original_text_expr,
            embedding_expr=embedding_expr,
            node_type_expr=node_type_expr,
        ))
        cursor.execute("DROP TABLE nodes")
        cursor.execute("ALTER TABLE nodes_wsid RENAME TO nodes")
