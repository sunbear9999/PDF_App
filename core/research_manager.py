import fitz
import re
import math
import os
import urllib.parse

class ResearchManager:
    def __init__(self, llm_manager, project_manager):
        self.llm = llm_manager
        self.pm = project_manager
        self.custom_url_template = "https://en.wikipedia.org/wiki/Special:Search?search={term}"

    # --- URL Formatting (From old model.py) ---
    def _format_boolean_query(self, term):
        term = re.sub(r'\b(and)\b', 'AND', term, flags=re.IGNORECASE)
        term = re.sub(r'\b(or)\b', 'OR', term, flags=re.IGNORECASE)
        term = re.sub(r'\b(not)\b', 'NOT', term, flags=re.IGNORECASE)
        return urllib.parse.quote_plus(term)

    def get_jstor_url(self, term):
        clean_term = re.sub(r'\b(and|or|not)\b', lambda m: m.group(1).upper(), term, flags=re.IGNORECASE)
        tokens = re.split(r'\s+(AND|OR|NOT)\s+', clean_term)
        if len(tokens) == 1 or "(" in term or ")" in term:
            return f"https://www.jstor.org/action/doBasicSearch?Query={urllib.parse.quote_plus(clean_term)}"
        
        base_url = "https://www.jstor.org/action/doAdvancedSearch?acc=on&so=rel"
        params, q_idx, c_idx = [], 0, 1
        for token in tokens:
            token = token.strip()
            if not token: continue
            if token in ["AND", "OR", "NOT"]:
                params.append(f"c{c_idx}={token}")
                c_idx += 1
            else:
                params.append(f"q{q_idx}={urllib.parse.quote_plus(token)}")
                params.append(f"f{q_idx}=all")
                q_idx += 1
        return base_url + "&" + "&".join(params) if params else f"https://www.jstor.org/action/doBasicSearch?Query={urllib.parse.quote_plus(clean_term)}"

    def get_scholar_url(self, term):
        return f"https://scholar.google.com/scholar?q={self._format_boolean_query(term)}"

    def get_google_url(self, term):
        return f"https://www.google.com/search?q={self._format_boolean_query(term)}"

    # --- Core Logic (From old controller.py) ---
    def extract_and_rank_citations(self, goal, allowed_docs):
        """Pure math extraction. No LLM hallucination possible."""
        citation_pattern = re.compile(r'(\([A-Za-z][^\)]+?,\s*\d{4}[a-z]?\)|\[\d+(?:,\s*\d+)*\])')
        all_citations = []

        for doc_name in allowed_docs:
            doc_path = next((p for p in self.pm.pdfs if doc_name in os.path.basename(p)), None)
            if not doc_path: continue
            try:
                doc = fitz.open(doc_path)
                for page_num in range(len(doc)):
                    text = doc.load_page(page_num).get_text("text").replace('\n', ' ')
                    sentences = re.split(r'(?<=[.!?]) +', text)
                    for i, sentence in enumerate(sentences):
                        if citation_pattern.search(sentence) and len(sentence) > 20:
                            context_text = (sentences[i-1].strip() + " " if i > 0 else "") + sentence.strip()
                            all_citations.append({"doc": doc_name, "text": context_text})
                doc.close()
            except Exception as e:
                print(f"Extraction error: {e}")

        if not all_citations: return []

        try:
            goal_emb = self.llm.get_embedding(f"search_query: {goal}")
            texts_to_embed = [f"search_document: {c['text']}" for c in all_citations]
            cit_embs = self.llm.get_batch_embeddings(texts_to_embed)

            def cosine_sim(v1, v2):
                norm1, norm2 = math.sqrt(sum(a*a for a in v1)), math.sqrt(sum(b*b for b in v2))
                return sum(a*b for a, b in zip(v1, v2)) / (norm1 * norm2) if norm1 and norm2 else 0

            valid_citations = []
            for i, c in enumerate(all_citations):
                score = cosine_sim(goal_emb, cit_embs[i])
                if score > 0.50: 
                    c["score"] = score
                    valid_citations.append(c)

            valid_citations.sort(key=lambda x: x["score"], reverse=True)
            
            seen_texts, unique_citations = set(), []
            for c in valid_citations:
                if c["text"] not in seen_texts:
                    seen_texts.add(c["text"])
                    unique_citations.append(c)
                    if len(unique_citations) == 5: break
            return unique_citations
        except Exception: return []

    def generate_search_terms(self, goal, model, stream_callback):
        """Standard AI Keyword Generation"""
        prompt = (
            f"You are an expert academic research assistant. The user's research goal is: '{goal}'\n\n"
            "INSTRUCTIONS:\n"
            "1. Generate 3 to 5 highly specific, advanced academic search queries.\n"
            "2. Format each search term strictly on a new single line exactly like this:\n"
            "%%TERM | exact boolean search phrase | A brief sentence explaining why this specific query is effective.\n"
        )
        self.llm.query(
            question=prompt,
            selected_model=model,
            rag_enabled=False,
            use_agents=False,
            custom_system_prompt="You are a strict output generator. Follow format rules exactly.",
            callback=stream_callback
        )