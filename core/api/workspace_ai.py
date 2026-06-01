# core/api/workspace_ai.py
import uuid
from core.models.workspace_models import WorkspaceModel, NodeModel, EdgeModel
from core.utils.text_utils import extract_and_heal_json

class WorkspaceAIApi:
    def __init__(self, project_manager):
        self.pm = project_manager
        self.ai_to_real_id = {}

    def build_ai_context(self, workspace: WorkspaceModel, filters: list = None) -> str:
        """
        Converts the rich WorkspaceModel into a sparse, token-efficient JSON string for the LLM.
        Translates long UUIDs into short IDs (n1, n2) to save context space.
        """
        if filters is None: 
            filters = ["text", "layout", "color", "edges"]

        self.ai_to_real_id = {}
        data = {"nodes": [], "edges": []}
        
        for idx, n in enumerate(workspace.nodes):
            short_id = f"n{idx+1}" 
            self.ai_to_real_id[short_id] = n.id
            
            node_data = {"id": short_id}
            if "text" in filters or "all" in filters:
                node_data["text"] = f"{n.quote} {n.note}".strip()
            if "layout" in filters or "all" in filters:
                node_data["x"], node_data["y"] = int(n.x), int(n.y)
            if "doc_meta" in filters or "all" in filters:
                if n.pdf_path:
                    node_data["doc_name"] = n.pdf_path.split('/')[-1] # Simple basename
                    node_data["page"] = n.page_num
                else:
                    node_data["type"] = "user_concept"
            if "color" in filters or "all" in filters:
                node_data["color"] = n.color
                
            data["nodes"].append(node_data)

        if "edges" in filters or "all" in filters:
            node_ids = {n.id for n in workspace.nodes}
            for e in workspace.edges:
                if e.source in node_ids and e.target in node_ids:
                    src_short = next((k for k, v in self.ai_to_real_id.items() if v == e.source), None)
                    dst_short = next((k for k, v in self.ai_to_real_id.items() if v == e.target), None)
                    if src_short and dst_short:
                        data["edges"].append({"source": src_short, "target": dst_short, "label": e.label})
                
        import json
        return json.dumps(data, indent=2)

    def process_ai_response(self, raw_ai_text: str, current_workspace_id: int) -> tuple[bool, WorkspaceModel | str]:
        """
        Parses LLM output, maps short IDs back to real IDs, and generates a Delta WorkspaceModel.
        """
        success, parsed_data = extract_and_heal_json(raw_ai_text)
        if not success:
            return False, parsed_data # Returns the error message

        delta = WorkspaceModel(workspace_id=current_workspace_id)
        
        # 1. Process Nodes
        for n_data in parsed_data.get("nodes", []):
            short_id = str(n_data.get("id", ""))
            
            # --- THE FIX: Map the AI's short ID to the new real UUID ---
            real_id = self.ai_to_real_id.get(short_id)
            if not real_id:
                real_id = str(uuid.uuid4())
                if short_id: 
                    self.ai_to_real_id[short_id] = real_id  # Save it so edges can find it!

            node = NodeModel(
                id=real_id,
                quote=n_data.get("quote", ""),
                note=n_data.get("note", n_data.get("text", "")),
                color=n_data.get("color", "#4a148c"),
                is_custom=not bool(n_data.get("doc_name")),
                x=n_data.get("x", 0.0), 
                y=n_data.get("y", 0.0),
                width=220, height=100,
                node_origin="ai",
                pdf_path=n_data.get("doc_name"),
                is_verified=0
            )
            delta.nodes.append(node)

        # 2. Process Edges
        for e_data in parsed_data.get("edges", []):
            src_short = str(e_data.get("source", ""))
            tgt_short = str(e_data.get("target", ""))
            
            real_src = self.ai_to_real_id.get(src_short, src_short)
            real_tgt = self.ai_to_real_id.get(tgt_short, tgt_short)
            
            if real_src and real_tgt:
                edge = EdgeModel(
                    id=str(uuid.uuid4()), source=real_src, target=real_tgt,
                    label=e_data.get("label", ""), color="#9c27b0", weight=3
                )
                delta.edges.append(edge)

        # 3. Process Deletions
        for short_id in parsed_data.get("delete_nodes", []):
            real_id = self.ai_to_real_id.get(short_id, short_id) 
            if real_id:
                delta.deleted_node_ids.append(real_id)
            
        for short_id in parsed_data.get("delete_edges", []):
            real_id = self.ai_to_real_id.get(short_id)
            if real_id: delta.deleted_edge_ids.append(real_id)

        return True, delta