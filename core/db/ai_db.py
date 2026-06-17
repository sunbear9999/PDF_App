# core/db/ai_db.py
import json
import sqlite3
import uuid
from core.db.base_db import BaseDB
from core.models.prompt_models import PromptTraceRecord

class AIDB(BaseDB):
    def save_chat_message(self, tab_name, role, content, ui_format="live_stream", trace_id=None, ui_payload=None):
        if not self._conn: return
        try:
            cursor = self._conn.cursor()
            cursor.execute("PRAGMA table_info(chat_messages)")
            columns = [col[1] for col in cursor.fetchall()]
            if "ui_payload" in columns:
                self._conn.execute(
                    "INSERT INTO chat_messages (tab_name, role, content, ui_format, trace_id, ui_payload) VALUES (?, ?, ?, ?, ?, ?)",
                    (tab_name, role, content, ui_format, trace_id, ui_payload)
                )
            elif "trace_id" in columns:
                self._conn.execute(
                    "INSERT INTO chat_messages (tab_name, role, content, ui_format, trace_id) VALUES (?, ?, ?, ?, ?)",
                    (tab_name, role, content, ui_format, trace_id)
                )
            else:
                self._conn.execute(
                    "INSERT INTO chat_messages (tab_name, role, content, ui_format) VALUES (?, ?, ?, ?)",
                    (tab_name, role, content, ui_format)
                )
            self._conn.commit()
        except sqlite3.Error as e:
            print(f"Error saving chat message: {e}")

    def get_chat_history(self, tab_name):
        if not self._conn: return []
        try:
            cursor = self._conn.cursor()
            cursor.execute("PRAGMA table_info(chat_messages)")
            columns = [col[1] for col in cursor.fetchall()]
            has_trace = "trace_id" in columns
            has_payload = "ui_payload" in columns
            select_cols = ["role", "content", "ui_format"]
            if has_trace:
                select_cols.append("trace_id")
            if has_payload:
                select_cols.append("ui_payload")
            cursor.execute(
                f"SELECT {', '.join(select_cols)} FROM chat_messages WHERE tab_name = ? ORDER BY timestamp ASC, id ASC",
                (tab_name,),
            )
            history = []
            for row in cursor.fetchall():
                item = {
                    "role": row[0],
                    "content": row[1],
                    "ui_format": row[2],
                    "trace_id": None,
                    "ui_payload": None,
                }
                offset = 3
                if has_trace:
                    item["trace_id"] = row[offset]
                    offset += 1
                if has_payload:
                    item["ui_payload"] = row[offset]
                history.append(item)
            return history
        except sqlite3.Error as e:
            print(f"Error fetching chat history: {e}")
            return []

    def clear_chat_history(self, tab_name):
        if not self._conn: return
        try:
            self._conn.execute(
                "DELETE FROM chat_messages WHERE tab_name = ?", (tab_name,)
            )
            self._conn.commit()
        except sqlite3.Error as e:
            print(f"Error clearing chat history: {e}")

    def log_ai_interaction(self, prompt, response, model):
        if not self._conn: return
        try:
            self._conn.execute("INSERT INTO ai_audit_log (prompt, response, model_used) VALUES (?, ?, ?)", (prompt, response, model))
            self._conn.commit()
        except sqlite3.Error as e:
            print(f"Failed to log AI interaction: {e}")

    def log_ai_interaction_threadsafe(self, prompt, response, model):
        if not self.manager.project_filepath: return
        try:
            conn = sqlite3.connect(self.manager.project_filepath, timeout=10.0)
            conn.execute("INSERT INTO ai_audit_log (prompt, response, model_used) VALUES (?, ?, ?)", (prompt, response, model))
            conn.commit()
            conn.close()
        except sqlite3.Error as e:
            print(f"Failed to log AI interaction in background: {e}")

    def save_prompt_trace(self, trace_record):
        if not self._conn:
            return None
        try:
            if isinstance(trace_record, dict):
                record = PromptTraceRecord.from_dict(trace_record)
            else:
                record = trace_record
            if not record.calls:
                return None
            if not record.trace_id:
                record.trace_id = str(uuid.uuid4())
            data = record.as_dict()
            self._conn.execute(
                """
                INSERT OR REPLACE INTO ai_prompt_traces
                (trace_id, job_id, blueprint_id, blueprint_name, target_id, created_at, trace_json)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    record.trace_id,
                    record.job_id,
                    record.blueprint_id,
                    record.blueprint_name,
                    record.target_id,
                    record.created_at,
                    json.dumps(data),
                ),
            )
            self._conn.commit()
            return record.trace_id
        except sqlite3.Error as e:
            print(f"Failed to save prompt trace: {e}")
            return None

    def get_prompt_trace(self, trace_id):
        if not self._conn or not trace_id:
            return None
        try:
            cursor = self._conn.cursor()
            cursor.execute("SELECT trace_json FROM ai_prompt_traces WHERE trace_id = ?", (trace_id,))
            row = cursor.fetchone()
            if not row:
                return None
            return json.loads(row[0])
        except (sqlite3.Error, json.JSONDecodeError) as e:
            print(f"Failed to fetch prompt trace: {e}")
            return None

    def get_prompt_trace_for_job(self, job_id):
        if not self._conn or not job_id:
            return None
        try:
            cursor = self._conn.cursor()
            cursor.execute("SELECT trace_json FROM ai_prompt_traces WHERE job_id = ? ORDER BY created_at DESC LIMIT 1", (job_id,))
            row = cursor.fetchone()
            if not row:
                return None
            return json.loads(row[0])
        except (sqlite3.Error, json.JSONDecodeError) as e:
            print(f"Failed to fetch prompt trace for job: {e}")
            return None

    def generate_llm_log(self, export_path):
        if not self._conn: return False
        try:
            cursor = self._conn.cursor()
            
            cursor.execute("""
                SELECT COUNT(*) FROM nodes 
                WHERE node_origin = 'human' AND id NOT LIKE '%AINote|%' AND (highlight_id IS NULL OR highlight_id NOT LIKE '%AINote|%')
            """)
            n_h = cursor.fetchone()[0]
            
            cursor.execute("""
                SELECT COUNT(*) FROM nodes 
                WHERE (node_origin = 'ai' OR id LIKE '%AINote|%' OR highlight_id LIKE '%AINote|%') AND is_verified = 1
            """)
            n_v = cursor.fetchone()[0]
            
            cursor.execute("""
                SELECT COUNT(*) FROM nodes 
                WHERE (node_origin = 'ai' OR id LIKE '%AINote|%' OR highlight_id LIKE '%AINote|%') AND is_verified = 0
            """)
            n_ai_unverified = cursor.fetchone()[0]
            
            total_ai_notes = n_v + n_ai_unverified
            verification_score = (n_v / total_ai_notes * 100) if total_ai_notes > 0 else 100.0
            denominator = max(1, n_ai_unverified) 
            integrity_ratio = (n_h + n_v) / denominator
            
            cursor.execute("SELECT timestamp, prompt, response, model_used FROM ai_audit_log ORDER BY timestamp ASC")
            logs = cursor.fetchall()
            
            with open(export_path, 'w', encoding='utf-8') as f:
                f.write(f"# 🛡️ LLM Usage Log\n")
                f.write(f"**Project:** {self.manager.project_name}\n\n")
                f.write(f"## 📊 Cognitive Agency Dashboard\n")
                f.write(f"* **Independent Human Notes ($N_h$):** {n_h}\n")
                f.write(f"* **Human-Verified AI Notes ($N_v$):** {n_v}\n")
                f.write(f"* **Unverified AI Notes ($N_{{ai\\_unverified}}$):** {n_ai_unverified}\n")
                f.write(f"---\n")
                f.write(f"### **Human-to-AI Ratio:** `{integrity_ratio:.2f}`\n")
                f.write(f"### **Verification Score:** `{verification_score:.1f}%`\n")
                f.write(f"---\n\n")
                f.write(f"## 📜 Verified Interaction Log\n")
                f.write(f"*This section contains the immutable, raw prompt-response pairs processed by local hardware.*\n\n")
                
                for idx, (timestamp, prompt, response, model) in enumerate(logs, 1):
                    f.write(f"### Interaction #{idx} ({timestamp})\n")
                    f.write(f"**Model:** `{model}` (Local Inference)\n\n")
                    f.write(f"**Prompt:**\n> {prompt.replace(chr(10), chr(10) + '> ')}\n\n")
                    f.write(f"**Raw AI Response:**\n```text\n{response}\n```\n")
                    f.write(f"---\n\n")
                    
            return True
        except Exception as e:
            print(f"Error generating llm log: {e}")
            return False
