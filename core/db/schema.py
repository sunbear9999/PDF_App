# core/db/schema.py
import sqlite3
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
                rect_coords TEXT, text_content TEXT, color TEXT
            )''')
            
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
                    migrated_highlights.append((highlight_id, pdf_path, page_num, None, quote, color))

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
                INSERT OR IGNORE INTO highlights (id, doc_id, page_num, rect_coords, text_content, color)
                VALUES (?, ?, ?, ?, ?, ?)
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
            x REAL, y REAL, width REAL, height REAL
        )''')
        cursor.execute("""
            INSERT OR IGNORE INTO nodes_wsid
            SELECT id, highlight_id,
                   CASE WHEN workspace_id IS NULL OR workspace_id = 'default' THEN 1
                        ELSE CAST(workspace_id AS INTEGER) END,
                   quote, note_text, color, is_custom,
                   pdf_path, page_num, manual_font_size, x, y, width, height
            FROM nodes
        """)
        cursor.execute("DROP TABLE nodes")
        cursor.execute("ALTER TABLE nodes_wsid RENAME TO nodes")