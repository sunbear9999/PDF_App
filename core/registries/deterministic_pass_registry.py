"""
Registry for reusable deterministic (no-LLM) block ranking/filtering passes.

A "pass" is a named, parameterized wrapper around RankingFeatureService that
scores DocumentBlocks by intrinsic, query-agnostic features (citation
density, causal/argument-marker density, date density, etc.). Analysis
templates declare which passes to run (and with what params) via their
``deterministic_passes`` field; the RANK_BLOCKS step resolves pass_ids
against this registry at execution time.

Built-in passes are seeded in PapyrusCore.__init__() by calling
DeterministicPassRegistry.seed_builtin_passes(). Plugins register their own
passes via api.register_deterministic_pass(spec).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional

from core.models.analysis_models import DeterministicPassInput, DeterministicPassOutput


@dataclass
class DeterministicPassSpec:
    """A named, parameterized deterministic ranking/filtering pass."""
    pass_id: str
    pass_name: str
    description: str
    run_fn: Callable[[DeterministicPassInput], DeterministicPassOutput]
    param_schema: Dict[str, Any] = field(default_factory=dict)
    plugin_id: Optional[str] = None


class DeterministicPassRegistry:
    """Registry holding all deterministic ranking/filtering passes."""

    def __init__(self) -> None:
        self._passes: Dict[str, DeterministicPassSpec] = {}

    # ------------------------------------------------------------------

    def register(self, spec: DeterministicPassSpec) -> None:
        self._passes[spec.pass_id] = spec

    def get(self, pass_id: str) -> Optional[DeterministicPassSpec]:
        return self._passes.get(pass_id)

    def all(self) -> List[DeterministicPassSpec]:
        return list(self._passes.values())

    def remove_by_plugin(self, plugin_id: str) -> None:
        ids_to_remove = [
            pid for pid, spec in self._passes.items() if spec.plugin_id == plugin_id
        ]
        for pid in ids_to_remove:
            self._passes.pop(pid, None)

    # ------------------------------------------------------------------
    # Built-in pass seeding — called once at startup
    # ------------------------------------------------------------------

    @staticmethod
    def seed_builtin_passes(
        registry: "DeterministicPassRegistry",
        ranking_feature_service,
    ) -> None:
        """Seed the registry with built-in passes over RankingFeatureService.

        Each built-in pass is a thin, named parameterization — the
        preferred_features/preferred_block_types/boost_patterns choices
        below map to what each analysis-template family cares about most.
        """

        def _make_run_fn(preferred_features, preferred_block_types, boost_patterns):
            def _run(pass_input: DeterministicPassInput) -> DeterministicPassOutput:
                top_n = pass_input.params.get("top_n")
                ranked = ranking_feature_service.rank_with_compute(
                    pass_input.blocks,
                    preferred_features=preferred_features,
                    preferred_block_types=preferred_block_types,
                    boost_patterns=boost_patterns,
                    top_n=top_n,
                    citation_index=pass_input.citation_index,
                    entity_list=pass_input.entity_list,
                    date_list=pass_input.date_list,
                    table_list=pass_input.table_list,
                )
                return DeterministicPassOutput(
                    ranked_blocks=ranked,
                    stats={"candidate_count": len(ranked)},
                )
            return _run

        registry.register(DeterministicPassSpec(
            pass_id="quote_candidate_rank",
            pass_name="Quote Candidates (Argument/Causal Density)",
            description=(
                "Ranks paragraphs by quote-candidate score, causal/argument-marker "
                "density, and in-text citation presence. Default for Argument Map."
            ),
            run_fn=_make_run_fn(
                preferred_features=[
                    "quote_candidate_score", "causal_marker_density",
                    "argument_marker_density", "contains_in_text_citation",
                ],
                preferred_block_types=["paragraph"],
                boost_patterns=["causal_marker", "argument_marker"],
            ),
            param_schema={
                "top_n": {"type": "integer", "label": "Max blocks to keep", "default": 25, "min": 1, "max": 200},
            },
        ))

        registry.register(DeterministicPassSpec(
            pass_id="date_dense_rank",
            pass_name="Date-Dense Blocks",
            description="Ranks blocks by date presence/density. Useful for timeline-style templates.",
            run_fn=_make_run_fn(
                preferred_features=["contains_date", "date_density"],
                preferred_block_types=["paragraph"],
                boost_patterns=[],
            ),
            param_schema={
                "top_n": {"type": "integer", "label": "Max blocks to keep", "default": 25, "min": 1, "max": 200},
            },
        ))

        registry.register(DeterministicPassSpec(
            pass_id="citation_dense_rank",
            pass_name="Citation-Dense Blocks",
            description="Ranks blocks by in-text citation presence/density. Useful for citation-audit templates.",
            run_fn=_make_run_fn(
                preferred_features=["contains_in_text_citation", "citation_density"],
                preferred_block_types=["paragraph"],
                boost_patterns=[],
            ),
            param_schema={
                "top_n": {"type": "integer", "label": "Max blocks to keep", "default": 25, "min": 1, "max": 200},
            },
        ))

        registry.register(DeterministicPassSpec(
            pass_id="entity_dense_rank",
            pass_name="Entity-Dense Blocks (People/Orgs)",
            description="Ranks blocks by person/organization mention density.",
            run_fn=_make_run_fn(
                preferred_features=["contains_person", "contains_organization", "entity_density"],
                preferred_block_types=["paragraph"],
                boost_patterns=[],
            ),
            param_schema={
                "top_n": {"type": "integer", "label": "Max blocks to keep", "default": 25, "min": 1, "max": 200},
            },
        ))
