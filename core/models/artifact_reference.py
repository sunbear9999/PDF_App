"""Persistable source reference; the quoted snapshot is never silently rewritten."""
from dataclasses import asdict, dataclass, field


@dataclass(frozen=True)
class ArtifactRef:
    artifact_id: str = ""
    quote: str = ""
    revision: int | None = None
    source_id: str = ""
    source_title: str = ""
    locator: dict = field(default_factory=dict)

    @classmethod
    def from_value(cls, value):
        if isinstance(value, cls):
            return value
        data = dict(value or {})
        meta = data.get("metadata") or {}
        nested = data.get("artifact_ref") or {}
        return cls(
            artifact_id=str(nested.get("artifact_id") or data.get("artifact_id") or ""),
            quote=str(nested.get("quote", data.get("quote", data.get("exact_text", data.get("text", "")))) or ""),
            revision=nested.get("revision", data.get("revision", meta.get("revision"))),
            source_id=str(nested.get("source_id") or data.get("source_id") or ""),
            source_title=str(nested.get("source_title") or data.get("source_title") or data.get("doc_name") or ""),
            locator=dict(nested.get("locator") or data.get("locator") or data.get("source_locator") or {}),
        )

    def to_dict(self):
        return asdict(self)


def location_label(locator):
    """Recorded coordinates are descriptive; resolution reports actual precision."""
    parts = []
    page = locator.get("page")
    if isinstance(page, int) and page >= 0:
        parts.append(f"Page {page + 1}")
    start = locator.get("start")
    if isinstance(start, (int, float)) and start >= 0:
        minutes, seconds = divmod(int(start), 60)
        parts.append(f"Time {minutes}:{seconds:02d}")
    if locator.get("char_start") is not None:
        parts.append(f"Passage characters {locator['char_start']}–{locator.get('char_end', '?')}")
    return " · ".join(parts) or "Recorded location unavailable — inspect the artifact for details."
