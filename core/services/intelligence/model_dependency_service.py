"""Optional local-model manifests, installation, validation, and hot reload."""
from __future__ import annotations

from core.services.intelligence.model_installation import (
    perform_operation, ensure_runtimes, run_package_command,
    install_system_dependency, system_installable, system_status,
)

import importlib.util
import hashlib
import json
import os
import shutil
import sys
import tempfile
import threading
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from core.models.model_dependency_models import ModelDependencySpec
from core.services.intelligence.model_dependency_catalog import builtin_model_specs
from core.services.intelligence.sentence_role_model import read_role_labels
from core.services.intelligence.model_validation import (  # noqa: F401  (re-exported)
    check_files, check_labels as _check_labels, sha256 as _sha256, validate_model,
)
from core.services.intelligence.dataset_installation import dataset_digest, dataset_status, install_dataset
from core.services.intelligence.model_transforms import apply_transform


def _has_spacy_model(name: str) -> bool:
    try:
        return importlib.util.find_spec(name) is not None
    except Exception:
        return False


def _has_pip(pkg: str) -> bool:
    try:
        return importlib.util.find_spec(pkg) is not None
    except Exception:
        return False


class ModelDependencyService:
    def __init__(self, models_root: Optional[str] = None) -> None:
        default_root = os.environ.get("PAPYRUS_MODEL_DIR")
        if not default_root:
            default_root = os.path.join(
                os.path.expanduser("~"), ".local", "share", "Papyrus", "models"
            )
        self.models_root = os.path.abspath(models_root or default_root)
        self._reload_callbacks: Dict[str, List[Callable[[], None]]] = {}
        # Content for manifest ``extra_files`` (e.g. editable role hypotheses),
        # supplied by the owning service at composition time.
        self._extra_file_providers: Dict[str, Callable[[], str]] = {}
        self._validation_cache: Dict[str, tuple[bool, str]] = {}
        self._states: Dict[str, str] = {}
        self._specs = self._build_specs()
        self._install_lock = threading.Lock()
        self._operation_errors = {}

    def _build_specs(self) -> List[ModelDependencySpec]:
        return builtin_model_specs(self._model_dir)

    def _model_dir(self, name: str) -> str:
        return os.path.join(self.models_root, name)

    def model_dir(self, dep_id: str) -> str:
        spec = self.get(dep_id)
        return spec.dest_dir if spec else self._model_dir(dep_id)

    def register_reload_callback(
        self, dep_id: str, callback: Callable[[], None]
    ) -> None:
        if callable(callback):
            self._reload_callbacks.setdefault(dep_id, []).append(callback)

    def register_extra_file_provider(self, key: str, provider: Callable[[], str]) -> None:
        if callable(provider):
            self._extra_file_providers[key] = provider

    def _write_extra_files(self, spec: ModelDependencySpec, directory: Path) -> None:
        for name, key in (spec.extra_files or {}).items():
            provider = self._extra_file_providers.get(key)
            if provider and not (directory / name).exists():
                (directory / name).write_text(provider(), encoding="utf-8")

    def list_dependencies(self, *, probe=True) -> List[Dict[str, Any]]:
        return [
            {
                "id": spec.id,
                "label": spec.label,
                "description": spec.description,
                "kind": spec.kind,
                "role": spec.role,
                "optional": spec.optional,
                "size_hint": spec.size_hint,
                "installed": self.is_installed(spec.id) if probe else False,
                "status": self.status(spec.id) if probe else 'checking',
                "installable": spec.kind in {"spacy", "pip", "onnx_hf", "dataset"} or (
                    spec.kind == "system" and system_installable(spec)),
                "destination": spec.dest_dir,
                "license": spec.license,
                "repository": spec.hf_repo,
                "revision": spec.hf_revision,
                "task": spec.task,
                "hardware": spec.hardware,
                "evaluation": spec.evaluation,
                "last_error": self._operation_errors.get(spec.id, ""),
                "setting_key": spec.setting_key,
            }
            for spec in self._specs
        ]

    def get(self, dep_id: str) -> Optional[ModelDependencySpec]:
        return next((spec for spec in self._specs if spec.id == dep_id), None)

    def status(self, dep_id: str) -> str:
        if self._states.get(dep_id) in {"downloading", "validating"}:
            return self._states[dep_id]
        spec = self.get(dep_id)
        if not spec:
            return "missing"
        if spec.kind == "spacy":
            return "ready" if _has_spacy_model(spec.spacy_model) else "missing"
        if spec.kind == "pip":
            return "ready" if _has_pip(spec.pip_package) else "missing"
        if spec.kind == "system":
            return system_status(spec)
        if spec.kind == "dataset":
            return dataset_status(spec)
        if spec.kind in {"onnx_hf", "manual"}:
            required = list(spec.hf_files) if spec.hf_files else [
                "model.onnx", "tokenizer.json"
            ]
            present = [os.path.isfile(os.path.join(spec.dest_dir, name)) for name in required]
            if not any(present):
                return "missing"
            if not all(present):
                return "broken"
            if spec.kind == "manual" and spec.task == "sentence_roles":
                try:
                    read_role_labels(spec.dest_dir)
                except (OSError, ValueError, KeyError, TypeError):
                    return "broken"
            if not self._marker_valid(spec):
                return "broken"
            runtime_missing = [
                module for module in spec.runtime_modules
                if importlib.util.find_spec(module) is None
            ]
            if runtime_missing:
                return "incompatible"
            cached = self._validation_cache.get(dep_id)
            return "broken" if cached and not cached[0] else "ready"
        return "missing"

    def is_installed(self, dep_id: str) -> bool:
        return self.status(dep_id) == "ready"

    def install(self, dep_id, log=None, progress=None, cancel_check=None):
        if not self._install_lock.acquire(blocking=False):
            return {"ok": False, "error": "Another model installation is in progress."}
        try:
            result = self._install(dep_id, log, progress, cancel_check)
            self._operation_errors[dep_id] = str(result.get("error") or "")
            return result
        finally:
            self._install_lock.release()

    def perform_operation(self, dep_id, action="install", source_dir="", **kwargs):
        return perform_operation(self, dep_id, action, source_dir, **kwargs)

    def _install(
        self,
        dep_id: str,
        log: Optional[Callable[[str], None]] = None,
        progress: Optional[Callable[[int, int, str], None]] = None,
        cancel_check: Optional[Callable[[], bool]] = None,
    ) -> Dict[str, Any]:
        spec = self.get(dep_id)
        log = log or (lambda _message: None)
        progress = progress or (lambda _current, _total, _message: None)
        if not spec:
            return {"ok": False, "error": f"Unknown dependency '{dep_id}'"}
        try:
            self._states[dep_id] = "downloading"
            if spec.kind == "spacy":
                result = self._run_subprocess(
                    [sys.executable, "-m", "spacy", "download", spec.spacy_model],
                    log, cancel_check,
                )
            elif spec.kind == "pip":
                result = self._run_subprocess(
                    [sys.executable, "-m", "pip", "install", spec.pip_package],
                    log, cancel_check,
                )
            elif spec.kind == "system":
                result = install_system_dependency(spec, log, cancel_check or (lambda: False))
            elif spec.kind == "dataset":
                result = install_dataset(spec, self.models_root, self._activate_staging, log, progress,
                                         cancel_check or (lambda: False))
            elif spec.kind == "onnx_hf":
                result = ensure_runtimes(spec, log, cancel_check or (lambda: False))
                if result.get("ok"):
                    result = self._download_onnx(spec, log, progress, cancel_check or (lambda: False))
            else:
                result = {
                    "ok": False,
                    "error": (
                        "This model must be imported manually into "
                        f"'{spec.dest_dir}'."
                    ),
                }
        except Exception as exc:
            result = {"ok": False, "error": str(exc)}
        self._states.pop(dep_id, None)
        if result.get("ok"):
            self._validation_cache[dep_id] = (True, "")
            for callback in self._reload_callbacks.get(dep_id, []):
                try:
                    callback()
                except Exception as exc:
                    log(f"Model installed, but live reload failed: {exc}")
        result.setdefault("status", self.status(dep_id))
        result.setdefault("destination", spec.dest_dir)
        return result

    def remove(self, dep_id: str) -> Dict[str, Any]:
        spec = self.get(dep_id)
        if not spec or spec.kind not in {"onnx_hf", "manual", "dataset"}:
            return {"ok": False, "error": "Dependency has no removable model folder."}
        destination = Path(spec.dest_dir).resolve()
        root = Path(self.models_root).resolve()
        if destination == root or root not in destination.parents:
            return {"ok": False, "error": "Refusing to remove an unsafe model path."}
        if destination.exists():
            shutil.rmtree(destination)
        self._validation_cache.pop(dep_id, None)
        self._states.pop(dep_id, None)
        for callback in self._reload_callbacks.get(dep_id, []):
            callback()
        return {"ok": True, "status": "missing", "destination": str(destination)}

    def import_model_folder(self, dep_id: str, source_dir: str) -> Dict[str, Any]:
        spec = self.get(dep_id)
        if not spec or spec.kind not in {"onnx_hf", "manual"}:
            return {"ok": False, "error": "Dependency does not accept model folders."}
        source = Path(source_dir)
        if not source.is_dir():
            return {"ok": False, "error": "Model folder does not exist."}
        required = list(spec.hf_files) if spec.hf_files else ["model.onnx", "tokenizer.json"]
        if any(not (source / name).is_file() for name in required):
            return {"ok": False, "error": f"Required files: {', '.join(required)}"}
        Path(self.models_root).mkdir(parents=True, exist_ok=True)
        staging = Path(tempfile.mkdtemp(prefix=f".{dep_id}-", dir=self.models_root))
        try:
            for name in required:
                target = staging / name
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(source / name, target)
            staged_spec = ModelDependencySpec(**{
                **spec.__dict__, "dest_dir": str(staging)
            })
            ok, error = self._validate_onnx(staged_spec)
            if not ok:
                self._validation_cache[dep_id] = (False, error)
                return {"ok": False, "error": error}
            self._write_validation_marker(staged_spec)
            self._write_extra_files(spec, staging)
            self._activate_staging(staging, Path(spec.dest_dir))
        finally:
            shutil.rmtree(staging, ignore_errors=True)
        for callback in self._reload_callbacks.get(dep_id, []):
            callback()
        self._validation_cache[dep_id] = (True, "")
        return {"ok": True, "status": "ready", "destination": spec.dest_dir}

    def _transform(self, staged: ModelDependencySpec, staging: Path, log):
        """Verify the pinned download, transform it, and pin the result's hashes instead."""
        ok, error = check_files(staged)
        if not ok:
            return None, error
        result = apply_transform(staged, staging, log)
        if not result.get("ok"):
            return None, result.get("error", "")
        return ModelDependencySpec(**{**staged.__dict__, "file_sha256": result["file_sha256"], "file_sizes": {}}), ""

    def validate(self, dep_id: str) -> Dict[str, Any]:
        spec = self.get(dep_id)
        if not spec or spec.kind not in {"onnx_hf", "manual"}:
            return {"ok": self.is_installed(dep_id), "error": ""}
        if spec.transform:
            recorded = self._marker(spec).get("file_sha256") or {}
            spec = ModelDependencySpec(**{**spec.__dict__, "file_sha256": recorded, "file_sizes": {}})
        ok, error = self._validate_onnx(spec)
        self._validation_cache[dep_id] = (ok, error)
        if ok:
            self._write_validation_marker(spec)
        return {"ok": ok, "error": error, "status": self.status(dep_id)}

    def _run_subprocess(self, cmd: List[str], log, cancel_check=None) -> Dict[str, Any]:
        return run_package_command(cmd, log, cancel_check)

    def _download_onnx(self, spec, log, progress, cancel_check) -> Dict[str, Any]:
        try:
            from huggingface_hub import hf_hub_download  # type: ignore
        except Exception:
            return {
                "ok": False,
                "error": "huggingface_hub is required to download ONNX models.",
            }
        missing_modules = [
            module for module in spec.runtime_modules
            if importlib.util.find_spec(module) is None
        ]
        if missing_modules:
            return {
                "ok": False,
                "error": "Missing runtime modules: " + ", ".join(missing_modules),
            }
        root = Path(self.models_root)
        root.mkdir(parents=True, exist_ok=True)
        if not os.access(root, os.W_OK):
            return {"ok": False, "error": f"Model directory is not writable: {root}"}
        required_bytes = sum(spec.file_sizes.values()) or 150 * 1024 * 1024
        if shutil.disk_usage(root).free < required_bytes * 2:
            return {"ok": False, "error": "Not enough free space for download and validation."}
        staging = Path(tempfile.mkdtemp(prefix=f".{spec.id}-", dir=root))
        # A transformed model's source is not kept: it is downloaded into staging.
        cache_dir = staging / ".hf-cache" if spec.transform else root / ".download-cache"
        total = len(spec.hf_files)
        try:
            for index, (local_name, candidates) in enumerate(spec.hf_files.items(), 1):
                if cancel_check():
                    return {"ok": False, "error": "Installation cancelled."}
                paths = candidates if isinstance(candidates, (list, tuple)) else [candidates]
                downloaded = None
                last_error = ""
                for hf_path in paths:
                    try:
                        message = f"Downloading {spec.hf_repo}/{hf_path}"
                        log(message)
                        progress(index - 1, total, message)
                        downloaded = hf_hub_download(
                            repo_id=spec.hf_repo,
                            filename=hf_path,
                            revision=spec.hf_revision or None,
                            cache_dir=str(cache_dir),
                            token=False,
                        )
                        break
                    except Exception as exc:
                        last_error = str(exc)
                if not downloaded:
                    return {
                        "ok": False,
                        "error": (
                            f"Could not download {local_name} from "
                            f"{spec.hf_repo}: {last_error}"
                        ),
                    }
                target = staging / local_name
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(downloaded, target)
                progress(index, total, f"Downloaded {local_name}")
            self._states[spec.id] = "validating"
            staged = ModelDependencySpec(**{**spec.__dict__, "dest_dir": str(staging)})
            if spec.transform:
                shutil.rmtree(cache_dir, ignore_errors=True)
                staged, error = self._transform(staged, staging, log)
                if staged is None:
                    return {"ok": False, "error": error}
            progress(total, total, "Validating tokenizer and ONNX inference")
            ok, error = self._validate_onnx(staged)
            if not ok:
                return {"ok": False, "error": error}
            self._write_validation_marker(staged)
            self._write_extra_files(spec, staging)
            self._activate_staging(staging, Path(spec.dest_dir))
            progress(total, total, "Ready")
            return {"ok": True}
        finally:
            # Only the installer-owned staging path is ever removed.
            shutil.rmtree(staging, ignore_errors=True)

    def _validate_onnx(self, spec: ModelDependencySpec) -> tuple[bool, str]:
        return validate_model(spec)

    @staticmethod
    def _activate_staging(staging: Path, destination: Path) -> None:
        destination.parent.mkdir(parents=True, exist_ok=True)
        backup = destination.with_name(f".{destination.name}.previous")
        if backup.exists():
            shutil.rmtree(backup, ignore_errors=True)
        if destination.exists():
            os.replace(destination, backup)
        try:
            os.replace(staging, destination)
        except Exception:
            if backup.exists() and not destination.exists():
                os.replace(backup, destination)
            raise
        shutil.rmtree(backup, ignore_errors=True)

    @staticmethod
    def _write_validation_marker(spec: ModelDependencySpec) -> None:
        marker = {
            "manifest_id": spec.id,
            "repository": spec.hf_repo,
            "revision": spec.hf_revision,
            "files": sorted((spec.hf_files or {}).keys()),
            "validated_runtime": "onnxruntime-cpu",
        }
        if spec.transform:
            marker.update(transform=spec.transform, file_sha256=dict(spec.file_sha256))
        path = Path(spec.dest_dir) / ".papyrus-model.json"
        path.write_text(
            json.dumps(marker, sort_keys=True, indent=2),
            encoding="utf-8",
        )

    @staticmethod
    def _marker(spec: ModelDependencySpec) -> Dict[str, Any]:
        try:
            return json.loads((Path(spec.dest_dir) / ".papyrus-model.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}

    def model_digest(self, dep_id: str) -> str:
        """Digest of the installed files (pinned or recorded), for producer identities."""
        spec = self.get(dep_id)
        if spec is None:
            return ""
        if spec.kind == "dataset":
            return dataset_digest(spec)
        hashes = self._marker(spec).get("file_sha256") or spec.file_sha256
        return hashlib.sha256(json.dumps(hashes, sort_keys=True).encode()).hexdigest()[:16] if hashes else spec.hf_revision

    @staticmethod
    def _marker_valid(spec: ModelDependencySpec) -> bool:
        path = Path(spec.dest_dir) / ".papyrus-model.json"
        try:
            marker = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            return False
        if marker.get("manifest_id") != spec.id:
            return False
        if spec.hf_revision and marker.get("revision") != spec.hf_revision:
            return False
        return sorted(marker.get("files") or []) == sorted(
            (spec.hf_files or {}).keys()
        )

