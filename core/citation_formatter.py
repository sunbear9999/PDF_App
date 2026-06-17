from __future__ import annotations

import re
from typing import Any, Dict, Iterable, List, Tuple


class CitationFormatter:
    """Formats flexible citation records for display, clipboard, and bibliographies."""

    def __init__(self, style: str = "APA"):
        self.style = style

    def set_style(self, style: str) -> None:
        self.style = style or "APA"

    def parse_authors(self, raw_authors: Any) -> List[Tuple[str, str]]:
        if isinstance(raw_authors, list):
            raw_authors = "; ".join(
                self._creator_to_text(author) for author in raw_authors if author
            )
        raw_authors = str(raw_authors or "")
        if not raw_authors or raw_authors.lower() in {"unknown", "n/a"}:
            return []

        raw_list = [a.strip() for a in re.split(r";| and ", raw_authors) if a.strip()]
        parsed = []
        for author in raw_list:
            if "," in author:
                parts = author.split(",", 1)
                parsed.append((parts[0].strip(), parts[1].strip()))
            else:
                parts = author.split()
                if len(parts) > 1:
                    parsed.append((parts[-1], " ".join(parts[:-1])))
                else:
                    parsed.append((parts[0], ""))
        return parsed

    def format_in_text(self, record: Dict[str, Any], page_num=None) -> str:
        if not record:
            return f"(Unknown, Page {page_num + 1})"
        authors = self.parse_authors(record.get("authors") or record.get("creators"))
        year = record.get("year") or self._year_from_date(record.get("date")) or "n.d."
        page = page_num + 1 if page_num is not None else ""

        if not authors:
            author_text = (record.get("title") or "Unknown Source")[:15] + "..."
        elif len(authors) == 1:
            author_text = authors[0][0]
        elif len(authors) == 2:
            author_text = (
                f"{authors[0][0]} & {authors[1][0]}"
                if self.style == "APA"
                else f"{authors[0][0]} and {authors[1][0]}"
            )
        else:
            author_text = f"{authors[0][0]} et al."

        if self.style == "APA":
            return f"({author_text}, {year}, p. {page})" if page else f"({author_text}, {year})"
        if self.style == "MLA":
            return f"({author_text} {page})" if page else f"({author_text})"
        if self.style == "Chicago":
            return f"({author_text} {year}, {page})" if page else f"({author_text} {year})"
        return f"({author_text}, {year})"

    def format_entry(self, record: Dict[str, Any]) -> str:
        if not record:
            return ""
        authors = self.parse_authors(record.get("authors") or record.get("creators"))
        year = record.get("year") or self._year_from_date(record.get("date")) or "n.d."
        title = record.get("title") or "Untitled Document"
        journal = record.get("journal") or record.get("publicationTitle") or ""
        vol_issue = record.get("vol_issue") or self._volume_issue(record)
        publisher = record.get("publisher") or ""
        doi_url = record.get("doi") or record.get("DOI") or record.get("url") or ""

        auth_str = self._format_authors_for_bibliography(authors)
        if not auth_str.endswith("."):
            auth_str += "."

        if self.style == "MLA":
            citation = f'{auth_str} "{title}." '
            if journal:
                citation += f"*{journal}*, "
                if vol_issue:
                    citation += f"{vol_issue}, "
                citation += f"{year}. "
            elif publisher:
                citation += f"{publisher}, {year}. "
            else:
                citation += f"{year}. "
        elif self.style == "Chicago":
            citation = f'{auth_str} "{title}." '
            if journal:
                citation += f"*{journal}*"
                if vol_issue:
                    citation += f" {vol_issue}"
                citation += f" ({year}). "
            elif publisher:
                citation += f"{publisher}, {year}. "
            else:
                citation += f"{year}. "
        else:
            citation = f"{auth_str} ({year}). {title}. "
            if journal:
                citation += f"*{journal}*"
                if vol_issue:
                    citation += f", {vol_issue}"
                citation += ". "
            elif publisher:
                citation += f"{publisher}. "

        if doi_url:
            citation += str(doi_url)
        return re.sub(r"\s+", " ", citation).strip()

    def format_entries(self, records: Iterable[Dict[str, Any]]) -> List[str]:
        return sorted(c for c in (self.format_entry(record) for record in records) if c)

    def _format_authors_for_bibliography(self, authors: List[Tuple[str, str]]) -> str:
        if not authors:
            return "Unknown Author."
        if self.style in {"APA", "Chicago"}:
            formatted = [
                f"{last}, {first[0]}." if first else last
                for last, first in authors
            ]
        else:
            formatted = [
                f"{last}, {first}".strip(", ")
                for last, first in authors
            ]
        if len(formatted) == 1:
            return formatted[0]
        if len(formatted) == 2:
            return f"{formatted[0]}, and {formatted[1]}"
        return f"{formatted[0]}, et al."

    def _creator_to_text(self, creator: Any) -> str:
        if isinstance(creator, dict):
            if creator.get("last") or creator.get("first"):
                return f"{creator.get('last', '')}, {creator.get('first', '')}".strip(", ")
            if creator.get("lastName") or creator.get("firstName"):
                return f"{creator.get('lastName', '')}, {creator.get('firstName', '')}".strip(", ")
            return str(creator.get("name", ""))
        return str(creator)

    def _volume_issue(self, record: Dict[str, Any]) -> str:
        volume = record.get("volume") or ""
        issue = record.get("issue") or ""
        return f"{volume}({issue})" if volume and issue else volume or issue

    def _year_from_date(self, value: Any) -> str:
        match = re.search(r"\d{4}", str(value or ""))
        return match.group(0) if match else ""
