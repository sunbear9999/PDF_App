from core.engine.action_model import ActionStep
from core.engine.ui_payloads import build_ui_payloads, coerce_saved_payloads, serialize_payloads


def test_live_stream_payloads_extract_inline_citation_cards():
    result = (
        "Here is the answer.\n"
        "<CITATIONS>[{\"doc_name\":\"paper.pdf\",\"quote\":\"quoted text\",\"note\":\"why\"}]</CITATIONS>"
    )
    step = ActionStep(
        step_id="answer",
        ui_format="live_stream",
        inline_citations=True,
    )

    payloads = build_ui_payloads("live_stream", result, step=step, trace_id="trace-1")

    assert payloads[0] == {"type": "replace_stream_text", "text": "Here is the answer."}
    assert payloads[1]["type"] == "citation_cards"
    assert payloads[1]["items"] == [{"doc_name": "paper.pdf", "quote": "quoted text", "note": "why"}]
    assert payloads[1]["trace_id"] == "trace-1"
    assert payloads[-1] == {"type": "hide_status"}


def test_saved_payload_round_trip():
    payloads = [{"type": "citation_cards", "items": [{"quote": "q"}]}]

    assert coerce_saved_payloads(serialize_payloads(payloads)) == payloads
