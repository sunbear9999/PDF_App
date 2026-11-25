"""Deterministic source-tag rules shared by the GUI and plugins."""
from __future__ import annotations

from dataclasses import dataclass
import os
import re
from typing import Any, Iterable, Mapping


TAG_RULE_FIELDS = (
    ("name", "Name"),
    ("path", "Full path"),
    ("folder", "Containing folder"),
    ("extension", "File extension"),
    ("source_type", "Source type"),
    ("mime_type", "MIME type"),
)

TAG_RULE_OPERATORS = (
    ("contains", "contains"),
    ("not_contains", "does not contain"),
    ("starts_with", "starts with"),
    ("ends_with", "ends with"),
    ("equals", "equals"),
    ("regex", "matches regular expression"),
)


@dataclass(frozen=True)
class TagRule:
    field: str
    operator: str
    value: str
    case_sensitive: bool = False
    untagged_only: bool = False

    def validate(self) -> None:
        fields = {item[0] for item in TAG_RULE_FIELDS}
        operators = {item[0] for item in TAG_RULE_OPERATORS}
        if self.field not in fields or self.operator not in operators:
            raise ValueError("Choose a supported tag rule.")
        if not self.value:
            raise ValueError("Enter a value to match.")
        if self.operator == "regex":
            try:
                re.compile(self.value, 0 if self.case_sensitive else re.IGNORECASE)
            except re.error as exc:
                raise ValueError(f"Invalid regular expression: {exc}") from exc


class TagRuleEngine:
    """Matches rules against source records without file reads or model calls."""

    def matching_paths(
        self,
        sources: Iterable[Mapping[str, Any]],
        rule: TagRule,
        tagged_paths: set[str] | None = None,
    ) -> list[str]:
        rule.validate()
        tagged = tagged_paths or set()
        matches = []
        for source in sources:
            path = str(source.get("path") or "")
            if not path or (rule.untagged_only and path in tagged):
                continue
            if self._matches(self._field_value(source, rule.field), rule):
                matches.append(path)
        return matches

    @staticmethod
    def _field_value(source: Mapping[str, Any], field: str) -> str:
        path = str(source.get("path") or "")
        values = {
            "name": str(source.get("title") or os.path.basename(path)),
            "path": path,
            "folder": os.path.dirname(path),
            "extension": os.path.splitext(path)[1].lstrip("."),
            "source_type": str(source.get("source_type") or ""),
            "mime_type": str(source.get("mime_type") or ""),
        }
        return values[field]

    @staticmethod
    def _matches(actual: str, rule: TagRule) -> bool:
        expected = rule.value
        if rule.operator == "regex":
            flags = 0 if rule.case_sensitive else re.IGNORECASE
            return re.search(expected, actual, flags) is not None
        if not rule.case_sensitive:
            actual, expected = actual.casefold(), expected.casefold()
        operations = {
            "contains": lambda: expected in actual,
            "not_contains": lambda: expected not in actual,
            "starts_with": lambda: actual.startswith(expected),
            "ends_with": lambda: actual.endswith(expected),
            "equals": lambda: actual == expected,
        }
        return operations[rule.operator]()
