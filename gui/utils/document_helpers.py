"""Helpers for project document lists shown in GUI controls."""
from __future__ import annotations

import os
from typing import Iterable, List


def active_pdf_paths(project_manager) -> List[str]:
    """Return project PDF paths that are still active and not soft-removed."""
    if not project_manager:
        return []

    pdfs = list(getattr(project_manager, "pdfs", []) or [])
    if not pdfs:
        return []

    removed_paths = set()
    if hasattr(project_manager, "list_source_entities"):
        try:
            for source in project_manager.list_source_entities() or []:
                state = getattr(source, "state", {}) or {}
                if not state.get("is_removed"):
                    continue
                properties = getattr(source, "properties", {}) or {}
                path = properties.get("path") or getattr(source, "origin_id", "")
                if path:
                    removed_paths.add(path)
        except Exception:
            removed_paths = set()

    return [path for path in pdfs if path and path not in removed_paths]


def active_pdf_names(project_manager) -> List[str]:
    """Return basenames for active project PDFs."""
    return [os.path.basename(path) for path in active_pdf_paths(project_manager)]


def prune_doc_names(project_manager, doc_names: Iterable[str]) -> List[str]:
    """Drop document names that no longer correspond to active project PDFs."""
    allowed = set(active_pdf_names(project_manager))
    return [name for name in (doc_names or []) if name in allowed]
