import re
from collections import defaultdict
import math

_BIDI_AND_ZERO_WIDTH_RE = re.compile(r"[\u200b\u200e\u200f\u202a-\u202e]")
_SURROGATE_RE = re.compile(r"[\ud800-\udfff]")


def sanitize_extracted_text(raw_text: str, collapse_whitespace: bool = False) -> str:
    """Remove PDF annotation artifacts and invalid Unicode from extracted text."""
    if not raw_text:
        return ""

    clean_text = raw_text.replace("\r\n", "\n").replace("\r", "\n")
    clean_text = clean_text.replace("\ufffc", "").replace("\ufffd", "")
    clean_text = _SURROGATE_RE.sub("", clean_text)
    clean_text = _BIDI_AND_ZERO_WIDTH_RE.sub("", clean_text)

    # Join words split across line breaks during PDF extraction.
    clean_text = re.sub(r"-\s*\n\s*", "", clean_text)

    if collapse_whitespace:
        clean_text = re.sub(r"\s+", " ", clean_text)
    else:
        clean_text = re.sub(r"[ \t]+", " ", clean_text)
        clean_text = re.sub(r" *\n *", "\n", clean_text)
        clean_text = re.sub(r"\n{3,}", "\n\n", clean_text)

    # Final safety net: remove any residual code points that cannot be UTF-8 encoded.
    clean_text = clean_text.encode("utf-8", errors="ignore").decode("utf-8", errors="ignore")

    return clean_text.strip()

def get_semantic_similarity_matrix(node_ids, texts, llm_manager):
    """
    Generates embeddings and calculates pairwise cosine similarity.
    Returns a nested dictionary matrix mapping node IDs to similarity scores.
    """
    if not texts or not node_ids or len(texts) != len(node_ids):
        return defaultdict(dict)

    # 1. Generate embeddings using Ollama
    try:
        embeddings = llm_manager.get_batch_embeddings(texts)
    except Exception as e:
        print(f"[System] Failed to generate embeddings for similarity: {e}")
        return defaultdict(dict)

    # 2. Helper function for cosine similarity
    def cosine_similarity(v1, v2):
        dot_product = sum(a * b for a, b in zip(v1, v2))
        magnitude1 = math.sqrt(sum(a * a for a in v1))
        magnitude2 = math.sqrt(sum(b * b for b in v2))
        if magnitude1 * magnitude2 == 0: return 0.0
        return dot_product / (magnitude1 * magnitude2)

    # 3. Build the similarity matrix
    similarity_matrix = defaultdict(dict)
    for i in range(len(node_ids)):
        for j in range(i + 1, len(node_ids)):
            sim = cosine_similarity(embeddings[i], embeddings[j])
            id_a = node_ids[i]
            id_b = node_ids[j]
            similarity_matrix[id_a][id_b] = sim
            similarity_matrix[id_b][id_a] = sim # Store both ways for fast lookups

    return similarity_matrix