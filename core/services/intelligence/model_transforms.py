"""Post-download model transforms run by the installer (``spec.transform``).

``quantize_embeddings``: some published int8 exports are unusable (the
GLiNER multilingual int8/uint8 graphs score nothing above 0.15 where the fp32
graph scores 0.99), because quantising the attention MatMuls of DeBERTa-style
encoders destroys them. Most of such a model's size is the word-embedding
table, so only that table (the Gather op) is quantised, which keeps scores
within 0.01 of fp32 and halves the file. Weights are then stored as external
data, which onnxruntime memory-maps instead of copying (about 0.7 GB resident
instead of 1.2 GB). The pinned fp32 download is verified first; the output is
deterministic for a given onnx/onnxruntime and its hashes are recorded in the
install marker.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Callable, Dict

from core.models.model_dependency_models import ModelDependencySpec

EXTERNAL_DATA = "model.onnx.data"


def apply_transform(spec: ModelDependencySpec, staging: Path, log: Callable[[str], None]) -> Dict[str, Any]:
    transform = TRANSFORMS.get(spec.transform)
    if transform is None:
        return {"ok": False, "error": f"Unknown model transform '{spec.transform}'."}
    try:
        return transform(staging, log)
    except Exception as exc:
        return {"ok": False, "error": f"Model transform failed: {exc}"}


def _quantize_embeddings(staging: Path, log) -> Dict[str, Any]:
    import onnx  # type: ignore
    from onnxruntime.quantization import QuantType, quantize_dynamic  # type: ignore
    from core.services.intelligence.model_validation import sha256
    source, quantized = staging / "model.onnx", staging / ".quantized.onnx"
    log("Quantising the embedding table (keeps accuracy, halves the size)")
    quantize_dynamic(str(source), str(quantized), op_types_to_quantize=["Gather"], weight_type=QuantType.QUInt8)
    source.unlink()
    model = onnx.load(str(quantized))
    onnx.save_model(model, str(source), save_as_external_data=True, all_tensors_to_one_file=True,
                    location=EXTERNAL_DATA, size_threshold=1024)
    quantized.unlink()
    return {"ok": True, "file_sha256": {name: sha256(staging / name) for name in ("model.onnx", EXTERNAL_DATA)}}


TRANSFORMS: Dict[str, Callable[[Path, Callable[[str], None]], Dict[str, Any]]] = {
    "quantize_embeddings": _quantize_embeddings,
}
