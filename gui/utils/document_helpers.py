"""Helpers for project document lists shown in GUI controls."""
from __future__ import annotations

import os
from typing import Iterable, List


def active_pdf_paths(project_manager) -> List[str]:
    """Return active source paths. Name kept for compatibility with older callers."""
    if not project_manager:
        return []

    if hasattr(project_manager, "list_sources"):
        try:
            return [s.get("path") for s in project_manager.list_sources() if s.get("path")]
        except Exception:
            pass

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


def prune_doc_names(project_manager, doc_refs: Iterable[str]) -> List[str]:
    """Active source paths for saved document references, dropping removed documents.

    References are stored as paths: file names are not unique (the same name in two
    folders), and retrieval refuses a filter it cannot resolve to exactly one source.
    Older projects saved file names; such a name selects every active file of that name."""
    paths = active_pdf_paths(project_manager)
    by_name: dict = {}
    for path in paths:
        by_name.setdefault(os.path.basename(path), []).append(path)
    active = set(paths)
    result: List[str] = []
    for ref in doc_refs or []:
        for path in ([ref] if ref in active else by_name.get(ref, [])):
            if path not in result:
                result.append(path)
    return result


def document_labels(paths: Iterable[str]) -> dict:
    """Display label per path: the file name, plus its folder when another file shares the name."""
    paths = list(paths)
    counts: dict = {}
    for path in paths:
        counts[os.path.basename(path)] = counts.get(os.path.basename(path), 0) + 1
    return {path: os.path.basename(path) if counts[os.path.basename(path)] == 1
            else f"{os.path.basename(path)}  ({os.path.basename(os.path.dirname(path)) or os.path.dirname(path)})"
            for path in paths}
