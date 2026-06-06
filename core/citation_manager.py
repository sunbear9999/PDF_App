import re
import urllib.request
import json

class CitationManager:
    def __init__(self, project_manager):
        self.pm = project_manager
        self.current_style = "APA"

    def set_style(self, style):
        self.current_style = style

    def _parse_authors(self, raw_authors):
        """Intelligently parses messy author strings into standardized (Last, First) tuples."""
        if not raw_authors or raw_authors.lower() in ["unknown", "n/a"]:
            return []
            
        # Split by common delimiters
        raw_list = [a.strip() for a in re.split(r'[;,&]| and ', raw_authors) if a.strip()]
        parsed = []
        
        for author in raw_list:
            if ',' in author:
                # Assume "Last, First"
                parts = author.split(',', 1)
                parsed.append((parts[0].strip(), parts[1].strip()))
            else:
                # Assume "First Last"
                parts = author.split()
                if len(parts) > 1:
                    parsed.append((parts[-1], " ".join(parts[:-1])))
                else:
                    parsed.append((parts[0], ""))
        return parsed

    def format_in_text(self, doc_id, page_num):
        data = self.pm.get_citation(doc_id)
        if not data: return f"(Unknown, Page {page_num + 1})"

        authors = self._parse_authors(data.get("authors", ""))
        year = data.get("year", "n.d.")
        page = page_num + 1 if page_num is not None else ""

        if not authors:
            author_text = data.get("title", "Unknown Source")[:15] + "..."
        elif len(authors) == 1:
            author_text = authors[0][0]
        elif len(authors) == 2:
            author_text = f"{authors[0][0]} & {authors[1][0]}" if self.current_style == "APA" else f"{authors[0][0]} and {authors[1][0]}"
        else:
            author_text = f"{authors[0][0]} et al."

        if self.current_style == "APA":
            return f"({author_text}, {year}, p. {page})" if page else f"({author_text}, {year})"
        elif self.current_style == "MLA":
            return f"({author_text} {page})" if page else f"({author_text})"
        elif self.current_style == "Chicago":
            return f"({author_text} {year}, {page})" if page else f"({author_text} {year})"
        return ""

    def format_works_cited(self, doc_ids):
        works = []
        for doc_id in doc_ids:
            data = self.pm.get_citation(doc_id)
            if not data: continue
            
            authors = self._parse_authors(data.get("authors", ""))
            year = data.get("year", "n.d.")
            title = data.get("title", "Untitled Document")
            journal = data.get("journal", "")
            vol_issue = data.get("vol_issue", "")
            publisher = data.get("publisher", "")
            doi_url = data.get("doi", "")
            
            # Format Authors
            if not authors:
                auth_str = "Unknown Author."
            else:
                if self.current_style in ["APA", "Chicago"]:
                    formatted = [f"{last}, {first[0]}." if first else last for last, first in authors]
                else: # MLA
                    formatted = [f"{last}, {first}" for last, first in authors]
                
                if len(formatted) == 1: auth_str = formatted[0]
                elif len(formatted) == 2: auth_str = f"{formatted[0]}, and {formatted[1]}"
                else: auth_str = f"{formatted[0]}, et al."
            
            if not auth_str.endswith('.'): auth_str += "."

            # Construct Citation
            if self.current_style == "APA":
                cit = f"{auth_str} ({year}). {title}. "
                if journal: cit += f"*{journal}*, {vol_issue}. "
                elif publisher: cit += f"{publisher}. "
                if doi_url: cit += doi_url
                works.append(cit.strip())
                
            elif self.current_style == "MLA":
                cit = f"{auth_str} \"{title}.\" "
                if journal: cit += f"*{journal}*, {vol_issue}, {year}. "
                elif publisher: cit += f"{publisher}, {year}. "
                if doi_url: cit += doi_url
                works.append(cit.strip())
                
            elif self.current_style == "Chicago":
                cit = f"{auth_str} \"{title}.\" "
                if journal: cit += f"*{journal}* {vol_issue} ({year}). "
                elif publisher: cit += f"{publisher}, {year}. "
                if doi_url: cit += doi_url
                works.append(cit.strip())
                
        return sorted(works)

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