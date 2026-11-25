"""
Registry for reusable deterministic pattern packs.

Features and plugins register named pattern packs here instead of embedding
regex patterns directly in extractor code. All extractors query this registry
by entity type to get the patterns they need, so new patterns are available to
every consumer automatically.

Built-in packs are seeded in PapyrusCore.__init__() by calling
PatternDetectorRegistry.seed_builtin_packs().
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Pattern
from core.models.extraction_patterns import REGION_PATTERNS
from core.models.extraction_patterns import CUES


@dataclass
class PatternPack:
    """A named collection of regex patterns targeting one or more entity types."""
    pack_id: str
    pack_name: str
    raw_patterns: List[str]
    entity_types: List[str]     # entity type tags this pack applies to
    plugin_id: Optional[str] = None
    _compiled: List[Pattern] = field(default_factory=list, init=False, repr=False)

    def __post_init__(self) -> None:
        self._compiled = []
        for p in self.raw_patterns:
            try:
                self._compiled.append(re.compile(p))
            except re.error:
                pass

    @property
    def compiled(self) -> List[Pattern]:
        return self._compiled


class PatternDetectorRegistry:
    """
    Registry holding all deterministic pattern packs.

    Plugins and built-in code register packs here. Extractors query by
    entity_type to retrieve the relevant patterns without knowing their source.
    """

    def __init__(self) -> None:
        self._packs: Dict[str, PatternPack] = {}               # pack_id → pack
        self._by_type: Dict[str, List[str]] = {}               # entity_type → [pack_ids]

    # ------------------------------------------------------------------

    def register(self, pack: PatternPack) -> None:
        self._packs[pack.pack_id] = pack
        for et in pack.entity_types:
            self._by_type.setdefault(et, [])
            if pack.pack_id not in self._by_type[et]:
                self._by_type[et].append(pack.pack_id)

    def get_packs_for_type(self, entity_type: str) -> List[PatternPack]:
        return [
            self._packs[pid]
            for pid in self._by_type.get(entity_type, [])
            if pid in self._packs
        ]

    def get_pack(self, pack_id: str) -> Optional[PatternPack]:
        return self._packs.get(pack_id)

    def all_packs(self) -> List[PatternPack]:
        return list(self._packs.values())

    def remove_by_plugin(self, plugin_id: str) -> None:
        ids_to_remove = [
            pid for pid, p in self._packs.items() if p.plugin_id == plugin_id
        ]
        for pid in ids_to_remove:
            pack = self._packs.pop(pid)
            for et in pack.entity_types:
                lst = self._by_type.get(et, [])
                if pid in lst:
                    lst.remove(pid)

    # ------------------------------------------------------------------
    # Built-in pack seeding — called once at startup
    # ------------------------------------------------------------------

    def seed_builtin_packs(self) -> None:
        """Seed the registry with all built-in pattern packs."""

        from core.models.extraction_patterns import GENRE_CUES, NARRATIVE_ROLES
        for prefix, patterns in (("genre_", GENRE_CUES), ("narrative_", NARRATIVE_ROLES)):
            for name, pattern in patterns.items():
                self.register(PatternPack(prefix + name, name.title() + " cues", [pattern], [prefix + name]))
        from core.models.extraction_patterns import CROSS_REF_LABEL, HONORIFICS, LANGUAGE_STOPWORDS, NOMINAL_HEADS, USAGE_CUES
        self.register(PatternPack("nominal_heads", "Definite-description head nouns",
                                  [r"^(?:" + "|".join(NOMINAL_HEADS.split()) + r")$"], ["nominal_heads"]))
        for cue, pattern in USAGE_CUES.items():
            self.register(PatternPack("entity_usage_" + cue, cue.replace("_", " "), [pattern], ["entity_usage_" + cue]))
        for iso, words in LANGUAGE_STOPWORDS.items():
            self.register(PatternPack("language_stopwords_" + iso, iso + " function words",
                                      [r"(?i)^(?:" + "|".join(words.split()) + r")$"], ["language_stopwords_" + iso]))
        self.register(PatternPack("person_honorific", "Person titles", [HONORIFICS], ["person_honorific"]))
        self.register(PatternPack("cross_ref_label", "Document labels and citation short forms",
                                  [CROSS_REF_LABEL], ["cross_ref_label"]))
        from core.models.extraction_patterns import ROLE_MODIFIER, ROLE_TITLE
        self.register(PatternPack("role_title", "Titles and roles (Justice, Governor, batab)", [ROLE_TITLE], ["role_title"]))
        self.register(PatternPack("role_modifier", "Words that qualify a title (Chief, Associate, Deputy)",
                                  [ROLE_MODIFIER], ["role_modifier"]))
        from core.models.extraction_patterns import ABBREVIATION_KEY_INTRO, ABBREVIATION_TYPE_CUES
        self.register(PatternPack("abbreviation_key_intro", "Abbreviation key headings and introductions",
                                  [ABBREVIATION_KEY_INTRO], ["abbreviation_key_intro"]))
        for kind, pattern in ABBREVIATION_TYPE_CUES.items():
            self.register(PatternPack("abbreviation_type_" + kind, kind + " cues in abbreviation full names",
                                      [pattern], ["abbreviation_type_" + kind]))
        from core.services.extraction.cross_references import DEFAULT_PATTERN as CROSS_REF_MENTION
        self.register(PatternPack("cross_ref_mention", "Cross-references in running text (Table 7.2, note 12)",
                                  [CROSS_REF_MENTION], ["cross_ref_mention"]))
        for cue, pattern in CUES.items():
            self.register(PatternPack("entity_cue_" + cue, cue, [pattern], ["entity_cue_" + cue]))
        for kind, pattern in REGION_PATTERNS.items():
            self.register(PatternPack("region_" + kind, kind.title() + " headings",
                                      [pattern], ["region_" + kind]))

        # ---- Date patterns (7 tiers, priority order) ----
        self.register(PatternPack(
            pack_id="date_patterns",
            pack_name="Date Patterns",
            entity_types=["date", "timeline"],
            raw_patterns=[
                # Full named-month dates
                r"\b(?:January|February|March|April|May|June|July|August|September"
                r"|October|November|December)\s+\d{1,2},?\s+\d{4}\b",
                r"\b\d{1,2}\s+(?:January|February|March|April|May|June|July|August"
                r"|September|October|November|December)\s+\d{4}\b",
                # ISO date
                r"\b\d{4}-\d{2}-\d{2}\b",
                # Numeric slash dates
                r"\b\d{1,2}/\d{1,2}/\d{2,4}\b",
                # Month-year
                r"\b(?:January|February|March|April|May|June|July|August|September"
                r"|October|November|December)\s+\d{4}\b",
                # Year with preposition
                r"\b(?:in|since|by|after|before|until|from|circa|c\.)\s+(\d{4})\b",
                # Decades
                r"\b(?:the\s+)?(?:early\s+|late\s+|mid-?)?\d{4}s\b",
                # Bare 4-digit year (fallback)
                r"\b(1[0-9]{3}|20[0-2][0-9])\b",
            ],
        ))

        # ---- Numeric citation patterns ----
        self.register(PatternPack(
            pack_id="citation_numeric",
            pack_name="Numeric Citation Patterns",
            entity_types=["citation", "in_text_citation"],
            raw_patterns=[
                r"\[(\d+(?:[,\s]*\d+)*(?:\s*[-–]\s*\d+)*)\]",   # [1], [2,3], [4-6]
                r"\((\d+(?:[,\s]*\d+)*(?:\s*[-–]\s*\d+)*)\)",   # (1), (2,3)
                r"[¹²³⁰-⁹]+",               # superscript Unicode
            ],
        ))

        # ---- Author-year citation patterns ----
        self.register(PatternPack(
            pack_id="citation_author_year",
            pack_name="Author-Year Citation Patterns",
            entity_types=["citation", "in_text_citation"],
            raw_patterns=[
                r"\(([A-Z][a-zA-Z\-\']+(?:\s+et\s+al\.)?(?:\s*[&,]\s*[A-Z][a-zA-Z\-\']+)*"
                r"(?:,\s*\d{4})?)\)",                                  # (Smith 2020), (Smith & Jones, 2020)
                r"\b([A-Z][a-zA-Z\-\']+(?:\s+et\s+al\.)?)\s+\((\d{4})\)",  # Smith (2020)
            ],
        ))

        # ---- Bibliography section heading patterns ----
        self.register(PatternPack(
            pack_id="bibliography_headings",
            pack_name="Bibliography Heading Patterns",
            # Its own entity type: the "bibliography" type also carries content
            # packs (DOI/URL/arXiv), and matching those against a heading made
            # any line with an arXiv id start the reference section.
            entity_types=["bibliography_heading"],
            raw_patterns=[
                r"^(?:references?|works?\s+cited|bibliography|bibliograph[yi]|"
                r"sources?|literature\s+cited|"
                r"further\s+reading)\s*$",
            ],
        ))

        # ---- Author name patterns ----
        self.register(PatternPack(
            pack_id="author_name",
            pack_name="Author Name Patterns",
            entity_types=["person", "author"],
            raw_patterns=[
                r"\b(?:Dr\.?|Prof\.?|Mr\.?|Mrs\.?|Ms\.?|Rev\.?|Hon\.?)?\s*"
                r"[A-Z][a-z]+(?:\s+[A-Z][a-z]+){1,3}\b",
            ],
        ))

        # ---- DOI / URL / ISBN patterns ----
        self.register(PatternPack(
            pack_id="doi_url",
            pack_name="DOI and URL Patterns",
            entity_types=["bibliography", "citation"],
            raw_patterns=[
                r"\b10\.\d{4,}/\S+",                      # DOI
                r"https?://\S+",                           # URL
                r"arxiv[:\s]+\d+\.\d+",                    # arXiv
                r"(?:ISBN|ISSN)[:\s-]+[\d\-Xx]+",         # ISBN/ISSN
            ],
        ))

        # ---- Legal citation patterns ----
        self.register(PatternPack(
            pack_id="legal_citation",
            pack_name="Legal Citation Patterns",
            entity_types=["citation", "legal"],
            raw_patterns=[
                r"\d+\s+[A-Z][a-z]+\.?\s*\d+",            # 42 U.S.C. 1983
                r"\b(?:id\.|ibid\.|supra|infra|hereinafter)\b",
                r"\d+\s+[A-Z\.]+\s+(?:at\s+)?\d+",        # 123 F.3d at 456
            ],
        ))

        # ---- Footnote / endnote patterns ----
        self.register(PatternPack(
            pack_id="footnote_endnote",
            pack_name="Footnote and Endnote Patterns",
            entity_types=["citation", "footnote"],
            raw_patterns=[
                r"^[¹²³⁰-⁹\d]+\s+\S",   # starts with superscript/num
                r"^\s*\d+\.\s+[A-Z]",                             # "1. Smith 2020..."
            ],
        ))

        # ---- Causal / argument markers ----
        self.register(PatternPack(
            pack_id="causal_marker",
            pack_name="Causal Marker Patterns",
            entity_types=["argument", "causal"],
            raw_patterns=[
                r"\b(?:therefore|thus|hence|consequently|as\s+a\s+result|because|"
                r"since|due\s+to|owing\s+to|leads?\s+to|caused?\s+by|results?\s+in)\b",
            ],
        ))

        self.register(PatternPack(
            pack_id="argument_marker",
            pack_name="Argument Marker Patterns",
            entity_types=["argument"],
            raw_patterns=[
                r"\b(?:however|nevertheless|nonetheless|although|despite|"
                r"furthermore|moreover|in\s+contrast|on\s+the\s+other\s+hand|"
                r"in\s+addition|by\s+contrast|conversely|yet|but)\b",
            ],
        ))

        # ---- Data / statistical markers ----
        self.register(PatternPack(
            pack_id="data_statistical",
            pack_name="Data and Statistical Marker Patterns",
            entity_types=["data", "statistics"],
            raw_patterns=[
                r"\b\d+(?:\.\d+)?%",                       # percentages
                r"\bp\s*[<>=]\s*0\.\d+\b",                 # p-values
                r"\bn\s*=\s*\d+\b",                         # sample sizes
                r"\b(?:CI|confidence\s+interval|OR|RR|HR|SD|SE|IQR)\b",
                r"\b(?:mean|median|average|range|minimum|maximum)\s*[=:]\s*[\d\.\-]+",
            ],
        ))
