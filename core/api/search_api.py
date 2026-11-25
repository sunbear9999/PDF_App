"""Compatibility facade for citation search; extraction/ranking stays universal."""
from core.services.intelligence.artifact_service import ArtifactService
from core.services.intelligence.hybrid_retrieval_service import HybridRetrievalService


class SearchAPI:
    @staticmethod
    def extract_mathematical_citations(goal, allowed_docs, project_manager, llm_manager):
        """Return indexed citation passages. Run in a background task.

        Unprocessed sources produce no hits; callers should offer the shared
        Processing controls rather than silently reparsing PDFs differently.
        """
        retrieval = HybridRetrievalService(project_manager, llm_manager, ArtifactService(project_manager))
        return [{**item, "doc": item["source_title"], "text": item["exact_text"]}
                for item in retrieval.search_citations(goal, allowed_docs)]
