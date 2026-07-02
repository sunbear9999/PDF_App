"""Compatibility imports for reference services.

First-party code should import from ``core.services.reference``.
"""

from core.services.reference.citation_app_service import (
    CitationAppService,
    CitationProvider,
    ExtractionWorker,
)

__all__ = ["CitationAppService", "CitationProvider", "ExtractionWorker"]
