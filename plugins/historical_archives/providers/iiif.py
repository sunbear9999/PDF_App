"""Generic IIIF Presentation 2.x/3.x manifest adapter."""
from __future__ import annotations

from typing import Any, Dict, Iterable, List

from ..models import (
    ArchiveAsset,
    ArchiveQuery,
    ArchiveResult,
    ProviderCapabilities,
    ProviderSearchSchema,
)
from .base import clean_text, first_text


class IIIFManifestProvider:
    provider_id = "iiif"
    label = "IIIF Manifest"
    capabilities = ProviderCapabilities(
        metadata_search=False,
        thumbnails=True,
        ocr_snippets=True,
        page_images=True,
        iiif_manifest=True,
        stable_urls=True,
        manifest_import=True,
    )
    search_schema = ProviderSearchSchema(
        query_help="Paste a complete public HTTPS IIIF Presentation manifest URL.",
        accepts_manifest_url=True,
    )

    def search(self, query: ArchiveQuery, client, task_context=None) -> List[ArchiveResult]:
        url = query.original_query.strip()
        if not url.startswith("https://"):
            return []
        manifest = client.get_json(
            url,
            max_bytes=16 * 1024 * 1024,
            task_context=task_context,
            allow_unlisted_host=True,
        )
        return self.parse_manifest(manifest, url)

    def parse_manifest(self, manifest: Dict[str, Any], manifest_url: str) -> List[ArchiveResult]:
        if not isinstance(manifest, dict):
            raise ValueError("IIIF manifest must be a JSON object.")
        manifest_id = first_text(manifest.get("id") or manifest.get("@id") or manifest_url)
        title = first_text(manifest.get("label")) or "Untitled IIIF object"
        metadata = self._metadata(manifest.get("metadata", []))
        canvases = self._canvases(manifest)
        if not canvases:
            raise ValueError("IIIF manifest does not contain any canvases.")
        results = []
        for index, canvas in enumerate(canvases, start=1):
            image = self._image_asset(canvas, index)
            if image is None:
                continue
            canvas_id = first_text(canvas.get("id") or canvas.get("@id")) or f"canvas-{index}"
            page_label = first_text(canvas.get("label")) or f"Page {index}"
            results.append(ArchiveResult(
                provider_id=self.provider_id,
                provider_record_id=f"{manifest_id}#{canvas_id}",
                result_type="image",
                title=f"{title} — {page_label}",
                persistent_url=manifest_id,
                collection=metadata.get("Collection", ""),
                date=metadata.get("Date", ""),
                creators=[metadata["Creator"]] if metadata.get("Creator") else [],
                institution=metadata.get("Repository", ""),
                description=metadata.get("Description", ""),
                thumbnail_url=image.url,
                assets=[image],
                iiif_manifest_url=manifest_url,
                rights=first_text(manifest.get("rights")) or metadata.get("Rights", ""),
                license=first_text(manifest.get("license")),
                raw_metadata={
                    "identifiers": {"iiif_manifest": manifest_id, "canvas": canvas_id},
                    "manifest_metadata": metadata,
                    "manifest_fields": {
                        key: value for key, value in manifest.items()
                        if key not in {"items", "sequences"}
                    },
                    "canvas": canvas,
                    "page_index": index,
                },
                matched_variant=manifest_url,
            ))
        return results

    def resolve_assets(self, result, client, task_context=None) -> List[ArchiveAsset]:
        return list(result.assets)

    def retrieve_ocr(self, result, client, task_context=None) -> str:
        for url in self._annotation_text_urls(result.raw_metadata.get("canvas", {})):
            response = client.get_bytes(
                url,
                max_bytes=32 * 1024 * 1024,
                task_context=task_context,
                allow_unlisted_host=True,
            )
            text = response.content.decode("utf-8", errors="replace")
            if text.strip():
                return text.strip()
        return result.ocr_snippet

    def _canvases(self, manifest) -> List[Dict[str, Any]]:
        if isinstance(manifest.get("items"), list):
            return [item for item in manifest["items"] if isinstance(item, dict)]
        sequences = manifest.get("sequences", [])
        if sequences and isinstance(sequences[0], dict):
            return [
                item for item in sequences[0].get("canvases", [])
                if isinstance(item, dict)
            ]
        return []

    def _image_asset(self, canvas, page_num) -> ArchiveAsset | None:
        body = self._v3_image_body(canvas) or self._v2_image_body(canvas)
        if not body:
            return None
        service = body.get("service")
        if isinstance(service, list):
            service = service[0] if service else {}
        service = service if isinstance(service, dict) else {}
        service_id = first_text(service.get("id") or service.get("@id")).rstrip("/")
        image_id = first_text(body.get("id") or body.get("@id"))
        if service_id:
            url = f"{service_id}/full/2000,/0/default.jpg"
        else:
            url = image_id
        if not url.startswith("https://"):
            return None
        return ArchiveAsset(
            asset_id=f"iiif-page-{page_num}",
            kind="image",
            url=url,
            mime_type=first_text(body.get("format")) or "image/jpeg",
            label=f"IIIF page {page_num}",
            page_num=page_num,
            metadata={
                "canvas_id": first_text(canvas.get("id") or canvas.get("@id")),
                "image_service": service_id,
            },
        )

    @staticmethod
    def _v3_image_body(canvas):
        for page in canvas.get("items", []):
            for annotation in page.get("items", []) if isinstance(page, dict) else []:
                body = annotation.get("body", {}) if isinstance(annotation, dict) else {}
                if isinstance(body, dict) and (
                    body.get("type") == "Image" or first_text(body.get("format")).startswith("image/")
                ):
                    return body
        return {}

    @staticmethod
    def _v2_image_body(canvas):
        for annotation in canvas.get("images", []):
            body = annotation.get("resource", {}) if isinstance(annotation, dict) else {}
            if isinstance(body, dict):
                return body
        return {}

    @staticmethod
    def _metadata(entries) -> Dict[str, str]:
        result = {}
        for entry in entries if isinstance(entries, list) else []:
            if not isinstance(entry, dict):
                continue
            key = first_text(entry.get("label"))
            if key:
                result[key] = clean_text(entry.get("value"))
        return result

    def _annotation_text_urls(self, canvas) -> Iterable[str]:
        for value in self._walk(canvas):
            if not isinstance(value, str) or not value.startswith("https://"):
                continue
            lower = value.lower()
            if any(token in lower for token in ("alto", "ocr", ".txt", "fulltext")):
                yield value

    def _walk(self, value):
        if isinstance(value, dict):
            for child in value.values():
                yield from self._walk(child)
        elif isinstance(value, list):
            for child in value:
                yield from self._walk(child)
        else:
            yield value
