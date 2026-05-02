# core/citation_manager.py
import re
import fitz
import json
import urllib.request
from PySide6.QtCore import QObject, Signal, QThread

class CitationManager(QObject):
    def __init__(self, project_manager):
        super().__init__()
        self.pm = project_manager
        self.current_style = "APA" # Default style

    def set_style(self, style):
        self.current_style = style

    def format_in_text(self, doc_id, page_num):
        data = self.pm.get_citation(doc_id)
        if not data:
            return f"(Unknown, Page {page_num + 1})"

        raw_authors = data.get("authors", "Unknown Author")
        
        # Split authors by semicolon or comma to isolate individuals
        import re
        author_list = [a.strip() for a in re.split(r'[;,]', raw_authors) if a.strip()]
        
        if not author_list:
            author_text = "Unknown Author"
        else:
            # Extract the last word of the first author's name
            first_author_parts = author_list[0].split()
            first_last_name = first_author_parts[-1] if first_author_parts else "Unknown"
            
            if len(author_list) == 1:
                author_text = first_last_name
            elif len(author_list) == 2:
                # Extract the last word of the second author's name
                second_author_parts = author_list[1].split()
                second_last_name = second_author_parts[-1] if second_author_parts else ""
                author_text = f"{first_last_name} and {second_last_name}"
            else:
                # 3 or more authors gets 'et al.'
                author_text = f"{first_last_name} et al."

        year = data.get("year", "n.d.")
        page = page_num + 1 if page_num is not None else ""

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
            
            authors = data.get("authors", "Unknown Author")
            year = data.get("year", "n.d.")
            title = data.get("title", "Untitled Document")
            journal = data.get("journal", "")
            
            if self.current_style == "APA":
                works.append(f"{authors}. ({year}). {title}. {journal}.")
            elif self.current_style == "MLA":
                works.append(f"{authors}. \"{title}.\" {journal}, {year}.")
            elif self.current_style == "Chicago":
                works.append(f"{authors}. \"{title}.\" {journal} ({year}).")
                
        # Sort alphabetically for Works Cited
        return sorted(works)

    def extract_metadata(self, doc_path):
        """Attempts to find a DOI in the first few pages and fetch metadata, falling back to PDF metadata."""
        try:
            doc = self.pm.get_doc(doc_path)
            if not doc: return {}

            # Fallback metadata from PDF properties
            meta = doc.metadata or {}
            result = {
                "doc_id": doc_path,
                "title": meta.get("title", ""),
                "authors": meta.get("author", ""),
                "year": meta.get("creationDate", "")[2:6] if meta.get("creationDate") else "",
                "journal": "",
                "doi": ""
            }

            # Search for DOI in the first 3 pages
            doi = None
            for i in range(min(3, len(doc))):
                text = doc.load_page(i).get_text()
                # Standard DOI Regex
                match = re.search(r'(10\.\d{4,9}/[-._;()/:A-Z0-9]+)', text, re.IGNORECASE)
                if match:
                    doi = match.group(1)
                    break

            if doi:
                result["doi"] = doi
                # Try fetching from Crossref
                try:
                    url = f"https://api.crossref.org/works/{doi}"
                    req = urllib.request.Request(url, headers={'User-Agent': 'PapyrusResearchApp/1.0'})
                    with urllib.request.urlopen(req, timeout=3) as response:
                        api_data = json.loads(response.read().decode())['message']
                        result["title"] = api_data.get("title", [result["title"]])[0]
                        
                        # Parse authors
                        if "author" in api_data:
                            authors = [f"{a.get('family', '')}, {a.get('given', '')}" for a in api_data["author"]]
                            result["authors"] = "; ".join(authors)
                            
                        # Parse year
                        if "issued" in api_data and "date-parts" in api_data["issued"]:
                            result["year"] = str(api_data["issued"]["date-parts"][0][0])
                            
                        result["journal"] = api_data.get("container-title", [""])[0]
                except Exception as e:
                    print(f"Crossref lookup failed for DOI {doi}: {e}")

            return result
        except Exception as e:
            print(f"Error extracting metadata: {e}")
            return {"doc_id": doc_path}