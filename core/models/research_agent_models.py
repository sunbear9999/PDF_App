from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, List, Optional
from uuid import uuid4


def _now_iso() -> str:
    return datetime.utcnow().isoformat(timespec="seconds") + "Z"


@dataclass
class ResearchAgentArtifact:
    kind: str
    title: str
    content: Any
    source: Optional[str] = None
    created_at: str = field(default_factory=_now_iso)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "kind": self.kind,
            "title": self.title,
            "content": self.content,
            "source": self.source,
            "created_at": self.created_at,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "ResearchAgentArtifact":
        return cls(
            kind=data.get("kind", "artifact"),
            title=data.get("title", "Artifact"),
            content=data.get("content", ""),
            source=data.get("source"),
            created_at=data.get("created_at") or _now_iso(),
        )


@dataclass
class ResearchAgentCheckpoint:
    kind: str
    prompt: str
    options: List[str] = field(default_factory=list)
    status: str = "pending"
    response: Optional[str] = None
    created_at: str = field(default_factory=_now_iso)
    resolved_at: Optional[str] = None

    def resolve(self, response: str):
        self.status = "resolved"
        self.response = response
        self.resolved_at = _now_iso()

    def to_dict(self) -> Dict[str, Any]:
        return {
            "kind": self.kind,
            "prompt": self.prompt,
            "options": list(self.options),
            "status": self.status,
            "response": self.response,
            "created_at": self.created_at,
            "resolved_at": self.resolved_at,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "ResearchAgentCheckpoint":
        return cls(
            kind=data.get("kind", "custom"),
            prompt=data.get("prompt", ""),
            options=list(data.get("options") or []),
            status=data.get("status", "pending"),
            response=data.get("response"),
            created_at=data.get("created_at") or _now_iso(),
            resolved_at=data.get("resolved_at"),
        )


@dataclass
class ResearchAgentToolRun:
    blueprint_id: str
    reason: str
    inputs: Dict[str, Any] = field(default_factory=dict)
    status: str = "queued"
    result_summary: str = ""
    created_at: str = field(default_factory=_now_iso)
    completed_at: Optional[str] = None

    def complete(self, result_summary: str):
        self.status = "complete"
        self.result_summary = result_summary
        self.completed_at = _now_iso()

    def fail(self, message: str):
        self.status = "error"
        self.result_summary = message
        self.completed_at = _now_iso()

    def to_dict(self) -> Dict[str, Any]:
        return {
            "blueprint_id": self.blueprint_id,
            "reason": self.reason,
            "inputs": dict(self.inputs),
            "status": self.status,
            "result_summary": self.result_summary,
            "created_at": self.created_at,
            "completed_at": self.completed_at,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "ResearchAgentToolRun":
        return cls(
            blueprint_id=data.get("blueprint_id", ""),
            reason=data.get("reason", ""),
            inputs=dict(data.get("inputs") or {}),
            status=data.get("status", "queued"),
            result_summary=data.get("result_summary", ""),
            created_at=data.get("created_at") or _now_iso(),
            completed_at=data.get("completed_at"),
        )


@dataclass
class ResearchAgentSession:
    goal: str
    session_id: str = field(default_factory=lambda: str(uuid4()))
    status: str = "planning"
    memory: str = ""
    latest_user_input: str = ""
    artifacts: List[ResearchAgentArtifact] = field(default_factory=list)
    checkpoints: List[ResearchAgentCheckpoint] = field(default_factory=list)
    tool_runs: List[ResearchAgentToolRun] = field(default_factory=list)
    created_at: str = field(default_factory=_now_iso)
    updated_at: str = field(default_factory=_now_iso)

    def touch(self):
        self.updated_at = _now_iso()

    def pending_checkpoint(self) -> Optional[ResearchAgentCheckpoint]:
        for checkpoint in reversed(self.checkpoints):
            if checkpoint.status == "pending":
                return checkpoint
        return None

    def add_artifact(self, kind: str, title: str, content: Any, source: Optional[str] = None):
        self.artifacts.append(ResearchAgentArtifact(kind=kind, title=title, content=content, source=source))
        self.touch()

    def to_dict(self) -> Dict[str, Any]:
        return {
            "session_id": self.session_id,
            "goal": self.goal,
            "status": self.status,
            "memory": self.memory,
            "latest_user_input": self.latest_user_input,
            "artifacts": [artifact.to_dict() for artifact in self.artifacts],
            "checkpoints": [checkpoint.to_dict() for checkpoint in self.checkpoints],
            "tool_runs": [tool_run.to_dict() for tool_run in self.tool_runs],
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "ResearchAgentSession":
        return cls(
            goal=data.get("goal", ""),
            session_id=data.get("session_id") or str(uuid4()),
            status=data.get("status", "planning"),
            memory=data.get("memory", ""),
            latest_user_input=data.get("latest_user_input", ""),
            artifacts=[ResearchAgentArtifact.from_dict(item) for item in data.get("artifacts", []) if isinstance(item, dict)],
            checkpoints=[ResearchAgentCheckpoint.from_dict(item) for item in data.get("checkpoints", []) if isinstance(item, dict)],
            tool_runs=[ResearchAgentToolRun.from_dict(item) for item in data.get("tool_runs", []) if isinstance(item, dict)],
            created_at=data.get("created_at") or _now_iso(),
            updated_at=data.get("updated_at") or _now_iso(),
        )
