import re
import urllib.request
import json
from core.citation_formatter import CitationFormatter

class CitationManager:
    def __init__(self, project_manager):
        self.pm = project_manager
        self.current_style = "APA"
        self.formatter = CitationFormatter(self.current_style)

    def set_style(self, style):
        self.current_style = style
        self.formatter.set_style(style)

    def _parse_authors(self, raw_authors):
        """Intelligently parses messy author strings into standardized (Last, First) tuples."""
        return self.formatter.parse_authors(raw_authors)

    def format_in_text(self, doc_id, page_num):
        data = self.pm.get_citation(doc_id)
        return self.formatter.format_in_text(data, page_num)

    def format_works_cited(self, doc_ids):
        return self.formatter.format_entries(
            self.pm.get_citation(doc_id) for doc_id in doc_ids
        )

    def format_entry(self, citation_data):
        return self.formatter.format_entry(citation_data or {})

    def extract_metadata(self, doc_path):
        """Extracts metadata locally, falls back to CrossRef if online."""
        doc = self.pm.get_doc(doc_path)
        if not doc: return {"doc_id": doc_path}

        meta = doc.metadata or {}
        result = {
            "doc_id": doc_path,
            "title": meta.get("title", ""),
            "authors": meta.get("author", ""),
            "year": meta.get("creationDate", "")[2:6] if meta.get("creationDate") else "",
            "journal": "",
            "vol_issue": "",
            "publisher": "",
            "doi": ""
        }

        # Fast DOI regex scan (first 3 pages)
        doi = None
        for i in range(min(3, len(doc))):
            match = re.search(r'(10\.\d{4,9}/[-._;()/:A-Z0-9]+)', doc.load_page(i).get_text(), re.IGNORECASE)
            if match:
                doi = match.group(1)
                break

        if doi:
            result["doi"] = doi
            # Graceful network fallback for offline environments
            try:
                url = f"https://api.crossref.org/works/{doi}"
                req = urllib.request.Request(url, headers={'User-Agent': 'PapyrusResearchApp/1.0'})
                with urllib.request.urlopen(req, timeout=2) as response:
                    api_data = json.loads(response.read().decode())['message']
                    result["title"] = api_data.get("title", [result["title"]])[0]
                    
                    if "author" in api_data:
                        authors = [f"{a.get('family', '')}, {a.get('given', '')}" for a in api_data["author"]]
                        result["authors"] = "; ".join(authors)
                        
                    if "issued" in api_data and "date-parts" in api_data["issued"]:
                        result["year"] = str(api_data["issued"]["date-parts"][0][0])
                        
                    result["journal"] = api_data.get("container-title", [""])[0]
                    result["publisher"] = api_data.get("publisher", "")
                    
                    vol = api_data.get("volume", "")
                    issue = api_data.get("issue", "")
                    if vol and issue: result["vol_issue"] = f"{vol}({issue})"
                    elif vol: result["vol_issue"] = vol
            except Exception:
                pass # Completely silent fail for offline use

        return result
