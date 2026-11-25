"""Extensible typed artifact review queues, shared by docks and plugins."""
from dataclasses import dataclass, field


@dataclass(frozen=True)
class ReviewIssueSpec:
    id: str
    label: str
    description: str
    query: dict = field(default_factory=dict)
    plugin_id: str = ""
    resource_kind: str = "artifact"


class IntelligenceReviewRegistry:
    def __init__(self):
        self._specs = {}

    def register(self, spec):
        from core.db.intelligence_artifact_queries import coerce_query
        if spec.resource_kind not in {"artifact", "assertion", "merge"}:
            raise ValueError("Unknown review resource kind.")
        if spec.resource_kind == "artifact":
            coerce_query(spec.query)
        elif set(spec.query) - {"edge_types"} or not isinstance(spec.query.get("edge_types", []), list) or not all(isinstance(item, str) for item in spec.query.get("edge_types", [])):
            raise ValueError("Assertion queues accept typed edge_types only.")
        if not spec.id or (spec.id in self._specs and self._specs[spec.id].plugin_id != spec.plugin_id):
            raise ValueError("Review queue requires a unique owned ID.")
        self._specs[spec.id] = spec

    def get(self, issue_id):
        return self._specs.get(issue_id)

    def all(self):
        return list(self._specs.values())

    def remove_by_plugin(self, plugin_id):
        self._specs = {key: spec for key, spec in self._specs.items() if spec.plugin_id != plugin_id}


def builtin_review_issues():
    return [ReviewIssueSpec(identity, label, description, {"issue_kind": identity}) for identity, label, description in (
        ("low_confidence", "Low confidence / disagreement", "Confidence is a review cue, not a comparable probability across methods."),
        ("unclassified", "Unclassified argument roles", "Review eligible prose with an unknown role; background may correctly remain background."),
        ("citations", "Unmatched / ambiguous citations", "Choose an active bibliography entry from the same source, or confirm no match."),
        ("dates", "Unresolved dates", "Interpret the date and its precision; citation and publication dates may correctly stay excluded."),
        ("entities", "Uncertain entity categories", "Check the category in its source context. Merge/split decisions remain explicit."),
        ("migration", "Review migration conflicts", "A changed source span needs inspection before reapplying a previous decision."),
    )] + [ReviewIssueSpec("relations", "Relation proposals", "Suggested links between passages (supports, attacks…): check direction, endpoints and evidence. Links between entities are reviewed in Review connections. Acceptance is not model training.", resource_kind="assertion")] + [ReviewIssueSpec("merges", "Possible duplicate entities", "Entities of the same type with similar names (same name, one inside the other, initials, close spelling). Merge them, or choose Keep separate.", resource_kind="merge")]
