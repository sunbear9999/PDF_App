"""
ExtractiveSummarizer — deterministic TextRank summaries (the `summarize` primitive).

Produces a per-section (and per-document) extractive summary at ingestion time
with no LLM and no new dependency: sentence similarity via bag-of-words cosine,
ranked with networkx PageRank (both already project dependencies). The optional
abstractive pass is a separate, deferred blueprint — never blocking ingestion.
"""
from __future__ import annotations

import math
import re
from collections import Counter
from typing import List

_WORD_RE = re.compile(r"[A-Za-z0-9]+")
_STOPWORDS = {
    "the", "a", "an", "and", "or", "but", "of", "to", "in", "on", "for", "with",
    "as", "by", "at", "from", "that", "this", "these", "those", "is", "are",
    "was", "were", "be", "been", "it", "its", "his", "her", "their", "which",
    "who", "we", "they", "he", "she", "them", "our", "you", "not", "no",
}


class ExtractiveSummarizer:
    def summarize(self, sentences: List[str], max_sentences: int = 3) -> str:
        """Return the top-ranked sentences (in original order) as a summary."""
        ranked = self.rank(sentences)
        if not ranked:
            return ""
        keep = sorted(idx for idx, _ in ranked[:max_sentences])
        return " ".join(sentences[i].strip() for i in keep)

    def rank(self, sentences: List[str]):
        """Return [(index, score)] sorted by importance (highest first)."""
        clean = [s for s in (sentences or []) if s and s.strip()]
        if len(clean) <= 1:
            return [(i, 1.0) for i in range(len(sentences or []))]
        vectors = [self._bow(s) for s in sentences]
        try:
            import networkx as nx
            graph = nx.Graph()
            n = len(sentences)
            graph.add_nodes_from(range(n))
            for i in range(n):
                for j in range(i + 1, n):
                    sim = _cosine(vectors[i], vectors[j])
                    if sim > 0.0:
                        graph.add_edge(i, j, weight=sim)
            scores = nx.pagerank(graph, weight="weight") if graph.number_of_edges() else {
                i: 1.0 for i in range(n)
            }
        except Exception:
            # Fallback: rank by keyword mass (length-normalized).
            scores = {i: sum(vectors[i].values()) for i in range(len(sentences))}
        return sorted(scores.items(), key=lambda kv: kv[1], reverse=True)

    def _bow(self, sentence: str) -> Counter:
        words = [w.lower() for w in _WORD_RE.findall(sentence or "")]
        return Counter(w for w in words if w not in _STOPWORDS and len(w) > 2)


def _cosine(a: Counter, b: Counter) -> float:
    if not a or not b:
        return 0.0
    common = set(a) & set(b)
    if not common:
        return 0.0
    dot = sum(a[w] * b[w] for w in common)
    na = math.sqrt(sum(v * v for v in a.values()))
    nb = math.sqrt(sum(v * v for v in b.values()))
    return dot / (na * nb) if na and nb else 0.0
