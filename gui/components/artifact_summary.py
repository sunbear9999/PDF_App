"""The first section of the inspector: what the record is, in plain words.

An entity is not a passage: it shows its name, type, other spellings and how
often it is mentioned. Every other record shows the source passage it quotes.
"""
from core.models.entity_categories import type_label

MAX_ALIASES = 8


def caption(key: str) -> str:
    return key.replace("_", " ").capitalize()


def readable(value) -> str:
    if value is None:
        return "—"
    if isinstance(value, bool):
        return "Yes" if value else "No"
    if isinstance(value, (dict, list)):
        return f"{len(value)} item(s)"
    return str(value)


def summary(row):
    """``(title, [(help_key, value, caption), ...])`` for the record's leading section."""
    kind = row.get("artifact_type")
    meta = row.get("metadata") or {}
    if kind == "entity":
        fields = [("exact_text", row.get("exact_text"), "Name"),
                  ("metadata.entity_type", type_label(meta.get("entity_type")), "Type")]
        aliases = [a for a in meta.get("aliases") or [] if a and a != row.get("exact_text")]
        if aliases:
            more = f" (+{len(aliases) - MAX_ALIASES} more)" if len(aliases) > MAX_ALIASES else ""
            fields.append(("metadata.aliases", ", ".join(aliases[:MAX_ALIASES]) + more, "Also written as"))
        fields.append(("metadata.mention_count", _mentions(meta), "Mentions"))
        if meta.get("parent_entity"):
            fields.append(("metadata.parent_entity", meta["parent_entity"].get("exact_text"), "Part of"))
        if meta.get("cited_entry"):
            fields.append(("bibliography_match", meta["cited_entry"].get("exact_text"), "Also cites"))
        return "Entity", fields
    if kind == "entity_mention":
        fields = [("exact_text", row.get("exact_text"), "Mention"),
                  ("metadata.entity_type", type_label(meta.get("entity_type")), "Type")]
        if meta.get("context_text"):
            fields.append(("surrounding_context", meta["context_text"], "Surrounding context"))
        return "Entity mention", fields
    fields = [("artifact_type", row.get("artifact_type"), "Artifact type"), ("exact_text", row.get("exact_text"), "Exact text")]
    if meta.get("context_text"):
        fields.append(("surrounding_context", meta["context_text"], "Surrounding context"))
    return "Source passage", fields


def _mentions(meta):
    count, sources = meta.get("mention_count"), meta.get("source_count")
    project = meta.get("project_mention_count")
    if count is None:
        return "—"
    text = f"{count}"
    if sources:
        text += f" in {sources} source{'s' if int(sources) != 1 else ''}"
    if project not in (None, count):
        text += f" · {project} in the whole project"
    return text


def sentence_with_mention(sentence):
    """Escaped sentence HTML with the mention in bold."""
    import html
    text, start, end = sentence["text"], sentence.get("start"), sentence.get("end")
    if start is None:
        return html.escape(text)
    return html.escape(text[:start]) + "<b>" + html.escape(text[start:end]) + "</b>" + html.escape(text[end:])


def example_fields(row):
    """One example sentence: its text, file name and printed page (Jump opens the exact spot)."""
    import os
    page = (row.get("locator") or {}).get("page")
    where = os.path.basename(row.get("source_path") or "") or "—"
    if isinstance(page, int):
        where += f", page {page + 1}"
    return [("exact_text", row.get("exact_text"), "Sentence"), ("locator", where, "Where")]


def sentence_box(parent, sentence):
    """Group box with the full sentence around a mention, so its context shows without jumping."""
    from PySide6.QtCore import Qt
    from PySide6.QtWidgets import QGroupBox, QLabel, QVBoxLayout
    page = sentence.get("page")
    box = QGroupBox("In this sentence" + (f" — page {page + 1}" if isinstance(page, int) else ""), parent)
    label = QLabel(sentence_with_mention(sentence), box)
    label.setTextFormat(Qt.TextFormat.RichText)
    label.setWordWrap(True)
    label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
    label.setAccessibleName("Sentence containing this mention; the mention is in bold")
    QVBoxLayout(box).addWidget(label)
    return box
