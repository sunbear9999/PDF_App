# core/vector_store.py
# [REFACTOR] Extracted Vector Store logic - handles ChromaDB and embeddings
# Single responsibility: Vector database operations, document management, embedding batching

import chromadb
import os
import re
import fitz
import gc
from chromadb.config import Settings

class VectorStore:
    """[REFACTOR] ChromaDB wrapper for RAG document storage and retrieval.
    
    Handles:
    - ChromaDB client initialization
    - Collection management
    - Document indexing with batching
    - Vector similarity search
    - Metadata-filtered queries
    """

    def __init__(self):
        self.chroma_client = None
        self.collection = None

    def initialize(self, project_filepath):
        """[REFACTOR] Initialize vector store for a project."""
        if not project_filepath:
            self.collection = None
            return
        
        db_path = project_filepath + "_chroma_db"
        os.makedirs(db_path, exist_ok=True)
        
        self.chroma_client = chromadb.PersistentClient(
            path=db_path, 
            settings=Settings(anonymized_telemetry=False)
        )
        self.collection = self.chroma_client.get_or_create_collection(name="pdf_workspace")

    def is_ready(self):
        """[REFACTOR] Check if vector store is initialized and has content."""
        return self.collection is not None and self.collection.count() > 0

    def clear(self):
        """[REFACTOR] Clear all documents from collection."""
        if self.collection:
            existing_ids = self.collection.get()["ids"]
            if existing_ids:
                self.collection.delete(ids=existing_ids)

    def index_documents(self, pdf_paths, embedding_fn, progress_callback=None):
        """[REFACTOR] Index PDFs into vector store with batching.
        
        Args:
            pdf_paths: List of PDF file paths
            embedding_fn: Callable that takes list of texts and returns embeddings
            progress_callback: Optional progress reporting function
        """
        if not self.collection:
            raise Exception("Vector store not initialized. Please save project first.")

        # Clear existing data
        self.clear()

        chunks = []
        metadatas = []
        ids = []

        chunk_word_size = 150
        overlap_words = 30

        # [REFACTOR] Extract and chunk documents
        for doc_idx, pdf_path in enumerate(pdf_paths):
            doc_name = os.path.basename(pdf_path)
            try:
                doc = fitz.open(pdf_path)
                total_pages = len(doc)
                
                chunk_counter = 0
                for page_num in range(total_pages):
                    if progress_callback:
                        progress_callback(f"[{doc_name}] Extracting Page {page_num+1}/{total_pages}...")
                    
                    page = doc.load_page(page_num)
                    text = page.get_text("text").replace('\n', ' ').strip()
                    text = re.sub(r'\s+', ' ', text)
                    
                    words = text.split(' ')
                    
                    for i in range(0, len(words), chunk_word_size - overlap_words):
                        chunk_text = ' '.join(words[i:i + chunk_word_size])
                        if len(chunk_text) > 50:
                            chunks.append(chunk_text)
                            metadatas.append({"doc_name": doc_name, "page": page_num})
                            ids.append(f"{doc_name}_p{page_num}_c{chunk_counter}")
                            chunk_counter += 1
                doc.close()
            except Exception as e:
                print(f"[VectorStore] Failed to index {doc_name}: {e}")

        total_chunks = len(chunks)
        if total_chunks == 0:
            return

        # [REFACTOR] Batch embeddings and upsert to ChromaDB
        batch_size = 100
        total_batches = (total_chunks // batch_size) + (1 if total_chunks % batch_size != 0 else 0)
        
        for i in range(0, total_chunks, batch_size):
            current_batch_num = (i // batch_size) + 1
            if progress_callback:
                progress_callback(f"Embedding and saving batch {current_batch_num} of {total_batches}...")
            
            batch_texts = chunks[i:i+batch_size]
            batch_metadatas = metadatas[i:i+batch_size]
            batch_ids = ids[i:i+batch_size]
            
            # [REFACTOR] Use embedding function provided by orchestrator
            batch_embs = embedding_fn(batch_texts)
            
            self.collection.upsert(
                documents=batch_texts,
                embeddings=batch_embs,
                metadatas=batch_metadatas,
                ids=batch_ids
            )
            
            # [REFACTOR] Memory management after batch
            batch_texts.clear()
            batch_metadatas.clear()
            batch_ids.clear()
            del batch_embs
            if (current_batch_num % 3) == 0:
                gc.collect()
        
        # [REFACTOR] Final cleanup
        chunks.clear()
        metadatas.clear()
        ids.clear()
        gc.collect()

    def search(self, query_embedding, where_clause=None, n_results=6):
        """[REFACTOR] Search vector store using embedding.
        
        Args:
            query_embedding: Vector embedding of query
            where_clause: ChromaDB where filter (optional)
            n_results: Number of results to return
        
        Returns:
            Dict with 'documents', 'ids', 'metadatas' keys
        """
        if not self.collection:
            return {"documents": [[]], "ids": [[]], "metadatas": [[]]}

        try:
            return self.collection.query(
                query_embeddings=[query_embedding],
                n_results=n_results,
                where=where_clause
            )
        except Exception as e:
            print(f"[VectorStore] Search failed: {e}")
            return {"documents": [[]], "ids": [[]], "metadatas": [[]]}

    def get_collection_count(self):
        """[REFACTOR] Get number of documents in collection."""
        return self.collection.count() if self.collection else 0
