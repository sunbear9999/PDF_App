"""Worker-only, page-scoped reading links from canonical artifact edges.

Reads never launch extraction. Missing/stale geometry abstains rather than
making a whole paragraph clickable. Plugins contribute through the same API.
"""
import logging

import fitz

from core.services.extraction.pdf_layout_parser import block_char_rects, parse_block_id
from core.services.intelligence.entity_reading_links import entity_links

MAX_PAGES = 8
PREVIEW_CHARS = 600
XREF_LABEL_CHARS = 60
LINK_KINDS = {"refers_to_note": "note", "cites": "citation", "links_to": "toc", "refers_to": "xref"}


class DocumentLinkService:
    def __init__(self, project_manager, artifact_service, document_extraction_service, memory_manager=None):
        self.project_manager = project_manager
        self.artifacts = artifact_service
        self.extraction = document_extraction_service
        self.memory_manager = memory_manager
        self._providers = {}

    @property
    def db(self):
        return self.artifacts.db

    def register_provider(self, provider_id, provider, plugin_id=None):
        self._providers[provider_id] = (provider, plugin_id)

    def remove_by_plugin(self, plugin_id):
        self._providers = {k: v for k, v in self._providers.items() if v[1] != plugin_id}

    def page_links(self, source_id=None, page_indices=(), source_path=None, entities=False):
        """Reading links on these pages; ``entities`` adds hoverable entity mentions (after
        note, citation and cross-reference links, which take precedence where they overlap)."""
        pages = sorted(set(int(p) for p in page_indices if int(p) >= 0))
        if len(pages) > MAX_PAGES:
            raise ValueError("Request at most eight pages per batch.")
        source = (self.project_manager.get_source_record(source_id) if source_id else
                  self.project_manager.get_source_record_by_path(source_path))
        if not source or not pages:
            return []
        if self.memory_manager and not self.memory_manager.reserve(64 * 1024 * 1024, "Reading link geometry").ok:
            raise ValueError("Not enough memory to prepare reading links. Try again after processing finishes.")
        path, source_id = source["path"], source["id"]
        try:
            blocks = self.extraction.get_cached_block_snapshot(source_id)["blocks"]
        except (ValueError, OSError):
            blocks = []
        by_id = {b.block_id: b for b in blocks}
        rows = self.db.document_link_sources(source_id, pages) if by_id else []
        result = []
        with fitz.open(path) as document:
            for row in rows:
                result.extend(self._artifact_links(document, row, by_id))
            result.extend(_native_links(document, pages))
            if entities:
                names = entity_links(self.db, document, source_id, pages, by_id)
                # A name that also cites a source is a citation link too, offered first on the card.
                result.extend(self._name_citations(document, names, by_id, source_id))
                result.extend(names)
        for provider, _owner in self._providers.values():
            try:
                result.extend(provider(source_id, pages))
            except Exception:
                logging.getLogger(__name__).exception("Document link provider failed")
        return result

    def _name_citations(self, document, names, blocks, source_id):
        entries = {}
        for hit in names:
            entry_id = hit.get("cited_entry_id")
            if entry_id and entry_id not in entries:
                entry = self.db.get_artifact(entry_id)
                entries[entry_id] = entry if _current(entry) and entry.get("source_id") == source_id else None
        out = []
        for hit in names:
            entry = entries.get(hit.get("cited_entry_id"))
            if entry:
                number = (entry.get("metadata") or {}).get("reference_number")
                out.append({**{k: hit[k] for k in ("page", "rects", "source_artifact_id")}, "kind": "citation",
                            "label": "Reference" + (f" {number}" if number else "") + " (cited by name)",
                            "target_artifact_id": entry["artifact_id"],
                            "preview_text": (entry.get("exact_text") or "")[:PREVIEW_CHARS],
                            "target_page": (entry.get("locator") or {}).get("page"),
                            "target_rects": _rects(document, entry, blocks)})
        return out

    def _artifact_links(self, document, row, blocks):
        rects = _rects(document, row, blocks)
        if not rects:
            return []
        results = []
        for edge in self.db.list_edges(row["artifact_id"]):
            if edge["source_artifact_id"] != row["artifact_id"] or edge["edge_type"] not in LINK_KINDS:
                continue
            assertions = edge.get("assertions", [])
            if assertions and not any(a.get("review_state") not in {"rejected", "superseded"} for a in assertions):
                continue
            target = self.db.get_artifact(edge["target_artifact_id"])
            if not _current(target) or target.get("source_id") != row.get("source_id"):
                continue
            target_rects = _rects(document, target, blocks)
            if not target_rects:
                continue
            kind = LINK_KINDS[edge["edge_type"]]
            label = {"note": "Note", "citation": "Reference", "toc": "Contents", "xref": "See"}[kind]
            heading = (target.get("exact_text") or "").strip().splitlines()
            if kind == "xref" and heading:
                label += " " + heading[0][:XREF_LABEL_CHARS]   # "See Table 8.1 Maya kinship terms"

            number = (target.get("metadata") or {}).get("reference_number")
            if number and kind in {"note", "citation"}:
                label += " " + str(number)
            results.append({"page": row["locator"]["page"], "rects": rects, "kind": kind,
                            "label": label,
                            "source_artifact_id": row["artifact_id"], "target_artifact_id": target["artifact_id"],
                            "preview_text": (target.get("exact_text") or "")[:PREVIEW_CHARS],
                            "target_page": target["locator"]["page"], "target_rects": target_rects})
        # A note marker may indirectly cite a reference too. Its primary hover
        # and click target is always the note, where that reference is shown.
        notes = [hit for hit in results if hit["kind"] == "note"]
        return notes or results


def _current(row):
    return bool(row and row.get("status") == "complete"
                and row.get("review_state") not in {"rejected", "superseded"}
                and not (row.get("metadata") or {}).get("migration_conflict"))


def _rects(document, row, blocks):
    loc = row.get("locator") or {}
    page, block_id = loc.get("page"), loc.get("block_id")
    block, parsed = blocks.get(block_id), parse_block_id(block_id or "")
    start, end = loc.get("char_start"), loc.get("char_end")
    if (block is None or parsed is None or not isinstance(page, int) or not 0 <= page < len(document)
            or not isinstance(start, int) or not isinstance(end, int) or end <= start):
        return []
    result = block_char_rects(document[page], parsed[1], start, end)
    return result[1] if result and result[0] == block.exact_text else []


def _native_links(document, pages):
    result = []
    for page in pages:
        if page >= len(document):
            continue
        for link in document[page].get_links():
            target = link.get("page", -1)
            if link["kind"] != fitz.LINK_GOTO or not 0 <= target < len(document):
                continue
            point = link.get("to") or fitz.Point(0, 0)
            result.append({"page": page, "rects": [list(link["from"])], "kind": "xref",
                           "label": "PDF link", "target_artifact_id": None,
                           "preview_text": _native_preview(document[target], point),
                           "target_page": target, "target_rects": [[point.x, point.y, point.x+1, point.y+1]]})
    return result


def _native_preview(page, point):
    clip = fitz.Rect(page.rect.x0, max(page.rect.y0, point.y-8), page.rect.x1,
                     min(page.rect.y1, point.y+200))
    return page.get_text(clip=clip)[:PREVIEW_CHARS]
