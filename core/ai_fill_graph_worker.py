import json
from core.base_ai_worker import BaseAIWorker
from core.prompts import Prompts


class AIFillGraphWorker(BaseAIWorker):
    """[REFACTOR] Fill graph with evidence using BaseAIWorker.
    
    [AI OPTIMIZATION] Features:
    - Temperature=0.0 for deterministic claim extraction
    - Few-shot examples from centralized prompts
    - JSON mode enforcement for structured output
    - Multi-phase evidence mining pipeline
    """

    def __init__(self, llm_manager, model, nodes_data, edges_data, allowed_docs, parent=None):
        super().__init__()
        self.llm_manager = llm_manager
        self.model = model
        self.nodes_data = nodes_data
        self.edges_data = edges_data
        self.allowed_docs = allowed_docs

    def execute_task(self):
        """[REFACTOR] Execute evidence filling task with optimized settings."""
        if not self.nodes_data:
            raise ValueError("No nodes available to analyze.")

        self.emit_progress("✨ AI is analyzing your argument structure...")

        # =================================================================
        # PHASE 1: Logical Analysis & Claim Extraction
        # =================================================================
        # [REFACTOR] Use centralized prompt with few-shot examples
        agent1_system = Prompts.get_system_prompt('fill_graph_claims')

        agent1_prompt = (
            f"Nodes Data:\n{json.dumps(self.nodes_data, indent=2)}\n\n"
            f"Connections:\n{json.dumps(self.edges_data, indent=2)}\n\n"
            "Identify claims needing evidence and generate multiple concise search queries per claim. Return JSON ONLY."
        )

        structure_result = ""
        def handle_chunk1(chunk):
            nonlocal structure_result
            structure_result += chunk

        # [AI OPTIMIZATION] Query with temperature=0.0 for deterministic extraction
        self.llm_manager.query(
            agent1_prompt,
            self.model,
            allowed_docs=[],
            callback=handle_chunk1,
            rag_enabled=False,
            use_agents=False,
            custom_system_prompt=agent1_system,
            temperature=0.0  # [AI OPTIMIZATION] Deterministic claim extraction
        )

        # [REFACTOR] Error checking
        if "[Generation Error" in structure_result or "[System Error" in structure_result:
            raise Exception(f"AI Analysis Failed:\n{structure_result.strip()}")

        # [REFACTOR] Use inherited JSON parsing utility
        cleaned_result = BaseAIWorker.clean_and_parse_json(structure_result.strip())
        claims_to_support = self.safe_parse_json(cleaned_result, default=[], json_mode=True)

        if not claims_to_support:
            raise ValueError("AI could not identify any claims needing support in the selected nodes.")

        # =================================================================
        # PHASE 2: Evidence Mining (Deep Manual RAG Pipeline)
        # =================================================================
        if not self.llm_manager.vector_store.is_ready():
            raise ValueError("Please build the search index first in the LLM Chat tab.")

        evidence_items = []

        for i, claim_item in enumerate(claims_to_support):
            node_id = claim_item.get("node_id")
            claim_text = claim_item.get("claim")

            # Support both old and new JSON schema
            search_queries = claim_item.get("search_queries", [])
            if not search_queries and claim_item.get("search_query"):
                search_queries = [claim_item.get("search_query")]

            if not node_id or not search_queries:
                continue

            # Shorten for UI display
            display_claim = claim_text[:35] + "..." if len(claim_text) > 35 else claim_text
            self.emit_progress(f"🔍 Searching documents for evidence ({i+1}/{len(claims_to_support)}):\n'{display_claim}'")

            try:
                # 1. PURE SEMANTIC VECTOR SEARCH ACROSS MULTIPLE QUERIES
                aggregated_docs = {}
                docs_to_search = self.allowed_docs if self.allowed_docs else [None]

                for sq in search_queries:
                    sq_emb = self.llm_manager.get_embedding(sq)

                    for doc in docs_to_search:
                        doc_where = {"doc_name": doc} if doc else None
                        results = self.llm_manager.vector_store.search(sq_emb, doc_where, n_results=5)
                        if results.get('documents') and results['documents'][0]:
                            for idx, doc_text in enumerate(results['documents'][0]):
                                doc_id_val = results['ids'][0][idx]
                                meta = results['metadatas'][0][idx]
                                if doc_id_val not in aggregated_docs:
                                    aggregated_docs[doc_id_val] = {
                                        "text": doc_text,
                                        "doc_name": meta['doc_name'],
                                        "page": meta['page']
                                    }

                if not aggregated_docs:
                    continue  # No context found in DB, skip to next claim

                sorted_docs = sorted(aggregated_docs.values(), key=lambda x: (x['doc_name'], x['page']))
                context_pieces = [f"--- DOCUMENT: {d['doc_name']} | PAGE {d['page'] + 1} ---\n{d['text']}" for d in sorted_docs]
                context_str = "\n\n".join(context_pieces)

                # 2. STRICT QUOTE EXTRACTION
                system_prompt = (
                    "You are an expert AI research assistant. Your task is to find concrete textual evidence to support a specific claim.\n"
                    "Read the provided CONTEXT documents thoroughly. Extract 2 to 5 highly relevant, VERBATIM quotes that strongly prove the claim.\n"
                    "CRITICAL RULES:\n"
                    "1. Quotes MUST be exactly copy-pasted from the text. Do not paraphrase.\n"
                    "2. Try to find evidence from MULTIPLE different documents if the context supports it.\n"
                    "3. Output ONLY using the following format on a single line for each quote:\n"
                    "%%QUOTE | Document_Name.pdf | Exact verbatim quote text here | Brief explanation of how it supports the claim\n"
                    "4. Do not include introductory text, headers, or a final answer block. Output ONLY the %%QUOTE lines."
                )

                prompt = f"TARGET CLAIM TO SUPPORT: '{claim_text}'\n\nCONTEXT:\n{context_str}"

                rag_result = ""
                def handle_chunk2(chunk):
                    nonlocal rag_result
                    rag_result += chunk

                # Execute the query using the deep context we just built
                self.llm_manager.query(
                    prompt,
                    self.model,
                    allowed_docs=[],
                    callback=handle_chunk2,
                    rag_enabled=False,  # Disabled because we manually retrieved the context above!
                    use_agents=False,
                    custom_system_prompt=system_prompt
                )

                if "[Generation Error" in rag_result or "[System Error" in rag_result:
                    continue

                # Parse results robustly
                for line in rag_result.split('\n'):
                    line = line.strip()
                    if '%%QUOTE' in line.upper():
                        parts = line.split('|')
                        if len(parts) >= 4:
                            doc_name = parts[1].strip()
                            raw_quote = parts[2].strip()
                            note = '|'.join(parts[3:]).strip()

                            raw_quote = raw_quote.strip('"\'')
                            note = note.rstrip('%%').strip()

                            if "|" in doc_name:
                                doc_name = doc_name.split("|")[0].strip()

                            evidence_items.append({
                                "target_node_id": node_id,
                                "doc": doc_name,
                                "quote": raw_quote,
                                "note": note
                            })

            except Exception as e:
                print(f"Error processing claim '{claim_text}': {e}")
                continue

        return evidence_items