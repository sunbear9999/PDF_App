"""Text of an (X)HTML element as reflowable-format parsers should store it.

* Inline elements join without an invented space: ``<span>T</span>he`` is
  "The" (a drop cap), ``<span>Barton</span>, Director`` is "Barton,
  Director"; ``<br/>`` and source line breaks become a single space.
* Page-break markers that print editions leave inline ("[ 29 ]") are kept in
  the text but returned as furniture spans, the same contract PDF furniture
  uses: EPUB 3 ``epub:type="pagebreak"``, DPUB-ARIA ``role="doc-pagebreak"``
  and the common ``pagenum``/``pageno``/``page-number`` class names.
* An element inside ``<header>``, ``<footer>`` or ``<nav>``, or inside a
  container classed ``boilerplate``, is publisher furniture as a whole
  (e-book distributor headers and licences).
"""
from __future__ import annotations

import re
from typing import Any, List, Tuple

_PAGE_CLASS = re.compile(r"\bpage[-_]?(?:num|no|number|break|ref)\b|\bpagenum\b|\bpageno\b", re.I)
_FURNITURE_TAGS = {"header", "footer", "nav"}
_BOILERPLATE_CLASS = re.compile(r"boilerplate|colophon|licen[cs]e|transcriber", re.I)


def element_text(tag: Any) -> Tuple[str, List[List[Any]]]:
    """(normalized text, furniture spans [[start, end, label]])."""
    text, furniture, _ = element_content(tag)
    return text, furniture


def element_content(tag: Any):
    """Text, furniture and superscript spans, all in the same text coordinates.

    Keep typography separate from text: a formatted ASCII digit remains an
    ASCII digit in the source anchor. Citation extraction decides its role.
    """
    parts: List[str] = []
    spans: List[List[Any]] = []
    _walk(tag, parts, spans)
    raw = "".join(parts)
    text, mapped = _collapse(raw, spans)
    return (text, [s for s in mapped if s[2] != "superscript"],
            [s[:2] for s in mapped if s[2] == "superscript"])


def is_publisher_furniture(tag: Any) -> bool:
    for parent in [tag, *tag.parents]:
        name = getattr(parent, "name", None)
        if name in _FURNITURE_TAGS:
            return True
        classes = " ".join(parent.get("class") or []) if hasattr(parent, "get") else ""
        if classes and _BOILERPLATE_CLASS.search(classes):
            return True
    return False


def _walk(node: Any, parts: List[str], spans: List[List[Any]]) -> None:
    for child in getattr(node, "children", []):
        name = getattr(child, "name", None)
        if name is None:
            parts.append(str(child))
        elif name == "br":
            parts.append(" ")
        elif name in {"script", "style"}:
            continue
        elif name in {"p", "li", "div"}:
            parts.append(" ")
            _walk(child, parts, spans)
            parts.append(" ")
        elif _page_marker(child) or name == "sup":
            start = sum(len(p) for p in parts)
            _walk(child, parts, spans)
            label = "page_number" if _page_marker(child) else "superscript"
            spans.append([start, sum(len(p) for p in parts), label])
        else:
            _walk(child, parts, spans)


def _page_marker(tag: Any) -> bool:
    kinds = " ".join(filter(None, [str(tag.get("epub:type") or ""), str(tag.get("role") or "")]))
    classes = " ".join(tag.get("class") or [])
    return "pagebreak" in kinds or bool(_PAGE_CLASS.search(classes))


def _collapse(raw: str, spans: List[List[Any]]) -> Tuple[str, List[List[Any]]]:
    """Collapse whitespace runs to one space, strip, and remap span offsets."""
    out: List[str] = []
    position: List[int] = []          # raw offset → output offset
    previous_space = True
    for char in raw:
        position.append(len(out))
        if char.isspace():
            if not previous_space:
                out.append(" ")
            previous_space = True
        else:
            out.append(char)
            previous_space = False
    position.append(len(out))
    text = "".join(out)
    end = len(text.rstrip())
    mapped = []
    for start, stop, label in spans:
        a, b = position[start], position[stop]
        while a < b and text[a:a + 1] == " ":
            a += 1
        b = min(b, end)
        while b > a and text[b - 1:b] == " ":
            b -= 1
        if b > a:
            mapped.append([a, b, label])
    return text[:end], mapped
