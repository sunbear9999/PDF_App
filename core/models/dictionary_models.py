from __future__ import annotations

from dataclasses import dataclass, field
from typing import List


@dataclass
class DictionaryDefinitionGroup:
    word: str
    sources: List[str] = field(default_factory=list)
    definitions: List[str] = field(default_factory=list)

    def as_dict(self) -> dict:
        return {
            "word": self.word,
            "sources": list(self.sources),
            "definitions": list(self.definitions),
            "source": ", ".join(self.sources),
        }
