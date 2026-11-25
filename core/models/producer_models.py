"""Truthful producer identity for intelligence artifacts and stage runs.

Every artifact says *how* it was made: a deterministic algorithm, a heuristic,
a learned model, a generative model or a person. When a model ran, its actual
ID/version/digest is recorded; when none ran, ``model_name`` is the explicit
``NO_MODEL`` value instead of a blank, and a fallback reason is kept when a
model was expected but unavailable. Learned/generative records with an
unrecorded originating model use ``unknown``; this never means no model ran.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, replace
from enum import Enum
from typing import Any, Dict, Optional

NO_MODEL = "none"
PRODUCER_METADATA_KEY = "producer"


class MethodKind(str, Enum):
    DETERMINISTIC = "deterministic"
    HEURISTIC = "heuristic"
    LEARNED = "learned"
    GENERATIVE = "generative"
    HUMAN = "human"


@dataclass(frozen=True)
class ProducerIdentity:
    producer_id: str
    producer_version: str = "1.0"
    method_kind: str = MethodKind.DETERMINISTIC.value
    backend: str = ""
    model_id: str = ""
    model_version: str = ""
    model_digest: str = ""
    fallback_reason: str = ""
    # Source revision (file content) the producer read; set at publication.
    source_revision: str = ""
    configuration_digest: str = ""

    @property
    def model_name(self) -> str:
        return self.model_id or ("unknown" if self.method_kind in {"learned", "generative"} else NO_MODEL)

    def to_metadata(self) -> Dict[str, Any]:
        return {key: value for key, value in asdict(self).items() if value}

    @classmethod
    def from_metadata(cls, data: Optional[Dict[str, Any]]) -> Optional["ProducerIdentity"]:
        if not isinstance(data, dict) or not data.get("producer_id"):
            return None
        fields = cls.__dataclass_fields__
        return cls(**{key: str(value) for key, value in data.items() if key in fields})

    def stamp(self, record: Any) -> Any:
        """Return ``record`` (an ArtifactRecord) carrying this identity."""
        metadata = dict(getattr(record, "metadata", None) or {})
        metadata[PRODUCER_METADATA_KEY] = self.to_metadata()
        return replace(
            record,
            metadata=metadata,
            model_name=self.model_name,
            extractor_name=record.extractor_name or self.producer_id,
            extractor_version=record.extractor_version or self.producer_version,
        )

    def describe(self) -> str:
        """One-line, user-facing description of how something was produced."""
        kind = _KIND_LABELS.get(self.method_kind, self.method_kind)
        head = f"{kind}: {self.producer_id} v{self.producer_version}"
        if self.model_id:
            version = f" {self.model_version}" if self.model_version else ""
            head += f" · model {self.model_id}{version}"
        elif self.method_kind in {"learned", "generative"}:
            head += " · model not recorded"
        else:
            head += " · no model used"
        if self.fallback_reason:
            head += f" · fallback: {self.fallback_reason}"
        return head


_KIND_LABELS = {
    MethodKind.DETERMINISTIC.value: "Deterministic rules",
    MethodKind.HEURISTIC.value: "Heuristic scoring",
    MethodKind.LEARNED.value: "Learned model",
    MethodKind.GENERATIVE.value: "Generative model",
    MethodKind.HUMAN.value: "Person",
}


def deterministic(producer_id: str, version: str = "1.0") -> ProducerIdentity:
    return ProducerIdentity(producer_id=producer_id, producer_version=version)


def human(producer_id: str = "user") -> ProducerIdentity:
    return ProducerIdentity(
        producer_id=producer_id, method_kind=MethodKind.HUMAN.value
    )


def transcription_identity(transcript: Dict[str, Any]) -> ProducerIdentity:
    """Identity for transcript segments from the stored transcript record."""
    model = str((transcript or {}).get("model") or "")
    return ProducerIdentity(
        producer_id="local_transcription",
        method_kind=MethodKind.LEARNED.value,
        backend="speech_to_text",
        model_id=model,
        fallback_reason="" if model else "transcript record does not name its model",
    )


def identity_of(row: Dict[str, Any]) -> Optional[ProducerIdentity]:
    """Producer identity recorded on an artifact row, if any."""
    return ProducerIdentity.from_metadata(
        (row.get("metadata") or {}).get(PRODUCER_METADATA_KEY)
    )
