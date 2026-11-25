"""Registry and dependency graph for source-intelligence processing stages."""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from core.models.intelligence_models import (
    ProcessingStage,
    StageResourceClass,
    StageSpec,
)


class IntelligenceStageRegistry:
    def __init__(self, producer_registry: Any = None) -> None:
        self._specs: Dict[str, StageSpec] = {}
        self._plugin_ids: Dict[str, Optional[str]] = {}
        self._project_inputs: Dict[str, Any] = {}
        self.producer_registry = producer_registry

    def register(self, spec: StageSpec, plugin_id: Optional[str] = None) -> None:
        """Register a stage; reject missing producers and dependency cycles."""
        if not spec.id:
            raise ValueError("StageSpec requires a stable id.")
        handler = spec.handler_id or spec.id
        producers = self.producer_registry
        if spec.enabled and producers is not None and not producers.has(handler):
            raise ValueError(
                f"Stage '{spec.id}' has no registered producer '{handler}'. "
                "Register the producer before the stage."
            )
        previous = (self._specs.get(spec.id), self._plugin_ids.get(spec.id))
        self._specs[spec.id] = spec
        self._plugin_ids[spec.id] = plugin_id
        try:
            for category in spec.source_categories:
                self.expand_dependencies([spec.id], category)
        except ValueError:
            self._restore(spec.id, previous)
            raise

    def _restore(self, stage_id: str, previous) -> None:
        if previous[0] is None:
            self.unregister(stage_id)
        else:
            self._specs[stage_id], self._plugin_ids[stage_id] = previous

    def bind_producers(self, producer_registry: Any) -> None:
        """Attach the producer registry and verify every enabled stage can run."""
        self.producer_registry = producer_registry
        missing = [
            spec.id for spec in self.all()
            if not producer_registry.has(spec.handler_id or spec.id)
        ]
        if missing:
            raise ValueError(f"Stages without a registered producer: {', '.join(missing)}")

    def stages_owned_by(self, plugin_id: str) -> List[str]:
        return [key for key, owner in self._plugin_ids.items() if owner == plugin_id]

    def plugin_owner(self, stage_id: str) -> Optional[str]:
        return self._plugin_ids.get(stage_id)

    def register_project_input(self, name: str, provider) -> None:
        """``provider(db) -> str``: a digest of project-wide state a stage reads."""
        self._project_inputs[name] = provider

    def project_input_digests(self, spec: StageSpec, db) -> dict:
        return {name: self._project_inputs[name](db) for name in spec.project_inputs if name in self._project_inputs}

    def unregister(self, stage_id: str) -> None:
        self._specs.pop(stage_id, None)
        self._plugin_ids.pop(stage_id, None)

    def get(self, stage_id: str) -> Optional[StageSpec]:
        return self._specs.get(stage_id)

    def all(self, *, include_disabled: bool = False) -> List[StageSpec]:
        return [
            spec for spec in self._specs.values()
            if include_disabled or spec.enabled
        ]

    def for_category(self, category: str) -> List[StageSpec]:
        return [
            spec for spec in self.all()
            if category in spec.source_categories
        ]

    def dependencies_for(self, stage_id: str, category: str) -> List[str]:
        """Resolve category-specific dependencies without duplicating specs."""
        if category == "data":
            data_deps = _DATA_DEPENDENCIES.get(stage_id)
            if data_deps is not None:
                return list(data_deps)
        if stage_id in {
            ProcessingStage.ENTITIES.value,
            ProcessingStage.DATES.value,
            ProcessingStage.CLAIMS.value,
            ProcessingStage.SUMMARIES.value,
        }:
            if category in {"audio", "video"}:
                return [ProcessingStage.TRANSCRIPT.value]
            if stage_id == ProcessingStage.ENTITIES.value:
                # The document's abbreviation key names and types its short forms (S15).
                return [ProcessingStage.SECTIONS.value, ProcessingStage.ABBREVIATIONS.value]
            return [ProcessingStage.SECTIONS.value]
        if stage_id == ProcessingStage.EMBEDDINGS.value:
            return [
                ProcessingStage.TRANSCRIPT.value
                if category in {"audio", "video"}
                else ProcessingStage.FULL_TEXT_INDEX.value
            ]
        spec = self.get(stage_id)
        return list(spec.dependencies) if spec else []

    def expand_dependencies(self, stage_ids: List[str], category: str) -> List[str]:
        """Topologically expand a requested stage set, preserving stable order."""
        result: List[str] = []
        visiting = set()
        visited = set()

        def visit(stage_id: str) -> None:
            if stage_id in visited:
                return
            if stage_id in visiting:
                raise ValueError(f"Intelligence stage dependency cycle at '{stage_id}'")
            spec = self.get(stage_id)
            if not spec or not spec.enabled or category not in spec.source_categories:
                return
            visiting.add(stage_id)
            for dependency in self.dependencies_for(stage_id, category):
                visit(dependency)
            visiting.remove(stage_id)
            visited.add(stage_id)
            result.append(stage_id)

        for stage_id in stage_ids:
            visit(stage_id)
        return result

    def downstream_of(self, stage_id: str, category: str) -> List[str]:
        result = []
        frontier = [stage_id]
        seen = {stage_id}
        while frontier:
            current = frontier.pop(0)
            for spec in self.for_category(category):
                if spec.id in seen:
                    continue
                if current in self.dependencies_for(spec.id, category):
                    seen.add(spec.id)
                    result.append(spec.id)
                    frontier.append(spec.id)
        return result

    def remove_by_plugin(self, plugin_id: str) -> None:
        for stage_id in [
            key for key, owner in self._plugin_ids.items() if owner == plugin_id
        ]:
            self.unregister(stage_id)


# Data files (CSV/TSV/XLSX) have no pages or sentences: their grid is loaded
# directly into table/row artifacts, which are then indexed.
_DATA_DEPENDENCIES = {
    ProcessingStage.TABLES.value: [ProcessingStage.SOURCE_REGISTERED.value],
    ProcessingStage.FULL_TEXT_INDEX.value: [ProcessingStage.TABLES.value],
    ProcessingStage.EMBEDDINGS.value: [ProcessingStage.FULL_TEXT_INDEX.value],
}
_DOCUMENT = ["document", "image"]
_ALL = ["document", "image", "audio", "video", "data"]
_TEXT_AND_MEDIA = ["document", "image", "audio", "video"]


def builtin_stage_specs() -> List[StageSpec]:
    return [
        StageSpec(
            id=ProcessingStage.SOURCE_REGISTERED.value, source_categories=_ALL,
            output_artifact_types=["source"], handler_id="source_registered",
            label="Register source", description="Source record every artifact links to.",
            cost_hint="Local · instant",
        ),
        StageSpec(
            id=ProcessingStage.PARSED_BLOCKS.value,
            dependencies=[ProcessingStage.SOURCE_REGISTERED.value],
            source_categories=_DOCUMENT, output_artifact_types=["block"],
            handler_id="parsed_blocks", producer_version="3.6", label="Parse / segment",
            description="Canonical text blocks with page positions.", cost_hint="Local · fast",
        ),
        StageSpec(
            id=ProcessingStage.SECTIONS.value,
            dependencies=[ProcessingStage.PARSED_BLOCKS.value],
            source_categories=_DOCUMENT, output_artifact_types=["sentence", "section", "toc_entry"],
            # 4.3: endnote text is segmented like footnotes.
            # 4.4: sentences no longer copy their block's line geometry (memory on long documents).
            handler_id="sections", producer_version="4.4", label="Sentences & sections",
            description="Jumpable sentence artifacts and the heading outline.",
            cost_hint="Local · fast",
        ),
        StageSpec(
            id=ProcessingStage.TRANSCRIPT.value,
            dependencies=[ProcessingStage.SOURCE_REGISTERED.value],
            source_categories=["audio", "video"], output_artifact_types=["transcript_segment"],
            handler_id="transcript", label="Index transcript",
            description=(
                "Indexes an existing transcript's timed segments. It does not "
                "transcribe; it waits until transcription has finished."
            ),
            cost_hint="Local · fast", method_kind="learned",
        ),
        StageSpec(
            id=ProcessingStage.FULL_TEXT_INDEX.value,
            dependencies=[ProcessingStage.SECTIONS.value],
            source_categories=[*_DOCUMENT, "data"], output_artifact_types=["block", "sentence"],
            handler_id="full_text_index", label="Full-text index",
            description="Exact keyword search.", cost_hint="Local · fast",
        ),
        StageSpec(
            id=ProcessingStage.ENTITIES.value, source_categories=_TEXT_AND_MEDIA,
            output_artifact_types=["entity", "entity_mention"], handler_id="entities",
            # 3.0: NER surface sanitation + METHOD/DATASET/CONCEPT patterns.
            # 3.1: mentions record the extractor/model that found them.
            # 3.2: explicit grouping chains and split-specific relation endpoints.
            # 4.3: names only (no dates/quantities/document labels), possessive and
            #      lowercase-safe propagation, OCR digit-for-letter variants join their name.
            # 4.4: mentions in notes/captions/tables with a region tag; NER labels are not
            #      trusted in other languages (repeated names kept untyped).
            # 4.5: one type per name from document-wide usage rates (length-invariant);
            #      nominal coreference ("the Tribe" -> Crow Tribe); names trimmed at colons.
            # 4.6: optional typing evidence (GeoNames gazetteer, GLiNER) and language model,
            #      each behind its own setting; votes recorded on mentions.
            # 4.7: a name with no letters ("1%" -> "1") is never matched elsewhere; no
            #      co-occurrence links with dates/quantities/measures; links record a reason.
            # 4.8: short forms named and typed by the document's abbreviation key; OCR fragments
            #      without a three-letter word ("Vv Vv") are not names.
            # 4.9: titles and roles ("Justice", "Chief Justice", "batab") are TITLE, not PERSON;
            #      "Justice Gorsuch" splits into the title and the person.
            producer_version="4.9", label="Entities",
            description="People, organizations, places and concepts with their mentions.",
            cost_hint="Local NLP · medium; optional models slower", method_kind="learned",
            settings_keys=["use_gliner", "use_gazetteer", "use_language_model"],
            model_dependencies=["spacy_md", "gliner_multi", "geonames_gazetteer", "language_id"],
        ),
        StageSpec(
            id=ProcessingStage.ENTITY_LINKING.value, dependencies=[ProcessingStage.ENTITIES.value],
            source_categories=_TEXT_AND_MEDIA, output_artifact_types=["entity_mention"],
            handler_id="entity_linking", producer_version="1.0", label="Entity linking",
            description=(
                "Finds names established in your other sources inside this one, and suggests entities "
                "that may be the same across sources (for your review)."
            ),
            cost_hint="Local · fast", project_inputs=["entity_lexicon"],
        ),
        StageSpec(
            id=ProcessingStage.ABBREVIATIONS.value, dependencies=[ProcessingStage.PARSED_BLOCKS.value],
            source_categories=_DOCUMENT, output_artifact_types=["abbreviation"],
            handler_id="abbreviations", producer_version="1.0", label="Abbreviations",
            description=(
                "Reads the document's own abbreviation key (“AGN  Archivo General de la Nación”, "
                "“AD, Alzheimer’s disease;”) so its short forms are named and typed by their full names."
            ),
            cost_hint="Local · fast",
        ),
        StageSpec(
            id=ProcessingStage.CROSS_REFERENCES.value,
            dependencies=[ProcessingStage.SECTIONS.value, ProcessingStage.CITATIONS.value],
            source_categories=_DOCUMENT, output_artifact_types=["cross_reference", "caption"],
            handler_id="cross_references", producer_version="1.0", label="Cross-references",
            description=(
                "Links “see Table 7.2”, “Figure 3”, “Chapter 5”, “Appendix B”, “§ 4” and “note 12” "
                "to the caption, heading or note they name; abstains when the target is not unique."
            ),
            cost_hint="Local · fast",
        ),
        StageSpec(
            id=ProcessingStage.DATES.value, source_categories=_TEXT_AND_MEDIA,
            output_artifact_types=["date_mention", "entity", "entity_mention"], handler_id="dates",
            # 1.3: contextual timeline eligibility, identifier/quantity exclusions.
            # 1.7: bare years dated by syntax (prepositions, ranges, signatures); decades with apostrophes;
            #      dates of the world link to one project-wide DATE entity per value and precision.
            # 1.8: numeric dates ("4-2-1647") resolved by the document's day/month convention.
            producer_version="1.8", label="Dates",
            description="Contextual dates; reference and metadata years are kept separately from event dates.",
            cost_hint="Local · fast",
        ),
        StageSpec(
            id=ProcessingStage.CITATIONS.value,
            dependencies=[ProcessingStage.SECTIONS.value],
            source_categories=_DOCUMENT,
            output_artifact_types=["citation_entry", "in_text_citation", "document_note", "note_reference"],
            handler_id="citations",
            # 2.0: acronym/footnote noise removed, grouped citations credit every
            # entry they name, per-entry pages, inline legal authorities.
            # 2.2: in-text citation artifacts keep page/block/char offsets.
            # 3.1: note markers an OCR layer misread are re-read from the page image (Tesseract).
            producer_version="3.1", label="Citations", model_dependencies=["tesseract_ocr"],
            description="Bibliography entries, in-text references and their matches.",
            cost_hint="Local · medium",
        ),
        StageSpec(
            id=ProcessingStage.TABLES.value,
            dependencies=[ProcessingStage.SECTIONS.value],
            source_categories=[*_DOCUMENT, "data"], output_artifact_types=["table", "table_row"],
            handler_id="tables", producer_version="1.2", label="Tables",
            description=(
                "Tables found in documents; for CSV/XLSX files, the sheet and "
                "one searchable artifact per row."
            ),
            cost_hint="Local · medium",
        ),
        StageSpec(
            id=ProcessingStage.CLAIMS.value, source_categories=_TEXT_AND_MEDIA,
            output_artifact_types=["argument_unit"], handler_id="claims",
            # 5.0: argument eligibility before roles; abstaining NLI fallback.
            producer_version="5.4", label="Argument roles",
            description=(
                "Detects substantive argument spans and labels claim, evidence, reasoning, methodology, "
                "counterclaim or limitation (or unknown)."
            ),
            cost_hint="Built-in heuristic · fast; optional enhancer · slower",
            method_kind="heuristic", settings_keys=["use_sentence_role_enhancer"],
            model_dependencies=["sentence_role_enhancer", "sentence_classifier"],
        ),
        StageSpec(
            id=ProcessingStage.SUMMARIES.value, source_categories=_TEXT_AND_MEDIA,
            output_artifact_types=["summary"], handler_id="summaries",
            label="Extractive summaries",
            description="Representative sentences per section and document.",
            cost_hint="Local · medium",
        ),
        StageSpec(
            id=ProcessingStage.GRAPH_SEEDS.value,
            dependencies=[ProcessingStage.CLAIMS.value],
            source_categories=_TEXT_AND_MEDIA, output_artifact_types=[],
            handler_id="graph_seeds", producer_version="4.2", label="Graph seeds",
            description="Suggested relations between argument units (review before trusting).",
            cost_hint="Local/ONNX · medium", method_kind="heuristic",
            model_dependencies=["nli_relation"],
        ),
        StageSpec(
            id=ProcessingStage.EMBEDDINGS.value, source_categories=_ALL,
            resource_class=StageResourceClass.ONNX.value,
            output_artifact_types=["artifact_embedding"], handler_id="embeddings",
            label="Embeddings", description="Semantic (meaning-based) retrieval.",
            cost_hint="Configured embedding model", method_kind="learned",
            model_role="embedding_model",
        ),
        # Registered for compatibility, but never scheduled until a real
        # producer is explicitly installed and enables it.
        StageSpec(
            id=ProcessingStage.FIGURES.value,
            dependencies=[ProcessingStage.PARSED_BLOCKS.value],
            source_categories=["image", "document"],
            resource_class=StageResourceClass.ONNX.value,
            output_artifact_types=["figure"], handler_id="figures", enabled=False,
            label="Figures",
        ),
    ]
