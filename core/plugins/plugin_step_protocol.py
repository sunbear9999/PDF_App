"""
core/plugins/plugin_step_protocol.py

Protocol for plugin-contributed workflow execution steps.

Usage::

    from core.plugins.plugin_step_protocol import CustomExecutionStep, StepContext

    class FetchWebPageStep(CustomExecutionStep):
        step_type = "FETCH_WEB_PAGE"
        label = "Fetch Web Page"
        category = "Plugin"
        description = "Fetch the contents of a URL."
        requires_internet = True
        input_schema = {
            "url": {"type": "string", "label": "URL", "required": True},
        }
        output_schema = {
            "html": {"type": "string", "label": "Page HTML"},
        }

        def execute(self, context: StepContext, inputs: dict) -> dict:
            import urllib.request
            with urllib.request.urlopen(inputs["url"]) as r:
                return {"html": r.read().decode("utf-8", errors="replace")}

Then register it in on_load::

    def on_load(self, api):
        api.register_workflow_step(FetchWebPageStep)
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, Optional, Union, TYPE_CHECKING

if TYPE_CHECKING:
    from core.project_manager import ProjectManager
    from core.llm_manager import LocalLLMManager
    from core.engine.execution_result import ExecutionResult
    from core.engine.workflow_step_api import WorkflowStepAPI


@dataclass
class StepContext:
    """Read-only context passed to CustomExecutionStep.execute().

    Provides access to the project database, LLM, and the current workflow
    state.  Do not store references to this object across calls.

    All fields added after the original four are Optional and default to None
    so that existing plugin steps that construct StepContext directly remain
    unaffected.
    """

    # ---- original fields (DO NOT reorder — positional compat) ----
    project_manager: "Optional[ProjectManager]" = None
    llm_manager: "Optional[LocalLLMManager]" = None
    state: Dict[str, Any] = field(default_factory=dict)
    abort_check: Optional[Callable[[], bool]] = None

    # ---- enriched fields (Phase A1) ----
    api: "Optional[WorkflowStepAPI]" = None         # sandbox API for PYTHON_SCRIPT
    prompt_manager: Optional[Any] = None            # PromptManager for {prompt:Key} resolution
    ontology_registry: Optional[Any] = None         # OntologyRegistry for graph-aware steps
    trace_id: Optional[str] = None                  # propagated to ui_payloads for trace linking
    node_type_registry: Optional[Any] = None        # BlueprintNodeTypeRegistry (meta-steps)
    data_provider_registry: Optional[Any] = None    # deterministic data/chart extension registry
    # pause primitives forwarded from the runner for USER_INPUT / AWAIT_EVENT steps
    _pause_mutex: Optional[Any] = None              # QMutex
    _wait_condition: Optional[Any] = None           # QWaitCondition
    _user_response_holder: Optional[list] = None    # [response_value] single-element list

    def is_aborted(self) -> bool:
        """Return True if the running workflow job has been cancelled."""
        return bool(self.abort_check and self.abort_check())


class CustomExecutionStep(ABC):
    """Base class for plugin-contributed workflow steps.

    Class attributes (override in your subclass):

    - ``step_type`` — unique string key used as the dispatch identifier in
      blueprints (e.g. ``"FETCH_WEB_PAGE"``).
    - ``label`` — human-readable name shown in the workflow builder UI.
    - ``category`` — group in the workflow builder palette (default ``"Plugin"``).
    - ``description`` — short description shown in the UI.
    - ``requires_internet`` — hint flag for the airgap warning system.
    - ``input_schema`` — dict of ``{key: {type, label, required}}`` used by the
      UI to auto-generate a configuration form.
    - ``output_schema`` — dict of ``{key: {type, label}}`` describing outputs.
    - ``plugin_id`` — set automatically by ``api.register_workflow_step()``.
    """

    step_type: str = ""
    label: str = ""
    category: str = "Plugin"
    description: str = ""
    requires_internet: bool = False
    input_schema: Dict[str, Any] = {}
    output_schema: Dict[str, Any] = {}
    plugin_id: str = ""

    @abstractmethod
    def execute(self, context: StepContext, inputs: dict) -> "Union[dict, ExecutionResult]":
        """Execute the step.

        Runs inside a QThread (background).  Must be thread-safe.
        Check ``context.is_aborted()`` periodically for cancellation.

        :param context: Workflow context (project, LLM, state, optional services).
        :param inputs: Resolved input values for this step invocation.
        :returns: Either a plain dict (legacy — wrapped by the runner shim) or a
                  fully structured ExecutionResult.  New step implementations should
                  always return ExecutionResult.
        """
