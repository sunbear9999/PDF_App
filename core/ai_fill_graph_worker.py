import json
import re
from PyQt6.QtCore import QThread, pyqtSignal
import os

class AIFillGraphWorker(QThread):
    finished = pyqtSignal(list, str) 
    progress = pyqtSignal(str) 

    def __init__(self, llm_manager, model, nodes_data, edges_data, allowed_docs, enforce_tags=False, node_tags_map=None, parent=None):
        super().__init__(parent)
        self.llm_manager = llm_manager
        self.model = model
        self.nodes_data = nodes_data
        self.edges_data = edges_data
        self.allowed_docs = allowed_docs
        self.enforce_tags = enforce_tags
        self.node_tags_map = node_tags_map or {}

    def run(self):
        if not self.nodes_data:
            self.finished.emit([], "No nodes available to analyze.")
            return

        try:
            self.progress.emit("✨ AI is analyzing your argument structure...")
            
            agent1_system = self.llm_manager.get_system_prompt(
                "AI Fill Graph Worker - Claim Finder",
                (
                    "You are an expert logical analyst. Review the provided graph of notes. "
                    "Identify which user-created nodes represent 'claims' or 'reasons' that could use concrete textual evidence from the documents to support them. "
                    "For each such claim, generate 3 to 5 highly specific search queries (2-6 words each, keywords only) to capture different ways the text might discuss this topic. "
                    "Return ONLY a valid JSON array of objects. "
                    "Format: [{{\"node_id\": \"id1\", \"claim\": \"The user's claim\", \"search_queries\": [\"keyword phrase one\", \"keyword phrase two\"]}}]"
                ),
            )
            
            agent1_prompt = (
                f"Nodes Data:\n{json.dumps(self.nodes_data, indent=2)}\n\n"
                f"Connections:\n{json.dumps(self.edges_data, indent=2)}\n\n"
                "Identify claims needing evidence and generate multiple concise search queries per claim. Return JSON ONLY."
            )

            structure_result = ""
            def handle_chunk1(chunk):
                nonlocal structure_result
                structure_result += chunk

            self.llm_manager.query(
                agent1_prompt,
                self.model,
                allowed_docs=[],
                callback=handle_chunk1,
                rag_enabled=False,
                use_agents=False,
                custom_system_prompt=agent1_system
            )

            if "[Generation Error" in structure_result or "[System Error" in structure_result:
                self.finished.emit([], f"AI Analysis Failed:\n{structure_result.strip()}")
                return

            cleaned_result = structure_result.strip()
            
            if "```json" in cleaned_result.lower():
                cleaned_result = cleaned_result.split("```json", 1)[-1].split("```")[0].strip()
            elif "```" in cleaned_result:
                cleaned_result = cleaned_result.split("```", 1)[-1].split("```")[0].strip()

            match = re.search(r'\[.*\]', cleaned_result, re.DOTALL)
            if match:
                cleaned_result = match.group(0)

            try:
                claims_to_support = json.loads(cleaned_result)
            except json.JSONDecodeError:
                self.finished.emit([], f"Failed to parse AI claims. The model may have hallucinated.\nResponse: {structure_result}")
                return
            
            if not claims_to_support:
                self.finished.emit([], "AI could not identify any claims needing support in the selected nodes.")
                return

            if not self.llm_manager.collection:
                self.finished.emit([], "Please build the search index first in the LLM Chat tab.")
                return

            evidence_items = []
            
            for i, claim_item in enumerate(claims_to_support):
                node_id = claim_item.get("node_id")
                claim_text = claim_item.get("claim")
                
                search_queries = claim_item.get("search_queries", [])
                if not search_queries and claim_item.get("search_query"):
                    search_queries = [claim_item.get("search_query")]
                    
                if isinstance(search_queries, str):
                    search_queries = [search_queries]
                
                if not node_id or not search_queries: continue
                
                display_claim = claim_text[:35] + "..." if len(claim_text) > 35 else claim_text
                self.progress.emit(f"🔍 Searching database for:\n'{display_claim}'")
                
                try:
                    aggregated_docs = {}
                    docs_to_search = self.allowed_docs if self.allowed_docs else [None]
                    
                    # 1. RETRIEVAL PHASE: Grab chunks AND their relevance scores (distances)
                    for sq in search_queries:
                        sq_emb = self.llm_manager.get_embedding(sq)
                        
                        for doc in docs_to_search:
                            doc_where = {"doc_name": os.path.basename(doc)} if doc else None
                            
                            final_where = doc_where
                            if self.enforce_tags and node_id in self.node_tags_map:
                                node_tags = self.node_tags_map[node_id]
                                tag_conditions = [{f"tag_{t}": True} for t in node_tags]
                                
                                tag_where = None
                                if len(tag_conditions) == 1:
                                    tag_where = tag_conditions[0]
                                elif len(tag_conditions) > 1:
                                    tag_where = {"$or": tag_conditions} 
                                
                                if tag_where and doc_where:
                                    final_where = {"$and": [doc_where, tag_where]}
                                elif tag_where:
                                    final_where = tag_where

                            # Ask ChromaDB for distances to rank the results
                            results = self.llm_manager.collection.query(
                                query_embeddings=[sq_emb],
                                n_results=5, 
                                where=final_where,
                                include=["documents", "metadatas", "distances"]
                            )
                            
                            if results.get('documents') and results['documents'][0]:
                                for idx, doc_text in enumerate(results['documents'][0]):
                                    doc_id_val = results['ids'][0][idx]
                                    meta = results['metadatas'][0][idx]
                                    dist = results['distances'][0][idx]
                                    
                                    # Prevent duplicate chunks from multiple queries
                                    if doc_id_val not in aggregated_docs:
                                        aggregated_docs[doc_id_val] = {
                                            "text": doc_text,
                                            "doc_name": meta['doc_name'],
                                            "page": meta['page'],
                                            "distance": dist
                                        }
                    
                    if not aggregated_docs:
                        continue 
                        
                    # 2. SEMANTIC FILTERING: Sort by relevance instead of batching
                    # Lower distance is better in ChromaDB. Sort to get the most relevant chunks globally.
                    best_chunks = sorted(aggregated_docs.values(), key=lambda x: x['distance'])
                    
                    # Keep ONLY the top 10 most relevant chunks to keep the context window tight and lightning fast
                    top_chunks = best_chunks[:10]
                    
                    # Sort those top 10 chunks back into reading order so the text makes sense to the LLM
                    reading_order_chunks = sorted(top_chunks, key=lambda x: (x['doc_name'], x['page']))
                    
                    context_pieces = [f"--- DOCUMENT: {d['doc_name']} | PAGE {d['page'] + 1} ---\n{d['text']}" for d in reading_order_chunks]
                    context_str = "\n\n".join(context_pieces)
                    
                    available_docs_list = list(set([d['doc_name'] for d in reading_order_chunks]))
                    valid_docs_str = ", ".join(available_docs_list)
                    
                    # 3. EXTRACTION PHASE: ONE single fast LLM call per node
                    system_prompt = self.llm_manager.get_system_prompt(
                        "AI Fill Graph Worker - Evidence Extractor",
                        (
                            "You are an expert AI research assistant. Your task is to find concrete textual evidence to support a specific claim.\n"
                            "Read the provided CONTEXT excerpts thoroughly. Extract 1 to 3 highly relevant, VERBATIM quotes that strongly prove the claim.\n"
                            "CRITICAL RULES:\n"
                            "1. Quotes MUST be EXACTLY copy-pasted from the text. Do not paraphrase, fix typos, change punctuation, or use ellipses (...).\n"
                            "2. Keep quotes short (10 to 30 words maximum) to ensure they can be located in the UI.\n"
                            f"3. The ONLY valid document names you can use are: {valid_docs_str}\n"
                            "4. You MUST structure your response in two parts. First, write a brief '--- REASONING ---' section where you think about the claim and identify relevant parts of the text. Second, write a '--- QUOTES ---' section.\n"
                            "5. If these specific excerpts do NOT contain strong evidence for the claim, DO NOT FORCE IT. Skip it entirely.\n"
                            "6. In the Quotes section, you MUST format each quote on its own line EXACTLY like this:\n"
                            "%%QUOTE | DocumentName.pdf | The exact verbatim text goes here | A brief explanation\n"
                        ),
                        valid_docs_str=valid_docs_str
                    )

                    prompt = f"TARGET CLAIM TO SUPPORT: '{claim_text}'\n\nCONTEXT:\n{context_str}"
                    
                    rag_result = ""
                    def handle_chunk2(chunk):
                        nonlocal rag_result
                        rag_result += chunk
                        
                    self.llm_manager.query(
                        prompt,
                        self.model,
                        allowed_docs=[],
                        callback=handle_chunk2,
                        rag_enabled=False, 
                        use_agents=False,
                        custom_system_prompt=system_prompt
                    )
                    
                    if "[Generation Error" in rag_result or "[System Error" in rag_result:
                        continue
                    
                    for line in rag_result.split('\n'):
                        line = line.strip()
                        if 'QUOTE' in line.upper() and '|' in line:
                            line = line.replace('*', '').replace('%%', '%')
                            parts = line.split('|')
                            if len(parts) >= 4:
                                doc_name = parts[1].strip()
                                raw_quote = parts[2].strip()
                                note = '|'.join(parts[3:]).strip() 
                                
                                raw_quote = raw_quote.strip(' "\'“”‘’')
                                note = re.sub(r'%%$', '', note).strip()
                                
                                if doc_name and "|" in doc_name:
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
            
            self.finished.emit(evidence_items, "")

        except Exception as e:
            self.finished.emit([], f"An unexpected error occurred: {str(e)}")