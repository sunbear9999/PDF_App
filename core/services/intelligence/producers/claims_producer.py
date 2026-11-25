"""Claims stage: classify current source spans into grounded argument units.

Each argument unit records the method that actually labelled it — the
built-in heuristic, the optional zero-shot enhancer (only for sentences the
heuristic left unknown) or a custom ONNX model — not just "sentence
classifier".
"""
from __future__ import annotations

from dataclasses import replace
import re
from typing import Any, Dict, List

from core.models.intelligence_models import ArtifactRecord, ProcessingStage
from core.models.producer_contract import ProducerContext, ProducerResult
from core.models.producer_models import ProducerIdentity
from core.services.intelligence.artifact_eligibility import is_current_artifact
from core.services.intelligence.argument_role_policy import VERSION, ROLES
from core.services.intelligence.review_migration import ReviewMigration, source_span_key

ENHANCER_SETTING = "use_sentence_role_enhancer"
_BATCH = 256


class ClaimsProducer:
    producer_id = "sentence_role_classifier"

    def __init__(self, project_manager: Any, artifact_service: Any, classifier: Any) -> None:
        self._pm = project_manager
        self._artifacts = artifact_service
        self._classifier = classifier

    def identity(self, context: ProducerContext) -> ProducerIdentity:
        if not self._classifier:
            return ProducerIdentity(producer_id=self.producer_id,
                                    fallback_reason="no classifier service")
        status = self._classifier.status(self._use_enhancer(context))
        if status["custom_model_installed"]:
            return self._classifier.identity_for({"backend": "onnx"})
        base = self._classifier.identity_for({"backend": "heuristic"})
        if status["enhancer_enabled"]:
            enhancer = self._classifier.identity_for({"backend": "zero_shot_nli"})
            return replace(
                base, backend="heuristic+zero_shot_nli",
                model_id=enhancer.model_id, model_version=enhancer.model_version,
                model_digest=enhancer.model_digest,
                configuration_digest=enhancer.configuration_digest,
            )
        return base

    def run(self, context: ProducerContext) -> ProducerResult:
        db = getattr(self._pm, "db_intelligence", None)
        if not db or not self._classifier or not self._artifacts:
            return ProducerResult()
        sentences = self._current_sentences(db, context.source_id)
        if not sentences:
            return ProducerResult(identity=self.identity(context))
        contexts = [_context(sentences, index) for index in range(len(sentences))]
        texts = [item.get("exact_text") or "" for item in sentences]
        results: List[Dict[str, Any]] = []
        use_enhancer = self._use_enhancer(context)
        for start in range(0, len(texts), _BATCH):
            context.raise_if_cancelled()
            results.extend(self._classifier.classify(
                texts[start:start + _BATCH], contexts=contexts[start:start + _BATCH],
                use_enhancer=use_enhancer,
            ))
        units = self._build_units(db, context, sentences, results)
        context.raise_if_cancelled()
        db.upsert_artifacts(units)
        current = {unit.artifact_id for unit in units}
        previous = {
            str(row.get("artifact_id")) for row in db.list_artifacts(
                source_id=context.source_id, artifact_type="argument_unit", limit=100000,
            ) if row.get("artifact_id")
        }
        # Retire only after the replacement set is published.
        retiring = previous - current
        retiring -= ReviewMigration(self._artifacts).carry_forward(retiring, current, source_span_key)
        db.mark_artifact_ids_status(sorted(retiring), "superseded")
        return ProducerResult(output_ids=[unit.artifact_id for unit in units],
                              identity=self.identity(context),
                              diagnostics=_role_counts(results))

    def _build_units(self, db, context, sentences, results):
        reviewed = {
            row.get("parent_artifact_id") for row in db.list_artifacts(
                source_id=context.source_id, artifact_type="argument_unit", limit=100000,
            ) if is_current_artifact(db, row)
            and row.get("review_state") in {"verified", "corrected"}
        }
        units = []
        for sentence, result in zip(sentences, results):
            context.raise_if_cancelled()
            result = dict(result)
            metadata = sentence.get("metadata") or {}
            if sentence.get("review_state") in {"verified", "corrected"} and metadata.get("class_label") in ROLES:
                result.update(label=metadata["class_label"], argument_eligible=True)
            self._annotate_sentence(db, sentence, result)
            if result.get("argument_eligible", result.get("label") in ROLES) or sentence["artifact_id"] in reviewed:
                units.append(self._unit(db, context.source_id, sentence, result))
        return units

    def _annotate_sentence(self, db, sentence, result):
        if sentence.get("review_state") in {"verified", "corrected"}:
            return
        db.update_artifact_metadata(sentence["artifact_id"], {
            "class_label": result.get("label", "unknown"),
            "class_confidence": result.get("confidence"),
            "class_scores": result.get("scores") or {},
            "aux_tags": result.get("aux", []),
            "classifier_backend": result.get("backend"), "classifier_version": VERSION,
            "argument_eligible": result.get("argument_eligible", result.get("label") in ROLES),
            "argument_status": result.get("argument_status"),
            "eligibility_reason": result.get("eligibility_reason"),
            "score_kind": result.get("score_kind"),
            "suggested_role": result.get("suggested_role"),
            "role_candidates": result.get("role_candidates") or [],
            "abstention_reason": result.get("abstention_reason"),
            "classifier_identity": self._classifier.identity_for(result).to_metadata(),
        })

    @staticmethod
    def _use_enhancer(context: ProducerContext) -> bool:
        return bool(context.settings.get(ENHANCER_SETTING, True))

    def _current_sentences(self, db, source_id: str) -> List[Dict[str, Any]]:
        rows: List[Dict[str, Any]] = []
        for kind in ("sentence", "transcript_segment"):
            rows.extend(self._artifacts.get_all_artifacts(
                source_id, {"artifact_type": kind, "status": "complete"}
            ))
        rows = [row for row in rows if is_current_artifact(db, row)]
        rows.sort(key=lambda row: (
            int((row.get("locator") or {}).get("page") or 0),
            _block_order((row.get("locator") or {}).get("block_id")),
            int((row.get("locator") or {}).get("char_start") or 0),
        ))
        return rows

    def _unit(self, db, source_id: str, sentence: Dict[str, Any],
              result: Dict[str, Any]) -> ArtifactRecord:
        role = str(result.get("label") or "unknown")
        human_role = (sentence.get("metadata") or {}).get("class_label")
        if sentence.get("review_state") in {"corrected", "verified"} and human_role:
            role = str(human_role)
        record = ArtifactRecord(
            artifact_id=ArtifactRecord.stable_id(
                source_id, "argument_unit", "source_span", str(sentence["artifact_id"])
            ),
            source_id=source_id, source_path=sentence.get("source_path"),
            source_type=sentence.get("source_type"), artifact_type="argument_unit",
            storage_ref_type="source_span", storage_ref_id=str(sentence["artifact_id"]),
            parent_artifact_id=str(sentence["artifact_id"]),
            exact_text=str(sentence.get("exact_text") or ""),
            normalized_text=str(sentence.get("normalized_text") or ""),
            locator=sentence.get("locator") or {},
            section_path=sentence.get("section_path") or [],
            metadata=_unit_metadata(sentence, result, role),
            confidence=result.get("confidence"), created_by="sentence_classifier",
            extractor_name="sentence_role_classifier", extractor_version=VERSION,
            input_hash=self._artifacts.text_input_hash("|".join([
                str(sentence.get("exact_text") or ""),
                " > ".join(sentence.get("section_path") or []),
            ])),
        )
        return self._classifier.identity_for(result).stamp(record)


def _unit_metadata(sentence, result, role):
    metadata = sentence.get("metadata") or {}
    return {
        "stage": ProcessingStage.CLAIMS.value, "argument_role": role,
        "role_scores": result.get("scores") or {},
        "aux_tags": result.get("aux") or [],
        "canonical_source_artifact_id": sentence["artifact_id"],
        "classifier_backend": result.get("backend"), "classifier_version": VERSION,
        "argument_eligible": result.get("argument_eligible", role in ROLES),
        "eligibility_reason": result.get("eligibility_reason"),
        "score_kind": result.get("score_kind"),
        "decision_cues": result.get("decision_cues") or [],
        "role_candidates": result.get("role_candidates") or [],
        "role_margin": result.get("role_margin"),
        "heuristic_label": result.get("heuristic_label"),
        "provenance_kind": "grounded_source_span",
        "structure": metadata.get("structure") or {},
        "block_type": metadata.get("block_type", ""),
        "is_fragment": bool(metadata.get("is_fragment")),
    }


def _context(sentences: List[Dict[str, Any]], index: int) -> Dict[str, Any]:
    sentence = sentences[index]
    metadata = sentence.get("metadata") or {}
    return {
        "section_path": sentence.get("section_path") or [],
        "locator": sentence.get("locator") or {},
        "structure": metadata.get("structure") or {},
        "block_type": metadata.get("block_type", ""),
        "is_fragment": bool(metadata.get("is_fragment")),
        "previous_text": _neighbor_text(sentences, index, -1),
        "next_text": (
            _neighbor_text(sentences, index, 1)
        ),
    }


def _block_order(block_id):
    match = re.search(r"_b(\d+)$", str(block_id or ""))
    return (int(match[1]) if match else 0, str(block_id or ""))


def _neighbor_text(sentences, index, delta):
    other = index + delta
    if not 0 <= other < len(sentences):
        return ""
    left, right = sentences[index], sentences[other]
    if left.get("section_path") != right.get("section_path"):
        return ""
    if (left.get("locator") or {}).get("page") != (right.get("locator") or {}).get("page"):
        return ""
    return str(right.get("exact_text") or "")


def _role_counts(results: List[Dict[str, Any]]) -> Dict[str, Any]:
    counts: Dict[str, int] = {}
    for result in results:
        key = f"{result.get('backend')}:{result.get('label')}"
        counts[key] = counts.get(key, 0) + 1
    return {"labels_by_backend": counts,
            "eligible": sum(bool(item.get("argument_eligible")) for item in results),
            "excluded": sum(item.get("argument_status") == "excluded" for item in results),
            "background": sum(item.get("argument_status") == "background" for item in results)}
