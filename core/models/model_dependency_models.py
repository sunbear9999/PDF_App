"""Manifest record for an optional local model or package (see AI Setup)."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List


@dataclass
class ModelDependencySpec:
    id: str
    label: str
    description: str
    kind: str
    role: str = ""
    optional: bool = True
    size_hint: str = ""
    spacy_model: str = ""
    pip_package: str = ""
    hf_repo: str = ""
    hf_revision: str = ""
    hf_files: Dict[str, Any] = field(default_factory=dict)
    file_sha256: Dict[str, str] = field(default_factory=dict)
    file_sizes: Dict[str, int] = field(default_factory=dict)
    dest_dir: str = ""
    license: str = ""
    runtime_modules: List[str] = field(default_factory=list)
    # Task-level catalog data shown in AI Setup.
    task: str = ""
    hardware: str = ""
    evaluation: str = ""
    extra_files: Dict[str, str] = field(default_factory=dict)  # written on install
    required_labels: List[str] = field(default_factory=list)   # config id2label must include
    system_binary: str = ""  # kind "system": a program installed by the OS package manager
    # Packages needed only while installing (e.g. by ``transform``), not at run time.
    install_modules: List[str] = field(default_factory=list)
    # Post-download step (see model_transforms.TRANSFORMS); its output hashes are recorded.
    transform: str = ""
    # Smoke test run after download/import (see model_validation.VALIDATORS).
    validator: str = "text_pair"
    # kind "dataset": published files fetched by URL and built into a local index.
    dataset_urls: Dict[str, str] = field(default_factory=dict)
    dataset_builder: str = ""
    # Project setting that switches the resource on (off until its gold gate passes).
    setting_key: str = ""
