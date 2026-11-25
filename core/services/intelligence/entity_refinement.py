"""Document-aware refinement of raw entity mentions (after recognition,
before resolution).

Statistical NER labels each sentence on its own. Many of its errors are only
visible with the whole document in view, so this stage applies explicit,
inspectable rules — every dropped or retyped mention records why:

* **Drop** non-entities: degrees (PhD, MsC), author-initial lists (MVZ, RFD),
  reporter/code abbreviations (U.S., U.S.C.), pieces of e-mail addresses and
  URLs, case-name parties ("Martinez" in *Santa Clara Pueblo v. Martinez*),
  lowercase spans, generic words (Court, Society, Background) and
  sentence-initial common words the document uses in lowercase.
* **Clean** surfaces: star-page markers ("*866the Bill of Rights"), "et al.".
* **Retype** with document evidence: an acronym the text defines takes its
  expansion's type (``Indian Civil Rights Act (ICRA)`` → LAW, ``major
  depressive disorder (MDD)`` → CONCEPT); institution keywords → ORG;
  street names → LOCATION; academic fields → CONCEPT; a bare surname used
  for a PERSON elsewhere in the document stays PERSON.
"""
from __future__ import annotations

import re
from collections import Counter
from typing import Dict, Iterable, List, Optional, Set

from core.models.intelligence_models import EntityMention
from core.services.intelligence.entity_document_consistency import (
    OcrNoise, glued_words, merge_spelling_variants, place_evidence, spelling_variants, type_consensus,
)
from core.services.intelligence.entity_extractor import ACRONYM_DEF_RE, acronym_expansion, normalize_key

VERSION = "1.2"
_PROPER = {"PERSON", "ORG", "LOCATION", "NORP", "EVENT", "WORK", "LAW", "PRODUCT", "FAC"}
_DEGREES = {"phd", "md", "ms", "msc", "bsc", "ba", "ma", "mph", "jd", "llm", "rn", "dphil", "esq", "mba", "edd"}
_REPORTERS = re.compile(r"^(?:U\.\s?S\.?(?:\s?C\.?)?(?:\s?(?:App|Supp|Ann)\.?)?|S\.?\s?Ct\.?|L\.?\s?Ed\.?(?:\s?2d)?|F\.?\s?(?:\d+d|Supp\.?|App'x)|"
                        r"C\.?F\.?R\.?|Stat\.?|Cir\.?|Fed\.?\s?Reg\.?|Pub\.?\s?L\.?|App\.?|Pet\.?|Cert\.?)$")
_GENERIC = {
    "court", "society", "government", "state", "states", "justice", "department", "committee", "board",
    "agency", "article", "section", "table", "figure", "background", "introduction", "conclusion",
    "abstract", "keywords", "opinion", "summary", "counsel", "petitioner", "petitioners", "respondent",
    "plaintiff", "defendant", "appellant", "appellee", "tribe", "nation", "circuit", "district",
    "amicus", "amici", "curiae", "circuit judges", "district judge", "judges",
}
_ORG_WORDS = re.compile(
    r"\b(?:universit\w*|universidade|departament\w*|department|institut\w*|faculdade|facult\w*|hospital|"
    r"cl[ií]nicas|school|college|cent(?:er|re|ro)|foundation|funda[cç][aã]o|program(?:me)?|association|"
    r"council|commission|committee|agency|bureau|ministry|tribunal|company|corporation|inc|ltd|llc|press|"
    r"laborator\w*|academy|organi[sz]ation|union|bank|administration|authority|network|"
    r"court|congress|parliament|senate|police|service)\b", re.I)
_ADDRESS = re.compile(r"^(?:rua|avenida|av\.|street|st\.|road|rd\.|avenue|boulevard|blvd\.?)\b|\b(?:street|avenue|road)$", re.I)
_LAW_END = re.compile(r"\b(?:acts?|codes?|statutes?|treaty|treaties|convention|constitution|amendments?|regulations?|"
                      r"directive|charter|ordinances?|bill of rights)$", re.I)
# Geographic heads: "Bighorn National Forest", "the Wyoming Territory".
_PLACE_END = re.compile(r"\b(?:national (?:forest|park|monument|wildlife refuge)|mountains?|river|lake|valley|county|"
                        r"territory|islands?|bay|peninsula|basin|desert|canyon|coast|province|prefecture)$", re.I)
_TITLE_WORD = re.compile(r"^(?:(?:Chief\s+)?Justice|Judge|Mr\.|Mrs\.|Ms\.|Dr\.|Prof\.|Professor|Senator|"
                         r"Representative|President|Governor|Secretary)\s+[A-Z]")
_TITLE_BEFORE = re.compile(r"\b(?:(?:Chief\s+)?Justice|Judge|Mr\.|Mrs\.|Ms\.|Dr\.|Prof\.|Professor|Senator|"
                           r"Governor)\s*$")
_INITIAL_NAME = re.compile(r"^(?:[A-Z]\.\s?){1,3}[A-Z][\w’'\-]+(?:\s+[A-Z][\w’'\-]+){0,3}$")
_EDITOR_AFTER = re.compile(r"^\s*,?\s*\(?eds?\.")
# "(Jung 2006)", "Smith et al., 2018", "Okamura 2008, 12": a cited author.
_YEAR_AFTER = re.compile(r"^(?:\s+et\s+al\.?)?,?\s*(?:1[5-9]|20)\d{2}[a-z]?\b")
_TRAILING_YEAR = re.compile(r",?\s*(?:1[5-9]|20)\d{2}[a-z]?$")
_ABBREVIATION_CHAIN = re.compile(r"^\.\s+[A-Z][a-z]{0,4}\.")
# Names of academic disciplines (generic vocabulary, not document-specific).
_FIELDS = {
    "artificial intelligence", "machine learning", "deep learning", "computer science", "data science",
    "natural language processing", "neuroscience", "psychiatry", "philosophy", "sociology",
    "game theory", "political science", "economics", "linguistics", "anthropology",
}
_EMAIL_URL = re.compile(r"\S+@\S+\.\w+|https?://\S+|www\.\S+|\b10\.\d{4,}/\S+")
_CASE_NAME = re.compile(r"(?:[A-Z][\w.&'’\-]*\s+){0,6}?[A-Z][\w.&'’\-]*,?\s+v\.\s+(?:[A-Z][\w.&'’\-]*[\s,]+){0,8}")
_STAR_PAGE = re.compile(r"^\*?\d+(?=[A-Za-z])")
_ET_AL = re.compile(r"\s+et\s+al\.?$")
_POSSESSIVE = re.compile(r"[’']s?$")
_TRAILING_LOWER = re.compile(r"(?<=\w)\s+(?!(?:de|da|van|von|der|del|la|le|du|bin|al)$)[a-z]{1,4}$")
_DEBRIS = re.compile(r"[\\•«»|*¦~^]")


def _ocr_debris(surface: str, known_word) -> bool:
    """Recognition debris: stray symbols inside a name, or an unknown token
    with a capital in mid-word ("cUfßenlt", "ADeceived") that no naming
    convention (Mc-, Mac-, De-, O'-) explains."""
    if _DEBRIS.search(surface):
        return True
    if known_word is None:
        return False
    for token in surface.split():
        camel = re.search(r"[a-zß][A-Z]|^[A-Z]{2}[a-z]{3,}", token)
        if camel and not re.match(r"^(?:Mc|Mac|De|Di|Da|La|Le|Van|Von|O['’])", token) and known_word(token) is False:
            return True
    return False


class EntityMentionRefiner:
    """Pure, deterministic; plugins may register extra extractors before it."""
    id = "entity_refinement"
    version = VERSION

    def refine(self, mentions: List[EntityMention], texts: Dict[str, str],
               captions: Optional[Set[str]] = None, known_word=None,
               ocr_sentences: Optional[Set[str]] = None) -> List[EntityMention]:
        """``texts``: sentence id → the exact text the mention offsets index.
        ``captions``: sentence ids of the document's own caption/title, whose
        case name names this document's parties. ``known_word``: the language
        model's vocabulary lookup; ``ocr_sentences``: sentence ids whose text
        was recognised from images (see entity_document_consistency)."""
        self._known_word = known_word
        variants = spelling_variants(mentions, known_word)
        self._noise = OcrNoise(ocr_sentences, known_word, variants)
        self._counts = Counter(m.surface_text.strip() for m in mentions)
        mentions = _joined(mentions, texts)
        context = _DocumentContext(mentions, texts)
        refined = []
        for mention in mentions:
            reason = self._drop_reason(mention, context, captions or set())
            if reason:
                continue
            self._retype(mention, context)
            refined.append(mention)
        place_evidence(refined, texts, _ORG_WORDS)
        type_consensus(refined)
        merge_spelling_variants(refined, variants)
        return _dedupe(refined)

    def _drop_reason(self, mention, context, captions) -> str:
        _clean(mention)
        surface = mention.surface_text.strip()
        key = surface.casefold().strip(".")
        if not surface or mention.entity_type not in _PROPER:
            return "" if surface else "empty"
        if key in _DEGREES or _REPORTERS.match(surface) or "·" in surface:
            return "not_an_entity"
        if (surface.isupper() and len(surface) <= 5 and surface in context.initials
                and surface not in context.acronyms and _author_initial_context(mention, context)):
            return "author_initials"
        if surface == surface.lower():
            return "lowercase"
        if key in _GENERIC:
            return "generic_word"
        if self._counts.get(surface, 0) < 3 and (glued_words(surface, getattr(self, "_known_word", None)) or _ocr_debris(surface, getattr(self, "_known_word", None))):
            return "ocr_noise"
        noise = getattr(self, "_noise", None)
        if noise is not None and noise.unreliable(mention, self._counts.get(surface, 0)):
            mention.metadata["ocr_uncertain"] = True
        if surface[:1].isdigit() and mention.entity_type in {"PERSON", "ORG", "LOCATION"}:
            return "not_an_entity"     # "7 Stat", "450 U.S."
        text = context.texts.get(mention.sentence_id, "")
        if _abbreviation_or_label(mention, text, context.lowercase_words):
            return "abbreviation_or_list_label"
        if _inside(mention, context.email_spans(mention.sentence_id)):
            return "email_or_url"
        if mention.sentence_id not in captions and _inside(mention, context.case_spans(mention.sentence_id)):
            return "case_name_party"
        if _case_short_form(mention, text, context.case_parties):
            return "case_short_form"
        if _sentence_initial_common_word(mention, text, context.lowercase_words):
            return "sentence_initial_common_word"
        return ""

    @staticmethod
    def _retype(mention, context) -> None:
        surface = mention.surface_text.strip()
        new = _type_from_evidence(surface, mention.entity_type, context, mention)
        if new and new != mention.entity_type:
            mention.metadata["retyped_from"] = mention.entity_type
            # A word that ends someone's full name is weak evidence: places end
            # names too ("... de Mérida"). Document-wide usage may outweigh it.
            weak = new == "PERSON" and (surface in context.surnames or surface in context.name_tails)
            mention.metadata["type_evidence"] = "weak" if weak else True
            mention.entity_type = new
            mention.normalized_key = normalize_key(surface, new)


class _DocumentContext:
    def __init__(self, mentions: List[EntityMention], texts: Dict[str, str]) -> None:
        self.texts = texts
        words = Counter(w for text in texts.values() for w in re.findall(r"\b[a-z][a-z\-]+\b", text))
        capitals = Counter(w.casefold() for text in texts.values() for w in re.findall(r"\b[A-Z][a-z\-]+\b", text))
        self.lowercase_words = {w for w, n in words.items() if n >= 2 and n > capitals[w]}
        self.acronyms = _acronym_types(texts.values())
        people = [m.surface_text for m in mentions if m.entity_type == "PERSON" and len(m.surface_text.split()) >= 2]
        self.surnames = {p.split()[-1].strip(".,") for p in people}
        names = [m.surface_text for m in mentions if m.entity_type in {"PERSON", "ORG"}
                 and len(m.surface_text.split()) >= 2 and not _ORG_WORDS.search(m.surface_text)]
        self.initials = {variant for name in names for variant in _initials(name)}
        self.by_sentence: Dict[str, List[EntityMention]] = {}
        for m in mentions:
            self.by_sentence.setdefault(m.sentence_id, []).append(m)
        self._emails: Dict[str, list] = {}
        self._cases: Dict[str, list] = {}
        self.case_parties = {party for text in texts.values() for party in _parties(text)}
        # "White Horses" is the tail of "James Many White Horses"
        self.name_tails = {" ".join(p.split()[k:]) for p in people if not _ORG_WORDS.search(p)
                           and not re.search(r"\b(?:de|da|do|das|dos|e|of|the)\b", p)
                           for k in range(1, len(p.split())) if p.split()[k][:1].isupper()}

    def near_person(self, mention) -> bool:
        """A PERSON mention within 30 characters in the same sentence."""
        return any(m is not mention and m.entity_type == "PERSON" and m.char_start is not None
                   and abs(m.char_start - (mention.char_end or 0)) <= 30
                   or (m is not mention and m.entity_type == "PERSON" and m.char_end is not None
                       and abs((mention.char_start or 0) - m.char_end) <= 30)
                   for m in self.by_sentence.get(mention.sentence_id, []))

    def followed_by_place(self, mention) -> bool:
        """The next mention after ", " is a LOCATION ("São Paulo, Brazil")."""
        later = [m for m in self.by_sentence.get(mention.sentence_id, [])
                 if m.char_start is not None and mention.char_end is not None
                 and 0 <= m.char_start - mention.char_end <= 3]
        return any(m.entity_type == "LOCATION" for m in later)

    def email_spans(self, sentence_id):
        if sentence_id not in self._emails:
            self._emails[sentence_id] = [m.span() for m in _EMAIL_URL.finditer(self.texts.get(sentence_id, ""))]
        return self._emails[sentence_id]

    def case_spans(self, sentence_id):
        if sentence_id not in self._cases:
            self._cases[sentence_id] = [m.span() for m in _CASE_NAME.finditer(self.texts.get(sentence_id, ""))]
        return self._cases[sentence_id]


def _joined(mentions: List[EntityMention], texts: Dict[str, str]) -> List[EntityMention]:
    from core.services.intelligence.entity_mention_completion import join_split_names
    by_sentence: Dict[str, List[EntityMention]] = {}
    for mention in mentions:
        by_sentence.setdefault(mention.sentence_id, []).append(mention)
    return [m for sid, found in by_sentence.items() for m in join_split_names(found, texts.get(sid, ""))]


def _author_initial_context(mention, context) -> bool:
    text = context.texts.get(mention.sentence_id, "")
    if mention.metadata.get("reference_context"):
        return True
    # Affiliation lists explicitly tie parenthesized author initials to an
    # institution. A matching sequence elsewhere may be a real acronym.
    start, end = mention.char_start, mention.char_end
    if start is None or end is None or not _ORG_WORDS.search(text):
        return False
    for match in re.finditer(r"\([^()]+\)", text):
        if match.start() < start and end < match.end():
            return bool(re.fullmatch(r"[A-Z.,;\s&]+", match[0][1:-1]))
    return False


def _initials(name: str) -> Set[str]:
    """"Leticia Lopes de Figueiredo" → {"LLF", "LLDF"}; "Marcus V. Zanetti" → {"MVZ"}."""
    words = re.findall(r"[^\W\d_][\w’'\-]*", name)
    capital = "".join(w[0] for w in words if w[0].isupper()).upper()
    every = "".join(w[0] for w in words).upper()
    return {capital, every}


def _dedupe(mentions: List[EntityMention]) -> List[EntityMention]:
    """One mention per (sentence, span, type): a retyped NER "MDD" and the
    pattern layer's CONCEPT "MDD" are the same observation."""
    best: Dict[tuple, EntityMention] = {}
    for mention in mentions:
        key = (mention.sentence_id, mention.char_start, mention.char_end, mention.entity_type)
        if key not in best or mention.confidence > best[key].confidence:
            best[key] = mention
    return [m for m in mentions if best.get((m.sentence_id, m.char_start, m.char_end, m.entity_type)) is m]


def _clean(mention: EntityMention) -> None:
    """Strip a glued star-page marker ("*866the Bill of Rights") and "et al."."""
    surface, start = mention.surface_text, mention.char_start
    star = _STAR_PAGE.match(surface)
    if star:
        lead = re.match(r"(?:the|The)\s+", surface[star.end():])
        cut = star.end() + (lead.end() if lead else 0)
        surface = surface[cut:]
        start = start + cut if start is not None else None
    surface = _ET_AL.sub("", surface)
    surface = _POSSESSIVE.sub("", surface)                 # "John Challoner's" → "John Challoner"
    if mention.entity_type == "PERSON":
        surface = _TRAILING_LOWER.sub("", surface)         # "Madge ho": a stray word after the name
    if mention.entity_type in {"PERSON", "ORG"} and _TRAILING_YEAR.search(surface) and \
            _TRAILING_YEAR.sub("", surface).strip():
        surface = _TRAILING_YEAR.sub("", surface).rstrip(" ,")       # "Smith, 2018" → "Smith"
    if surface != mention.surface_text:
        mention.surface_text = surface
        mention.char_start = start
        mention.char_end = start + len(surface) if start is not None else mention.char_end
        mention.normalized_key = normalize_key(surface, mention.entity_type)


def _inside(mention: EntityMention, spans) -> bool:
    start, end = mention.char_start, mention.char_end
    return start is not None and end is not None and any(a <= start and end <= b for a, b in spans)


_SHORT_FORM_AFTER = re.compile(r"^(?:,\s*(?:\d+\s+[A-Z][\w.]*|supra\b)|\s+(?:analysis|test|framework|rule|doctrine)\b)")


def _parties(text: str) -> Set[str]:
    """Party names of every "X v. Y" case name in a sentence."""
    parties = set()
    for match in re.finditer(r"([A-Z][\w.'’\-]*(?:\s+[A-Z][\w.'’\-]*){0,4})\s+v\.\s+"
                             r"([A-Z][\w.'’\-]*(?:\s+[A-Z][\w.'’\-]*){0,4})", text):
        for party in match.groups():
            words = party.split()
            parties.update(" ".join(words[k:]) for k in range(len(words)))   # "Minnesota v. Mille Lacs Band"
            parties.update(" ".join(words[:k]) for k in range(1, len(words) + 1))
    return parties


def _case_short_form(mention, text: str, parties: Set[str]) -> bool:
    """"Mille Lacs, 526 U.S. at 207", "the Mille Lacs analysis": a case
    referred to by a party name, not the party itself."""
    surface = mention.surface_text.strip()
    end = mention.char_end
    return end is not None and surface in parties and bool(_SHORT_FORM_AFTER.match(text[end:]))


def _abbreviation_or_label(mention, text: str, lowercase: Set[str]) -> bool:
    """"Ann." in "Ann. Rep.": a short word in a chain of abbreviations; "D.
    Attendance": an outline letter before a common word."""
    surface = mention.surface_text.strip()
    end = mention.char_end
    if " " not in surface and end is not None and len(surface) <= 5 and _ABBREVIATION_CHAIN.match(text[end:]):
        return True
    label = re.fullmatch(r"[A-Z]\.\s+([A-Z][a-z]+)", surface)
    return bool(label and label.group(1).lower() in lowercase)


def _sentence_initial_common_word(mention, text: str, lowercase: Set[str]) -> bool:
    """"Microglial activation…", "Background", "Interestingly": a single
    capitalised word that opens a sentence and appears in lowercase elsewhere
    in the document is a common word, not a name. People keep their names."""
    surface = mention.surface_text.strip()
    if " " in surface or mention.char_start is None or mention.entity_type == "PERSON" or surface.isupper():
        return False
    before = text[:mention.char_start].rstrip()
    at_start = not before or before[-1] in ".!?:(\"“"
    return at_start and surface.lower() in lowercase


_ACRONYM_FIRST = re.compile(r"\b([A-Z][A-Z0-9]{1,7})s?\s*\(([A-Z][\w\-]*(?:\s+[\w\-]+){0,8})\)")
_APPOSITIVE_ACRONYM = re.compile(r"\b((?:[A-Z][\w-]*\s+){1,6}[A-Z][\w-]*)(?:\s+(?:models?|methods?|systems?))?,\s+(?:or|hereafter)\s+([A-Z]{2,8})\b")


def _definitions(text: str):
    """(acronym, expansion) for "expansion (ACR)" and "ACR (Expansion)"."""
    for match in ACRONYM_DEF_RE.finditer(text):
        yield match.group(2).rstrip("s") if match.group(2).endswith("s") and match.group(2)[:-1].isupper() else match.group(2), \
            acronym_expansion(match.group(1), match.group(2))
    for match in _APPOSITIVE_ACRONYM.finditer(text):
        expansion = acronym_expansion(match[1], match[2])
        if expansion:
            yield match[2], expansion
    for match in _ACRONYM_FIRST.finditer(text):
        expansion = acronym_expansion(match.group(2), match.group(1))
        if expansion and expansion == match.group(2).strip():
            yield match.group(1), expansion


def _acronym_types(texts: Iterable[str]) -> Dict[str, str]:
    """Acronym → type implied by the expansion the document gives it."""
    return {acronym: etype for acronym, (etype, _expansion) in defined_acronyms(texts).items()}


def defined_acronyms(texts: Iterable[str]) -> Dict[str, tuple]:
    """Acronym → (type, expansion) for every acronym the document defines."""
    found: Dict[str, tuple] = {}
    for text in texts:
        for acronym, expansion in _definitions(text):
            if not expansion or len(acronym) < 2:
                continue
            if _LAW_END.search(expansion):
                found[acronym] = ("LAW", expansion)
            elif _ORG_WORDS.search(expansion):
                found[acronym] = ("ORG", expansion)
            elif acronym not in found:
                found[acronym] = ("CONCEPT", expansion)
    return found


_GROUPS = {"latinx", "latino", "latina", "latinos", "hispanic", "hispanics", "indigenous", "asian americans",
           "african americans", "native americans", "native hawaiian", "native hawaiians"}
_CREDENTIAL_AFTER = re.compile(r"^\s*,\s*(?:MD|PhD|MsC|MSc|MS|BSc|BA|MA|MPH|JD|DPhil|RN)\b")
_PLACE_AFTER = re.compile(r"^\s*,\s*[A-ZÀ-Þ]")


def _context_type(mention: EntityMention, current: str, context: "_DocumentContext") -> str:
    """Evidence from the words around the mention in its own sentence."""
    text = context.texts.get(mention.sentence_id, "")
    end, start = mention.char_end or 0, mention.char_start or 0
    after, before = text[end:end + 40], text[max(0, start - 40):start]
    surface = mention.surface_text.strip()
    if not _ORG_WORDS.search(surface) and not surface.isupper():
        if _CREDENTIAL_AFTER.match(after) or _EDITOR_AFTER.match(after):
            return "PERSON"        # "Marjorie Mastellaro Baruzzi, MD", "Nell Jessup Newton ed."
        if _TITLE_WORD.match(surface) or _TITLE_BEFORE.search(before):
            return "PERSON"        # "Justice Sotomayor", "Judge Smith"
        if current in {"ORG", "LOCATION", "WORK"} and _INITIAL_NAME.match(surface):
            return "PERSON"        # "J. Medicine Crow", "F. Hoxie"
        if current in {"ORG", "LOCATION", "NORP"} and _YEAR_AFTER.match(after) and len(surface.split()) <= 3 \
                and surface not in context.acronyms:
            return "PERSON"        # "(Jung 2006)": a cited author
    if _TITLE_WORD.match(surface):
        return "PERSON"            # "Justice SOTOMAYOR" (caps surname)
    if current in {"ORG", "LOCATION"} and " " not in mention.surface_text.strip() and (
            re.search(r"\band\s*$", before) or re.match(r"^\s*and\s+[A-ZÀ-Þ]", after)) and \
            mention.surface_text.strip() not in context.acronyms and context.near_person(mention):
        return "PERSON"            # "Norvig and Russell", "Kearns and Roth"
    surface = mention.surface_text.strip()
    if current in {"PERSON", "ORG"} and _PLACE_AFTER.match(after) and context.followed_by_place(mention) \
            and not _ORG_WORDS.search(surface) and not surface.isupper():
        return "LOCATION"          # "São Paulo, Brazil", "Cerqueira César, São Paulo"
    return ""


def _head_type(surface: str, current: str) -> str:
    """The name's own head word: "… National Forest" is a place, "… Code" a law."""
    bare = re.sub(r"^the\s+", "", surface, flags=re.I)
    if current in {"ORG", "PERSON", "WORK", "NORP"} and _PLACE_END.search(bare) and not _ORG_WORDS.search(bare):
        return "LOCATION"
    if current in {"ORG", "WORK", "EVENT", "PERSON", "CONCEPT"} and _LAW_END.search(bare) and len(bare.split()) >= 2:
        return "LAW"
    return ""


def _type_from_evidence(surface: str, current: str, context: _DocumentContext, mention=None) -> str:
    if surface in context.acronyms:
        return context.acronyms[surface]
    if surface.casefold() in _GROUPS:
        return "NORP"
    if current == "PERSON" and surface.isupper() and len(surface) <= 6:
        return "CONCEPT"           # "SCFA" is never a person
    found = _context_type(mention, current, context) if mention is not None else ""
    if found:
        return found
    found = _head_type(surface, current)
    if found:
        return found
    if re.fullmatch(r"[A-Z]{1,6}-?\d+[A-Za-zα-ω]*|[A-Z]+-\d+[αβγ]?", surface):
        return "CONCEPT"            # IL-6, LAG3, TLR4: technical identifiers
    if surface.casefold() in _FIELDS:
        return "CONCEPT"
    if _ADDRESS.search(surface):
        return "LOCATION"
    if current in {"PERSON", "LOCATION", "NORP", "EVENT"} and _ORG_WORDS.search(surface):
        return "ORG"
    if current in {"ORG", "LOCATION"} and (" " not in surface and surface in context.surnames
                                           or surface in context.name_tails):
        return "PERSON"
    if _LAW_END.search(surface) and current in {"ORG", "WORK", "EVENT"}:
        return "LAW"
    return current
