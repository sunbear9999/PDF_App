"""Library of Congress provider using the current loc.gov JSON API."""
from __future__ import annotations

import re
from typing import Any, Dict, List
from urllib.parse import urlparse

from ..models import (
    ArchiveAsset,
    ArchiveQuery,
    ArchiveResult,
    ProviderCapabilities,
    ProviderSearchSchema,
)
from ..services.query_language import build_structured_expression
from .base import (
    clean_text,
    first_text,
    flatten_urls,
    secure_public_url,
    text_list,
)
from .search_schema import RESULT_TYPES, STANDARD_SORTS, US_STATES


LOC_COLLECTION_URL = "https://www.loc.gov/collections/chronicling-america/"
LOC_ENDPOINTS = {
    "newspaper": LOC_COLLECTION_URL,
    "book": "https://www.loc.gov/books/",
    "text": "https://www.loc.gov/books/",
    "image": "https://www.loc.gov/photos/",
    "map": "https://www.loc.gov/maps/",
    "manuscript": "https://www.loc.gov/manuscripts/",
    "audio": "https://www.loc.gov/audio/",
    "video": "https://www.loc.gov/film-and-videos/",
    "all": "https://www.loc.gov/search/",
}


class LibraryOfCongressProvider:
    provider_id = "loc"
    label = "Library of Congress"
    capabilities = ProviderCapabilities(
        metadata_search=True,
        full_text_search=True,
        newspaper_search=True,
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
        iiif_manifest=True,
        pagination=True,
        structured_rights=True,
    )
    search_schema = ProviderSearchSchema(
        fields=(
            "exact_phrase", "all_words", "any_words", "excluded_words",
            "title", "creator", "subject", "date", "location", "publication",
            "language", "result_type", "sort",
        ),
        result_types=RESULT_TYPES,
        sort_options=STANDARD_SORTS,
        location_options=US_STATES,
        collection_options=(
            "Chronicling America", "American Memory", "National Jukebox",
            "Veterans History Project", "World Digital Library",
        ),
        query_help=(
            "LOC searches metadata and available full text; place, subject, "
            "creator, language, and collection use native facets."
        ),
    )

    def build_params(self, query: ArchiveQuery) -> Dict[str, Any]:
        expression = build_structured_expression(query)
        if query.title:
            expression = self._and(expression, f'"{query.title}"')
        params: Dict[str, Any] = {
            "fo": "json",
            "at": "results,pagination",
            "c": max(1, min(int(query.max_results), 100)),
            "sp": max(1, int(query.page)),
        }
        if expression != "*":
            params["q"] = expression
        start, end = self._year(query.date_from), self._year(query.date_to)
        if start or end:
            params["dates"] = f"{start or end}/{end or start}"
        facets = []
        if query.location:
            facets.append(f"location:{query.location}")
        if query.publication:
            facets.append(f"partof:{query.publication}")
        if query.creator:
            facets.append(f"contributor:{query.creator}")
        if query.subject:
            facets.append(f"subject:{query.subject}")
        if query.language:
            facets.append(f"language:{query.language}")
        if facets:
            params["fa"] = "|".join(facets)
        sort_map = {"newest": "date_desc", "oldest": "date", "title": "title_s"}
        if query.sort in sort_map:
            params["sb"] = sort_map[query.sort]
        return params

    def search(self, query, client, task_context=None) -> List[ArchiveResult]:
        payload = client.get_json(
            LOC_ENDPOINTS.get(query.result_type, LOC_ENDPOINTS["all"]),
            params=self.build_params(query),
            max_bytes=12 * 1024 * 1024,
            task_context=task_context,
        )
        return [
            self.normalize(item, query)
            for item in payload.get("results", [])
            if isinstance(item, dict)
        ]

    def normalize(self, item: Dict[str, Any], query: ArchiveQuery) -> ArchiveResult:
        record_url = secure_public_url(first_text(item.get("id") or item.get("url")))
        lccn = first_text(item.get("number_lccn"))
        sequence = first_text(item.get("number_page") or item.get("number"))
        edition = first_text(item.get("number_edition"))
        date = first_text(item.get("date"))
        identifier = lccn or self._record_identifier(record_url)
        record_id = "|".join(filter(None, [identifier, date, edition, sequence]))
        image_urls = list(flatten_urls(item.get("image_url")))
        thumbnail = (
            image_urls[0]
            if image_urls
            else secure_public_url(first_text(item.get("thumbnail")))
        )
        assets = self._assets_from_item(item, image_urls)
        descriptions = text_list(item.get("description"))
        metadata = self._result_metadata(item, lccn)
        return ArchiveResult(
            provider_id=self.provider_id,
            provider_record_id=record_id or record_url,
            result_type=query.result_type if query.result_type != "all" else "text",
            title=first_text(item.get("title")) or "Untitled archive record",
            persistent_url=record_url,
            alternative_titles=text_list(item.get("other_title")),
            publication=first_text(item.get("partof_title")),
            collection=first_text(item.get("partof")),
            date=date,
            edition=edition,
            page=sequence,
            sequence=sequence,
            creators=text_list(item.get("contributor")),
            institution=first_text(item.get("location")),
            places=text_list(item.get("location")) + text_list(item.get("subject")),
            language=text_list(item.get("language")),
            description=" ".join(descriptions[:2]),
            ocr_snippet=" ".join(descriptions[:1]),
            thumbnail_url=thumbnail,
            assets=assets,
            iiif_manifest_url=self._find_manifest(item),
            rights=first_text(item.get("rights")),
            license=first_text(item.get("license")),
            access_restrictions=first_text(item.get("access_restricted")),
            provider_score=float(item.get("score") or 0.0),
            raw_metadata=metadata,
            matched_variant=query.original_query,
        )

    @staticmethod
    def _result_metadata(item, lccn):
        return {
            "identifiers": {"lccn": lccn},
            "word_coordinates_url": secure_public_url(
                first_text(item.get("word_coordinates_url"))
            ),
            "resources": item.get("resources", []),
            "original": item,
        }

    def resolve_assets(self, result, client, task_context=None) -> List[ArchiveAsset]:
        if result.assets:
            return result.assets
        record_url = secure_public_url(result.persistent_url)
        if not record_url:
            return []
        result.persistent_url = record_url
        payload = client.get_json(
            record_url,
            params={"fo": "json", "at": "item,resources"},
            max_bytes=12 * 1024 * 1024,
            task_context=task_context,
        )
        urls = list(flatten_urls(payload.get("resources", [])))
        return self._assets_from_urls(urls)

    def retrieve_ocr(self, result, client, task_context=None) -> str:
        url = secure_public_url(
            first_text(result.raw_metadata.get("word_coordinates_url"))
        )
        if not url:
            return result.ocr_snippet
        response = client.get_bytes(
            url,
            params={"fo": "json", "format": "json", "full_text": "1"},
            max_bytes=32 * 1024 * 1024,
            task_context=task_context,
        )
        content_type = response.headers.get("content-type", "").lower()
        text = response.content.decode("utf-8", errors="replace")
        if "json" not in content_type:
            return text.strip()
        try:
            return self._find_full_text(response.json()) or result.ocr_snippet
        except ValueError:
            return result.ocr_snippet

    def _assets_from_item(self, item, image_urls) -> List[ArchiveAsset]:
        urls = list(image_urls[-1:])
        urls.extend(flatten_urls(item.get("resources", [])))
        return self._assets_from_urls(urls)

    def _assets_from_urls(self, urls) -> List[ArchiveAsset]:
        assets = []
        seen = set()
        for index, url in enumerate(urls):
            if url in seen:
                continue
            seen.add(url)
            path = urlparse(url).path.lower()
            if path.endswith(".pdf"):
                kind, mime = "pdf", "application/pdf"
            elif re.search(r"\.(jpe?g|png|tiff?|webp)(?:$|\?)", url, re.I):
                kind, mime = "image", "image/jpeg"
            else:
                continue
            assets.append(ArchiveAsset(
                asset_id=f"loc-{index}",
                kind=kind,
                url=url,
                mime_type=mime,
                label="Page PDF" if kind == "pdf" else "Page image",
            ))
        return assets

    @staticmethod
    def _find_manifest(item) -> str:
        for url in flatten_urls(item):
            if "manifest" in url.lower() and "iiif" in url.lower():
                return url
        return ""

    @staticmethod
    def _find_full_text(value) -> str:
        if isinstance(value, dict):
            for key in ("full_text", "fulltext", "text", "ocr"):
                if key in value and isinstance(value[key], str):
                    return value[key].strip()
            for child in value.values():
                found = LibraryOfCongressProvider._find_full_text(child)
                if found:
                    return found
        elif isinstance(value, list):
            for child in value:
                found = LibraryOfCongressProvider._find_full_text(child)
                if found:
                    return found
        return ""

    @staticmethod
    def _record_identifier(url: str) -> str:
        return urlparse(url).path.strip("/").replace("/", ":")

    @staticmethod
    def _year(value: str) -> str:
        match = re.search(r"\d{4}", str(value or ""))
        return match.group(0) if match else ""

    @staticmethod
    def _and(first: str, second: str) -> str:
        return second if first == "*" else f"({first}) AND {second}"
