"""Atomic, project-owned import of files acquired by plugins or integrations."""
from __future__ import annotations

import hashlib
import os
import re
import threading
from pathlib import Path
from typing import Any, Callable, Dict, Iterable

from core.events.domains.citation_events import CitationIntent, CitationPayload
from core.events.domains.document_events import (
    DocumentEvent,
    DocumentEventPayload,
    SourceEvent,
)
from core.models.source_import_models import SourceImportRequest, SourceImportResult


HASH_CHUNK_BYTES = 1024 * 1024
SAFE_NAME_RE = re.compile(r"[^A-Za-z0-9._ -]+")


class SourceImportService:
    """Imports a validated local asset into the current project.

    The copied asset, source metadata, citation, OCR cache, and normal Papyrus
    events are treated as one logical operation. Failures remove partial state.
    """

    def __init__(self, project_manager, extraction_service, bus) -> None:
        self._pm = project_manager
        self._extraction = extraction_service
        self._bus = bus
        self._lock = threading.RLock()

    def import_source(
        self,
        request: SourceImportRequest,
        *,
        cancel_check: Callable[[], bool] | None = None,
        progress: Callable[[int, int, str], None] | None = None,
    ) -> SourceImportResult:
        with self._lock:
            return self._import_locked(request, cancel_check, progress)

    def _import_locked(self, request, cancel_check, progress) -> SourceImportResult:
        self._validate_project_and_file(request)
        checksum = self._checksum(request.local_path, cancel_check, progress)
        existing = self._find_existing(checksum, request.provenance)
        if existing:
            return SourceImportResult(
                source_id=existing["id"],
                path=existing["path"],
                source_type=existing["source_type"],
                checksum=checksum,
                reused=True,
            )
        source_type = request.source_type or self._pm.detect_source_type(request.local_path)
        if source_type == "unknown":
            raise ValueError("Papyrus does not recognize this archive asset type.")
        destination = self._destination(request, checksum)
        copied = False
        added = False
        try:
            self._copy_atomic(request.local_path, destination, cancel_check, progress)
            copied = True
            metadata = self._metadata(request, checksum)
            if not self._pm.add_source(str(destination), source_type, metadata):
                raise RuntimeError("Papyrus could not create the project source.")
            added = True
            source = self._pm.get_source_entity_by_path(str(destination))
            source_id = source.id if source else self._pm._source_row_id(str(destination))
            ocr_attached = self._attach_ocr(str(destination), request.ocr_blocks)
            citation_created = self._upsert_citation(str(destination), request.citation)
            self._emit_added(source_id, str(destination), source_type, metadata)
            return SourceImportResult(
                source_id=source_id,
                path=str(destination),
                source_type=source_type,
                checksum=checksum,
                citation_created=citation_created,
                ocr_attached=ocr_attached,
            )
        except Exception:
            if added:
                self._extraction.invalidate(str(destination))
                self._pm.remove_source(str(destination))
            if copied:
                destination.unlink(missing_ok=True)
                self._remove_empty_parents(destination)
            raise

    def _validate_project_and_file(self, request: SourceImportRequest) -> None:
        if not self._pm.project_filepath or self._pm._conn is None:
            raise RuntimeError("Open a Papyrus project before importing.")
        source = Path(request.local_path)
        if not source.is_file() or source.is_symlink():
            raise ValueError("The downloaded archive asset is not a regular file.")
        if source.stat().st_size <= 0:
            raise ValueError("The downloaded archive asset is empty.")
        self._validate_content(source, request.source_type)

    def _validate_content(self, source: Path, source_type: str) -> None:
        detected = source_type or self._pm.detect_source_type(str(source))
        if detected == "pdf":
            with source.open("rb") as stream:
                if stream.read(5) != b"%PDF-":
                    raise ValueError("Archive asset is not a valid PDF.")
            return
        if detected == "image":
            try:
                from PIL import Image
                with Image.open(source) as image:
                    width, height = image.size
                    if width <= 0 or height <= 0 or width * height > 200_000_000:
                        raise ValueError("Archive image dimensions are unsafe.")
                    image.verify()
            except ValueError:
                raise
            except Exception as exc:
                raise ValueError("Archive asset is not a valid supported image.") from exc

    def _checksum(self, path, cancel_check, progress) -> str:
        total = os.path.getsize(path)
        current = 0
        digest = hashlib.sha256()
        with open(path, "rb") as stream:
            while True:
                if cancel_check and cancel_check():
                    raise RuntimeError("Import cancelled.")
                chunk = stream.read(HASH_CHUNK_BYTES)
                if not chunk:
                    break
                digest.update(chunk)
                current += len(chunk)
                if progress:
                    progress(current, total, "Validating archive asset")
        return digest.hexdigest()

    def _find_existing(self, checksum: str, provenance: Dict[str, Any]) -> Dict[str, Any] | None:
        provider = str(provenance.get("provider", ""))
        record_id = str(provenance.get("provider_record_id", ""))
        for source in self._pm.list_sources():
            metadata = source.get("metadata", {})
            archive = metadata.get("archive_provenance", {})
            if metadata.get("sha256") == checksum:
                return source
            if provider and record_id:
                if archive.get("provider") == provider and str(
                    archive.get("provider_record_id", "")
                ) == record_id:
                    return source
        return None

    def _destination(self, request: SourceImportRequest, checksum: str) -> Path:
        project_path = Path(self._pm.project_filepath)
        root = Path(f"{project_path}_assets") / "sources" / checksum[:2] / checksum
        supplied = request.preferred_filename or request.title or Path(request.local_path).name
        source_suffix = Path(request.local_path).suffix.lower()
        name = self._safe_filename(supplied, source_suffix)
        destination = (root / name).resolve()
        if root.resolve() not in destination.parents:
            raise ValueError("Unsafe imported filename.")
        return destination

    def _copy_atomic(self, source, destination, cancel_check, progress) -> None:
        destination.parent.mkdir(parents=True, exist_ok=True)
        partial = destination.with_name(f"{destination.name}.part")
        current = 0
        total = os.path.getsize(source)
        try:
            with open(source, "rb") as src, partial.open("wb") as dst:
                while True:
                    if cancel_check and cancel_check():
                        raise RuntimeError("Import cancelled.")
                    chunk = src.read(HASH_CHUNK_BYTES)
                    if not chunk:
                        break
                    dst.write(chunk)
                    current += len(chunk)
                    if progress:
                        progress(current, total, "Copying asset into project")
            os.replace(partial, destination)
        finally:
            partial.unlink(missing_ok=True)

    def _metadata(self, request, checksum) -> Dict[str, Any]:
        metadata = dict(request.metadata)
        metadata.update({
            "sha256": checksum,
            "managed_by": "source_import_service",
            "archive_provenance": dict(request.provenance),
        })
        if request.title:
            metadata["display_title"] = request.title
        return metadata

    def _attach_ocr(self, doc_id: str, blocks: Iterable[Dict[str, Any]]) -> bool:
        normalized = []
        for index, raw in enumerate(blocks or []):
            exact = str(raw.get("exact_text") or raw.get("text") or "").strip()
            if not exact:
                continue
            normalized.append({
                "block_id": raw.get("block_id") or f"{doc_id}_p{raw.get('page_num', 0)}_archive_{index}",
                "doc_id": doc_id,
                "page_num": raw.get("page_num"),
                "block_type": raw.get("block_type", "paragraph"),
                "exact_text": exact,
                "normalized_text": raw.get("normalized_text") or " ".join(exact.split()),
                "bbox": raw.get("bbox"),
                "font_size": raw.get("font_size"),
                "is_bold": raw.get("is_bold"),
                "heading_level": raw.get("heading_level"),
                "section_path": raw.get("section_path", []),
                "has_superscript": False,
                "extractor_name": raw.get("extractor_name", "archive_ocr"),
                "extractor_version": raw.get("extractor_version", "1.0"),
            })
        if not normalized:
            return False
        self._extraction.import_blocks(doc_id, normalized)
        return True

    def _upsert_citation(self, doc_id: str, citation: Dict[str, Any]) -> bool:
        if not citation:
            return False
        record = dict(citation)
        record["doc_id"] = doc_id
        self._pm.upsert_citation(record)
        self._bus.citation_action_requested.emit(
            CitationIntent.REFRESH_TABLE, CitationPayload(doc_ids=[doc_id])
        )
        return True

    def _emit_added(self, source_id, path, source_type, metadata) -> None:
        payload = DocumentEventPayload(
            path=path,
            source_id=source_id,
            source_type=source_type,
            context={"managed_import": True, "metadata": metadata},
        )
        self._bus.source_added.emit(SourceEvent.ADDED, payload)
        self._bus.document_added.emit(DocumentEvent.DOCUMENT_ADDED, payload)

    @staticmethod
    def _safe_filename(value: str, suffix: str) -> str:
        base = Path(str(value or "")).name.strip().replace("\x00", "")
        base = SAFE_NAME_RE.sub("_", base).strip(" .")[:120] or "archive-source"
        if suffix and not base.lower().endswith(suffix):
            base = f"{base}{suffix}"
        return base

    @staticmethod
    def _remove_empty_parents(path: Path) -> None:
        for parent in list(path.parents)[:3]:
            try:
                parent.rmdir()
            except OSError:
                break
