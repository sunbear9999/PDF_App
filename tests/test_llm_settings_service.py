import json

from core.engine.action_model import AIActionBlueprint, ActionStep
from core.engine.steps.llm_schema_query_step import LLMSchemaQueryStep
from core.registries.blueprint_registry import BlueprintDefinition, BlueprintRegistry
from core.services.ai.llm_settings_service import LLMSettingsService


class _PM:
    def __init__(self):
        self.meta = {}

    def get_metadata(self, key, default=None):
        return self.meta.get(key, default)

    def set_metadata(self, key, value):
        self.meta[key] = value


class _NodeType:
    def __init__(self, step_cls):
        self.step_cls = step_cls


class _NodeTypeRegistry:
    def get_by_step_type(self, step_type):
        if step_type == "LLM_SCHEMA_QUERY":
            return _NodeType(LLMSchemaQueryStep)
        return None


def _blueprint():
    child = AIActionBlueprint(name="child", description="", steps=[
        ActionStep(
            step_id="child_answer",
            step_type="LLM_QUERY",
            llm_options={"temperature": 0.2, "num_predict": 900},
        )
    ])
    return AIActionBlueprint(name="Demo", description="", steps=[
        ActionStep(
            step_id="answer",
            step_type="LLM_QUERY",
            llm_options={"temperature": 0.4, "num_predict": 1200, "num_ctx": 32768},
        ),
        ActionStep(
            step_id="router",
            step_type="BRANCH",
            if_true=[
                ActionStep(
                    step_id="manifest",
                    step_type="LLM_QUERY",
                    llm_options={"temperature": 0.1, "num_predict": 3000},
                )
            ],
        ),
        ActionStep(
            step_id="loop",
            step_type="FOREACH",
            inputs={"sub_blueprint": child},
        ),
    ])


def test_llm_settings_service_discovers_nested_llm_steps():
    registry = BlueprintRegistry()
    registry.register(BlueprintDefinition(
        id="demo",
        label="Demo",
        description="",
        factory=lambda **_: _blueprint(),
    ))
    service = LLMSettingsService(project_manager=_PM(), blueprint_registry=registry)

    steps = service.list_blueprint_steps("demo")
    paths = {step["step_path"] for step in steps}

    assert paths == {
        "answer",
        "router.if_true.manifest",
        "loop.sub.child_answer",
    }


def test_llm_settings_service_discovers_schema_query_subclasses():
    registry = BlueprintRegistry()
    registry.register(BlueprintDefinition(
        id="extract_claims",
        label="Extract Claims",
        description="",
        factory=lambda **_: AIActionBlueprint(name="Extract Claims", description="", steps=[
            ActionStep(
                step_id="extract_claims",
                step_type="LLM_SCHEMA_QUERY",
                llm_options={"temperature": 0.1},
            )
        ]),
    ))
    service = LLMSettingsService(
        project_manager=_PM(),
        blueprint_registry=registry,
        node_type_registry=_NodeTypeRegistry(),
    )

    steps = service.list_blueprint_steps("extract_claims")

    assert len(steps) == 1
    assert steps[0]["step_path"] == "extract_claims"
    assert steps[0]["step_type"] == "LLM_SCHEMA_QUERY"
    assert steps[0]["effective"]["json_mode"] is True


def test_llm_settings_service_applies_and_resets_project_overrides_without_mutating_blueprint():
    pm = _PM()
    bp = _blueprint()
    service = LLMSettingsService(project_manager=pm)

    service.set_step_overrides("demo", "answer", {"num_predict": 8000, "temperature": 0.15})
    resolved = service.resolve_options(
        blueprint_id="demo",
        step_path="answer",
        step=bp.steps[0],
        base_options=bp.steps[0].llm_options,
    )

    assert resolved["num_predict"] == 8000
    assert resolved["temperature"] == 0.15
    assert bp.steps[0].llm_options["num_predict"] == 1200

    service.reset_step_overrides("demo", "answer")
    assert json.loads(pm.meta["blueprint_llm_overrides"]) == {}
