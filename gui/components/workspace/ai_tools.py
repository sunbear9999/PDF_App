from gui.components.workspace.ai.organize import WorkspaceAIOrganizeHandler
from gui.components.workspace.ai.connections import WorkspaceAIFindConnectionsHandler
from gui.components.workspace.ai.outline import WorkspaceAIOutlineHandler
from gui.components.workspace.ai.weakpoints import WorkspaceAIWeakpointsHandler
from gui.components.workspace.ai.evidence import WorkspaceAIEvidenceHandler


class WorkspaceAITools:
    """
    AI actions/handlers for WorkspaceView.

    Kept as a small facade that delegates to focused handlers.
    """

    def __init__(self, view):
        self.view = view
        self.organize_handler = WorkspaceAIOrganizeHandler(view)
        self.connections_handler = WorkspaceAIFindConnectionsHandler(view)
        self.outline_handler = WorkspaceAIOutlineHandler(view)
        self.weakpoints_handler = WorkspaceAIWeakpointsHandler(view)
        self.evidence_handler = WorkspaceAIEvidenceHandler(view)

    def trigger_ai_organize(self, selected_nodes):
        return self.organize_handler.trigger_ai_organize(selected_nodes)

    def _on_ai_organize_finished(self, clusters, error_msg):
        return self.organize_handler._on_ai_organize_finished(clusters, error_msg)

    def trigger_find_connections(self):
        return self.connections_handler.trigger_find_connections()

    def _on_find_connections_finished(self, new_connections, error_msg):
        return self.connections_handler._on_find_connections_finished(new_connections, error_msg)

    def trigger_generate_outline(self):
        return self.outline_handler.trigger_generate_outline()

    def _on_generate_outline_finished(self, outline_text, error_msg):
        return self.outline_handler._on_generate_outline_finished(outline_text, error_msg)

    def trigger_identify_weakpoints(self):
        return self.weakpoints_handler.trigger_identify_weakpoints()

    def _on_identify_weakpoints_finished(self, analysis_text, error_msg):
        return self.weakpoints_handler._on_identify_weakpoints_finished(analysis_text, error_msg)

    def trigger_fill_graph(self):
        return self.evidence_handler.trigger_fill_graph()

    def _update_loading_label(self, text):
        return self.evidence_handler.update_loading_label(text)

    def _on_fill_graph_finished(self, evidence_items, error_msg):
        return self.evidence_handler._on_fill_graph_finished(evidence_items, error_msg)

    def trigger_consolidate_notes(self):
        return self.evidence_handler.trigger_consolidate_notes()

    def _on_consolidate_finished(self, result_dict, error_msg):
        return self.evidence_handler._on_consolidate_finished(result_dict, error_msg)

