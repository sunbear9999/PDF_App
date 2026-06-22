"""
plugins/locallaws/law_manager.py
"""
import os
import chromadb
from chromadb.config import Settings
import uuid
import pandas as pd

class LocalLawManager:
    def __init__(self, api):
        self.api = api
        self.plugin_dir = os.path.dirname(os.path.abspath(__file__))
        self.download_dir = os.path.join(self.plugin_dir, "dbs", "downloads")
        os.makedirs(self.download_dir, exist_ok=True)
        
        self.db_path = os.path.join(self.plugin_dir, "dbs")
        self.client = chromadb.PersistentClient(
            path=self.db_path, 
            settings=Settings(anonymized_telemetry=False)
        )

    def is_available(self):
        return self.client is not None

    def get_installed_dbs(self):
        """Scans the downloads folder and returns a list of installed databases with file sizes."""
        dbs = []
        if not os.path.exists(self.download_dir): return dbs

        for file in os.listdir(self.download_dir):
            if file.endswith(".parquet"):
                path = os.path.join(self.download_dir, file)
                size_mb = os.path.getsize(path) / (1024 * 1024)
                
                name_parts = file.replace(".parquet", "").split("_")
                if len(name_parts) == 2:
                    city, state = name_parts[0], name_parts[1]
                    dbs.append({
                        "city": city.title(),
                        "state": state.upper(),
                        "size_mb": round(size_mb, 2),
                        "file_id": f"{city.title()}_{state.upper()}"
                    })
        return dbs

    def remove_db(self, city: str, state: str):
        """Deletes both the Parquet file and the associated vectors from Chroma."""
        # 1. Remove from ChromaDB
        try:
            col = self.client.get_collection(name="us_local_laws")
            col.delete(where={"$and": [{"city": city.title()}, {"state": state.upper()}]})
        except Exception:
            pass
            
        # 2. Delete Parquet file
        file_path = os.path.join(self.download_dir, f"{city.lower()}_{state.lower()}.parquet")
        if os.path.exists(file_path):
            os.remove(file_path)

    def query_laws(self, embedding_vector, n_results, active_dbs: list):
        """Queries only the jurisdictions the user has toggled ON."""
        try:
            collection = self.client.get_collection(name="us_local_laws")
            if not collection or collection.count() == 0: return None
            
            # Build the granular WHERE clause
            where_conditions = []
            for db_id in active_dbs:
                city, state = db_id.split("_")
                where_conditions.append({"$and": [{"city": city}, {"state": state}]})
                
            if not where_conditions:
                return None
            elif len(where_conditions) == 1:
                where_clause = where_conditions[0]
            else:
                where_clause = {"$or": where_conditions}
                
            return collection.query(
                query_embeddings=[embedding_vector],
                n_results=n_results,
                where=where_clause
            )
        except Exception as e:
            print(f"[Local Laws] Search Error: {e}")
            return None

    def merge_chroma_results(self, res_a, res_b, max_results):
        if not res_a and not res_b: return None
        if not res_a: return res_b
        if not res_b: return res_a

        combined = []
        if res_a.get("ids") and res_a["ids"][0]:
            for i in range(len(res_a["ids"][0])):
                combined.append({"id": res_a["ids"][0][i], "distance": res_a["distances"][0][i], "metadata": res_a["metadatas"][0][i], "document": res_a["documents"][0][i]})
        if res_b.get("ids") and res_b["ids"][0]:
            for i in range(len(res_b["ids"][0])):
                combined.append({"id": res_b["ids"][0][i], "distance": res_b["distances"][0][i], "metadata": res_b["metadatas"][0][i], "document": res_b["documents"][0][i]})

        combined.sort(key=lambda x: x["distance"])
        top = combined[:max_results]

        return {
            "ids": [[i["id"] for i in top]],
            "distances": [[i["distance"] for i in top]],
            "metadatas": [[i["metadata"] for i in top]],
            "documents": [[i["document"] for i in top]]
        }

    def index_real_jurisdiction(self, state: str, city: str):
        import duckdb
        search_state = state.lower().strip()
        search_city = city.lower().strip()

        try:
            local_cache_file = os.path.join(self.download_dir, f"{search_city}_{search_state}.parquet")

            if os.path.exists(local_cache_file) and os.path.getsize(local_cache_file) < 10000:
                os.remove(local_cache_file)
                self.api.notify("Cleared empty cache file.", level="info")

            if os.path.exists(local_cache_file):
                self.api.notify(f"Loading cached dataset for {city.title()}...", level="info")
                df = pd.read_parquet(local_cache_file)
            else:
                self.api.notify(f"Scanning LOCUS for {search_city}, {search_state}... (Takes 3-5 mins)", level="info")
                
                # THE THREAD FIX: Using the DuckDB context manager guarantees the network socket closes
                with duckdb.connect() as con:
                    con.execute("INSTALL httpfs; LOAD httpfs; SET http_timeout=600;")
                    
                    schema_df = con.execute("DESCRIBE SELECT * FROM read_parquet('hf://datasets/LocalLaws/LOCUS-v1/**/*.parquet') LIMIT 0").df()
                    cols = [c.lower() for c in schema_df['column_name'].tolist()]
                    
                    state_col = "state" if "state" in cols else cols[0]
                    city_col = next((c for c in ["city", "locality", "municipality", "jurisdiction"] if c in cols), None)

                    query = f"""
                        COPY (
                            SELECT * FROM read_parquet('hf://datasets/LocalLaws/LOCUS-v1/**/*.parquet')
                            WHERE LOWER({state_col}) = '{search_state}' AND LOWER({city_col}) LIKE '%{search_city}%'
                        ) TO '{local_cache_file}' (FORMAT PARQUET);
                    """
                    con.execute(query)
                
                self.api.notify("Download complete!", level="success")
                df = pd.read_parquet(local_cache_file)

            total_records = len(df)
            if df.empty:
                if os.path.exists(local_cache_file): os.remove(local_cache_file)
                return {"success": False, "error": f"No laws found for '{city.title()}', '{state.upper()}'."}

            self.api.notify(f"Indexing {total_records} laws...", level="info")
            collection = self.client.get_or_create_collection(name="us_local_laws")
            
            batch_size = 25
            for i in range(0, total_records, batch_size):
                if i > 0: self.api.notify(f"Indexing progress: {int((i / total_records) * 100)}% ({i}/{total_records})", level="info", duration=2000)
                
                batch = df.iloc[i:i+batch_size]
                batch_texts, metadatas, ids = [], [], []

                for _, row in batch.iterrows():
                    title = row.get('title', 'N/A')
                    chapter = row.get('chapter', 'N/A')
                    section = row.get('section', 'N/A')
                    
                    # CITATION FORMATTING: This ensures the AI bubble explicitly states the section reference
                    code_ref = f"Title {title}, Chap {chapter}, Sec {section}"
                    
                    txt_val = next((row[c] for c in row.index if 'text' in str(c).lower() or 'content' in str(c).lower()), None)
                    clean_text = str(txt_val).strip() if txt_val else "No text found."
                    if len(clean_text) > 4000: clean_text = clean_text[:4000] + "... [Truncated]"
                    
                    full_text = f"[{code_ref}] {clean_text}"
                    
                    batch_texts.append(full_text)
                    metadatas.append({
                        "doc_name": f"{city.title()} Municipal Code: {code_ref}", 
                        "state": search_state.upper(),
                        "city": city.title(),
                        "section": str(section),
                    })
                    ids.append(f"law_{uuid.uuid4().hex[:8]}")

                batch_embeddings = []
                for text in batch_texts:
                    emb = self.api.llm.get_embedding(text)
                    if not emb: return {"success": False, "error": "Ollama returned empty embedding."}
                    batch_embeddings.append(emb)
                
                collection.upsert(documents=batch_texts, embeddings=batch_embeddings, metadatas=metadatas, ids=ids)
                
            return {"success": True, "city": city}
        except Exception as e:
            return {"success": False, "error": str(e)}