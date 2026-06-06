import json
import re


def extract_inline_citations(text: str) -> tuple[bool, list]:
    block = _extract_tag_content(text, "CITATIONS")
    if not block:
        return False, []

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
    return re.sub(r"\s*<CITATIONS>.*?</CITATIONS>\s*", "", text, flags=re.DOTALL).strip()


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
