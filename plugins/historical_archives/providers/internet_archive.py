"""Internet Archive Advanced Search and metadata adapter."""
from __future__ import annotations

import re
from typing import Any, Dict, List
from urllib.parse import quote

from ..models import (
    ArchiveAsset,
    ArchiveQuery,
    ArchiveResult,
    ProviderCapabilities,
    ProviderSearchSchema,
    SearchOption,
)
from ..services.query_language import (
    build_structured_expression,
    render_boolean_expression,
)
from .base import clean_text, first_text, text_list
from .search_schema import RESULT_TYPES, STANDARD_SORTS, US_STATES


IA_SEARCH_URL = "https://archive.org/advancedsearch.php"
IA_METADATA_URL = "https://archive.org/metadata/{identifier}"
IA_DOWNLOAD_URL = "https://archive.org/download/{identifier}/{filename}"


class InternetArchiveProvider:
    provider_id = "internet_archive"
    label = "Internet Archive"
    capabilities = ProviderCapabilities(
        metadata_search=True,
        full_text_search=False,
        date_filter=True,
        location_filter=True,
        publication_filter=True,
        title_filter=True,
        creator_filter=True,
        subject_filter=True,
        language_filter=True,
        boolean_search=True,
        thumbnails=True,
        ocr_snippets=True,
        complete_ocr=True,
        page_images=True,
        pdf_download=True,
        pagination=True,
        stable_urls=True,
        structured_rights=True,
    )
    search_schema = ProviderSearchSchema(
        fields=(
            "exact_phrase", "all_words", "any_words", "excluded_words",
            "title", "creator", "subject", "date", "location", "publication",
            "language", "result_type", "sort",
        ),
        result_types=RESULT_TYPES,
        sort_options=STANDARD_SORTS + (
            SearchOption("popularity", "Most downloaded"),
        ),
        location_options=US_STATES,
        collection_options=(
            "americana", "newspapers", "library_of_congress", "fedlink",
            "coloradohistoricnewspapers", "internetarchivebooks",
        ),
        query_help=(
            "Internet Archive uses Lucene-style Boolean metadata search with "
            "native title, creator, subject, collection, language, and date fields."
        ),
    )

    def build_query(self, query: ArchiveQuery) -> str:
        text = build_structured_expression(query, self._escape)
        clauses = []
        if text != "*":
            clauses.append(
                f"(title:({text}) OR description:({text}) OR subject:({text}))"
            )
        self._field_clause(clauses, "title", query.title)
        self._field_clause(clauses, "creator", query.creator)
        self._field_clause(clauses, "subject", query.subject)
        if query.date_from or query.date_to:
            start = query.date_from or "0001-01-01"
            end = query.date_to or "9999-12-31"
            clauses.append(f"date:[{start} TO {end}]")
        if query.language:
            clauses.append(f'language:"{self._escape(query.language)}"')
        if query.publication:
            clauses.append(f'collection:"{self._escape(query.publication)}"')
        if query.location:
            place = self._escape(query.location)
            clauses.append(f'(coverage:"{place}" OR subject:"{place}")')
        mediatype = {
            "newspaper": "texts", "book": "texts", "text": "texts",
            "image": "image", "map": "image", "manuscript": "texts",
            "audio": "audio", "video": "movies",
        }.get(query.result_type)
        if mediatype:
            clauses.append(f"mediatype:{mediatype}")
        return " AND ".join(clauses) or "mediatype:texts"

    def build_params(self, query: ArchiveQuery) -> Dict[str, Any]:
        return {
            "q": self.build_query(query),
            "fl[]": [
                "identifier", "title", "description", "date", "creator",
                "collection", "language", "subject", "coverage", "downloads",
                "mediatype", "rights", "licenseurl",
            ],
            "rows": max(1, min(int(query.max_results), 100)),
            "page": max(1, int(query.page)),
            "output": "json",
            "sort[]": {
                "popularity": "downloads desc",
                "newest": "date desc",
                "oldest": "date asc",
                "title": "titleSorter asc",
            }.get(query.sort, "score desc"),
        }

    def search(self, query, client, task_context=None) -> List[ArchiveResult]:
        payload = client.get_json(
            IA_SEARCH_URL,
            params=self.build_params(query),
            max_bytes=12 * 1024 * 1024,
            task_context=task_context,
        )
        docs = payload.get("response", {}).get("docs", [])
        return [self.normalize(item, query) for item in docs if isinstance(item, dict)]

    def normalize(self, item, query) -> ArchiveResult:
        identifier = first_text(item.get("identifier"))
        description = clean_text(item.get("description"))
        subjects = text_list(item.get("subject"))
        return ArchiveResult(
            provider_id=self.provider_id,
            provider_record_id=identifier,
            result_type=first_text(item.get("mediatype")) or "text",
            title=first_text(item.get("title")) or identifier,
            persistent_url=f"https://archive.org/details/{quote(identifier)}",
            publication=first_text(item.get("collection")),
            collection=first_text(item.get("collection")),
            date=first_text(item.get("date")),
            creators=text_list(item.get("creator")),
            places=text_list(item.get("coverage")) + subjects,
            language=text_list(item.get("language")),
            description=description,
            ocr_snippet=description,
            thumbnail_url=f"https://archive.org/services/img/{quote(identifier)}",
            rights=first_text(item.get("rights")),
            provider_score=float(item.get("downloads") or 0.0),
            raw_metadata={
                "identifiers": {"identifier": identifier},
                "original": item,
            },
            matched_variant=query.original_query,
        )

    def resolve_assets(self, result, client, task_context=None) -> List[ArchiveAsset]:
        payload = client.get_json(
            IA_METADATA_URL.format(identifier=quote(result.provider_record_id)),
            max_bytes=16 * 1024 * 1024,
            task_context=task_context,
        )
        item_metadata = payload.get("metadata", {})
        result.raw_metadata["item_metadata"] = item_metadata
        result.rights = first_text(
            item_metadata.get("rights")
            or item_metadata.get("licenseurl")
            or item_metadata.get("possible-copyright-status")
        )
        result.license = first_text(item_metadata.get("licenseurl"))
        result.institution = first_text(
            item_metadata.get("contributor") or item_metadata.get("sponsor")
        )
        assets = []
        for file_data in payload.get("files", []):
            asset = self._file_to_asset(result.provider_record_id, file_data)
            if asset:
                assets.append(asset)
        assets.sort(key=self._asset_priority)
        result.assets = assets
        return assets

    def retrieve_ocr(self, result, client, task_context=None) -> str:
        assets = result.assets or self.resolve_assets(result, client, task_context)
        ocr = next((asset for asset in assets if asset.kind == "ocr"), None)
        if not ocr:
            return result.ocr_snippet
        response = client.get_bytes(
            ocr.url,
            max_bytes=64 * 1024 * 1024,
            task_context=task_context,
        )
        return response.content.decode("utf-8", errors="replace")

    def _file_to_asset(self, identifier: str, item: Dict[str, Any]):
        name = first_text(item.get("name"))
        fmt = first_text(item.get("format"))
        lower = f"{name} {fmt}".lower()
        if not name or "jp2 zip" in lower or name.lower().endswith(".zip"):
            return None
        if name.lower().endswith(".pdf") or "text pdf" in lower:
            kind, mime = "pdf", "application/pdf"
        elif "djvutxt" in lower or name.lower().endswith(("_djvu.txt", "_text.pdf.txt")):
            kind, mime = "ocr", "text/plain"
        elif name.lower().endswith((".jpg", ".jpeg", ".png", ".tif", ".tiff")):
            kind, mime = "image", "image/jpeg"
        else:
            return None
        size = None
        try:
            size = int(item.get("size")) if item.get("size") else None
        except (TypeError, ValueError):
            pass
        return ArchiveAsset(
            asset_id=f"ia:{name}",
            kind=kind,
            url=IA_DOWNLOAD_URL.format(identifier=quote(identifier), filename=quote(name)),
            mime_type=mime,
            label=fmt or name,
            size=size,
            metadata={"name": name, "format": fmt, "source": item.get("source", "")},
        )

    @staticmethod
    def _asset_priority(asset: ArchiveAsset):
        label = asset.label.lower()
        if asset.kind == "pdf" and "text" in label:
            return 0
        if asset.kind == "pdf":
            return 1
        if asset.kind == "image":
            return 2
        return 3

    @staticmethod
    def _escape(value: str) -> str:
        return re.sub(r'([+\-!(){}\[\]^"~*?:\\/])', r"\\\1", str(value or "").strip())

    def _field_clause(self, clauses, field: str, value: str) -> None:
        if value:
            rendered = render_boolean_expression(value, self._escape)
            clauses.append(f"{field}:({rendered})")
