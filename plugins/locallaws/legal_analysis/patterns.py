"""Patterns for reading court opinions: authorities cited, opinion structure and legal reasoning.

Written for U.S.-style opinions and citation forms (reporters, U.S.C., C.F.R., constitutional
provisions), with the common cues judges use to state questions, rules, applications,
holdings and dispositions. Every pattern is a named constant so it can be read and tested.
"""
import re

# ---- authorities -------------------------------------------------------------------------
_PARTY = r"(?:[A-Z][\w'’.&-]*(?:\s+(?:of|the|and|for|de|ex\s+rel\.|[A-Z][\w'’.&-]*)){0,7})"
CASE_NAME = re.compile(r"(?<![\w.])(" + _PARTY + r")\s+v\.\s+(" + _PARTY + r")")
REPORTER = re.compile(r"\b(\d{1,4})\s+((?:U\.\s?S\.|S\.\s?Ct\.|L\.\s?Ed\.(?:\s?2d)?|F\.(?:\s?(?:2d|3d|4th))?|F\.\s?Supp\.(?:\s?(?:2d|3d))?"
                      r"|F\.\s?App'?x|[A-Z][a-z]{0,3}\.(?:\s?(?:2d|3d|4th))?|P\.(?:\s?(?:2d|3d))?|A\.(?:\s?(?:2d|3d))?|N\.[EW]\.(?:\s?(?:2d|3d))?"
                      r"|S\.[EW]\.(?:\s?2d)?|So\.(?:\s?(?:2d|3d))?))\s+(\d{1,6})")
STATUTE = re.compile(r"\b(\d{1,3})\s+(U\.\s?S\.\s?C\.(?:\s?A\.)?|C\.\s?F\.\s?R\.)\s*(?:§§?|[Ss]ec(?:tion)?\.?)\s*([\d\w.()–-]+)")
CONSTITUTION = re.compile(r"\bU\.\s?S\.\s+Const(?:itution|\.)?,?\s+(?:[Aa]rt\.|[Aa]rticle|[Aa]mend\.|[Aa]mendment)\s*[IVXLC\d]+(?:,\s*§\s*\d+)?(?:,\s*cl\.\s*\d+)?")
NAMED_ACT = re.compile(r"\b(?:the\s+)?((?:[A-Z][\w'’-]+\s+){1,8}(?:Act|Code|Treaty|Amendment|Clause)(?:\s+of\s+\d{4})?)\b")

SIGNAL = re.compile(r"(?:\b(See also|See generally|See, e\.\s?g\.|See|But see|But cf\.|Cf\.|Compare|Accord|E\.\s?g\.|Contra)\b[,]?\s*)$")
TREATMENT = {
    "overruled": re.compile(r"\b(?:overrul(?:ed|ing|e)|abrogat(?:ed|ing))\b", re.I),
    "distinguished": re.compile(r"\bdistinguish(?:ed|ing|able)?\b", re.I),
    "followed": re.compile(r"\b(?:follow(?:ed|ing)|rel(?:ied|y|ying) (?:on|upon)|reaffirm(?:ed|ing))\b", re.I),
    "questioned": re.compile(r"\b(?:question(?:ed|ing)|disapprov(?:ed|ing)|cast doubt)\b", re.I),
    "limited": re.compile(r"\blimit(?:ed|ing) (?:to its facts|its holding)\b", re.I),
}

# ---- opinion structure -------------------------------------------------------------------
_JUSTICE = r"(?:CHIEF\s+JUSTICE|JUSTICE|Chief\s+Justice|Justice|Judge|JUDGE)"
OPINION_OF_COURT = re.compile(_JUSTICE + r"\s+([A-Z][A-Za-z'’-]+)\s+delivered\s+the\s+opinion\s+of\s+the\s+Court")
# A separate opinion opens its first sentence: "CHIEF JUSTICE ROBERTS, with whom JUSTICE ALITO ... join,
# and with whom JUSTICE THOMAS joins except as to footnote 9, dissenting."
SEPARATE_OPINION = re.compile(r"^\W{0,3}" + _JUSTICE + r"\s+([A-Z][A-Za-z'’-]+)((?:,\s+with\s+whom\b[^.]{0,300}?)?),?\s+"
                              r"(concurring(?:\s+in\s+(?:part|the\s+judgment)(?:\s+and\s+dissenting\s+in\s+part)?)?|dissenting(?:\s+in\s+part)?)\b")
JOINER = re.compile(r"(?:THE\s+CHIEF\s+JUSTICE|The\s+Chief\s+Justice|(?:JUSTICE|Justice)\s+([A-Z][A-Za-z'’-]+))")
TREATMENT_DISTANCE = 80      # characters between an authority and the word saying how it is treated
PANEL_OPINION = re.compile(r"^([A-Z][A-Za-z'’-]+),\s+(?:Senior\s+|Chief\s+)?(?:Circuit|District|Appellate)\s+Judge(?:,\s+(dissenting|concurring))?:")
PER_CURIAM = re.compile(r"^\s*(?:PER\s+CURIAM|Per\s+Curiam)\b")

# ---- reasoning (IRAC-style roles) --------------------------------------------------------
REASONING = {
    "issue": re.compile(r"\b(?:(?:the\s+)?questions?\s+(?:presented|before\s+(?:us|the\s+Court)|in\s+this\s+case)\s+(?:is|are)"
                        r"|we\s+granted\s+certiorari\s+to\s+(?:decide|resolve|consider|determine)|this\s+case\s+(?:asks|presents\s+the\s+question)"
                        r"|(?:at|in)\s+issue\s+(?:here|in\s+this\s+case)\s+is|the\s+issue\s+(?:here\s+)?is\s+whether)\b", re.I),
    "rule": re.compile(r"\b(?:under\s+(?:the\s+)?(?:[\w'’-]+\s+){0,5}(?:test|standard|doctrine|framework|canon|rule)\b|the\s+(?:test|standard)\s+(?:is|asks|requires)"
                       r"|standard\s+of\s+review|de\s+novo|abuse\s+of\s+discretion|clear(?:ly\s+erroneous|\s+error)|strict\s+scrutiny|rational[-\s]basis"
                       r"|intermediate\s+scrutiny|substantial\s+evidence|(?:a|the)\s+(?:court|plaintiff|party|State|defendant)\s+must"
                       r"|(?:requires|demands)\s+(?:that|a\s+showing)|it\s+is\s+(?:well[-\s])?settled\s+that)\b", re.I),
    "application": re.compile(r"^(?:Here|In\s+this\s+case|Applying\s+(?:that|this|these|the)|Under\s+(?:that|this)\s+(?:standard|test|framework)"
                              r"|Under\s+these\s+circumstances|On\s+(?:this|that)\s+record)\b"),
    "holding": re.compile(r"\b(?:(?:we|the\s+Court|this\s+Court)\s+(?:therefore\s+|now\s+|accordingly\s+|thus\s+)?(?:hold|holds|held|conclude|concludes)\s+that"
                          r"|^Held:)", re.I),
}
DISPOSITION = re.compile(r"(?:\b(?:judgment\s+(?:of\s+the\s+[\w\s.]+?\s+)?is|we|is\s+hereby|are)\s+(?:therefore\s+|accordingly\s+)?"
                         r"(affirmed|reversed|vacated|remanded)\b|^\s*(Affirmed|Reversed|Vacated|Remanded|AFFIRMED|REVERSED|VACATED|REMANDED)\b"
                         r"|\b(affirm|reverse|vacate)\s+(?:the\s+judgment|and\s+remand)\b|\bIt\s+is\s+so\s+ordered\b)")

# ---- does the document read as a court opinion? -------------------------------------------
LEGAL_MARKERS = re.compile(r"\b(?:petitioner|respondent|appellant|appellee|certiorari|opinion\s+of\s+the\s+Court|Circuit\s+Judge"
                           r"|plaintiffs?|defendants?|U\.\s?S\.\s?C\.|delivered\s+the\s+opinion|dissenting|concurring|remanded?)\b")
MIN_LEGAL_SIGNALS = 8        # markers + citations a document needs before it is read as an opinion
