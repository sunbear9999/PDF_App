from __future__ import annotations

from typing import Dict, Iterable, Optional

from core.engine.workflow_model import WorkflowNodeType


class BlueprintNodeTypeRegistry:
    """Registry for workflow execution node types (LLM_QUERY, RAG_SEARCH, etc.).

    Named 'Blueprint' to distinguish from WorkspaceNodeTypeRegistry, which
    governs node types on the user's research canvas.
    """

    def __init__(self):
        self._types: Dict[str, WorkflowNodeType] = {}

    def register(self, node_type: WorkflowNodeType):
        self._types[node_type.id] = node_type

    def get(self, type_id: str) -> Optional[WorkflowNodeType]:
        return self._types.get(type_id)

    def get_by_step_type(self, step_type: str) -> Optional[WorkflowNodeType]:
        """Look up a WorkflowNodeType by its step_type string (e.g. 'LLM_QUERY').

        Used by MasterActionRunner._dispatch_step() to resolve the step_cls for
        registry-driven dispatch without needing to know the node's registry id.
        """
        for node_type in self._types.values():
            if node_type.step_type == step_type:
                return node_type
        return None

    def all(self) -> Iterable[WorkflowNodeType]:
        return self._types.values()

    def iter_category(self, category: str) -> Iterable[WorkflowNodeType]:
        for node_type in self._types.values():
            if node_type.category == category:
                yield node_type

    def remove_by_plugin(self, plugin_id: str) -> None:
        self._types = {k: v for k, v in self._types.items()
                       if getattr(v, "plugin_id", None) != plugin_id}


def build_default_blueprint_node_type_registry() -> BlueprintNodeTypeRegistry:
    # Import step classes here (not at module level) to avoid circular imports at
    # papyrus_core boot time.  Registry construction is lazy and only happens once.
    from core.engine.steps.llm_query_step import LLMQueryStep
    from core.engine.steps.rag_search_step import RagSearchStep
    from core.engine.steps.python_script_step import PythonScriptStep
    from core.engine.steps.user_input_step import UserInputStep
    from core.engine.steps.database_write_step import DatabaseWriteStep
    from core.engine.steps.graph_validator_step import GraphValidatorStep
    from core.engine.steps.analysis_contract_step import AnalysisContractStep
    from core.engine.steps.document_chunk_step import DocumentChunkStep
    from core.engine.steps.analysis_compact_step import AnalysisCompactStep
    from core.engine.steps.analysis_finalize_step import AnalysisFinalizeStep
    from core.engine.steps.analysis_send_to_workspace_step import AnalysisSendToWorkspaceStep
    from core.engine.steps.evidence_store_step import EvidenceStoreStep
    from core.engine.steps.section_synthesis_step import SectionGroupStep
    from core.engine.steps.quote_hydration_step import QuoteHydrationStep
    from core.engine.steps.ontology_upsert_step import OntologyUpsertStep
    from core.engine.steps.await_event_step import AwaitEventStep
    from core.engine.steps.dispatch_event_step import DispatchEventStep
    from core.engine.steps.llm_schema_query_step import LLMSchemaQueryStep
    from core.engine.steps.gather_registry_context_step import GatherRegistryContextStep
    from core.engine.steps.show_item_selector_step import ShowItemSelectorStep
    from core.engine.steps.workspace_write_step import WorkspaceWriteStep
    from core.engine.steps.read_document_step import ReadDocumentStep
    from core.engine.steps.query_database_step import QueryDatabaseStep
    from core.engine.steps.notes_read_step import NotesReadStep
    from core.engine.steps.source_statistics_step import SourceStatisticsStep
    from core.engine.steps.ontology_catalog_step import OntologyCatalogStep
    from core.engine.steps.extract_pdf_grid_step import ExtractPdfGridStep
    from core.engine.steps.select_pdf_region_step import SelectPdfRegionStep

    registry = BlueprintNodeTypeRegistry()
    defaults = [
        WorkflowNodeType(
            "workflow.select_pdf_region", "Select PDF Region", "Interaction", "SELECT_PDF_REGION",
            SelectPdfRegionStep.description, step_cls=SelectPdfRegionStep,
            input_schema=SelectPdfRegionStep.input_schema, output_schema=SelectPdfRegionStep.output_schema,
        ),
        WorkflowNodeType(
            "workflow.extract_pdf_grid", "Extract PDF Grid", "Data", "EXTRACT_PDF_GRID",
            ExtractPdfGridStep.description, step_cls=ExtractPdfGridStep,
            input_schema=ExtractPdfGridStep.input_schema, output_schema=ExtractPdfGridStep.output_schema,
        ),
        WorkflowNodeType(
            "workflow.llm_query", "LLM Query", "AI", "LLM_QUERY",
            "Send a query to the AI model and stream a text response. Combine with Prompt Key "
            "to use a saved system prompt, or write the system prompt directly in the inspector.",
            default_ui_format="live_stream", step_cls=LLMQueryStep,
            input_schema={
                "query": {
                    "type": "textarea",
                    "label": "Query / User Message",
                    "description": "The main input passed to the model. Use {state_key} to inject "
                                   "values from previous steps. Example: Summarize the following:\n\n{rag_context}",
                    "required": True,
                },
                "images": {
                    "type": "json",
                    "label": "Image Inputs",
                    "description": "Optional image objects containing base64 data and mime_type. Requires a vision-capable local model.",
                    "placeholder": "{selection_images}",
                },
            },
        ),
        WorkflowNodeType(
            "workflow.rag_search", "RAG Search", "Search", "RAG_SEARCH",
            "Search indexed project documents and return relevant chunks as context. "
            "Use n_results to control breadth. Tag filters restrict to specific document sets.",
            step_cls=RagSearchStep,
            input_schema={
                "queries": {
                    "type": "textarea",
                    "label": "Search Queries",
                    "description": "Search string(s) to run. Enter one query per line, or use a "
                                   "{state_key} that holds a JSON list. Example: What are the main claims?",
                    "required": True,
                    "placeholder": "Enter search query or {state_key}",
                },
                "n_results": {
                    "type": "integer",
                    "label": "Results Per Query",
                    "description": "How many document chunks to return per query. Default: 5. "
                                   "Increase to 8–10 for broader coverage.",
                    "default": 5,
                    "placeholder": "5",
                },
                "tag_filters": {
                    "type": "text",
                    "label": "Tag Filters (comma-separated)",
                    "description": "Restrict results to documents with these tags. Leave blank to search all docs. "
                                   "Example: primary_source, literature_review",
                    "placeholder": "e.g. primary_source, key_reading",
                },
                "tag_logic": {
                    "type": "choice",
                    "label": "Tag Logic",
                    "description": "AND = document must have ALL listed tags. OR = document must have at least one.",
                    "choices": ["AND", "OR"],
                    "default": "AND",
                },
            },
        ),
        WorkflowNodeType(
            "workflow.source_statistics", "Source Statistics", "Context", "SOURCE_STATISTICS",
            "Collect reusable source-level facts for adaptive workflows. Metrics are selected "
            "at runtime and can be extended without changing blueprint logic.",
            step_cls=SourceStatisticsStep,
            input_schema=SourceStatisticsStep.input_schema,
            output_schema=SourceStatisticsStep.output_schema,
        ),
        WorkflowNodeType(
            "workflow.ontology_catalog", "Ontology Catalog", "Context", "ONTOLOGY_CATALOG",
            "Load registered entity and relation categories only when a workflow needs them.",
            step_cls=OntologyCatalogStep,
            input_schema=OntologyCatalogStep.input_schema,
            output_schema=OntologyCatalogStep.output_schema,
        ),
        # FOREACH and BRANCH are flow-control primitives handled natively by the runner.
        WorkflowNodeType(
            "workflow.foreach", "For Each", "Control", "FOREACH",
            "Loop over every item in a list stored in the workflow state. Each iteration "
            "runs the connected child steps with {item} holding the current element.",
            input_schema={
                "query": {
                    "type": "text",
                    "label": "List State Key",
                    "description": "The workflow state key that holds the list to iterate over. "
                                   "Each element is available as {item} inside the loop body. "
                                   "Example: claims_list  (from a previous PYTHON_SCRIPT that parsed JSON)",
                    "required": True,
                    "placeholder": "e.g. claims_list",
                },
            },
        ),
        WorkflowNodeType(
            "workflow.branch", "Branch", "Control", "BRANCH",
            "Route execution down a true or false path based on a condition. "
            "Connect True steps via the 'true' port and False steps via 'false'.",
            output_ports=["true", "false"],
            input_schema={
                "query": {
                    "type": "textarea",
                    "label": "Condition Expression",
                    "description": "Python-style boolean expression evaluated at runtime. "
                                   "Use {state_key} to reference values from previous steps. "
                                   "Examples:\n  {confidence} > 0.8\n  {status} == 'approved'\n  len({items}) > 0",
                    "required": True,
                    "placeholder": "e.g. {confidence_score} > 0.8",
                },
            },
        ),
        WorkflowNodeType(
            "workflow.python_script", "Python Script", "Transform", "PYTHON_SCRIPT",
            "Run a sandboxed Python snippet to transform or inspect state data. "
            "Access state via the 'state' dict. Set result = your_value to return output.",
            step_cls=PythonScriptStep,
            input_schema={
                "script": {
                    "type": "code",
                    "label": "Python Script",
                    "description": "Sandboxed Python code. Available globals:\n"
                                   "  state  — dict of all workflow state (read-only snapshot)\n"
                                   "  result — set this to return a value\n"
                                   "  workflow_api.emit_event(type, payload) — fire an event\n\n"
                                   "Example:\nimport json\ndata = json.loads(state.get('raw_json', '{}'))\nresult = data.get('items', [])",
                    "required": True,
                    "placeholder": "import json\n\n# Access previous step output:\nraw = state.get('my_output', '{}')\nresult = json.loads(raw)",
                },
            },
        ),
        WorkflowNodeType(
            "workflow.user_input", "User Input", "Interaction", "USER_INPUT",
            "Pause the workflow and show a prompt to the user. "
            "The user types a response which becomes the step output.",
            step_cls=UserInputStep,
            input_schema={
                "query": {
                    "type": "textarea",
                    "label": "Prompt / Question",
                    "description": "The message shown to the user. They type a response which becomes "
                                   "this step's output. Use {state_key} to include dynamic context. "
                                   "Example: Please review the following claims and type 'approve' or 'reject': {claims_list}",
                    "placeholder": "e.g. Please review the summary above and type any corrections:",
                },
            },
        ),
        WorkflowNodeType(
            "workflow.database_write", "Database Write", "Persistence", "DATABASE_WRITE",
            "Write a row directly to the project SQLite database. "
            "Useful for saving custom output that isn't a workspace node or note.",
            step_cls=DatabaseWriteStep,
            input_schema={
                "table": {
                    "type": "db_table",
                    "label": "Table Name",
                    "description": "Which project SQLite table to write to. "
                                   "Common tables: annotations, notes, tags, documents. "
                                   "The dropdown lists tables in the current open project.",
                    "required": True,
                    "placeholder": "e.g. annotations",
                },
                "payload": {
                    "type": "json",
                    "label": "Row Data",
                    "description": "JSON object mapping column names to values. "
                                   "Use {state_key} for dynamic values from previous steps. "
                                   "Only columns that exist in the table will be written.\n\n"
                                   "Example:\n{\"content\": \"{summary_text}\", \"doc_path\": \"{source_path}\"}",
                    "required": True,
                    "placeholder": '{"column_name": "value or {state_key}"}',
                },
            },
        ),
        WorkflowNodeType(
            "workflow.library_ref", "Reusable Step", "Library", "LIBRARY_REF",
            "Run a saved step configuration from the Step Library. "
            "Create reusable steps by configuring a step and clicking 'Save as Reusable Step'.",
            input_schema={
                "step_ref": {
                    "type": "text",
                    "label": "Saved Step Name",
                    "description": "The name you gave the step when saving it to the library. "
                                   "Reusable steps are listed in the sidebar and can be exported via Pack Manager.",
                    "required": True,
                    "placeholder": "e.g. core_extract_citations",
                },
            },
        ),
        WorkflowNodeType(
            "workflow.graph_validator", "Graph Validator", "Transform", "GRAPH_VALIDATOR",
            "Validate and auto-heal a JSON graph payload against the ontology schema. "
            "Removes unknown entity types and fixes malformed relation keys.",
            step_cls=GraphValidatorStep,
        ),
        WorkflowNodeType(
            "workflow.analysis_contract", "Analysis Contract", "Analysis", "ANALYSIS_CONTRACT",
            "Start a multi-chunk document analysis. Defines the expected output structure "
            "for subsequent Document Chunk → Analysis Compact → Analysis Finalize steps.",
            step_cls=AnalysisContractStep,
        ),
        WorkflowNodeType(
            "workflow.document_chunk", "Document Chunk", "Analysis", "DOCUMENT_CHUNK",
            "Split a document into analysis chunks and run the extraction prompt on each. "
            "Chain after Analysis Contract.",
            step_cls=DocumentChunkStep,
        ),
        WorkflowNodeType(
            "workflow.analysis_compact", "Analysis Compact", "Analysis", "ANALYSIS_COMPACT",
            "Merge partial chunk results into running summaries that fit the synthesis window. "
            "Chain after Document Chunk.",
            step_cls=AnalysisCompactStep,
        ),
        WorkflowNodeType(
            "workflow.analysis_finalize", "Analysis Finalize", "Analysis", "ANALYSIS_FINALIZE",
            "Run the synthesis and master passes, emit the completed analysis to the workspace. "
            "Final step in the analysis chain.",
            step_cls=AnalysisFinalizeStep,
        ),
        WorkflowNodeType(
            "workflow.evidence_store", "Evidence Store", "Analysis", "EVIDENCE_STORE",
            EvidenceStoreStep.description,
            step_cls=EvidenceStoreStep,
            input_schema=EvidenceStoreStep.input_schema,
        ),
        WorkflowNodeType(
            "workflow.section_group", "Section Group", "Analysis", "SECTION_GROUP",
            SectionGroupStep.description,
            step_cls=SectionGroupStep,
            input_schema=SectionGroupStep.input_schema,
        ),
        WorkflowNodeType(
            "workflow.quote_hydration", "Quote Hydration", "Analysis", "QUOTE_HYDRATION",
            QuoteHydrationStep.description,
            step_cls=QuoteHydrationStep,
            input_schema=QuoteHydrationStep.input_schema,
        ),
        WorkflowNodeType(
            "workflow.analysis_send_to_workspace", "Send Analysis to Workspace", "Analysis",
            "ANALYSIS_SEND_TO_WORKSPACE",
            "Push a completed analysis result (entities + relations) into the workspace graph.",
            step_cls=AnalysisSendToWorkspaceStep,
        ),
        WorkflowNodeType(
            "workflow.ontology_upsert", "Ontology Upsert", "Persistence", "ONTOLOGY_UPSERT",
            "Validate LLM-extracted entities and relations against the project ontology "
            "and persist them to the GraphDB. Input must be a JSON object with 'entities' and 'relations' lists.",
            step_cls=OntologyUpsertStep,
            input_schema={
                "data": {
                    "type": "json",
                    "label": "Graph Payload",
                    "description": 'JSON object with "entities" and "relations" arrays. '
                                   "Use {state_key} to reference output from a previous LLM step.\n\n"
                                   'Example: {my_llm_output}  (where the LLM produced {\"entities\":[...], \"relations\":[...]})',
                    "required": True,
                    "placeholder": '{"entities": [...], "relations": [...]}',
                },
                "workspace_id": {
                    "type": "integer",
                    "label": "Workspace ID (optional)",
                    "description": "Target workspace ID. Leave blank to use the currently active workspace.",
                    "placeholder": "leave blank for active workspace",
                },
            },
        ),
        WorkflowNodeType(
            "workflow.await_event", "Await Event", "Interaction", "AWAIT_EVENT",
            "Pause the workflow until a specific EventBus signal fires, then continue "
            "with the signal payload as output. Use timeout_ms to give up after N milliseconds.",
            step_cls=AwaitEventStep,
            input_schema={
                "signal_name": {
                    "type": "event_signal",
                    "label": "EventBus Signal to Wait For",
                    "description": "The signal this step listens for. When it fires, "
                                   "the step resumes and the payload is returned as output.\n"
                                   "Pick from the dropdown to see available signals.",
                    "required": True,
                },
                "timeout_ms": {
                    "type": "integer",
                    "label": "Timeout (ms)",
                    "description": "How long to wait before giving up. Default: 30000 (30 seconds). "
                                   "If the timeout expires, output will contain {\"timed_out\": true}.",
                    "default": 30000,
                    "placeholder": "30000",
                },
            },
        ),
        WorkflowNodeType(
            "workflow.dispatch_event", "Dispatch Event", "Interaction", "DISPATCH_EVENT",
            "Fire an EventBus signal from within a workflow step. "
            "Use this to trigger workspace actions, switch tabs, open documents, etc. without user interaction.",
            step_cls=DispatchEventStep,
            input_schema={
                "signal_name": {
                    "type": "event_signal",
                    "label": "EventBus Signal",
                    "description": "Which signal to emit. Pick from the dropdown — "
                                   "the Intent dropdown below updates automatically to show valid actions.",
                    "required": True,
                },
                "intent": {
                    "type": "event_intent",
                    "label": "Intent / Action",
                    "description": "The specific action to perform within this signal. "
                                   "Options update automatically when you select a signal above. "
                                   "Hover each option for details.",
                },
                "payload": {
                    "type": "json",
                    "label": "Payload (optional)",
                    "description": "Extra data sent with the signal. Depends on signal + intent. "
                                   "Use {state_key} for dynamic values from previous steps.\n\n"
                                   "Hint: select a signal to see tooltip showing its payload fields.",
                    "placeholder": '{"key": "value or {state_key}"}',
                },
            },
        ),
        WorkflowNodeType(
            "workflow.llm_schema_query", "LLM Schema Query", "AI", "LLM_SCHEMA_QUERY",
            "Like LLM Query but forces JSON mode. Set an output_schema to enforce the exact "
            "structure the model must return. Great for claim extraction, structured data, etc.",
            step_cls=LLMSchemaQueryStep,
            input_schema={
                "query": {
                    "type": "textarea",
                    "label": "Query / User Message",
                    "description": "The main input passed to the model. Use {state_key} to inject values.",
                    "required": True,
                    "placeholder": "Extract all claims from the following:\n\n{selected_text}",
                },
            },
        ),
        WorkflowNodeType(
            "workflow.gather_registry_context", "Gather Registry Context", "Meta",
            "GATHER_REGISTRY_CONTEXT",
            "Read all registered step types (including plugin-contributed steps) and store "
            "their documentation in state['plugin_step_docs']. Inject via {plugin_step_docs} "
            "into Blueprint Architect prompts so the AI knows about available steps.",
            step_cls=GatherRegistryContextStep,
        ),
        WorkflowNodeType(
            "workflow.show_item_selector", "Show Item Selector", "Interaction",
            "SHOW_ITEM_SELECTOR",
            "Pause the workflow and show a checkable list in the GUI. The user reviews items, "
            "checks the ones they want, and clicks the action button. Selected items are returned as JSON.",
            step_cls=ShowItemSelectorStep,
            input_schema={
                "items": {
                    "type": "text",
                    "label": "Items State Key",
                    "description": "State key holding a JSON array of items to display. "
                                   "Each item can be a string or a dict with display/subtitle keys.\n"
                                   "Example: {claims_list}  (from a previous PYTHON_SCRIPT step)",
                    "required": True,
                    "placeholder": "{claims_list}",
                },
                "title": {
                    "type": "text",
                    "label": "Dialog Title",
                    "description": "Title shown at the top of the selection dialog.",
                    "placeholder": "Select items to save",
                },
                "action_label": {
                    "type": "text",
                    "label": "Action Button Label",
                    "description": "Text on the confirm button. Keep it action-oriented.",
                    "placeholder": "Save Selected",
                },
                "item_display_key": {
                    "type": "text",
                    "label": "Display Key",
                    "description": "For dict items: which key to show as the main label. "
                                   "Example: claim  (shows item['claim'] as the row text)",
                    "placeholder": "e.g. claim or title",
                },
                "item_subtitle_key": {
                    "type": "text",
                    "label": "Subtitle Key",
                    "description": "For dict items: which key to show as a secondary label below the main text. "
                                   "Example: type  (shows item['type'] in grey beneath the claim)",
                    "placeholder": "e.g. type or category",
                },
            },
        ),
        WorkflowNodeType(
            "workflow.workspace_write", "Workspace Write", "Persistence",
            "WORKSPACE_WRITE",
            "Write nodes (and optionally edges) directly into the active workspace graph without an LLM. "
            "Accepts a flat list of items or a {entities, relations} dict.",
            step_cls=WorkspaceWriteStep,
            input_schema={
                "data": {
                    "type": "text",
                    "label": "Data (state key or JSON)",
                    "description": "State key holding a JSON list of items, or a raw JSON dict with "
                                   "'entities' and 'relations' arrays. For list input, each item becomes a node.\n"
                                   "Example: {selected_claims}  (from SHOW_ITEM_SELECTOR output)",
                    "required": True,
                    "placeholder": "{selected_items}",
                },
                "node_type": {
                    "type": "text",
                    "label": "Node Type",
                    "description": "The node type assigned to each created workspace node. "
                                   "Must match a type registered in the workspace ontology. Default: claim",
                    "placeholder": "claim",
                    "default": "claim",
                },
                "source_path": {
                    "type": "text",
                    "label": "Source Document Path",
                    "description": "File path of the source document. Stored as a property on each node. "
                                   "Use {source_path} to pass in the active PDF path automatically.",
                    "placeholder": "{source_path}",
                },
                "workspace_id": {
                    "type": "integer",
                    "label": "Workspace ID (optional)",
                    "description": "Target workspace. Leave blank to use the currently active workspace.",
                    "placeholder": "leave blank for active workspace",
                },
            },
        ),
        WorkflowNodeType(
            "workflow.read_document_text", "Read Document Text", "Data",
            "READ_DOCUMENT_TEXT",
            "Fetch raw text from an indexed project document by file path. "
            "No LLM is involved — this just reads the stored text so you can pass it to a later step.",
            step_cls=ReadDocumentStep,
            input_schema={
                "doc_path": {
                    "type": "text",
                    "label": "Document Path",
                    "description": "Absolute file path of the document to read. "
                                   "Use {source_path} to read the currently active PDF, or "
                                   "hard-code a path like /home/user/docs/paper.pdf",
                    "required": True,
                    "placeholder": "{source_path}",
                },
                "max_chars": {
                    "type": "integer",
                    "label": "Max Characters",
                    "description": "Truncate the output to this many characters. Default: 20000. "
                                   "Increase for long documents; decrease to keep prompts lean.",
                    "default": 20000,
                    "placeholder": "20000",
                },
                "page": {
                    "type": "integer",
                    "label": "Page Number (optional)",
                    "description": "Zero-indexed page number. Leave blank to read all pages. "
                                   "Example: 0 = first page, 1 = second page.",
                    "placeholder": "blank = all pages",
                },
            },
        ),
        WorkflowNodeType(
            "workflow.query_database", "Query Database", "Data",
            "QUERY_DATABASE",
            "Run a SELECT query against the project SQLite database and return rows as JSON. "
            "Only SELECT is allowed — no INSERT/UPDATE/DELETE.",
            step_cls=QueryDatabaseStep,
            input_schema={
                "sql": {
                    "type": "code",
                    "label": "SQL SELECT Statement",
                    "description": "A SELECT query to run against the project database. "
                                   "Only SELECT is allowed for safety.\n\n"
                                   "Useful tables:\n"
                                   "  documents (id, path, title, added_date)\n"
                                   "  annotations (id, doc_path, highlighted_text, color)\n"
                                   "  notes (id, content, doc_path, created_at)\n"
                                   "  tags (id, name)\n\n"
                                   "Example: SELECT * FROM annotations WHERE doc_path LIKE '%{source_path}%' LIMIT 20",
                    "required": True,
                    "placeholder": "SELECT id, content FROM notes WHERE doc_path = '{source_path}' LIMIT 10",
                },
                "output_format": {
                    "type": "choice",
                    "label": "Output Format",
                    "description": "list = JSON array of all rows (default).\n"
                                   "first = single row dict (null if no results).\n"
                                   "count = integer number of matching rows.",
                    "choices": ["list", "first", "count"],
                    "default": "list",
                },
                "max_rows": {
                    "type": "integer",
                    "label": "Max Rows",
                    "description": "Maximum number of rows to return (for 'list' format). Default: 100.",
                    "default": 100,
                    "placeholder": "100",
                },
            },
        ),
        WorkflowNodeType(
            "workflow.notes_read", "Read Notes", "Data",
            "NOTES_READ",
            "Read notes from the project database. Optionally filter by tag name or source document. "
            "Returns a JSON array of note objects.",
            step_cls=NotesReadStep,
            input_schema={
                "limit": {
                    "type": "integer",
                    "label": "Max Notes",
                    "description": "Maximum number of notes to return. Default: 50.",
                    "default": 50,
                    "placeholder": "50",
                },
                "filter_tag": {
                    "type": "text",
                    "label": "Filter by Tag (optional)",
                    "description": "Return only notes that have this tag. Leave blank to include all notes.",
                    "placeholder": "e.g. key_point",
                },
                "doc_path": {
                    "type": "text",
                    "label": "Filter by Document (optional)",
                    "description": "Return only notes linked to this document path. "
                                   "Use {source_path} to filter to the active PDF.",
                    "placeholder": "{source_path} or leave blank for all docs",
                },
            },
        ),
    ]
    for node_type in defaults:
        registry.register(node_type)
    return registry
