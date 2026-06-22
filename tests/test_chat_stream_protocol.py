from core.engine.ui_payloads import build_ui_payloads
from core.utils.json_utils import TaggedBlockStreamFilter, strip_tagged_block


def test_manifest_protocol_is_removed_from_visible_and_saved_text():
    raw = (
        "## Ideas\n\n- First\n\n"
        '<UPDATE_MANIFEST>{"Core Thesis":"First"}</UPDATE_MANIFEST>'
    )

    assert strip_tagged_block(raw, "UPDATE_MANIFEST") == "## Ideas\n\n- First"
    payloads = build_ui_payloads("live_stream", raw)
    assert payloads[0] == {
        "type": "replace_stream_text",
        "text": "## Ideas\n\n- First",
    }


def test_manifest_protocol_stripping_tolerates_case_and_tag_spacing():
    raw = 'Visible answer < update_manifest >{"topic":"hidden"}</ UPDATE_MANIFEST >'

    assert strip_tagged_block(raw, "UPDATE_MANIFEST") == "Visible answer"


def test_manifest_protocol_filter_does_not_leak_split_tags():
    chunks = [
        "## Answer\n\nUseful text.\n<UP",
        'DATE_MANIFEST>{"topic":',
        '"hidden"}</UPDATE_MAN',
        "IFEST>\n\nMore text.",
    ]
    stream_filter = TaggedBlockStreamFilter("UPDATE_MANIFEST")

    visible = "".join(stream_filter.feed(chunk) for chunk in chunks)
    visible += stream_filter.flush()

    assert visible == "## Answer\n\nUseful text.\n\n\nMore text."
    assert "MANIFEST" not in visible
    assert "hidden" not in visible
