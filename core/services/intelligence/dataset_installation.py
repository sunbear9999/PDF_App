"""Install ``dataset`` dependencies: published data files built into a local index.

Unlike model repositories, public datasets such as GeoNames are republished
daily, so their bytes cannot be pinned in advance. Installation therefore
checks structure instead (the builder rejects malformed files and a minimum
row count), records the SHA-256 of every downloaded file and of the built
index in the install marker, and exposes that digest so stages that use the
data re-run when it changes.
"""
from __future__ import annotations

import hashlib
import json
import shutil
import tempfile
import urllib.request
from pathlib import Path
from typing import Any, Callable, Dict

from core.models.model_dependency_models import ModelDependencySpec

MARKER = ".papyrus-dataset.json"
CHUNK = 1 << 20
DOWNLOAD_TIMEOUT_S = 60
MIN_FREE_BYTES = 600 * 1024 * 1024


def _builders() -> Dict[str, Callable[[Dict[str, Path], Path], int]]:
    from core.services.intelligence.gazetteer import build_geonames
    return {"geonames_places": build_geonames}


def dataset_status(spec: ModelDependencySpec) -> str:
    marker = read_marker(spec)
    if not marker:
        return "missing"
    if marker.get("manifest_id") != spec.id or marker.get("builder") != spec.dataset_builder:
        return "broken"
    return "ready" if (Path(spec.dest_dir) / marker.get("index", "")).is_file() else "broken"


def read_marker(spec: ModelDependencySpec) -> Dict[str, Any]:
    try:
        return json.loads((Path(spec.dest_dir) / MARKER).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def dataset_digest(spec: ModelDependencySpec) -> str:
    """Digest of the installed build ('' when not installed)."""
    return str(read_marker(spec).get("index_sha256") or "")


def install_dataset(spec: ModelDependencySpec, models_root: str, activate: Callable[[Path, Path], None],
                    log, progress, cancelled) -> Dict[str, Any]:
    builder = _builders().get(spec.dataset_builder)
    if builder is None:
        return {"ok": False, "error": f"Unknown dataset builder '{spec.dataset_builder}'."}
    root = Path(models_root)
    root.mkdir(parents=True, exist_ok=True)
    if shutil.disk_usage(root).free < MIN_FREE_BYTES:
        return {"ok": False, "error": "Not enough free space to download and build the dataset."}
    staging = Path(tempfile.mkdtemp(prefix=f".{spec.id}-", dir=root))
    try:
        files, hashes = {}, {}
        total = len(spec.dataset_urls) + 1
        for index, (name, url) in enumerate(spec.dataset_urls.items()):
            if cancelled():
                return {"ok": False, "error": "Installation cancelled."}
            progress(index, total, f"Downloading {name}")
            log(f"Downloading {url}")
            target = staging / "download" / name
            hashes[name] = _download(url, target, cancelled)
            if hashes[name] is None:
                return {"ok": False, "error": "Installation cancelled."}
            files[name] = target
        progress(total - 1, total, "Building local index")
        index_path = staging / "index.sqlite"
        rows = builder(files, index_path)
        shutil.rmtree(staging / "download", ignore_errors=True)
        marker = {"manifest_id": spec.id, "builder": spec.dataset_builder, "index": index_path.name,
                  "rows": rows, "source_sha256": hashes, "sources": dict(spec.dataset_urls),
                  "index_sha256": _sha256(index_path), "license": spec.license}
        (staging / MARKER).write_text(json.dumps(marker, indent=2, sort_keys=True), encoding="utf-8")
        log(f"Built {rows} entries.")
        activate(staging, Path(spec.dest_dir))
        progress(total, total, "Ready")
        return {"ok": True}
    except (OSError, ValueError) as exc:
        return {"ok": False, "error": f"Dataset installation failed: {exc}"}
    finally:
        shutil.rmtree(staging, ignore_errors=True)


def _download(url: str, target: Path, cancelled):
    target.parent.mkdir(parents=True, exist_ok=True)
    digest = hashlib.sha256()
    with urllib.request.urlopen(url, timeout=DOWNLOAD_TIMEOUT_S) as response, target.open("wb") as out:  # noqa: S310
        while True:
            if cancelled():
                return None
            chunk = response.read(CHUNK)
            if not chunk:
                break
            digest.update(chunk)
            out.write(chunk)
    return digest.hexdigest()


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(CHUNK), b""):
            digest.update(chunk)
    return digest.hexdigest()


def remove_dataset(spec: ModelDependencySpec, models_root: str) -> bool:
    destination, root = Path(spec.dest_dir).resolve(), Path(models_root).resolve()
    if destination == root or root not in destination.parents:
        return False
    if destination.exists():
        shutil.rmtree(destination)
    return True


__all__ = ["dataset_status", "dataset_digest", "install_dataset", "read_marker", "remove_dataset"]
