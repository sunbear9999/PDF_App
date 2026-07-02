from core.engine.action_model import ActionStep
from core.engine.steps.llm_query_step import LLMQueryStep
from core.plugins.plugin_step_protocol import StepContext


class _LLM:
    def __init__(self):
        self.kwargs = None

    def query(self, **kwargs):
        self.kwargs = kwargs
        if kwargs.get("callback"):
            kwargs["callback"]("answer")
        return "answer"


class _Settings:
    def resolve_options(self, **kwargs):
        return {"temperature": 0.2, "num_predict": 9000, "num_ctx": 65536, "json_mode": False}


def test_llm_query_step_uses_settings_service_over_step_defaults():
    llm = _LLM()
    step = ActionStep(
        step_id="answer",
        step_type="LLM_QUERY",
        inputs={"query": "hello"},
        llm_options={"temperature": 0.9, "num_predict": 100},
    )
    ctx = StepContext(
        llm_manager=llm,
        state={"selected_model": "model"},
        llm_settings_service=_Settings(),
        blueprint_id="demo",
        step_path="answer",
    )
    ctx._step_proxy = step

    result = LLMQueryStep().execute(ctx, {"query": "hello", "_resolved_model": "model"})

    assert result.raw_value == "answer"
    assert llm.kwargs["temperature"] == 0.2
    assert llm.kwargs["num_predict"] == 9000
    assert llm.kwargs["num_ctx"] == 65536
