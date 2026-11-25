"""Declarative extraction vocabulary shared by services and pattern registries."""

CUES = {
    "LOCATION_before": r"(?i)\b(?:city|town|village|province|district|municipality|parish|cabildo|cah)\s+of\s+$",
    "NORP_after": r"(?i)^\s+(?:people|speakers|communities|population|ethnic\s+group)\b",
    "LANGUAGE_after": r"(?i)^\s+(?:language|dialect)\b",
    "LANGUAGE_before": r"(?i)\b(?:written|spoken|translated|speaking)\s+(?:in|into)\s+$",
    "PERSON_before": r"(?i)\b(?:dr|prof|mr|mrs|fray|don|doña|padre|bishop|governor|judge|justice)\.?\s+$",
}


GENRE_CUES = {
    'legal': r'\b(?:petitioner|respondent|appellant|court of appeals|summary judgment|U\. S\. C\.)\b',
    'scientific': r'\b(?:randomized|confidence interval|participants|p\s*[<=]|regression|clinical|experiment)\b',
    'humanities': r'\b(?:archival|historian|notarial|manuscript|colonial|medieval|chronicle|historical)\w*\b',
}
NARRATIVE_ROLES = {
    'claim': r'\b(?:(?:evidence|pattern|record|finding|comparison|practice)\w* .{0,35}(?:suggests?|indicates?|reflects?|points? to)|I argue|it is likely that|this (?:shows|suggests|indicates) that)\b',
    'evidence': r'\b(?:(?:records?|archives?|letters?|wills?|testimonies|documents?) .{0,45}(?:show|record|state|reveal|describe|mention|attest)\w*|(?:appears?|recorded|documented) in (?:the |an? )?(?:record|archive|register))\b',
    'counterclaim': r'\b(?:some (?:scholars|historians) .{0,25}(?:argue|claimed|maintain)|contrary to (?:the |this )?(?:interpretation|argument|view))\b',
    'limitation': r'\b(?:(?:assumption|interpretation|reconstruction|evidence) .{0,35}(?:requires? qualification|is incomplete|remains? uncertain)|we can only (?:guess|speculate))\b',
}


# A document part with its number: "Table 7.4", "Fig. 2", "Appendix E", "§ 1983", "Part II".
DOCUMENT_LABEL = (
    r"(?:(?:table|tab|fig(?:ure)?|appendix|app|chapter|chap|part|section|sect?|map|plate|note|nn?|"
    r"exhibit|ex)(?:\.\s*|\s+)|§{1,2}\s*)(?:\d+[a-z]?(?:[.\-–]\d+[a-z]?)*|[ivxlc]+|[a-z])"
)
# A whole surface that names a part of the document or a citation short form
# ("Table 7.4", "Appendix E", "supra note 12", "Ibid."). These are cross-reference
# targets, never entities.
CROSS_REF_LABEL = (
    r"(?i)^(?:" + DOCUMENT_LABEL +
    r"|(?:supra|infra)(?:\s+(?:note|n\.)\s*\d+)?|ibid\.?|ibidem|id\.|op\.\s*cit\.?|loc\.\s*cit\.?)\s*$"
)
# A caption or reference that opens with a document label ("TABLE 4.1 Evolution of ...").
LABELLED_CAPTION = r"(?i)^" + DOCUMENT_LABEL + r"(?=[\s).,:;—–-])"

# Frequent function words per language (ISO 639). They vote on the language of
# a sentence; plugins add languages with a "language_stopwords_<iso>" pack.
LANGUAGE_STOPWORDS = {
    "en": "the of and to in is that was for it with as on by be are this from at which were have has not or an but",
    "es": "el la los las de del y que en un una por con para es se al lo su como más pero sus le ya fue",
    "fr": "le la les des du de et est un une dans que qui pour pas sur au aux ce cette par avec sont il elle",
    "de": "der die das und ist nicht ein eine zu den von mit sich des auf für im dem auch es wird",
    "pt": "o os as do da dos das e que em um uma para com não por mais se na no ao foi",
    "it": "il lo gli le di del della e che è un una per non con si da nel sono",
    "la": "et in est non ad cum quod qui quae sed ut esse ab per sunt atque enim etiam",
    "haw": "ka ke o i a na ma ia me e la no mai aku ua nei lakou oia keia kela ana hoi ae aole loa",
}

# How a name is used across a document (rates, so they do not grow with length).
USAGE_CUES = {
    # "in Mérida", "from the Yucatan": the name is a place.
    "locative_before": r"(?i)\b(?:in|at|near|from|to|into|across|through|toward|towards|around|throughout)\s+(?:the\s+)?$",
    # "Pedro, who", "Landa wrote", "don Juan": the name is a person.
    "person_after": r"(?i)^(?:,\s*(?:who|whose)\b|\s+(?:said|says|wrote|writes|argued|argues|died|married|claimed|"
                    r"testified|replied|was born|himself|herself))",
    "person_before": r"(?i)\b(?:don|doña|fray|dr|mr|mrs|ms|sir|prof|professor|governor|bishop|justice|judge|padre|"
                     r"saint|st|king|queen|president|senator)\.?\s+$",
    # An author-date citation follows ("in Lockhart 1992: 176", "Farriss (1984)"): the
    # preposition introduces a work, not a place. Neutral evidence: archives are
    # also cited by place ("Ixil 1766"), so it only cancels the locative reading.
    "citation_after": r"^\s*(?:,\s*|\(\s*)?(?:1[5-9]|20)\d\d[a-z]?\s*(?:[:;),]|$)",
    # Words after a name that do not make it attributive ("Maya is", "Maya and").
    "not_attributive": r"(?i)^(?:and|or|but|of|in|to|is|was|were|are|be|been|had|has|have|who|which|that|as|for|with|by|on|"
                       r"at|from|the|a|an|than|then|did|does|do|also|himself|herself|said|wrote|could|would|should|"
                       r"may|might|must|can|will|not|were|its|his|her|their)$",
}

# Head nouns that a definite description ("the Tribe", "the Act") shares with
# the full name it refers back to ("Crow Tribe", "Voting Rights Act").
NOMINAL_HEADS = (
    "Act Agency Agreement Army Band Bank Board Bureau Charter Church Code Commission Committee Company "
    "Compact Constitution Convention Corporation Council Court Department Government Institute Island "
    "Museum Nation Navy Ordinance Party Regulation Reservation River Statute Treaty Tribe University Valley"
)

HONORIFICS = r'(?i)^(?:(?:dr|prof|mr|mrs|ms|sir|hon|justice|judge|fray|don|doña|padre|bishop|governor|professor)\.?\s+)+'


REGION_PATTERNS = {
    "contents": r"(?i)^\s*(?:table\s+of\s+contents|contents|sommaire|table\s+des\s+matières|inhalt|inhaltsverzeichnis|contenido[s]?)\s*[.:]?\s*$",
    "notes": r"(?i)^\s*(?:(?:end|foot)?notes|notas|anmerkungen)(?:\s+(?:to|for|on)\s+(?:chapter|pages?)\s+.+)?\s*[.:]?\s*$",
    "references": r"(?i)^\s*(?:references(?:\s+cited)?|works\s+cited|bibliograph[yi]|bibliographie|bibliografía|literature\s+cited)\s*[.:]?\s*$",
    "index": r"(?i)^\s*(?:index|subject\s+index|name\s+index|índice\s+analítico)\s*[.:]?\s*$",
    "glossary": r"(?i)^\s*(?:glossary|glossaire|glosario)\s*[.:]?\s*$",
    "appendix": r"(?i)^\s*(?:appendix|appendices|annexe|apéndice)(?:\s+[A-Z0-9]+)?\s*[.:]?\s*$",
}


# An abbreviation key: a heading or an introducing phrase ("The following abbreviations
# for archives ... are used", "Abbreviations: AD, Alzheimer's disease; ...").
ABBREVIATION_KEY_INTRO = (r"(?im)^\s*(?:list\s+of\s+)?abbreviations\b|\bthe\s+following\s+abbreviations\b"
                          r"|\babbreviations\s+(?:used|for)\b")
# What a full name says about the key's type ("Archivo ..." is an organisation, "Testaments of
# Ixil" a work). Plugins add types with an "abbreviation_type_<TYPE>" pack.
ABBREVIATION_TYPE_CUES = {
    "ORG": (r"(?i)\b(?:archiv\w*|library|biblioteca|collection|colecci\w*|museum|institut\w*|centro|centre|center"
            r"|universit\w*|department|ministry|society|office|court|council|commission|agency|bureau)\b"),
    "WORK": (r"(?i)\b(?:testament\w*|titles?|documents?|documentos?|papers|book|chronicles?|codex|records"
             r"|manuscripts?|letters|census|register|reports?|journal|diary|protocol\w*)\b"),
}

# Titles and roles ("Justice", "Chief Justice", "Governor", "batab"): a name made only of these
# is a TITLE, and a person name opening with them splits into title + person. Plugins add
# words with a "role_title" pack (one alternation of words).
ROLE_TITLE_WORDS = (
    "justice justices judge judges magistrate president presidents senator senators representative congressman "
    "congresswoman governor governors mayor secretary minister commissioner attorney solicitor ambassador "
    "general admiral colonel major captain lieutenant sergeant king queen emperor empress prince princess duke "
    "duchess count countess marquis baron lord bishop archbishop cardinal pope priest father friar fray padre "
    "pastor reverend rabbi imam chief chairman chairwoman chairperson director professor dean chancellor provost "
    "sheriff marshal clerk alcalde alcaldes regidor regidores escribano cacique caciques batab batabob teniente "
    "gobernador encomendero viceroy virrey corregidor oidor treasurer prosecutor counsel")
ROLE_MODIFIER_WORDS = ("chief associate deputy vice acting assistant senior junior former late first presiding "
                       "district circuit supreme high grand lord")
ROLE_TITLE = r"(?i)^(?:" + "|".join(ROLE_TITLE_WORDS.split()) + r")$"
ROLE_MODIFIER = r"(?i)^(?:" + "|".join(ROLE_MODIFIER_WORDS.split()) + r")$"
