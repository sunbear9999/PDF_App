"""Archive asset selection, download, OCR, provenance, and Papyrus import."""
from __future__ import annotations

import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict
from urllib.parse import urlparse

from ..models import ArchiveAsset, ArchiveResult


class ArchiveImportCoordinator:
    def __init__(self, api, providers: Dict[str, object], client, repository) -> None:
        self._api = api
        self._providers = providers
        self._client = client
        self._repository = repository

    def import_result(self, result: ArchiveResult, task_context=None):
        self._report(task_context, 0, 4, "Checking for an existing project source")
        existing, warnings = self._existing_import(result)
        if existing and os.path.exists(existing["path"]):
            return {
                "reused": True,
                "path": existing["path"],
                "source_id": existing["source_id"],
                "warnings": warnings,
            }
        provider = self._providers[result.provider_id]
        self._report(task_context, 1, 4, "Resolving downloadable archive files")
        assets = result.assets or provider.resolve_assets(result, self._client, task_context)
        asset = self._choose_asset(assets)
        if asset is None:
            raise RuntimeError("No legally accessible PDF or image is available.")
        suffix = self._suffix(asset)
        temp_path = self._api.storage.temp_path(suffix)
        allow_unlisted = result.provider_id == "iiif"
        try:
            self._report(task_context, 2, 4, "Downloading the selected archive source")
            self._client.download(
                asset.url,
                temp_path,
                max_bytes=int(self._api.config.get("max_download_mb", 250)) * 1024 * 1024,
                timeout=float(self._api.config.get("request_timeout", 20)),
                task_context=task_context,
                allow_unlisted_host=allow_unlisted,
            )
            ocr_text, ocr_warnings = self._optional_ocr(
                provider, result, task_context
            )
            warnings.extend(ocr_warnings)
            provenance = self._provenance(result, asset)
            request = self._import_request(
                result, asset, suffix, temp_path, ocr_text, provenance
            )
            self._report(task_context, 3, 4, "Adding the source to the project")
            imported = self._api.sources.import_file(request, task_context=task_context)
            try:
                self._repository.mark_imported(result, imported, provenance)
            except Exception as exc:
                warnings.append(f"Archive import history was not saved: {exc}")
            self._report(task_context, 4, 4, "Archive source imported")
            return self._success_result(imported, warnings)
        finally:
            Path(temp_path).unlink(missing_ok=True)

    def _existing_import(self, result):
        try:
            return self._repository.imported_record(result.provider_key), []
        except Exception as exc:
            return None, [f"Archive import history could not be checked: {exc}"]

    @staticmethod
    def _success_result(imported, warnings):
        return {
            "reused": imported.reused,
            "path": imported.path,
            "source_id": imported.source_id,
            "checksum": imported.checksum,
            "warnings": warnings,
        }

    def _optional_ocr(self, provider, result, task_context):
        self._report(task_context, 2, 4, "Retrieving optional archive OCR")
        try:
            return provider.retrieve_ocr(result, self._client, task_context), []
        except Exception as exc:
            if task_context and getattr(task_context, "is_cancelled", False):
                raise
            fallback = result.ocr_snippet or result.description
            return fallback, [f"Full archive OCR was unavailable: {exc}"]

    @staticmethod
    def _report(task_context, current, total, message):
        if task_context is not None:
            task_context.report(current, total, message)

    def _import_request(self, result, asset, suffix, temp_path, ocr_text, provenance):
        return {
            "local_path": temp_path,
            "source_type": "pdf" if asset.kind == "pdf" else "image",
            "title": result.title,
            "preferred_filename": self._filename(result, suffix),
            "metadata": {
                "historical_archives": {
                    "provider_metadata": result.raw_metadata,
                    "match_reasons": result.match_reasons,
                    "duplicate_of": result.duplicate_of,
                }
            },
            "provenance": provenance,
            "citation": self._citation(result),
            "ocr_blocks": self._ocr_blocks(result, ocr_text),
        }

    @staticmethod
    def _choose_asset(assets):
        allowed = [item for item in assets if item.download_allowed and item.kind in {"pdf", "image"}]
        return min(
            allowed,
            key=lambda item: (
                0 if item.kind == "pdf" and "text" in item.label.lower() else
                1 if item.kind == "pdf" else 2,
                item.size or 0,
            ),
            default=None,
        )

    @staticmethod
    def _suffix(asset: ArchiveAsset) -> str:
        suffix = Path(urlparse(asset.url).path).suffix.lower()
        if suffix in {".pdf", ".jpg", ".jpeg", ".png", ".tif", ".tiff", ".webp"}:
            return suffix
        return ".pdf" if asset.kind == "pdf" else ".jpg"

    @staticmethod
    def _filename(result: ArchiveResult, suffix: str) -> str:
        stem = f"{result.publication or result.title} {result.date} {result.page}".strip()
        return f"{stem}{suffix}"

    @staticmethod
    def _provenance(result, asset):
        return {
            "provider": result.provider_id,
            "archive": result.institution or result.provider_id,
            "collection": result.collection,
            "contributing_institution": result.institution,
            "provider_record_id": result.provider_record_id,
            "stable_record_url": result.persistent_url,
            "access_url": result.persistent_url,
            "original_search_query": result.matched_variant,
            "matched_query_variant": result.matched_variant,
            "retrieved_at": datetime.now(timezone.utc).isoformat(),
            "source_title": result.title,
            "publication_title": result.publication,
            "publication_date": result.date,
            "edition": result.edition,
            "page_number": result.page,
            "sequence": result.sequence,
            "creator": list(result.creators),
            "language": list(result.language),
            "rights_statement": result.rights,
            "license": result.license,
            "access_restrictions": result.access_restrictions,
            "asset_url": asset.url,
            "asset_type": asset.kind,
            "iiif_manifest_url": result.iiif_manifest_url,
            "ocr_source": result.provider_id if result.ocr_snippet else "",
            "import_method": "historical_archives_plugin",
            "transformations": [],
        }

    @staticmethod
    def _citation(result):
        return {
            "title": result.title,
            "authors": "; ".join(result.creators),
            "year": result.date[:4] if result.date else "",
            "journal": result.publication,
            "publisher": result.institution,
            "url": result.persistent_url,
            "item_type": "newspaperArticle" if result.result_type == "newspaper" else result.result_type,
            "source_provider": result.provider_id,
            "source_item_key": result.provider_record_id,
            "source_updated_at": result.date,
            "fields": {
                "date": result.date,
                "edition": result.edition,
                "page": result.page,
                "archive": result.institution,
                "collection": result.collection,
                "accessed": datetime.now(timezone.utc).date().isoformat(),
                "rights": result.rights,
            },
        }

    @staticmethod
    def _ocr_blocks(result, text):
        exact = str(text or "").strip()
        if not exact:
            return []
        page_num = max(0, int(result.page) - 1) if str(result.page).isdigit() else 0
        chunks = []
        for paragraph in re.split(r"\n\s*\n", exact):
            paragraph = paragraph.strip()
            for start in range(0, len(paragraph), 8000):
                chunk = paragraph[start:start + 8000]
                if chunk:
                    chunks.append({
                        "page_num": page_num,
                        "block_type": "paragraph",
                        "exact_text": chunk,
                        "normalized_text": " ".join(chunk.split()),
                        "extractor_name": f"{result.provider_id}_ocr",
                        "extractor_version": "1.0",
                    })
        return chunks
