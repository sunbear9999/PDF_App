from core.engine.action_model import AIActionBlueprint, ActionStep
from core.engine.master_runner import MasterActionRunner


def test_foreach_compact_state_only_inherits_declared_keys():
    runner = MasterActionRunner(
        AIActionBlueprint(name="test", description="", steps=[]),
        {
            "selected_model": "m",
            "target_id": "analysis_tab",
            "needed": "yes",
            "huge_previous_output": "x" * 10000,
        },
    )

    sub_state = runner._build_foreach_sub_state(
        {"inherit_state_keys": ["needed"]},
        {"chunk_index": 1},
    )

    assert sub_state["needed"] == "yes"
    assert sub_state["selected_model"] == "m"
    assert sub_state["target_id"] == "analysis_tab"
    assert "huge_previous_output" not in sub_state
