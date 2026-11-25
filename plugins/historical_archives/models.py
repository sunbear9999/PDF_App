"""Normalized provider-independent models for Historical Archives."""
from __future__ import annotations

import hashlib
import json
import re
from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Optional, Tuple


@dataclass(frozen=True)
class ProviderCapabilities:
    metadata_search: bool = True
    full_text_search: bool = False
    newspaper_search: bool = False
    date_filter: bool = False
    location_filter: bool = False
    publication_filter: bool = False
    title_filter: bool = False
    creator_filter: bool = False
    subject_filter: bool = False
    language_filter: bool = False
    boolean_search: bool = False
    thumbnails: bool = False
    ocr_snippets: bool = False
    complete_ocr: bool = False
    page_images: bool = False
    pdf_download: bool = False
    iiif_manifest: bool = False
    pagination: bool = False
    stable_urls: bool = True
    structured_rights: bool = False
    manifest_import: bool = False


@dataclass(frozen=True)
class SearchOption:
    value: str
    label: str


@dataclass(frozen=True)
class ProviderSearchSchema:
    """Declarative fields and choices understood by one archive API."""
    fields: Tuple[str, ...] = ()
    result_types: Tuple[SearchOption, ...] = ()
    sort_options: Tuple[SearchOption, ...] = ()
    location_options: Tuple[str, ...] = ()
    collection_options: Tuple[str, ...] = ()
    query_help: str = ""
    accepts_manifest_url: bool = False


@dataclass(frozen=True)
class QueryVariant:
    text: str
    reason: str
    enabled: bool = True


@dataclass(frozen=True)
class ArchiveQuery:
    original_query: str
    raw_query: str = ""
    exact_phrase: str = ""
    all_words: str = ""
    any_words: str = ""
    excluded_words: str = ""
    title: str = ""
    creator: str = ""
    subject: str = ""
    date_from: str = ""
    date_to: str = ""
    location: str = ""
    publication: str = ""
    language: str = ""
    result_type: str = "newspaper"
    sort: str = "relevance"
    max_results: int = 25
    page: int = 1
    provider_ids: List[str] = field(default_factory=list)
    variants: List[QueryVariant] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, value: Dict[str, Any]) -> "ArchiveQuery":
        data = dict(value)
        data["variants"] = [
            item if isinstance(item, QueryVariant) else QueryVariant(**item)
            for item in data.get("variants", [])
        ]
        return cls(**data)


@dataclass(frozen=True)
class ArchiveAsset:
    asset_id: str
    kind: str
    url: str
    mime_type: str = ""
    label: str = ""
    size: Optional[int] = None
    page_num: Optional[int] = None
    download_allowed: bool = True
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass
class ArchiveResult:
    provider_id: str
    provider_record_id: str
    result_type: str
    title: str
    persistent_url: str
    alternative_titles: List[str] = field(default_factory=list)
    publication: str = ""
    collection: str = ""
    date: str = ""
    date_end: str = ""
    edition: str = ""
    page: str = ""
    sequence: str = ""
    creators: List[str] = field(default_factory=list)
    institution: str = ""
    places: List[str] = field(default_factory=list)
    coordinates: Optional[List[float]] = None
    language: List[str] = field(default_factory=list)
    description: str = ""
    ocr_snippet: str = ""
    matching_terms: List[str] = field(default_factory=list)
    thumbnail_url: str = ""
    assets: List[ArchiveAsset] = field(default_factory=list)
    iiif_manifest_url: str = ""
    rights: str = ""
    license: str = ""
    access_restrictions: str = ""
    provider_score: float = 0.0
    local_score: float = 0.0
    match_reasons: List[str] = field(default_factory=list)
    raw_metadata: Dict[str, Any] = field(default_factory=dict)
    matched_variant: str = ""
    duplicate_of: str = ""

    @property
    def provider_key(self) -> str:
        return f"{self.provider_id}:{self.provider_record_id}"

    @property
    def fingerprint(self) -> str:
        identifiers = self.raw_metadata.get("identifiers", {})
        stable = (
            identifiers.get("lccn")
            or identifiers.get("identifier")
            or self.iiif_manifest_url
            or self.persistent_url
        )
        if stable:
            value = f"{self.provider_id}|{stable}|{self.page}|{self.sequence}"
        else:
            title = re.sub(r"\W+", " ", self.title.lower()).strip()
            value = f"{title}|{self.publication.lower()}|{self.date}|{self.page}"
        return hashlib.sha256(value.encode("utf-8")).hexdigest()

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, value: Dict[str, Any]) -> "ArchiveResult":
        data = dict(value)
        data["assets"] = [
            item if isinstance(item, ArchiveAsset) else ArchiveAsset(**item)
            for item in data.get("assets", [])
        ]
        return cls(**data)


@dataclass(frozen=True)
class SearchBatch:
    results: List[ArchiveResult]
    errors: Dict[str, str]
    variants: List[QueryVariant]

    def to_dict(self) -> Dict[str, Any]:
        return {
            "results": [item.to_dict() for item in self.results],
            "errors": dict(self.errors),
            "variants": [asdict(item) for item in self.variants],
        }


def stable_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
