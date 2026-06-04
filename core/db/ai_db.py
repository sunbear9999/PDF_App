# core/db/ai_db.py
import sqlite3
from core.db.base_db import BaseDB

class AIDB(BaseDB):
    def save_chat_message(self, tab_name, role, content, ui_format="live_stream"):
        if not self._conn: return
        try:
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
            cursor.execute("SELECT role, content, ui_format FROM chat_messages WHERE tab_name = ? ORDER BY timestamp ASC", (tab_name,))
            return [{"role": row[0], "content": row[1], "ui_format": row[2]} for row in cursor.fetchall()]
        except sqlite3.Error as e:
            print(f"Error fetching chat history: {e}")
            return []

    def clear_chat_history(self, tab_name):
        if not self._conn: return
        try:
            self._conn.execute("DELETE FROM chat_messages WHERE tab_name = ?", (tab_name,))
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