"""Checks an installed local model before AI Setup activates it.

Every model first passes the pinned size and SHA-256 of each file, then a
smoke test that matches how Papyrus will call it (``spec.validator``):

* ``text_pair``: tokenizer + one sentence-pair inference (rerankers, NLI,
  classifiers), plus the label map the consumer needs;
* ``encoder``: one batch, a ``[batch, tokens, dim]`` hidden state;
* ``gliner``: one labelled span prediction through the numpy decoder;
* ``glotlid``: hashed features for one sentence give a probability per label.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Callable, Dict, Tuple

from core.models.model_dependency_models import ModelDependencySpec
from core.services.intelligence.sentence_role_model import read_role_labels

Result = Tuple[bool, str]


def validate_model(spec: ModelDependencySpec) -> Result:
    try:
        ok, error = check_files(spec)
        if not ok:
            return ok, error
        return VALIDATORS.get(spec.validator or "text_pair", _text_pair)(spec)
    except Exception as exc:
        return False, f"Model validation failed: {exc}"


def check_files(spec: ModelDependencySpec) -> Result:
    for local_name in spec.hf_files or {"model.onnx": [], "tokenizer.json": []}:
        path = Path(spec.dest_dir) / local_name
        if not path.is_file():
            return False, f"Missing required file: {local_name}"
        expected_size = spec.file_sizes.get(local_name)
        if expected_size and path.stat().st_size != expected_size:
            return False, f"Unexpected size for {local_name}"
        expected_hash = spec.file_sha256.get(local_name)
        if expected_hash and sha256(path) != expected_hash:
            return False, f"Checksum mismatch for {local_name}"
    return True, ""


def _session(spec: ModelDependencySpec):
    import onnxruntime  # type: ignore
    return onnxruntime.InferenceSession(str(Path(spec.dest_dir) / "model.onnx"), providers=["CPUExecutionProvider"])


def _text_pair(spec: ModelDependencySpec) -> Result:
    import numpy as np
    from tokenizers import Tokenizer  # type: ignore
    tokenizer = Tokenizer.from_file(str(Path(spec.dest_dir) / "tokenizer.json"))
    tokenizer.enable_truncation(max_length=512)
    session = _session(spec)
    encoded = tokenizer.encode("Papyrus intelligence", "Evidence-backed local retrieval")
    inputs = {item.name for item in session.get_inputs()}
    feeds = {
        "input_ids": np.asarray([encoded.ids], dtype=np.int64),
        "attention_mask": np.asarray([encoded.attention_mask], dtype=np.int64),
        "token_type_ids": np.asarray([encoded.type_ids], dtype=np.int64),
    }
    outputs = session.run(None, {key: value for key, value in feeds.items() if key in inputs})
    if not outputs:
        return False, "ONNX smoke test returned no output."
    return check_labels(spec, len(outputs[0][0]) if len(outputs[0]) else 0)


def _encoder(spec: ModelDependencySpec) -> Result:
    from core.services.intelligence.sentence_encoder import SentenceEncoder
    vectors = SentenceEncoder(spec.dest_dir).encode(["The court held the treaty valid.", "Maya notaries wrote wills."])
    if vectors is None or vectors.ndim != 2 or vectors.shape[0] != 2 or vectors.shape[1] < 64:
        return False, "Encoder smoke test did not return one vector per sentence."
    return True, ""


def _gliner(spec: ModelDependencySpec) -> Result:
    from core.services.intelligence.gliner_runtime import GlinerRuntime
    runtime = GlinerRuntime(spec.dest_dir)
    if not runtime.is_available():
        return False, "gliner_config.json, model.onnx and tokenizer.json are required."
    spans = runtime.predict("Barack Obama visited Paris in 2015.", ["person", "location"])
    if runtime.runtime.last_error:
        return False, runtime.runtime.last_error
    if not any(span.label == "person" for span in spans):
        return False, "GLiNER smoke test found no person in a sentence that names one."
    return True, ""


def _glotlid(spec: ModelDependencySpec) -> Result:
    from core.services.extraction.glotlid_runtime import GlotLidRuntime
    runtime = GlotLidRuntime(spec.dest_dir)
    found = runtime.detect("The court held that the treaty right survived statehood.")
    if found is None:
        return False, runtime.last_error or "Language model could not be loaded."
    if found[0] != "eng":
        return False, f"Language smoke test returned {found[0]} for English text."
    return True, ""


VALIDATORS: Dict[str, Callable[[ModelDependencySpec], Result]] = {
    "text_pair": _text_pair, "encoder": _encoder, "gliner": _gliner, "glotlid": _glotlid,
}


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        while True:
            chunk = stream.read(1024 * 1024)
            if not chunk:
                break
            digest.update(chunk)
    return digest.hexdigest()


def check_labels(spec: ModelDependencySpec, width: int) -> Result:
    """A task model must expose the labels its consumer relies on, in a
    label map whose size matches the model's output width."""
    if spec.kind == "manual" and spec.task == "sentence_roles":
        try:
            labels = read_role_labels(spec.dest_dir)
            if width != len(labels):
                return False, f"Model outputs {width} scores but declares {len(labels)} roles."
            return True, ""
        except (OSError, ValueError, KeyError, TypeError) as exc:
            return False, str(exc)
    if not spec.required_labels:
        return True, ""
    try:
        config = json.loads((Path(spec.dest_dir) / "config.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False, "Model config.json with a label map is required."
    labels = {str(label).lower() for label in (config.get("id2label") or {}).values()}
    missing = [label for label in spec.required_labels if label not in labels]
    if missing:
        return False, f"Model labels are missing: {', '.join(missing)}"
    if width and width != len(labels):
        return False, f"Model outputs {width} scores but declares {len(labels)} labels."
    return True, ""
