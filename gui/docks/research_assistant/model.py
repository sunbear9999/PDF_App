# gui/docks/research_assistant/model.py
import urllib.parse
import re
from PySide6.QtCore import QSettings

class ResearchModel:
    def __init__(self):
        self.settings = QSettings("PDFMultitool", "Workspace")
        # Default fallback is Wikipedia
        self._custom_url_template = self.settings.value(
            "custom_search_url", 
            "https://en.wikipedia.org/wiki/Special:Search?search={term}"
        )

    def get_custom_url_template(self):
        return self._custom_url_template

    def set_custom_url_template(self, url):
        self._custom_url_template = url
        self.settings.setValue("custom_search_url", url)

    def _format_boolean_query(self, term):
        """Standardizes operators and spacing for basic searches."""
        term = re.sub(r'\b(and)\b', 'AND', term, flags=re.IGNORECASE)
        term = re.sub(r'\b(or)\b', 'OR', term, flags=re.IGNORECASE)
        term = re.sub(r'\b(not)\b', 'NOT', term, flags=re.IGNORECASE)
        return urllib.parse.quote_plus(term)

    def get_jstor_url(self, term):
        """
        Parses a boolean string and maps it to JSTOR's specific Advanced Search parameters.
        Example target: doAdvancedSearch?q0=test&c1=AND&q1=meow&c2=OR&q2=amongus
        """
        # 1. Standardize operators to uppercase
        clean_term = re.sub(r'\b(and)\b', 'AND', term, flags=re.IGNORECASE)
        clean_term = re.sub(r'\b(or)\b', 'OR', clean_term, flags=re.IGNORECASE)
        clean_term = re.sub(r'\b(not)\b', 'NOT', clean_term, flags=re.IGNORECASE)
        
        # 2. Split the string by the operators, keeping the operators in the list
        tokens = re.split(r'\s+(AND|OR|NOT)\s+', clean_term)
        
        # Fallback: If no boolean operators are used, or if there are parentheses 
        # (which break simple q0/c1 linear mapping), use the basic search endpoint.
        if len(tokens) == 1 or "(" in term or ")" in term:
            encoded_term = urllib.parse.quote_plus(clean_term)
            return f"https://www.jstor.org/action/doBasicSearch?Query={encoded_term}"

        # 3. Build the advanced URL
        base_url = "https://www.jstor.org/action/doAdvancedSearch?acc=on&so=rel"
        params = []
        
        q_idx = 0 # query index (q0, q1, q2)
        c_idx = 1 # connector index (c1, c2)
        
        for token in tokens:
            token = token.strip()
            if not token:
                continue
                
            if token in ["AND", "OR", "NOT"]:
                params.append(f"c{c_idx}={token}")
                c_idx += 1
            else:
                encoded_val = urllib.parse.quote_plus(token)
                params.append(f"q{q_idx}={encoded_val}")
                params.append(f"f{q_idx}=all")
                q_idx += 1
                
        if params:
            return base_url + "&" + "&".join(params)
            
        # Ultimate fallback
        return f"https://www.jstor.org/action/doBasicSearch?Query={urllib.parse.quote_plus(clean_term)}"

    def get_scholar_url(self, term):
        encoded_term = self._format_boolean_query(term)
        return f"https://scholar.google.com/scholar?q={encoded_term}"

    def get_custom_url(self, term):
        """Relies on basic URL injection to guarantee stability."""
        encoded_term = self._format_boolean_query(term)
        if "{term}" in self._custom_url_template:
            return self._custom_url_template.replace("{term}", encoded_term)
        return f"{self._custom_url_template}{encoded_term}"
    def get_google_url(self, term):
        """Generates a standard Google search URL for manual queries."""
        encoded_term = self._format_boolean_query(term)
        return f"https://www.google.com/search?q={encoded_term}"