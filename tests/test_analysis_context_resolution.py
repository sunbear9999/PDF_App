from types import SimpleNamespace

from core.engine.analysis_runtime import AnalysisRuntime
from core.utils.state_resolver import StateResolver


def test_nested_chunk_prompt_receives_item_text_and_page_range():
    state = {
        "analysis_chunk_query_prompt": (
            "Pages {item.page_range}. Select quotes from TEXT:\n{item.text}\n"
            "Limit: {analysis_limits.max_quotes_per_chunk}"
        ),
        "item": {
            "page_range": "5-8",
            "text": "Actual document sentence about emulsifier exposure.",
        },
        "analysis_limits": {"max_quotes_per_chunk": 4},
    }

    resolved = StateResolver.resolve_val("{analysis_chunk_query_prompt}", state)

    assert "Pages 5-8" in resolved
    assert "Actual document sentence about emulsifier exposure." in resolved
    assert "Limit: 4" in resolved
    assert "{item.text}" not in resolved


def test_nested_synthesis_prompt_receives_compacted_evidence():
    state = {
        "analysis_synthesis_query_prompt": "Synthesize these quotes:\n{master_input}",
        "master_input": '[{"q":[{"x":"real quote"}]}]',
    }

    resolved = StateResolver.resolve_val("{analysis_synthesis_query_prompt}", state)

    assert "real quote" in resolved
    assert "{master_input}" not in resolved


def test_hallucinated_chunk_quotes_are_discarded():
    runtime = AnalysisRuntime(None, SimpleNamespace(), None)
    observation = {
        "s": "methodology",
        "q": [
            {"id": "q1", "x": "Data was collected from 100 participants.", "n": "fake"},
            {"id": "q2", "x": "male and female animals harbored distinct microbiota composition", "n": "real"},
        ],
    }
    source = "Following exposure, male and female animals harbored distinct microbiota composition based on treatment."

    cleaned = runtime.validate_chunk_observation_quotes(observation, source)

    assert [quote["id"] for quote in cleaned["q"]] == ["q2"]
    assert cleaned["discarded_unverified_quotes"] == 1
