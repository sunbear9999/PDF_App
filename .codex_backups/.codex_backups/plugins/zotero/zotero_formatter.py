"""
plugins/zotero/zotero_formatter.py

Maps Zotero item dicts (from ZoteroDB) to the app's citation dict format
and also produces BibTeX / RIS export strings.
"""
from __future__ import annotations

import re
from typing import Dict, List, Optional


class ZoteroFormatter:
    """Converts Zotero item dicts into formats understood by the rest of the app."""

    # ------------------------------------------------------------------
    # App citation dict format (matches CitationManager field names)
    # ------------------------------------------------------------------

    def to_citation_dict(self, item: Dict) -> Dict:
        """
        Convert a ZoteroDB item dict to the app's citation dict.
        doc_id will be 'zotero:{item_id}' so it can be round-tripped.
        """
        authors = self._format_authors_string(item.get("creators", []))
        vol = item.get("volume", "")
        issue = item.get("issue", "")
        vol_issue = f"{vol}({issue})" if vol and issue else vol or issue or ""
        journal = item.get("publicationTitle") or item.get("publisher", "")

        return {
            "doc_id": item.get("doc_id", f"zotero:{item.get('item_id', '')}"),
            "title": item.get("title", ""),
            "authors": authors,
            "year": item.get("year", ""),
            "journal": journal,
            "vol_issue": vol_issue,
            "doi": item.get("DOI", ""),
            "url": item.get("url", ""),
            "publisher": item.get("publisher", ""),
            "item_type": item.get("item_type", ""),
            "source": "zotero",
        }

    # ------------------------------------------------------------------
    # BibTeX export
    # ------------------------------------------------------------------

    def to_bibtex(self, item: Dict) -> str:
        """Generate a BibTeX entry string for a Zotero item."""
        item_type = item.get("item_type", "misc")
        bib_type = self._zotero_type_to_bibtex(item_type)
        key = self._make_bibtex_key(item)

        creators = item.get("creators", [])
        authors = [
            f"{c['last']}, {c['first']}" if c.get("first") else c.get("last", "")
            for c in creators
            if c.get("type") in ("author", "editor")
        ]

        fields: Dict[str, str] = {}
        fields["title"] = self._bib_escape(item.get("title", ""))
        if authors:
            fields["author"] = " and ".join(authors)
        if item.get("year"):
            fields["year"] = item["year"]
        pub = item.get("publicationTitle") or item.get("publisher", "")
        if pub:
            key_name = "journal" if "article" in item_type.lower() else "publisher"
            fields[key_name] = self._bib_escape(pub)
        if item.get("volume"):
            fields["volume"] = item["volume"]
        if item.get("issue"):
            fields["number"] = item["issue"]
        if item.get("pages"):
            fields["pages"] = item["pages"]
        if item.get("DOI"):
            fields["doi"] = item["DOI"]
        if item.get("url"):
            fields["url"] = item["url"]
        if item.get("ISBN"):
            fields["isbn"] = item["ISBN"]

        lines = [f"@{bib_type}{{{key},"]
        for k, v in fields.items():
            lines.append(f"  {k} = {{{v}}},")
        lines.append("}")
        return "\n".join(lines)

    # ------------------------------------------------------------------
    # RIS export
    # ------------------------------------------------------------------

    def to_ris(self, item: Dict) -> str:
        """Generate an RIS-format string for a Zotero item."""
        item_type = item.get("item_type", "misc")
        ris_type = self._zotero_type_to_ris(item_type)

        lines = [f"TY  - {ris_type}"]
        if item.get("title"):
            lines.append(f"TI  - {item['title']}")
        for c in item.get("creators", []):
            if c.get("type") in ("author", "editor"):
                name = f"{c.get('last', '')}, {c.get('first', '')}"
                lines.append(f"AU  - {name.strip(', ')}")
        if item.get("year"):
            lines.append(f"PY  - {item['year']}")
        pub = item.get("publicationTitle") or item.get("publisher", "")
        if pub:
            lines.append(f"JO  - {pub}")
        if item.get("volume"):
            lines.append(f"VL  - {item['volume']}")
        if item.get("issue"):
            lines.append(f"IS  - {item['issue']}")
        if item.get("pages"):
            lines.append(f"SP  - {item['pages']}")
        if item.get("DOI"):
            lines.append(f"DO  - {item['DOI']}")
        if item.get("url"):
            lines.append(f"UR  - {item['url']}")
        lines.append("ER  -")
        return "\n".join(lines)

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _format_authors_string(self, creators: List[Dict]) -> str:
        """Format creators list as 'Last, First; Last, First' for the app."""
        parts = []
        for c in creators:
            if c.get("type") not in ("author", "editor", "seriesEditor"):
                continue
            if c.get("last") and c.get("first"):
                parts.append(f"{c['last']}, {c['first']}")
            elif c.get("last"):
                parts.append(c["last"])
        return "; ".join(parts)

    def _make_bibtex_key(self, item: Dict) -> str:
        """Generate a BibTeX cite key like 'Smith2023empirical'."""
        creators = item.get("creators", [])
        last = (
            creators[0].get("last", "unknown")
            if creators
            else "unknown"
        )
        year = item.get("year", "")
        title_words = re.sub(r"[^a-zA-Z\s]", "", item.get("title", ""))
        first_word = (title_words.split() or [""])[0].lower()
        return re.sub(r"\s+", "", f"{last}{year}{first_word}")

    def _bib_escape(self, text: str) -> str:
        return text.replace("{", r"\{").replace("}", r"\}")

    def _zotero_type_to_bibtex(self, ztype: str) -> str:
        mapping = {
            "journalArticle": "article",
            "book": "book",
            "bookSection": "incollection",
            "conferencePaper": "inproceedings",
            "thesis": "phdthesis",
            "report": "techreport",
            "webpage": "misc",
            "magazineArticle": "article",
            "newspaperArticle": "article",
        }
        return mapping.get(ztype, "misc")

    def _zotero_type_to_ris(self, ztype: str) -> str:
        mapping = {
            "journalArticle": "JOUR",
            "book": "BOOK",
            "bookSection": "CHAP",
            "conferencePaper": "CONF",
            "thesis": "THES",
            "report": "RPRT",
            "webpage": "ELEC",
            "magazineArticle": "MGZN",
            "newspaperArticle": "NEWS",
        }
        return mapping.get(ztype, "GEN")
