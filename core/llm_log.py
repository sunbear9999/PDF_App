import sqlite3
import difflib
from datetime import datetime
import os
import sys
import tempfile
import subprocess

class LlmLogGenerator:
    def __init__(self, db_path, project_name):
        self.db_path = db_path
        self.project_name = project_name

    def generate_pdf(self, export_path):
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()

        try:
            # 1. Fetch Node Data with Original Text
            cursor.execute("""
                SELECT id, node_origin, is_verified, note_text, quote, original_text 
                FROM nodes ORDER BY node_origin DESC
            """)
            nodes = cursor.fetchall()

            # 2. Fetch Audit Logs
            cursor.execute("""
                SELECT timestamp, prompt, response, model_used 
                FROM ai_audit_log ORDER BY timestamp ASC
            """)
            logs = cursor.fetchall()

            # 3. Process Nodes & Calculate Edit Distances
            processed_nodes = []
            n_human = 0
            n_ai_verified = 0
            n_ai_unverified = 0
            total_edit_pct = 0.0

            for n in nodes:
                n_id, origin, verified, note_text, quote, orig_text = n
                change_pct = 0.0
                
                if origin == 'human':
                    n_human += 1
                else:
                    # Calculate how much the user edited the AI's note
                    if orig_text and note_text:
                        sim = difflib.SequenceMatcher(None, str(orig_text).strip(), str(note_text).strip()).ratio()
                        change_pct = (1.0 - sim) * 100.0
                    
                    if verified:
                        n_ai_verified += 1
                    else:
                        n_ai_unverified += 1
                        
                    total_edit_pct += change_pct

                processed_nodes.append({
                    "id": n_id, "origin": origin, "verified": verified, 
                    "text": note_text, "change_pct": change_pct
                })

            total_nodes = len(nodes)
            n_ai_total = n_ai_verified + n_ai_unverified
            
            # The New Metric: Cognitive Agency Score
            # (Human Notes + Verified AI) / Total Notes
            agency_score = ((n_human + n_ai_verified) / total_nodes * 100) if total_nodes > 0 else 100.0
            
            human_pct = (n_human / total_nodes * 100) if total_nodes > 0 else 0
            ai_pct = (n_ai_total / total_nodes * 100) if total_nodes > 0 else 0
            avg_edit = (total_edit_pct / n_ai_total) if n_ai_total > 0 else 0

            # 4. Group Logs into Sessions (120s rolling window)
            sessions = []
            current_session = None

            for row in logs:
                dt_str, prompt, response, model = row
                dt = datetime.strptime(dt_str.split(".")[0], "%Y-%m-%d %H:%M:%S")

                if current_session is None:
                    current_session = {"start_time": dt, "last_time": dt, "model": model, "prompts": [(prompt, response)]}
                else:
                    time_diff = (dt - current_session["last_time"]).total_seconds()
                    if time_diff <= 120:  
                        current_session["prompts"].append((prompt, response))
                        current_session["last_time"] = dt 
                    else:
                        sessions.append(current_session)
                        current_session = {"start_time": dt, "last_time": dt, "model": model, "prompts": [(prompt, response)]}
            if current_session:
                sessions.append(current_session)

            # 5. Build the Clean HTML Dashboard
            html = f"""
            <html>
            <head>
                <style>
                    body {{ font-family: 'Helvetica Neue', Helvetica, Arial, sans-serif; color: #222; font-size: 11px; }}
                    h1 {{ color: #2c3e50; border-bottom: 2px solid #3498db; padding-bottom: 8px; font-size: 18px; margin-bottom: 5px; }}
                    h2 {{ color: #2980b9; margin-top: 20px; border-bottom: 1px solid #eee; padding-bottom: 4px; font-size: 14px; }}
                    
                    .dashboard {{ width: 100%; margin-top: 15px; border-collapse: collapse; }}
                    .dash-box {{ background-color: #f8f9fa; border: 1px solid #e9ecef; padding: 15px; text-align: center; width: 33%; }}
                    .dash-val-main {{ font-size: 32px; font-weight: bold; color: #2c3e50; }}
                    .dash-val-sub {{ font-size: 18px; font-weight: bold; color: #7f8c8d; }}
                    .dash-label {{ font-size: 10px; color: #95a5a6; text-transform: uppercase; font-weight: bold; letter-spacing: 1px; }}
                    .text-success {{ color: #27ae60; }}
                    
                    .node-table {{ width: 100%; border-collapse: collapse; margin-top: 10px; font-size: 10px; }}
                    .node-table th {{ background-color: #ecf0f1; border: 1px solid #bdc3c7; padding: 6px; text-align: left; }}
                    .node-table td {{ border: 1px solid #ecf0f1; padding: 6px; vertical-align: top; }}
                    
                    .tag-human {{ color: #2980b9; font-weight: bold; }}
                    .tag-ai-v {{ color: #27ae60; font-weight: bold; }}
                    .tag-ai-u {{ color: #c0392b; font-weight: bold; }}
                    .tag-edit {{ color: #8e44ad; font-size: 9px; }}

                    .session-box {{ margin-bottom: 20px; page-break-inside: avoid; }}
                    .session-header {{ background-color: #34495e; color: white; padding: 6px 10px; font-weight: bold; font-size: 11px; }}
                    .prompt-box {{ border: 1px solid #bdc3c7; border-top: none; padding: 10px; }}
                    .user-intent {{ background-color: #f1f8ff; padding: 8px; border-left: 3px solid #3498db; margin-bottom: 8px; font-weight: bold; }}
                    .agent-intent {{ background-color: #fffde7; padding: 6px; border-left: 3px solid #f1c40f; margin-bottom: 8px; color: #555; font-size: 9px; }}
                    .ai-out {{ background-color: #fafafa; padding: 8px; border: 1px solid #eee; white-space: pre-wrap; font-family: monospace; font-size: 9px; }}
                </style>
            </head>
            <body>
                <h1>Papyrus LLM Usage Log</h1>
                <p><strong>Project:</strong> {self.project_name} | <strong>Generated:</strong> {datetime.now().strftime("%B %d, %Y at %I:%M %p")}</p>

                <table class="dashboard">
                    <tr>
                        <td class="dash-box">
                            <div class="dash-val-main text-success">{agency_score:.1f}%</div>
                            <div class="dash-label">Cognitive Agency Score</div>
                        </td>
                        <td class="dash-box">
                            <div class="dash-val-sub">{human_pct:.1f}% / {ai_pct:.1f}%</div>
                            <div class="dash-label">Human vs AI Composition</div>
                        </td>
                        <td class="dash-box">
                            <div class="dash-val-sub">{avg_edit:.1f}%</div>
                            <div class="dash-label">Avg. Manual Edit Extent</div>
                        </td>
                    </tr>
                </table>
                
                <div style="text-align: center; font-size: 10px; color: #7f8c8d; margin-top: 8px; margin-bottom: 25px;">
                    <em>* <strong>Cognitive Agency Score Formula:</strong> (Human Authored Nodes + Human-Verified AI Nodes) &divide; Total Nodes</em>
                </div>

                <h2>1. Workspace Node Pedigree</h2>
                <table class="node-table">
                    <tr>
                        <th width="15%">Node ID</th>
                        <th width="20%">Pedigree Status</th>
                        <th width="65%">Content Snippet</th>
                    </tr>
            """

            for n in processed_nodes:
                short_id = n['id'][-8:] if n['id'] else "Unknown"
                snippet = (n['text'][:120] + '...') if n['text'] and len(n['text']) > 120 else (n['text'] or "No text")
                
                if n['origin'] == 'human':
                    status = "<span class='tag-human'>👤 Human Authored</span>"
                else:
                    if n['verified']:
                        status = "<span class='tag-ai-v'>🛡️ AI (Verified)</span>"
                    else:
                        status = "<span class='tag-ai-u'>⚠️ AI (Unverified)</span>"
                    
                    if n['change_pct'] > 1.0:
                        status += f"<br><span class='tag-edit'>✎ {n['change_pct']:.0f}% Modified</span>"

                html += f"""
                    <tr>
                        <td>...{short_id}</td>
                        <td>{status}</td>
                        <td><i>"{snippet}"</i></td>
                    </tr>
                """

            html += """
                </table>
                <br>
                <h2>2. AI Interaction Audit Log</h2>
            """

            for idx, session in enumerate(sessions, 1):
                html += f"""
                <div class="session-box">
                    <div class="session-header">Interaction #{idx} &nbsp;|&nbsp; {session['start_time'].strftime("%H:%M:%S")} &nbsp;|&nbsp; Model: {session['model']}</div>
                    <div class="prompt-box">
                """
                
                main_prompt, main_response = session['prompts'][0]
                html += f"<div class='user-intent'>USER PROMPT:<br><span style='font-weight:normal;'>{main_prompt}</span></div>"

                for agent_prompt, agent_resp in session['prompts'][1:]:
                    trunc_prompt = agent_prompt[:150] + " ... [Context Omitted for Brevity]"
                    html += f"<div class='agent-intent'>AGENT BACKGROUND TASK:<br><span style='font-weight:normal;'>{trunc_prompt}</span></div>"

                final_response = session['prompts'][-1][1]
                html += f"<div class='ai-out'><strong>FINAL AI OUTPUT:</strong><br>{final_response}</div>"
                html += "</div></div>"

            html += "</body></html>"

            with tempfile.NamedTemporaryFile(delete=False, suffix=".html", mode='w', encoding='utf-8') as f:
                f.write(html)
                temp_html_path = f.name
                
            try:
                # 2. Locate our isolated worker script
                worker_script = os.path.join(os.path.dirname(__file__), "pdf_worker.py")
                
                # 3. Execute the worker in the background using the same Python environment
                # sys.executable ensures it uses your (venv)
                result = subprocess.run(
                    [sys.executable, worker_script, temp_html_path, export_path], 
                    capture_output=True, 
                    text=True
                )
                
                if result.returncode != 0:
                    print(f"PDF Worker Error: {result.stderr}")
                    return False
                    
                return True
                
            except Exception as e:
                print(f"Subprocess Execution Error: {e}")
                return False
                
            finally:
                # 4. Clean up the temporary HTML file silently
                if os.path.exists(temp_html_path):
                    os.remove(temp_html_path)

        except Exception as e:
            print(f"Error gathering LLM Log Data: {e}")
            return False
        finally:
            conn.close()

        