from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, List, Optional


@dataclass
class BlueprintPromptUsage:
    step_id: str
    step_type: str
    explicit: List[str] = field(default_factory=list)
    implicit: List[str] = field(default_factory=list)

    def as_dict(self) -> dict:
        return {
            "step_id": self.step_id,
            "step_type": self.step_type,
            "explicit": list(self.explicit),
            "implicit": list(self.implicit),
        }


ANALYSIS_STATE_PROMPTS = {
    "{analysis_chunk_system_prompt}": ["Graph Analysis Chunk Observations System"],
    "{analysis_synthesis_system_prompt}": ["Graph Analysis Synthesis System"],
    "{analysis_master_system_prompt}": [
        "Graph Analysis Master System",
        "Graph Analysis Compact Output Contract",
        "Graph Analysis Argument Chain Directive",
    ],
    "{analysis_chunk_query_prompt}": ["Graph Analysis Chunk Observations Query"],
    "{analysis_synthesis_query_prompt}": ["Graph Analysis Synthesis Query"],
    "{analysis_master_query_prompt}": ["Graph Analysis Master Query"],
}


def collect_step_prompt_usage(step: Any) -> BlueprintPromptUsage:
    explicit = set()
    implicit = set()

    prompt_key = getattr(step, "prompt_key", None)
    if prompt_key:
        if prompt_key == "{chat_persona}":
            explicit.update(["General Assistant", "RAG Agent Mode"])
        elif "{" not in str(prompt_key):
            explicit.add(str(prompt_key))
    explicit.update(str(key) for key in (getattr(step, "required_prompt_keys", []) or []) if key)

    texts_to_scan = [getattr(step, "system_prompt", "")]
    if isinstance(getattr(step, "inputs", None), dict):
        texts_to_scan.extend([str(value) for value in step.inputs.values()])

    for text in texts_to_scan:
        if text:
            explicit.update(re.findall(r"\{prompt:(.*?)\}", text))
            for state_token, prompt_keys in ANALYSIS_STATE_PROMPTS.items():
                if state_token in text:
                    explicit.update(prompt_keys)

    if getattr(step, "step_type", "") in {"LLM_QUERY", "LLM_SCHEMA_QUERY"}:
        opts = getattr(step, "llm_options", {}) or {}
        if getattr(step, "output_schema", None) or opts.get("json_mode"):
            implicit.add("JSON Schema Enforcer")

        ui_fmt = getattr(step, "ui_format", "")
        if ui_fmt == "chat_widgets":
            implicit.add("Format Enforcer - Chat Widgets")
        elif ui_fmt == "data_table":
            implicit.add("Format Enforcer - Data Table")
        elif ui_fmt == "card_grid":
            implicit.add("Format Enforcer - Card Grid")
        elif ui_fmt == "live_stream":
            implicit.add("Format Enforcer - Live Stream")

        if getattr(step, "inline_citations", False):
            implicit.add("Inline Citation Directive")

        req_context = getattr(step, "required_context", [])
        if "manifest" in req_context:
            implicit.add("Manifest Update Directive")
            implicit.add("Context Inject - Manifest")
        if "workspace" in req_context:
            implicit.add("Context Inject - Workspace")
        if "selected_nodes" in req_context:
            implicit.add("Context Inject - Selected")
        if "analyses" in req_context:
            implicit.add("Context Inject - Analyses")

    return BlueprintPromptUsage(
        step_id=getattr(step, "step_id", "Unknown Step"),
        step_type=getattr(step, "step_type", "UNKNOWN"),
        explicit=sorted(explicit),
        implicit=sorted(implicit),
    )


@dataclass
class PromptTraceCall:
    step_id: str
    step_type: str
    model: str
    query: str
    system_prompt: str
    prompt_key: Optional[str] = None
    explicit_prompt_keys: List[str] = field(default_factory=list)
    implicit_prompt_keys: List[str] = field(default_factory=list)
    llm_options: Dict[str, Any] = field(default_factory=dict)
    timestamp: str = field(default_factory=lambda: datetime.utcnow().isoformat(timespec="seconds") + "Z")

    def as_dict(self) -> dict:
        return {
            "step_id": self.step_id,
            "step_type": self.step_type,
            "model": self.model,
            "query": self.query,
            "system_prompt": self.system_prompt,
            "prompt_key": self.prompt_key,
            "explicit_prompt_keys": list(self.explicit_prompt_keys),
            "implicit_prompt_keys": list(self.implicit_prompt_keys),
            "llm_options": dict(self.llm_options),
            "timestamp": self.timestamp,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "PromptTraceCall":
        return cls(
            step_id=data.get("step_id", ""),
            step_type=data.get("step_type", ""),
            model=data.get("model", ""),
            query=data.get("query", ""),
            system_prompt=data.get("system_prompt", ""),
            prompt_key=data.get("prompt_key"),
            explicit_prompt_keys=list(data.get("explicit_prompt_keys") or []),
            implicit_prompt_keys=list(data.get("implicit_prompt_keys") or []),
            llm_options=dict(data.get("llm_options") or {}),
            timestamp=data.get("timestamp") or datetime.utcnow().isoformat(timespec="seconds") + "Z",
        )


@dataclass
class PromptTraceRecord:
    trace_id: Optional[str] = None
    job_id: Optional[str] = None
    blueprint_id: Optional[str] = None
    blueprint_name: str = ""
    target_id: Optional[str] = None
    created_at: str = field(default_factory=lambda: datetime.utcnow().isoformat(timespec="seconds") + "Z")
    calls: List[PromptTraceCall] = field(default_factory=list)

    def as_dict(self) -> dict:
        return {
            "trace_id": self.trace_id,
            "job_id": self.job_id,
            "blueprint_id": self.blueprint_id,
            "blueprint_name": self.blueprint_name,
            "target_id": self.target_id,
            "created_at": self.created_at,
            "calls": [call.as_dict() if hasattr(call, "as_dict") else dict(call) for call in self.calls],
        }

    @classmethod
    def from_dict(cls, data: dict) -> "PromptTraceRecord":
        return cls(
            trace_id=data.get("trace_id"),
            job_id=data.get("job_id"),
            blueprint_id=data.get("blueprint_id"),
            blueprint_name=data.get("blueprint_name", ""),
            target_id=data.get("target_id"),
            created_at=data.get("created_at") or datetime.utcnow().isoformat(timespec="seconds") + "Z",
            calls=[
                call if isinstance(call, PromptTraceCall) else PromptTraceCall.from_dict(call)
                for call in data.get("calls", [])
            ],
        )
