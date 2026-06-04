# core/api/search_api.py
import os
import re
import math
import fitz

class SearchAPI:
    @staticmethod
    def extract_mathematical_citations(goal: str, allowed_docs: list, project_manager, llm_manager) -> list:
        """Headless utility to perform cosine similarity searches on pure citations."""
        citation_pattern = re.compile(r'(\([A-Za-z][^\)]+?,\s*\d{4}[a-z]?\)|\[\d+(?:,\s*\d+)*\])')
        all_citations = []
        
        for doc_name in allowed_docs:
            doc_path = next((p for p in project_manager.pdfs if doc_name in os.path.basename(p)), None)
            if not doc_path: continue
            try:
                doc = fitz.open(doc_path)
                for page_num in range(len(doc)):
                    text = doc.load_page(page_num).get_text("text").replace('\n', ' ')
                    sentences = re.split(r'(?<=[.!?]) +', text)
                    for i, sentence in enumerate(sentences):
                        if citation_pattern.search(sentence) and len(sentence) > 20:
                            context_text = ""
                            if i > 0: context_text += sentences[i-1].strip() + " "
                            context_text += sentence.strip()
                            all_citations.append({"doc": doc_name, "text": context_text})
                doc.close()
            except Exception: pass
            
        if not all_citations: return []
            
        try:
            goal_emb = llm_manager.get_embedding(f"search_query: {goal}")
            texts_to_embed = [f"search_document: {c['text']}" for c in all_citations]
            cit_embs = llm_manager.get_batch_embeddings(texts_to_embed)
            
            def cosine_sim(v1, v2):
                dot = sum(a*b for a, b in zip(v1, v2))
                n1, n2 = math.sqrt(sum(a*a for a in v1)), math.sqrt(sum(b*b for b in v2))
                return dot / (n1 * n2) if n1 and n2 else 0
                
            valid_citations = []
            for i, c in enumerate(all_citations):
                score = cosine_sim(goal_emb, cit_embs[i])
                if score > 0.50: 
                    c["score"] = score
                    valid_citations.append(c)
                    
            valid_citations.sort(key=lambda x: x["score"], reverse=True)
            
            # Deduplicate
            seen_texts = set()
            unique_citations = []
            for c in valid_citations:
                if c["text"] not in seen_texts:
                    seen_texts.add(c["text"])
                    unique_citations.append(c)
                    if len(unique_citations) == 5: break
            return unique_citations
        except Exception as e:
            print(f"[SearchAPI] Citation extraction failed: {e}")
            return []