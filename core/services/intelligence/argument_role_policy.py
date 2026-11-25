"""Shared argument eligibility and interpretable role features.

A sentence is retained in the source index even when it is not an argument.
Role scores are heuristic strengths, not calibrated model probabilities.
Contrast words, years and citation numbers alone never establish a role.
"""
from __future__ import annotations

import re
from typing import Any

from core.services.extraction.legal_citation_patterns import LEGAL_REPORTER_RE, STATUTE_RE
from core.services.extraction.author_year_citations import author_year_mentions

VERSION = "5.2"
ROLES = ("claim", "evidence", "reasoning", "methodology", "counterclaim", "limitation")
_WORDS = re.compile(r"[^\W\d_]+", re.U)
_BOILERPLATE = re.compile(
    r"^(?:copyright|all rights reserved|court:|filed\b|argued and submitted|"
    r"before:|appeal from|for (?:plaintiff|defendant|appellant|appellee))|"
    r"summary constitutes no part|prepared by court staff|for the convenience of the reader",
    re.I,
)
_PREDICATE = re.compile(
    r"\b(?:is|are|was|were|be|been|has|have|had|do|does|did|can|could|"
    r"may|might|must|shall|should|will|would|cannot|\w+ed|"
    r"(?:dis)?agree[sd]?|concurs?|dissents?|"
    r"said|wrote|found|saw|knew|led|left|made|took|gave|became|began|"
    r"rose|fell|grew|lost|won|brought|built|chose|drew|felt|kept|meant|"
    r"paid|ran|sent|sold|stood|taught|thought|told|understood|"
    r"argues?|holds?|retains?|remains?|requires?|provides?|prohibits?|"
    r"appears?|attests?|indicates?|shows?|illustrates?|demonstrates?|concludes?|supports?|reflects?|points?|"
    r"means?|allows?|enables?|prevents?|violates?|undermines?|refutes?|contradicts?|"
    r"exceeds?|conflicts?|infringes?|precludes?|operates?|depends?|suggests?|improves?|reports?|describes?|covers?|contends?|asserts?|segments?|scores?|rejects?|responds?)\b", re.I,
)
_RULES = {
    "claim": re.compile(
        r"\b(?:should|must|shall|argue[sd]? that|argue[sd]? (?:that )?it|claims? that|we propose|conclud\w+ that|"
        r"contend\w+ that|assert\w+ that|we (?:hold|believe|think)|our (?:central )?thesis|"
        r"it is clear|suggest(?:s|ed|ing)? that|show(?:s|ed|ing)? that|"
        r"demonstrat(?:e[sd]?|ing) that|indicat(?:e[sd]?|ing) that|"
        r"(?:is|are) (?:not )?(?:essential|necessary|permissible|unlawful|reasonable)|"
        r"(?:does not|cannot|did not) (?:expire|terminate|preclude|infringe|exceed|conflict)|"
        r"retains? (?:inherent )?(?:sovereign )?authority|no longer good law|(?:is|are|remain) essential|is likely to|was the main driver|cannot be trusted|is not tantamount|we (?:view|construe)|(?:argument|interpretation) is (?:not )?persuasive)\b", re.I),
    "evidence": re.compile(
        r"\b(?:results? (?:show|indicat|reveal)|(?:study|survey|data|records?|"
        r"letters?|treaty|statute|agreement|act|notices?|report|court) .{0,45}"
        r"(?-i:found|show\w*|report\w*|state[sd]|observ\w*|reserv\w*|provid\w*|describ\w*|require\w*|explained|recognized|specified)|"
        r"we observed|according to|experiment\w* (?:found|show)|"
        r"achieved|outperformed|measured|replications?|census|"
        r"table \d+ reports|(?:court|circuit) (?:held|explained|recognized|concluded)|(?:congress|the act|the treaty) (?:enacted|extended|reserved|provided)|(?:record|statute|treaty) (?:states|says|reads))\b", re.I),
    "reasoning": re.compile(
        r"\b(?:because|therefore|thus|hence|consequently|as a result|"
        r"it follows|for (?:this|these) reasons?|due to|owing to|"
        r"given that|implies? that|explains? why|leads? to|"
        r"allows?|enables?|prevents?|consistent with|"
        r"since (?!\d{4}\b)[^,.]{5,60}(?=,))\b", re.I),
    "methodology": re.compile(
        r"\b(?:we (?:conducted|coded|sampled|estimated|collected|annotated|"
        r"trained|tested|repeated|used)|(?:data|samples?) (?:were|was) collected|"
        r"randomly assigned|(?:model|algorithm) (?:was |is )?(?:fine-tuned|trained)|"
        r"(?:transcript|article|sentence)s? (?:was|were) annotated|"
        r"(?:our approach|the (?:algorithm|procedure)) (?:combines|first|involved)|"
        r"using (?:a |an )?(?:difference-in-differences|stratified|random sampling))\b", re.I),
    "counterclaim": re.compile(
        r"\b(?:(?:critics?|opponents?|skeptics?|the dissent|dissenting|others|some scholars|a competing view|proponents of the older theory)"
        r".{0,55}(?:argu\w*|contend\w*|maintain\w*|claim\w*|reject\w*|believ\w*)|"
        r"disagree\w* with|challenge[sd]? (?:the |this |that )?"
        r"(?:claim|argument|view|assumption)|(?:we|the authors|this evidence|these results) "
        r"(?:refut\w+|contradict\w+)|(?:claim|argument|view|hypothesis) "
        r"(?:is|was) not supported|neither argument is persuasive|contrary to our hypothesis|(?:contrary reading|interpretation|argument) .{0,35}(?:cannot be reconciled|not persuasive)|(?:dissent|opponents?) .{0,30}fails? to recognize)\b", re.I),
    "limitation": re.compile(
        r"\b(?:limitation|limited by|caveat|future work|remains? unclear|"
        r"cannot rule out|(?:sample|record|evaluation|analysis|study|model|results?|"
        r"conclusions?|method|respondents) .{0,55}(?:incomplete|uncertain|tentative|small|"
        r"covers? only|does not address|not tested|prevents? us|may differ)|"
        r"(?:sample|evaluation|study|analysis) .{0,30}(?:limited to|covers only)|"
        r"(?:method|model) cannot (?:represent|detect|handle)|"
        r"reliance on self-reported|these estimates are uncertain)\b", re.I),
}
_QUANTITY = re.compile(
    r"\d+(?:[.,]\d+)*\s*(?:%|percent\b|per cent\b|"
    r"(?:respondents|participants|votes|people|patients|mmHg|million|billion|"
    r"acres|days|weeks|months|hours|seconds|points|samples|residents|inhabitants|"
    r"voters|students|employees|individuals|effect sizes)\b)|"
    r"(?:as many as|more than|fewer than) \d[\d,.]+\s+[A-Za-z]+\b|"
    r"(?:F1|accuracy|precision|recall|effect size) .{0,20}\d+\.\d+", re.I,
)
_INFERENCE = re.compile(
    r"\b(?:in light of|on the grounds that|falls (?:squarely )?within|"
    r"in accord with|effects of|so that|as .{0,40}explained|"
    r"if .{5,180}(?:would|will|may)|even assum\w+|means that)\b", re.I,
)
_METADISCOURSE = re.compile(
    r"^(?:this appeal|the question here|we (?:now )?(?:turn|begin|start)|"
    r"with .{0,100}(?:framework|established)|on cross-motions)\b", re.I,
)
_EXPLICIT_INFERENCE = re.compile(
    r"\b(?:because|therefore|thus|hence|consequently|as a result|it follows|"
    r"for (?:this|these) reasons?|given that|implies? that|so that)\b", re.I,
)
_ATTRIBUTED_SOURCE = re.compile(
    r"\b(?:court|report|record|agreement|statute|treaty|act|congress|survey|"
    r"study|notices?)\b.{0,65}\b(?:told|stated|said|specified|provided|"
    r"reported|found|held|reserved|explained|specified|reads|says)\b", re.I,
)
_SHORT_STANCE = re.compile(r"(?:I|we) (?:agree|disagree|concur|dissent)[.!]", re.I)
_AUTHOR_STANCE = re.compile(
    r"^\s*(?:I|we) (?:agree|disagree|concur|dissent)\b(?:\s+(?:that|with|because)\b|[.!])", re.I,
)


def exclusion_reason(text: str, context: dict | None = None) -> str:
    """Hard exclusions apply to every backend, including custom classifiers."""
    context = context or {}
    if context.get("block_type") in {"heading", "caption", "bibliography", "code", "table", "toc", "index", "endnote", "footnote"}:
        return "non_prose_block"
    if (context.get("structure") or {}).get("region", "body") not in {"body", "appendix"}:
        return "non_prose_region"
    if _BOILERPLATE.search(text.strip()):
        return "publication_boilerplate"
    if _METADISCOURSE.search(text.strip().lstrip("*0123456789■ ")):
        return "topic_or_transition"
    if text.isupper():
        return "publication_boilerplate"
    prose = citation_free_text(text)
    # Word-count fragment heuristics cannot reject complete performative
    # statements. Structure exclusions above still apply to captions/headers.
    if _SHORT_STANCE.fullmatch(prose.strip()):
        return ""
    if len(_WORDS.findall(prose)) < 4 or not _PREDICATE.search(prose):
        return "no_proposition"
    if context.get("is_fragment"):
        return "incomplete_span"
    return ""


def citation_free_text(text: str) -> str:
    """Mask citation syntax only for scoring; stored quotes stay untouched."""
    characters = list(text)
    for start, end, _authors, _year in author_year_mentions(text):
        characters[start:end] = " " * (end - start)
    clean = STATUTE_RE.sub(" ", LEGAL_REPORTER_RE.sub(" ", "".join(characters)))
    clean = re.sub(r"\bId\.\s*(?:at\s+[\d–-]+)?|\[\d+(?:[,\s–-]+\d+)*\]", " ", clean)
    return clean


def role_features(text: str, context: dict | None = None) -> tuple[dict, list[str]]:
    """Measured quantities and discourse acts outweigh weak lexical priors."""
    prose = citation_free_text(text)
    scores = {name: min(3, len(pattern.findall(prose))) * 2.0
              for name, pattern in _RULES.items()}
    if _AUTHOR_STANCE.search(prose):
        scores["claim"] += 2.0
    structure = (context or {}).get("structure") or {}
    if structure.get("genre") == "humanities":
        for role, patterns in structure.get("argument_cues", {}).items():
            if role in scores and any(re.search(pattern, prose, re.I) for pattern in patterns):
                scores[role] += 2.0
    scores["unknown"] = 0.0
    if _QUANTITY.search(prose):
        scores["evidence"] += 2.0
    if _INFERENCE.search(prose):
        scores["reasoning"] += 1.0
    # An attributed quotation may be useful evidence even without a statistic.
    if re.search(r'[“"]([^”"]{25,})[”"]', prose) and re.search(r"\b(?:court|congress|act|treaty|statute|defined|quoting|defining|equating)\b", prose, re.I):
        scores["evidence"] += 1.0
    if re.search(r"\b(?:dispose of|reject) (?:the |this |that )?argument\b", prose, re.I):
        scores["counterclaim"] += 2.0
    # Negation/contrast is not an attack unless it targets a proposition.
    # Nor are administrative procedures experimental methods.
    cues = [name for name, value in scores.items() if value]
    return scores, cues


def heuristic_result(text: str, context: dict | None = None) -> dict[str, Any]:
    reason = exclusion_reason(text, context)
    scores, cues = role_features(text, context) if not reason else ({name: 0.0 for name in (*ROLES, "unknown")}, [])
    role = choose_role(text, scores) if cues else "unknown"
    candidates = [name for name in ROLES if scores.get(name)]
    return {
        "label": role, "confidence": (0.55 if scores.get(role) == 1.0 or len(candidates) > 1 else 0.72) if role != "unknown" else 0.25,
        "scores": scores, "score_kind": "heuristic_strength", "backend": "heuristic",
        "argument_eligible": bool(cues) and not reason,
        "argument_status": "excluded" if reason else "classified" if cues else "background",
        "eligibility_reason": reason or ("role_cues:" + ",".join(cues) if cues else "no_argument_cues"),
        "decision_cues": cues,
        "role_candidates": candidates,
    }


def choose_role(text: str, scores: dict) -> str:
    """Specific discourse acts take precedence over incidental result numbers."""
    for role in ("counterclaim", "limitation", "methodology"):
        if scores.get(role):
            if role == "methodology" and scores.get("reasoning") and re.match(r"\s*(?:because|since|given that)\b", text, re.I):
                return "reasoning"
            return role
    narrator = re.sub(r'[“"]([^”"]+)[”"]', " ", text)
    if _AUTHOR_STANCE.search(narrator) or re.search(r"\b(?:we hold|we argue|we propose|our .{0,10}thesis)\b", narrator, re.I):
        return "claim"
    if _ATTRIBUTED_SOURCE.search(narrator) and scores.get("evidence") and not _EXPLICIT_INFERENCE.search(narrator):
        return "evidence"
    if scores.get("claim") and re.search(r"\b(?:should|must|shall)\b", narrator, re.I) and not re.match(r"\s*(?:because|since|it follows|therefore|thus|hence)\b", narrator, re.I) and not re.search(r"\bso that\b", narrator, re.I):
        return "claim"
    if scores.get("reasoning") and _EXPLICIT_INFERENCE.search(narrator):
        return "reasoning"
    if scores.get("evidence"):
        return "evidence"
    if scores.get("reasoning"):
        return "reasoning"
    return max(scores, key=scores.get)


def model_role_allowed(role: str, text: str, context=None) -> bool:
    """Reject domain-inappropriate role proposals from a generic NLI model.

Law's restrictions are not research limitations; a disagreement with a deal
is not necessarily a counterargument. Such proposals stay on the sentence
for review, and cannot silently enter the argument graph.
"""
    scores, _cues = role_features(text, context)
    if role in {"methodology", "counterclaim", "limitation"}:
        return bool(scores[role])
    if role == "reasoning":
        return bool(scores["reasoning"])
    if role == "evidence":
        return bool(scores["evidence"])
    return role == "claim" and not exclusion_reason(text, context)
