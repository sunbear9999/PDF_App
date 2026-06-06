from __future__ import annotations

from dataclasses import dataclass, field
from typing import List


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
