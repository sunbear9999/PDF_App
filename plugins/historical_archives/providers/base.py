"""Provider protocol and shared normalization helpers."""
from __future__ import annotations

import html
import re
from typing import List, Protocol
from urllib.parse import urlparse

from ..models import (
    ArchiveAsset,
    ArchiveQuery,
    ArchiveResult,
    ProviderCapabilities,
    ProviderSearchSchema,
)


class ArchiveProvider(Protocol):
    provider_id: str
    label: str
    capabilities: ProviderCapabilities
    search_schema: ProviderSearchSchema

    def search(self, query: ArchiveQuery, client, task_context=None) -> List[ArchiveResult]:
        ...

    def resolve_assets(self, result: ArchiveResult, client, task_context=None) -> List[ArchiveAsset]:
        ...

    def retrieve_ocr(self, result: ArchiveResult, client, task_context=None) -> str:
        ...


def first_text(value) -> str:
    if isinstance(value, list):
        return first_text(value[0]) if value else ""
    if isinstance(value, dict):
        for key in ("en", "none", "@value", "value", "label"):
            if key in value:
                return first_text(value[key])
        return ""
    return str(value or "").strip()


def text_list(value) -> List[str]:
    if value is None:
        return []
    values = value if isinstance(value, list) else [value]
    return [first_text(item) for item in values if first_text(item)]


def clean_text(value) -> str:
    raw = first_text(value)
    raw = html.unescape(re.sub(r"<[^>]+>", " ", raw))
    return re.sub(r"\s+", " ", raw).strip()


def secure_public_url(value) -> str:
    """Return an HTTPS URL, upgrading provider-supplied HTTP links safely."""
    raw = str(value or "").strip()
    try:
        parsed = urlparse(raw)
    except ValueError:
        return ""
    if not parsed.hostname or parsed.username or parsed.password:
        return ""
    if parsed.scheme.lower() == "https":
        return raw
    if parsed.scheme.lower() == "http":
        return parsed._replace(scheme="https").geturl()
    return ""


def flatten_urls(value):
    if isinstance(value, str):
        secured = secure_public_url(value)
        if secured:
            yield secured
    elif isinstance(value, list):
        for item in value:
            yield from flatten_urls(item)
    elif isinstance(value, dict):
        for item in value.values():
            yield from flatten_urls(item)
