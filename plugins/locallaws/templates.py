"""
plugins/locallaws/templates.py

Legal analysis template dicts contributed to the Analysis Tab by the LocalLaws plugin.
Each template follows the same format as DocumentDB._default_analysis_templates():
id, title, instructions, node_types, relation_types, chunk/synthesis/master prompt keys,
limits, and analysis_template_version.
"""
from __future__ import annotations

_DEFAULT_LIMITS = {
    "chunk_pages": 4,
    "max_chunk_chars": 14000,
    "max_master_chars": 36000,
    "num_ctx": 24576,
    "chunk_num_predict": 1400,
    "synthesis_num_predict": 3500,
    "master_num_predict": 5000,
    "max_entities_per_chunk": 10,
    "max_relations_per_chunk": 14,
    "max_quotes_per_chunk": 4,
    "quote_words": 10,
    "max_quote_words": 18,
    "explanation_words": 10,
}


def get_legal_analysis_templates() -> list:
    """Return a list of template dicts for legal analysis modes."""
    return [
        _case_brief_template(),
        _legal_argument_map_template(),
        _irac_analysis_template(),
        _precedent_map_template(),
    ]


def _case_brief_template() -> dict:
    return {
        "id": "locallaws.case_brief",
        "title": "Case Brief",
        "instructions": (
            "Extract the structural anatomy of the case to produce a navigable case brief graph. "
            "Identify each legal issue presented, the applicable doctrines and rules invoked, "
            "the key undisputed and disputed facts (with exact quotes), the court's step-by-step "
            "reasoning chain, and the final holding for each issue. Show how the facts and "
            "reasoning nodes lead to each holding. Include cited authorities and how they are "
            "used. The resulting graph should read as a case brief — facts → reasoning → holding "
            "chains, with doctrines and authorities anchoring the analysis."
        ),
        "node_types": [
            "law.holding", "law.issue", "law.doctrine",
            "law.fact", "law.reasoning", "law.authority",
        ],
        "relation_types": [
            "law.supports", "law.applies_to", "law.resolves",
            "law.contradicts", "law.cites", "law.follows",
        ],
        "allow_text_nodes": False,
        "chunk_prompt_key": "Graph Analysis Chunk Observations System",
        "synthesis_prompt_key": "Graph Analysis Synthesis System",
        "master_prompt_key": "Graph Analysis Master System",
        "chunk_query_prompt_key": "Graph Analysis Chunk Observations Query",
        "synthesis_query_prompt_key": "Graph Analysis Synthesis Query",
        "master_query_prompt_key": "Graph Analysis Master Query",
        "limits": dict(_DEFAULT_LIMITS),
        "analysis_template_version": 1,
    }


def _legal_argument_map_template() -> dict:
    return {
        "id": "locallaws.legal_argument_map",
        "title": "Legal Argument Map",
        "instructions": (
            "Map the legal argument structure of this case. Identify each legal issue in dispute, "
            "the doctrines invoked on each side, the key facts and policy rationales marshaled in "
            "support, and the court's reasoning path to its holding. Highlight where the parties' "
            "arguments conflict and where the court resolves the tension. Show how facts support "
            "or contradict the legal positions being advanced."
        ),
        "node_types": [
            "law.issue", "law.holding", "law.doctrine",
            "law.fact", "law.reasoning", "law.policy",
        ],
        "relation_types": [
            "law.supports", "law.contradicts", "law.applies_to",
            "law.resolves", "law.argues", "law.rebuts",
        ],
        "allow_text_nodes": False,
        "chunk_prompt_key": "Graph Analysis Chunk Observations System",
        "synthesis_prompt_key": "Graph Analysis Synthesis System",
        "master_prompt_key": "Graph Analysis Master System",
        "chunk_query_prompt_key": "Graph Analysis Chunk Observations Query",
        "synthesis_query_prompt_key": "Graph Analysis Synthesis Query",
        "master_query_prompt_key": "Graph Analysis Master Query",
        "limits": dict(_DEFAULT_LIMITS),
        "analysis_template_version": 1,
    }


def _irac_analysis_template() -> dict:
    return {
        "id": "locallaws.irac_analysis",
        "title": "IRAC Analysis",
        "instructions": (
            "Analyze this document in strict IRAC order for each legal question. "
            "Issue: what specific legal question is being decided? "
            "Rule: what doctrine, test, or statute applies to that question? "
            "Application: how does the court apply the rule to the specific facts? "
            "Conclusion: what does the court decide and why? "
            "Each issue should yield a clear chain: issue → doctrine (rule) → fact (application) → holding (conclusion). "
            "Prefer concrete doctrinal labels and quoted fact statements over abstract summaries."
        ),
        "node_types": [
            "law.issue", "law.doctrine", "law.fact",
            "law.holding", "law.reasoning",
        ],
        "relation_types": [
            "law.applies_to", "law.supports", "law.resolves", "law.argues",
        ],
        "allow_text_nodes": False,
        "chunk_prompt_key": "Graph Analysis Chunk Observations System",
        "synthesis_prompt_key": "Graph Analysis Synthesis System",
        "master_prompt_key": "Graph Analysis Master System",
        "chunk_query_prompt_key": "Graph Analysis Chunk Observations Query",
        "synthesis_query_prompt_key": "Graph Analysis Synthesis Query",
        "master_query_prompt_key": "Graph Analysis Master Query",
        "limits": dict(_DEFAULT_LIMITS),
        "analysis_template_version": 1,
    }


def _precedent_map_template() -> dict:
    return {
        "id": "locallaws.precedent_map",
        "title": "Precedent Relationship Map",
        "instructions": (
            "Map how this case engages with the body of precedent. Identify every authority "
            "cited and characterize exactly how it is used: followed (as controlling), "
            "distinguished (factually or legally), overruled, or merely cited for a principle. "
            "Show what doctrine or holding each authority contributes. Map the hierarchical "
            "and semantic relationships between authorities so the reader can see how the "
            "current court positions its own holding within the precedent landscape."
        ),
        "node_types": [
            "law.authority", "law.holding", "law.doctrine", "law.issue",
        ],
        "relation_types": [
            "law.follows", "law.overrules", "law.distinguishes",
            "law.cites", "law.supports",
        ],
        "allow_text_nodes": False,
        "chunk_prompt_key": "Graph Analysis Chunk Observations System",
        "synthesis_prompt_key": "Graph Analysis Synthesis System",
        "master_prompt_key": "Graph Analysis Master System",
        "chunk_query_prompt_key": "Graph Analysis Chunk Observations Query",
        "synthesis_query_prompt_key": "Graph Analysis Synthesis Query",
        "master_query_prompt_key": "Graph Analysis Master Query",
        "limits": dict(_DEFAULT_LIMITS),
        "analysis_template_version": 1,
    }
