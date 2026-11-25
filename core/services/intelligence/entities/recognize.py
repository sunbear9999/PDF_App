"""Run every registered entity extractor over canonical sentence records."""
from __future__ import annotations

from typing import Any, Callable, Dict, List, Optional

from core.models.intelligence_models import EntityMention
from core.services.intelligence.entities.surfaces import region
from core.services.intelligence.entity_extractor import ExtractContext, normalize_key


def sentence_id(row: Dict[str, Any]) -> str:
    return str(row.get("storage_ref_id") or row.get("artifact_id") or "")


def contexts_for(records: List[Dict[str, Any]], source_id: str, glossary: Dict[str, List[str]]) -> List[ExtractContext]:
    return [
        ExtractContext(
            doc_id=source_id,
            block_id=str((row.get("locator") or {}).get("block_id") or ""),
            page_num=(row.get("locator") or {}).get("page"),
            section_path=list(row.get("section_path") or []),
            project_glossary=glossary,
        )
        for row in records
    ]


def _batches(extractor: Any, texts: List[str], contexts: List[ExtractContext]) -> List[List[EntityMention]]:
    failure = f"Entity extractor {extractor.id} failed; existing results were retained."
    if hasattr(extractor, "extract_batch"):
        try:
            batches = list(extractor.extract_batch(texts, contexts))
        except Exception as exc:
            raise RuntimeError(failure) from exc
        if len(batches) != len(texts):
            raise RuntimeError(failure) from ValueError(
                f"Expected {len(texts)} entity batches, received {len(batches)}")
        return batches
    batches = []
    for text, ctx in zip(texts, contexts):
        try:
            batches.append(extractor.extract(text, ctx))
        except Exception as exc:
            raise RuntimeError(failure) from exc
    return batches


def recognize(extractors: List[Any], records: List[Dict[str, Any]], source_id: str,
              glossary: Dict[str, List[str]], cancel_check: Callable[[], bool]) -> Optional[List[EntityMention]]:
    """Raw mentions placed on their sentence, or None when cancelled."""
    contexts = contexts_for(records, source_id, glossary)
    texts = [row.get("exact_text") or "" for row in records]
    mentions: List[EntityMention] = []
    for extractor in extractors:
        if cancel_check():
            return None
        for row, ctx, found in zip(records, contexts, _batches(extractor, texts, contexts)):
            for mention in found:
                mention.doc_id = source_id
                mention.block_id = ctx.block_id
                mention.page_num = ctx.page_num
                mention.section_path = ctx.section_path
                mention.sentence_id = sentence_id(row)
                mention.metadata["evidence_artifact_id"] = row.get("artifact_id")
                mention.metadata["region"] = region(row)
                if not mention.normalized_key:
                    mention.normalized_key = normalize_key(mention.surface_text, mention.entity_type)
                mentions.append(mention)
    return mentions
