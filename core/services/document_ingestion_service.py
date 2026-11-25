"""Compatibility imports for the canonical document-ingestion service."""

from core.services.document.document_ingestion_service import (
    BibliographyEntry,
    DocumentIngestionService,
    DocumentIngestionTask,
    IngestionResult,
    IngestionWorkerSignals,
    PageText,
)

__all__ = [
    "BibliographyEntry",
    "DocumentIngestionService",
    "DocumentIngestionTask",
    "IngestionResult",
    "IngestionWorkerSignals",
    "PageText",
]
