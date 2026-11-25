"""Shared local sentence-model label validation and auditable identity."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

from core.services.intelligence.argument_role_policy import ROLES


def read_role_labels(directory) -> list[str]:
    labels = json.loads((Path(directory) / "labels.json").read_text(encoding="utf-8"))
    if isinstance(labels, dict):
        labels = [labels[str(index)] for index in range(len(labels))]
    supported = {*ROLES, "unknown"}
    if (not isinstance(labels, list) or not labels
            or not all(isinstance(label, str) and label in supported for label in labels)
            or len(set(labels)) != len(labels)):
        raise ValueError("labels.json must explicitly map each output to a unique supported sentence role")
    return labels


def role_model_identity(directory) -> dict[str, str]:
    identity = encoder_model_identity(directory)
    identity["configuration_digest"] = hashlib.sha256(
        (Path(directory) / "labels.json").read_bytes()).hexdigest()
    return identity


def encoder_model_identity(directory) -> dict[str, str]:
    """Read small manifests only; never hash/load model weights on the GUI thread.

    Imported models without a manifest are identified by their full local path
    and file revision. This is a local revision indicator, not a weight digest.
    """
    path = Path(directory).resolve()
    marker = _read_json(path / ".papyrus-model.json")
    config = _read_json(path / "config.json")
    model = path / "model.onnx"
    stamp = model.stat() if model.is_file() else None
    return {
        "model_id": str(marker.get("repository") or config.get("_name_or_path") or model),
        "model_version": str(marker.get("revision") or (
            f"local:{stamp.st_size}:{stamp.st_mtime_ns}" if stamp else "")),
    }


def _read_json(path):
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else {}
    except (OSError, ValueError):
        return {}
