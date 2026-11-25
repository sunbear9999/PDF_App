"""Registry for reusable artifact-backed Intelligence views."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


@dataclass
class IntelligenceViewSpec:
    id: str
    label: str
    required_artifact_types: List[str] = field(default_factory=list)
    scopes: List[str] = field(default_factory=lambda: ["project", "source"])
    renderer_id: str = "intelligence.artifact_list"
    query: Dict[str, Any] = field(default_factory=dict)
    actions: List[str] = field(default_factory=list)
    empty_state: str = "No intelligence artifacts are available for this view."
    plugin_id: Optional[str] = None
    description: str = ""
    # Orders (``ArtifactQuery.order_by`` keys) and filter fields offered above the list.
    sorts: List[str] = field(default_factory=list)
    filters: List[str] = field(default_factory=list)
    default_sort: str = ""
    hide_measures: bool = False   # entity lists start without dates, numbers and measures


SORT_LABELS = {"mentions_desc": "Most mentioned", "mentions_asc": "Least mentioned", "alpha": "Name A–Z",
               "alpha_desc": "Name Z–A", "recent": "Recently updated", "date": "Date",
               "confidence_desc": "Most confident"}
FILTER_LABELS = {"entity_type": "Types", "artifact_type": "Kinds", "review_state": "Review",
                 "argument_role": "Roles", "edge_type": "Connections", "tag": "Tags", "source_tag": "Document tags"}
TAG_FILTERS = ["tag", "source_tag"]          # every tab with a filter row narrows by tags too
ENTITY_SORTS = ["mentions_desc", "mentions_asc", "alpha", "alpha_desc", "recent"]
TEXT_SORTS = ["recent", "alpha", "alpha_desc", "confidence_desc"]


class IntelligenceViewRegistry:
    def __init__(self) -> None:
        self._views: Dict[str, IntelligenceViewSpec] = {}

    def register(self, spec: IntelligenceViewSpec) -> None:
        if not spec.id:
            raise ValueError("IntelligenceViewSpec requires a stable id.")
        self._views[spec.id] = spec

    def unregister(self, view_id: str) -> None:
        self._views.pop(view_id, None)

    def get(self, view_id: str) -> Optional[IntelligenceViewSpec]:
        return self._views.get(view_id)

    def all(self) -> List[IntelligenceViewSpec]:
        return list(self._views.values())

    def remove_by_plugin(self, plugin_id: str) -> None:
        for view_id in [
            key for key, value in self._views.items() if value.plugin_id == plugin_id
        ]:
            self._views.pop(view_id, None)


def builtin_intelligence_views() -> List[IntelligenceViewSpec]:
    return [
        IntelligenceViewSpec(
            "overview", "Overview", renderer_id="intelligence.overview",
            actions=["refresh", "backfill", "rebuild_stale"],
            empty_state="Add a source or select sources to backfill.",
            description="Extraction coverage and processing status. Run missing prepares stages that have not completed.",
        ),
        IntelligenceViewSpec(
            "artifacts", "Explore", renderer_id="intelligence.artifact_list",
            actions=["search", "inspect_provenance", "review", "history", "merge"],
            description="Search everything the system extracted. Narrow by kind (entities, sentences, notes…), entity type and review state, and sort. "
                        "Choose Kinds: Entity to sort by mentions and merge duplicates, e.g. search “Spain”, select the spellings and Merge.",
            sorts=["recent", "alpha", "alpha_desc", "confidence_desc", "mentions_desc", "mentions_asc"],
            filters=["artifact_type", "entity_type", "review_state", *TAG_FILTERS],
        ),
        IntelligenceViewSpec(
            "entities", "People & Places", ["entity", "entity_mention"],
            renderer_id="intelligence.entities",
            actions=["verify", "correct", "reject", "merge", "split", "promote"],
            empty_state="Run the Entities stage to extract typed entities.",
            description="People, places, organisations, groups and other names, most mentioned first. Sort, filter by type, or search; select two or more to merge spellings of the same name.",
            sorts=ENTITY_SORTS, filters=["entity_type", "review_state", *TAG_FILTERS], default_sort="mentions_desc", hide_measures=True,
        ),
        IntelligenceViewSpec(
            "timeline", "Timeline", ["date_mention"],
            renderer_id="intelligence.timeline",
            actions=["filter", "jump_to_source", "verify"],
            empty_state="Run Dates and sentence/transcript processing for contextual event candidates, or broaden the timeline filters. All-date diagnostics can show excluded observations.",
            description="Chronological event candidates with sentence evidence, uncertainty, Boolean entity/proximity filters and saved personal exclusions. Co-mention does not establish participation; select a row to inspect its date and jump to the source.",
        ),
        IntelligenceViewSpec(
            "claims", "Claims & Evidence", ["argument_unit"],
            renderer_id="intelligence.claims",
            actions=["filter", "jump_to_source", "verify"],
            empty_state="Run Claims and Graph Seeds to connect claims with evidence.",
            description="Argument units labeled as claims, evidence, reasoning, counterclaims, or limitations. Filter by role; correct a role when the classifier is wrong.",
            sorts=TEXT_SORTS, filters=["argument_role", "review_state", *TAG_FILTERS],
        ),
        IntelligenceViewSpec(
            'network', 'Entity Network', ['entity'], renderer_id='intelligence.entity_network',
            actions=['filter', 'expand', 'review', 'jump_to_source'],
            empty_state='Run Entities and review its evidence-backed connections, or broaden the current network perspective.',
            description='Independent entity connections with source evidence. Candidate, co-occurrence and accepted semantic links are distinguished; explore endpoints and save personal perspectives.',
        ),
        IntelligenceViewSpec(
            "graph", "Graph", ["entity", "entity_mention", "argument_unit", "sentence"],
            renderer_id="intelligence.graph",
            actions=["filter", "expand_community", "explain_with_ai", "promote"],
            empty_state="Run Entities, Claims, and Graph Seeds to build the local graph.",
            description="Explore structural connections between extracted entities, passages and argument units. Co-occurrence is not evidence of a relationship.",
        ),
        IntelligenceViewSpec(
            "citations", "Citations", ["citation_entry", "in_text_citation"],
            renderer_id="intelligence.citations",
            actions=["filter", "jump_to_source", "verify"],
            empty_state="Run Citations to build the citation view.",
            description="Bibliography entries contain their in-text references. Edit a selected reference's assignment inline, or browse every reference to an entry.",
            sorts=["recent", "alpha", "alpha_desc"], filters=["artifact_type", "review_state", *TAG_FILTERS],
        ),
        IntelligenceViewSpec(
            "notes", "Notes", ["document_note", "note_reference"],
            description="Document footnotes and endnotes, plus their markers. Select an item and jump to its exact source passage.",
            empty_state="Run Citations to detect document notes and their markers.",
            sorts=["recent", "alpha"], filters=["artifact_type", "review_state", *TAG_FILTERS],
        ),
        IntelligenceViewSpec(
            "contents", "Contents", ["toc_entry", "section"],
            description="Contents entries and document headings. Linked entries point to their matching chapter or section.",
            empty_state="Run Sentences & sections to detect headings and contents links.",
            sorts=["recent", "alpha"], filters=["artifact_type", "review_state", *TAG_FILTERS],
        ),
        IntelligenceViewSpec(
            "runs", "Runs", renderer_id="intelligence.runs",
            description="Advanced processing history: stage versions, status, timing and failures. Select a run to inspect its diagnostics.",
            actions=["cancel", "rerun", "inspect_logs"],
        ),
    ]
