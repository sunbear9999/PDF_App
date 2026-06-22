import json
import re


def extract_inline_citations(text: str) -> tuple[bool, list]:
    block = _extract_tag_content(text, "CITATIONS")
    if not block:
        items = _extract_loose_citation_array(text)
        return (len(items) > 0), items

    try:
        parsed = json.loads(block, strict=False)
        items = _normalize_citation_items(parsed)
        if items:
            return True, items
    except json.JSONDecodeError:
        pass

    items = []
    for match in re.finditer(r'\{[^{}]*"doc_name"[^{}]*"quote"[^{}]*"note"[^{}]*\}', block, re.DOTALL):
        try:
            item = json.loads(match.group(0), strict=False)
        except json.JSONDecodeError:
            continue
        if isinstance(item, dict):
            items.append(item)

    return (len(items) > 0), items


def strip_inline_citation_block(text: str) -> str:
    if not isinstance(text, str):
        return text
    cleaned = re.sub(r"\s*<CITATIONS>.*?</CITATIONS>\s*", "", text, flags=re.DOTALL)
    loose_span = _loose_citation_span(cleaned)
    if loose_span:
        start, end = loose_span
        cleaned = (cleaned[:start] + cleaned[end:]).strip()
        cleaned = _strip_trailing_json(cleaned)
    return cleaned.strip()


def _extract_tag_content(text: str, tag_name: str) -> str:
    if not isinstance(text, str):
        return ""
    match = re.search(rf"<{tag_name}>(.*?)</{tag_name}>", text, re.DOTALL)
    return match.group(1).strip() if match else ""


def _normalize_citation_items(parsed) -> list:
    if isinstance(parsed, dict):
        parsed = parsed.get("citations", parsed)
    if isinstance(parsed, dict):
        parsed = [parsed]
    return [item for item in parsed if isinstance(item, dict)] if isinstance(parsed, list) else []


def _extract_loose_citation_array(text: str) -> list:
    span = _loose_citation_span(text)
    if not span:
        return []
    start, end = span
    try:
        parsed = json.loads(text[start:end], strict=False)
    except json.JSONDecodeError:
        return []
    return _normalize_citation_items(parsed)


def _loose_citation_span(text: str):
    if not isinstance(text, str):
        return None
    decoder = json.JSONDecoder(strict=False)
    for match in re.finditer(r"\[", text):
        start = match.start()
        try:
            parsed, offset = decoder.raw_decode(text[start:])
        except json.JSONDecodeError:
            continue
        items = _normalize_citation_items(parsed)
        if items and all(item.get("doc_name") and (item.get("quote") or item.get("text")) for item in items):
            return start, start + offset
    return None


def _strip_trailing_json(text: str) -> str:
    if not isinstance(text, str):
        return text
    stripped = text.rstrip()
    for match in list(re.finditer(r"[\[{]", stripped)):
        start = match.start()
        prefix = stripped[:start].rstrip()
        suffix = stripped[start:].strip()
        try:
            parsed, offset = json.JSONDecoder(strict=False).raw_decode(suffix)
        except json.JSONDecodeError:
            continue
        if offset == len(suffix) and isinstance(parsed, (dict, list)) and prefix:
            return prefix
    return text
