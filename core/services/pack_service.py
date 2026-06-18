"""
core/services/pack_service.py

Central service for .ppack (Papyrus Pack) import/export operations.

A .ppack is a ZIP archive with:
  manifest.json
  themes/<name>.json
  blueprints/<id>.json
  prompts/prompts.json
  steps/steps.json
  analysis_templates/templates.json
  shortcuts/shortcuts.json
  layouts/layouts.json
  plugins/<plugin_id>/  (full directory tree)
  plugin_configs/<plugin_id>.json
  plugin_data/<contributor_id>.json

GUI-side dependencies (theme_manager, keybinding_registry, layout_manager)
are injected after construction via configure_gui_services() because they
are built after PapyrusCore.
"""
from __future__ import annotations

import copy
import dataclasses
import json
import os
import shutil
import sys
import uuid
import zipfile
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Dict, List, Optional, Tuple

from PySide6.QtCore import QObject, Signal

from core.models.pack_models import (
    CATEGORY_ORDER,
    PackItem,
    PackManifest,
    PPACK_VERSION,
)

if TYPE_CHECKING:
    from core.engine.blueprint_manager import BlueprintManager
    from core.plugins.pack_contributor import PackContributorRegistry
    from core.prompt_manager import PromptManager
    from core.engine.step_manager import StepManager
    from core.project_manager import ProjectManager


_CORE_STEP_PREFIXES = ("core_",)
_SYSTEM_TEMPLATES_PATH = os.path.join(
    os.path.dirname(os.path.dirname(__file__)), "analysis_templates.json"
)


def _app_data_dir() -> str:
    app_name = "Papyrus Research"
    if sys.platform == "win32":
        base = os.getenv("LOCALAPPDATA", os.path.expanduser("~\\AppData\\Local"))
    elif sys.platform == "darwin":
        base = os.path.join(os.path.expanduser("~"), "Library", "Application Support")
    else:
        base = os.getenv("XDG_DATA_HOME", os.path.expanduser("~/.local/share"))
    return os.path.join(base, app_name)


def _plugins_dir() -> str:
    if hasattr(sys, "_MEIPASS"):
        return os.path.join(sys._MEIPASS, "plugins")
    return os.path.abspath(
        os.path.join(os.path.dirname(__file__), "..", "..", "plugins")
    )


def _plugin_configs_dir() -> str:
    return os.path.join(os.path.expanduser("~"), ".papyrus", "plugin_configs")


def _load_system_template_ids() -> set:
    try:
        with open(_SYSTEM_TEMPLATES_PATH, "r", encoding="utf-8") as f:
            templates = json.load(f)
        if isinstance(templates, list):
            return {t.get("id", "") for t in templates if "id" in t}
    except Exception:
        pass
    return set()


class PackService(QObject):
    """
    Handles all .ppack export and import logic.

    Signals
    -------
    pack_exported(file_path)  — emitted after a successful export
    pack_imported(summary)    — emitted after a successful import; summary is
                                dict {category: count_imported}
    operation_failed(message) — emitted on any error
    """

    pack_exported = Signal(str)
    pack_imported = Signal(dict)
    operation_failed = Signal(str)

    def __init__(
        self,
        blueprint_manager: Optional["BlueprintManager"] = None,
        prompt_manager: Optional["PromptManager"] = None,
        step_manager: Optional["StepManager"] = None,
        contributor_registry: Optional["PackContributorRegistry"] = None,
        project_manager: Optional["ProjectManager"] = None,
        parent: Optional[QObject] = None,
    ) -> None:
        super().__init__(parent)
        self._blueprint_manager = blueprint_manager
        self._prompt_manager = prompt_manager
        self._step_manager = step_manager
        self._contributor_registry = contributor_registry
        self._project_manager = project_manager

        # GUI-side deps — injected later via configure_gui_services()
        self._theme_manager = None
        self._keybinding_registry = None
        self._layout_manager = None

    def configure_gui_services(
        self, theme_manager=None, keybinding_registry=None, layout_manager=None
    ) -> None:
        """Inject GUI-side dependencies once the GUI layer is ready."""
        if theme_manager is not None:
            self._theme_manager = theme_manager
        if keybinding_registry is not None:
            self._keybinding_registry = keybinding_registry
        if layout_manager is not None:
            self._layout_manager = layout_manager

    # ─────────────────────────────────────────────────────────────────────
    # Public API
    # ─────────────────────────────────────────────────────────────────────

    def get_exportable_categories(self, include_empty: bool = True) -> Dict[str, List[PackItem]]:
        """
        Return all exportable categories mapped to their user-custom items.
        When include_empty=True (default), every category appears in the result
        even if it has no items yet — so the UI can always show what's possible.
        """
        result: Dict[str, List[PackItem]] = {}
        for cat in CATEGORY_ORDER:
            items = self._collect_category(cat)
            if items or include_empty:
                result[cat] = items
        return result

    def analyze_export_deps(
        self, selection: Dict[str, List[str]]
    ) -> List:
        """
        Return ExportDepWarning list for items in selection whose dependencies
        are not included in the selection.
        """
        from core.services.pack_dependency import build_export_dep_map, get_export_warnings
        analysis_templates = self._get_custom_templates()
        dep_map = build_export_dep_map(
            selection,
            self._blueprint_manager,
            self._step_manager,
            self._prompt_manager,
            analysis_templates,
        )
        return get_export_warnings(selection, dep_map)

    def analyze_import_deps(
        self,
        file_path: str,
        pack_categories: Dict[str, List[PackItem]],
        selection: Dict[str, List[str]],
    ) -> List:
        """
        Return ImportDepWarning list for selected items whose dependencies are
        missing from both the local installation and the pack itself.
        """
        from core.services.pack_dependency import get_import_warnings
        all_ids: Dict[str, set] = {
            cat: {it.item_id for it in items}
            for cat, items in pack_categories.items()
        }
        return get_import_warnings(
            pack_categories, selection, self._prompt_manager,
            self._step_manager, all_ids,
        )

    def find_import_conflicts(
        self,
        pack_categories: Dict[str, List[PackItem]],
        selection: Dict[str, List[str]],
    ) -> List:
        """Return ConflictItem list for items that already exist locally."""
        from core.services.pack_dependency import find_import_conflicts
        return find_import_conflicts(
            pack_categories, selection,
            self._prompt_manager, self._step_manager,
            self._blueprint_manager, self._theme_manager,
            self._layout_manager,
        )

    def export_pack(
        self,
        file_path: str,
        selection: Dict[str, List[str]],
        name: str = "",
        description: str = "",
    ) -> None:
        """
        Write a .ppack ZIP to file_path containing the selected items.

        selection maps category → list[item_id].  An empty list means
        "export all items in this category".
        """
        try:
            manifest_categories: Dict[str, List[str]] = {}
            with zipfile.ZipFile(file_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
                for cat, item_ids in selection.items():
                    written = self._write_category(zf, cat, item_ids)
                    if written:
                        manifest_categories[cat] = written

                # Build dependency map for written items
                from core.services.pack_dependency import build_export_dep_map
                dep_map = build_export_dep_map(
                    manifest_categories,
                    self._blueprint_manager,
                    self._step_manager,
                    self._prompt_manager,
                    self._get_custom_templates(),
                )
                dep_dict = {
                    k: {"prompts": v.prompt_deps, "steps": v.step_deps}
                    for k, v in dep_map.items()
                    if v.has_deps
                }

                manifest = PackManifest(
                    pack_id=str(uuid.uuid4()),
                    name=name or "My Pack",
                    description=description,
                    app_version=self._app_version(),
                    created_at=datetime.now(timezone.utc).isoformat(),
                    ppack_version=PPACK_VERSION,
                    categories=manifest_categories,
                    dependencies=dep_dict,
                )
                zf.writestr("manifest.json", json.dumps(manifest.to_dict(), indent=2))

            self.pack_exported.emit(file_path)
        except Exception as exc:
            self.operation_failed.emit(f"Export failed: {exc}")

    def preview_pack(
        self, file_path: str
    ) -> Tuple[Optional[PackManifest], Dict[str, List[PackItem]]]:
        """
        Open a .ppack and return (manifest, {category: [PackItem]}) without
        importing anything.  Returns (None, {}) on failure.
        """
        try:
            with zipfile.ZipFile(file_path, "r") as zf:
                manifest_data = json.loads(zf.read("manifest.json"))
                manifest = PackManifest.from_dict(manifest_data)
                categories: Dict[str, List[PackItem]] = {}
                for cat, item_ids in manifest.categories.items():
                    items = self._read_category_preview(zf, cat, item_ids)
                    # Attach dependency info from manifest to each PackItem
                    for item in items:
                        deps = manifest.dependencies.get(item.item_id, {})
                        if deps:
                            item.metadata["dep_prompts"] = deps.get("prompts", [])
                            item.metadata["dep_steps"] = deps.get("steps", [])
                    if items:
                        categories[cat] = items
            return manifest, categories
        except Exception as exc:
            self.operation_failed.emit(f"Preview failed: {exc}")
            return None, {}

    def import_pack(
        self,
        file_path: str,
        selection: Dict[str, List[str]],
        renames: Optional[Dict[str, str]] = None,
    ) -> Dict[str, int]:
        """
        Import selected items from a .ppack file.

        selection  — {category: [item_ids]} (empty list = import all in cat)
        renames    — {original_item_id: new_item_id} for conflict resolution
        Returns summary dict {category: count_imported}.
        """
        summary: Dict[str, int] = {}
        effective_renames = renames or {}
        try:
            with zipfile.ZipFile(file_path, "r") as zf:
                for cat, item_ids in selection.items():
                    count = self._apply_category(zf, cat, item_ids, effective_renames)
                    if count:
                        summary[cat] = count
            self.pack_imported.emit(summary)
        except Exception as exc:
            self.operation_failed.emit(f"Import failed: {exc}")
        return summary

    # ─────────────────────────────────────────────────────────────────────
    # Collection — gather exportable items per category
    # ─────────────────────────────────────────────────────────────────────

    def _collect_category(self, cat: str) -> List[PackItem]:
        collectors = {
            "themes": self._collect_themes,
            "blueprints": self._collect_blueprints,
            "prompts": self._collect_prompts,
            "steps": self._collect_steps,
            "analysis_templates": self._collect_analysis_templates,
            "shortcuts": self._collect_shortcuts,
            "layouts": self._collect_layouts,
            "plugins": self._collect_plugins,
            "plugin_configs": self._collect_plugin_configs,
            "plugin_data": self._collect_plugin_data,
        }
        fn = collectors.get(cat)
        return fn() if fn else []

    def _collect_themes(self) -> List[PackItem]:
        if not self._theme_manager:
            return []
        user_themes: dict = getattr(self._theme_manager, "_user_themes", {})
        items = []
        for name, colors in user_themes.items():
            preview = {k: colors.get(k, "") for k in ("bg_main", "accent", "text_main")}
            items.append(PackItem(name, name, "themes", {"colors_preview": preview}))
        return items

    def _collect_blueprints(self) -> List[PackItem]:
        if not self._blueprint_manager:
            return []
        from core.services.pack_dependency import get_blueprint_deps
        items = []
        for key, bp in self._blueprint_manager.blueprints.items():
            deps = get_blueprint_deps(bp, self._prompt_manager, self._step_manager)
            items.append(PackItem(
                key,
                getattr(bp, "name", key) or key,
                "blueprints",
                {
                    "description": getattr(bp, "description", ""),
                    "mount_points": getattr(bp, "mount_points", []),
                    "dep_prompts": deps.prompt_deps,
                    "dep_steps": deps.step_deps,
                },
            ))
        return items

    def _collect_prompts(self) -> List[PackItem]:
        if not self._prompt_manager:
            return []
        default_keys = set(getattr(self._prompt_manager, "DEFAULT_PROMPTS", {}).keys())
        items = []
        for key, text in self._prompt_manager.custom_prompts.items():
            is_override = key in default_keys
            items.append(PackItem(key, key, "prompts", {
                "preview": text[:120],
                "is_default_override": is_override,
            }))
        return items

    def _collect_steps(self) -> List[PackItem]:
        if not self._step_manager:
            return []
        items = []
        for key, step in self._step_manager.library.items():
            if any(key.startswith(p) for p in _CORE_STEP_PREFIXES):
                continue
            items.append(PackItem(
                key,
                getattr(step, "step_id", key) or key,
                "steps",
                {"step_type": getattr(step, "step_type", "")},
            ))
        return items

    def _collect_analysis_templates(self) -> List[PackItem]:
        system_ids = _load_system_template_ids()
        pm = self._project_manager
        if pm is None:
            return []
        db = getattr(pm, "db_docs", None)
        if db is None:
            return []
        try:
            templates = db.get_analysis_templates()
        except Exception:
            return []
        items = []
        for t in templates:
            tid = t.get("id", "")
            if tid and tid not in system_ids:
                items.append(PackItem(tid, t.get("title", tid), "analysis_templates", {}))
        return items

    def _collect_shortcuts(self) -> List[PackItem]:
        if not self._keybinding_registry:
            return []
        overrides: dict = getattr(self._keybinding_registry, "_overrides", {})
        items = []
        for action_id, key_str in overrides.items():
            items.append(PackItem(
                action_id,
                action_id.replace("_", " ").title(),
                "shortcuts",
                {"key": key_str},
            ))
        return items

    def _collect_layouts(self) -> List[PackItem]:
        if not self._layout_manager:
            return []
        items = []
        for name in self._layout_manager.get_template_names():
            items.append(PackItem(name, name, "layouts", {}))
        return items

    def _collect_plugins(self) -> List[PackItem]:
        plugins_dir = _plugins_dir()
        if not os.path.isdir(plugins_dir):
            return []
        items = []
        for entry in sorted(os.listdir(plugins_dir)):
            if not os.path.isfile(os.path.join(plugins_dir, entry, "plugin.py")):
                continue
            items.append(PackItem(entry, entry, "plugins", {}))
        return items

    def _collect_plugin_configs(self) -> List[PackItem]:
        configs_dir = _plugin_configs_dir()
        if not os.path.isdir(configs_dir):
            return []
        items = []
        for fname in sorted(os.listdir(configs_dir)):
            if fname.endswith(".json"):
                plugin_id = fname[:-5]
                items.append(PackItem(plugin_id, plugin_id, "plugin_configs", {}))
        return items

    def _collect_plugin_data(self) -> List[PackItem]:
        if not self._contributor_registry:
            return []
        items = []
        for contributor in self._contributor_registry.get_all():
            try:
                items.extend(contributor.get_exportable_items())
            except Exception:
                pass
        return items

    # ─────────────────────────────────────────────────────────────────────
    # Writing — serialize items into the ZIP
    # ─────────────────────────────────────────────────────────────────────

    def _write_category(
        self, zf: zipfile.ZipFile, cat: str, requested_ids: List[str]
    ) -> List[str]:
        writers = {
            "themes": self._write_themes,
            "blueprints": self._write_blueprints,
            "prompts": self._write_prompts,
            "steps": self._write_steps,
            "analysis_templates": self._write_analysis_templates,
            "shortcuts": self._write_shortcuts,
            "layouts": self._write_layouts,
            "plugins": self._write_plugins,
            "plugin_configs": self._write_plugin_configs,
            "plugin_data": self._write_plugin_data,
        }
        fn = writers.get(cat)
        return fn(zf, requested_ids) if fn else []

    def _write_themes(self, zf: zipfile.ZipFile, ids: List[str]) -> List[str]:
        if not self._theme_manager:
            return []
        user_themes: dict = getattr(self._theme_manager, "_user_themes", {})
        written = []
        for name, colors in user_themes.items():
            if ids and name not in ids:
                continue
            data = {"name": name, "version": 1, "colors": colors}
            safe = name.replace("/", "_").replace("\\", "_")
            zf.writestr(f"themes/{safe}.json", json.dumps(data, indent=2))
            written.append(name)
        return written

    def _write_blueprints(self, zf: zipfile.ZipFile, ids: List[str]) -> List[str]:
        if not self._blueprint_manager:
            return []
        written = []
        for key, bp in self._blueprint_manager.blueprints.items():
            if ids and key not in ids:
                continue
            data = dataclasses.asdict(bp)
            data["_pack_id"] = key
            safe = key.replace("/", "_").replace("\\", "_")
            zf.writestr(f"blueprints/{safe}.json", json.dumps(data, indent=2))
            written.append(key)
        return written

    def _write_prompts(self, zf: zipfile.ZipFile, ids: List[str]) -> List[str]:
        if not self._prompt_manager:
            return []
        subset = {
            k: v
            for k, v in self._prompt_manager.custom_prompts.items()
            if not ids or k in ids
        }
        if not subset:
            return []
        zf.writestr("prompts/prompts.json", json.dumps(subset, indent=2))
        return list(subset.keys())

    def _write_steps(self, zf: zipfile.ZipFile, ids: List[str]) -> List[str]:
        if not self._step_manager:
            return []
        subset = {
            k: dataclasses.asdict(v)
            for k, v in self._step_manager.library.items()
            if not any(k.startswith(p) for p in _CORE_STEP_PREFIXES)
            and (not ids or k in ids)
        }
        if not subset:
            return []
        zf.writestr("steps/steps.json", json.dumps(subset, indent=2))
        return list(subset.keys())

    def _write_analysis_templates(
        self, zf: zipfile.ZipFile, ids: List[str]
    ) -> List[str]:
        system_ids = _load_system_template_ids()
        pm = self._project_manager
        if pm is None:
            return []
        db = getattr(pm, "db_docs", None)
        if db is None:
            return []
        try:
            templates = db.get_analysis_templates()
        except Exception:
            return []
        custom = [
            t
            for t in templates
            if t.get("id") not in system_ids and (not ids or t.get("id") in ids)
        ]
        if not custom:
            return []
        zf.writestr(
            "analysis_templates/templates.json", json.dumps(custom, indent=2)
        )
        return [t.get("id", "") for t in custom]

    def _write_shortcuts(self, zf: zipfile.ZipFile, ids: List[str]) -> List[str]:
        if not self._keybinding_registry:
            return []
        overrides: dict = getattr(self._keybinding_registry, "_overrides", {})
        subset = {k: v for k, v in overrides.items() if not ids or k in ids}
        if not subset:
            return []
        zf.writestr("shortcuts/shortcuts.json", json.dumps(subset, indent=2))
        return list(subset.keys())

    def _write_layouts(self, zf: zipfile.ZipFile, ids: List[str]) -> List[str]:
        if not self._layout_manager:
            return []
        from PySide6.QtCore import QSettings
        settings = QSettings("PDFMultitool", "Workspace")
        written = []
        export_data = {}
        for name in self._layout_manager.get_template_names():
            if ids and name not in ids:
                continue
            payload_str = settings.value(f"layouts/{name}")
            if payload_str:
                export_data[name] = payload_str
                written.append(name)
        if export_data:
            zf.writestr("layouts/layouts.json", json.dumps(export_data, indent=2))
        return written

    def _write_plugins(self, zf: zipfile.ZipFile, ids: List[str]) -> List[str]:
        plugins_dir = _plugins_dir()
        if not os.path.isdir(plugins_dir):
            return []
        written = []
        for entry in sorted(os.listdir(plugins_dir)):
            if ids and entry not in ids:
                continue
            plugin_path = os.path.join(plugins_dir, entry)
            if not os.path.isfile(os.path.join(plugin_path, "plugin.py")):
                continue
            for root, dirs, files in os.walk(plugin_path):
                dirs[:] = [d for d in dirs if not d.startswith("__pycache__")]
                for fname in files:
                    if fname.endswith(".pyc"):
                        continue
                    abs_path = os.path.join(root, fname)
                    rel = os.path.relpath(abs_path, plugins_dir)
                    zf.write(abs_path, f"plugins/{rel}")
            written.append(entry)
        return written

    def _write_plugin_configs(self, zf: zipfile.ZipFile, ids: List[str]) -> List[str]:
        configs_dir = _plugin_configs_dir()
        if not os.path.isdir(configs_dir):
            return []
        written = []
        for fname in sorted(os.listdir(configs_dir)):
            if not fname.endswith(".json"):
                continue
            plugin_id = fname[:-5]
            if ids and plugin_id not in ids:
                continue
            abs_path = os.path.join(configs_dir, fname)
            zf.write(abs_path, f"plugin_configs/{fname}")
            written.append(plugin_id)
        return written

    def _write_plugin_data(self, zf: zipfile.ZipFile, ids: List[str]) -> List[str]:
        if not self._contributor_registry:
            return []
        written = []
        for contributor in self._contributor_registry.get_all():
            try:
                cid = contributor.contributor_id
                items = contributor.get_exportable_items()
                target_ids = [it.item_id for it in items if not ids or it.item_id in ids]
                if not target_ids:
                    continue
                data = contributor.export_items(target_ids)
                zf.writestr(
                    f"plugin_data/{cid}.json", json.dumps(data, indent=2)
                )
                written.extend(target_ids)
            except Exception:
                pass
        return written

    # ─────────────────────────────────────────────────────────────────────
    # Preview — parse ZIP contents without applying changes
    # ─────────────────────────────────────────────────────────────────────

    def _read_category_preview(
        self,
        zf: zipfile.ZipFile,
        cat: str,
        manifest_ids: List[str],
    ) -> List[PackItem]:
        readers = {
            "themes": self._preview_themes,
            "blueprints": self._preview_blueprints,
            "prompts": self._preview_prompts,
            "steps": self._preview_steps,
            "analysis_templates": self._preview_analysis_templates,
            "shortcuts": self._preview_shortcuts,
            "layouts": self._preview_layouts,
            "plugins": self._preview_plugins,
            "plugin_configs": self._preview_plugin_configs,
            "plugin_data": self._preview_plugin_data,
        }
        fn = readers.get(cat)
        return fn(zf, manifest_ids) if fn else []

    def _preview_themes(self, zf, ids):
        items = []
        for info in zf.infolist():
            if not info.filename.startswith("themes/") or not info.filename.endswith(".json"):
                continue
            try:
                data = json.loads(zf.read(info.filename))
                name = data.get("name", info.filename)
                colors = data.get("colors", {})
                preview = {k: colors.get(k, "") for k in ("bg_main", "accent", "text_main")}
                items.append(PackItem(name, name, "themes", {"colors_preview": preview}))
            except Exception:
                pass
        return items

    def _preview_blueprints(self, zf, ids):
        items = []
        for info in zf.infolist():
            if not info.filename.startswith("blueprints/") or not info.filename.endswith(".json"):
                continue
            try:
                data = json.loads(zf.read(info.filename))
                key = data.get("_pack_id", data.get("name", info.filename))
                items.append(PackItem(
                    key,
                    data.get("name", key),
                    "blueprints",
                    {
                        "description": data.get("description", ""),
                        "mount_points": data.get("mount_points", []),
                    },
                ))
            except Exception:
                pass
        return items

    def _preview_prompts(self, zf, ids):
        try:
            data = json.loads(zf.read("prompts/prompts.json"))
        except Exception:
            return []
        return [
            PackItem(k, k, "prompts", {"preview": v[:120]})
            for k, v in data.items()
            if not ids or k in ids
        ]

    def _preview_steps(self, zf, ids):
        try:
            data = json.loads(zf.read("steps/steps.json"))
        except Exception:
            return []
        return [
            PackItem(k, k, "steps", {"step_type": v.get("step_type", "")})
            for k, v in data.items()
            if not ids or k in ids
        ]

    def _preview_analysis_templates(self, zf, ids):
        try:
            templates = json.loads(zf.read("analysis_templates/templates.json"))
        except Exception:
            return []
        return [
            PackItem(t.get("id", ""), t.get("title", ""), "analysis_templates", {})
            for t in templates
            if not ids or t.get("id") in ids
        ]

    def _preview_shortcuts(self, zf, ids):
        try:
            data = json.loads(zf.read("shortcuts/shortcuts.json"))
        except Exception:
            return []
        return [
            PackItem(k, k.replace("_", " ").title(), "shortcuts", {"key": v})
            for k, v in data.items()
            if not ids or k in ids
        ]

    def _preview_layouts(self, zf, ids):
        try:
            data = json.loads(zf.read("layouts/layouts.json"))
        except Exception:
            return []
        return [
            PackItem(name, name, "layouts", {})
            for name in data
            if not ids or name in ids
        ]

    def _preview_plugins(self, zf, ids):
        seen = set()
        items = []
        for info in zf.infolist():
            if not info.filename.startswith("plugins/"):
                continue
            parts = info.filename.split("/")
            if len(parts) >= 2:
                plugin_dir = parts[1]
                if plugin_dir and plugin_dir not in seen:
                    if not ids or plugin_dir in ids:
                        seen.add(plugin_dir)
                        items.append(PackItem(plugin_dir, plugin_dir, "plugins", {}))
        return items

    def _preview_plugin_configs(self, zf, ids):
        items = []
        for info in zf.infolist():
            if not info.filename.startswith("plugin_configs/") or not info.filename.endswith(".json"):
                continue
            fname = info.filename.split("/")[-1]
            plugin_id = fname[:-5]
            if not ids or plugin_id in ids:
                items.append(PackItem(plugin_id, plugin_id, "plugin_configs", {}))
        return items

    def _preview_plugin_data(self, zf, ids):
        if not self._contributor_registry:
            return []
        items = []
        for contributor in self._contributor_registry.get_all():
            try:
                cid = contributor.contributor_id
                fname = f"plugin_data/{cid}.json"
                if fname not in zf.namelist():
                    continue
                data = json.loads(zf.read(fname))
                items.extend(contributor.preview_items(data))
            except Exception:
                pass
        return items

    # ─────────────────────────────────────────────────────────────────────
    # Apply — import items from ZIP into the application
    # ─────────────────────────────────────────────────────────────────────

    def _apply_category(
        self, zf: zipfile.ZipFile, cat: str, requested_ids: List[str],
        renames: Dict[str, str] = None,
    ) -> int:
        renames = renames or {}
        appliers = {
            "themes": self._apply_themes,
            "blueprints": self._apply_blueprints,
            "prompts": self._apply_prompts,
            "steps": self._apply_steps,
            "analysis_templates": self._apply_analysis_templates,
            "shortcuts": self._apply_shortcuts,
            "layouts": self._apply_layouts,
            "plugins": self._apply_plugins,
            "plugin_configs": self._apply_plugin_configs,
            "plugin_data": self._apply_plugin_data,
        }
        fn = appliers.get(cat)
        return fn(zf, requested_ids, renames) if fn else 0

    def _apply_themes(self, zf, ids, renames=None):
        renames = renames or {}
        if not self._theme_manager:
            return 0
        count = 0
        for info in zf.infolist():
            if not info.filename.startswith("themes/") or not info.filename.endswith(".json"):
                continue
            try:
                data = json.loads(zf.read(info.filename))
                name = data.get("name", "")
                if ids and name not in ids:
                    continue
                if name in renames:
                    data["name"] = renames[name]
                imported = self._theme_manager.import_theme(data)
                if imported:
                    count += 1
            except Exception:
                pass
        return count

    def _apply_blueprints(self, zf, ids, renames=None):
        renames = renames or {}
        if not self._blueprint_manager:
            return 0
        from core.engine.action_model import AIActionBlueprint
        count = 0
        for info in zf.infolist():
            if not info.filename.startswith("blueprints/") or not info.filename.endswith(".json"):
                continue
            try:
                data = json.loads(zf.read(info.filename))
                key = data.pop("_pack_id", data.get("name", ""))
                if not key or (ids and key not in ids):
                    continue
                new_key = renames.get(key, key)
                if new_key != key:
                    data["name"] = new_key
                bp = AIActionBlueprint.from_dict(data)
                self._blueprint_manager.blueprints[new_key] = bp
                count += 1
            except Exception:
                pass
        if count:
            self._save_blueprints()
        return count

    def _save_blueprints(self) -> None:
        if not self._blueprint_manager:
            return
        try:
            out_data = {
                k: dataclasses.asdict(v)
                for k, v in self._blueprint_manager.blueprints.items()
            }
            with open(self._blueprint_manager.blueprint_file, "w", encoding="utf-8") as f:
                json.dump(out_data, f, indent=4)
            if hasattr(self._blueprint_manager, "_register_custom_blueprints"):
                self._blueprint_manager._register_custom_blueprints()
        except Exception as exc:
            self.operation_failed.emit(f"Failed to save blueprints: {exc}")

    def _apply_prompts(self, zf, ids, renames=None):
        renames = renames or {}
        if not self._prompt_manager:
            return 0
        try:
            data = json.loads(zf.read("prompts/prompts.json"))
        except Exception:
            return 0
        count = 0
        for key, text in data.items():
            if ids and key not in ids:
                continue
            save_key = renames.get(key, key)
            self._prompt_manager.save_prompt(save_key, text)
            count += 1
        return count

    def _apply_steps(self, zf, ids, renames=None):
        renames = renames or {}
        if not self._step_manager:
            return 0
        try:
            data = json.loads(zf.read("steps/steps.json"))
        except Exception:
            return 0
        from core.engine.action_model import ActionStep
        count = 0
        for key, step_data in data.items():
            if any(key.startswith(p) for p in _CORE_STEP_PREFIXES):
                continue
            if ids and key not in ids:
                continue
            new_key = renames.get(key, key)
            try:
                self._step_manager.library[new_key] = ActionStep(**step_data)
                count += 1
            except Exception:
                pass
        if count:
            self._step_manager.save_library()
        return count

    def _apply_analysis_templates(self, zf, ids, renames=None):
        pm = self._project_manager
        if pm is None:
            return 0
        db = getattr(pm, "db_docs", None)
        if db is None:
            return 0
        try:
            templates = json.loads(zf.read("analysis_templates/templates.json"))
        except Exception:
            return 0
        system_ids = _load_system_template_ids()
        try:
            existing = db.get_analysis_templates()
            existing_ids = {t.get("id") for t in existing}
        except Exception:
            existing_ids = set()
        count = 0
        for t in templates:
            tid = t.get("id")
            if not tid or tid in system_ids:
                continue
            if ids and tid not in ids:
                continue
            try:
                all_templates = db.get_analysis_templates()
                updated = [x for x in all_templates if x.get("id") != tid]
                updated.append(t)
                db.save_analysis_templates(updated)
                count += 1
            except Exception:
                pass
        return count

    def _apply_shortcuts(self, zf, ids, renames=None):
        if not self._keybinding_registry:
            return 0
        try:
            data = json.loads(zf.read("shortcuts/shortcuts.json"))
        except Exception:
            return 0
        count = 0
        for action_id, key_str in data.items():
            if ids and action_id not in ids:
                continue
            try:
                self._keybinding_registry.set_override(action_id, key_str)
                count += 1
            except Exception:
                pass
        return count

    def _apply_layouts(self, zf, ids, renames=None):
        renames = renames or {}
        try:
            data = json.loads(zf.read("layouts/layouts.json"))
        except Exception:
            return 0
        from PySide6.QtCore import QSettings
        settings = QSettings("PDFMultitool", "Workspace")
        count = 0
        for name, payload_str in data.items():
            if ids and name not in ids:
                continue
            save_name = renames.get(name, name)
            settings.setValue(f"layouts/{save_name}", payload_str)
            count += 1
        if count:
            settings.sync()
        return count

    def _apply_plugins(self, zf, ids, renames=None):
        plugins_dir = _plugins_dir()
        os.makedirs(plugins_dir, exist_ok=True)
        installed: Dict[str, List[str]] = {}
        for info in zf.infolist():
            if not info.filename.startswith("plugins/"):
                continue
            parts = info.filename.split("/")
            if len(parts) < 3:
                continue
            plugin_dir_name = parts[1]
            if not plugin_dir_name:
                continue
            if ids and plugin_dir_name not in ids:
                continue
            installed.setdefault(plugin_dir_name, []).append(info.filename)

        count = 0
        for plugin_dir_name, file_list in installed.items():
            dest = os.path.join(plugins_dir, plugin_dir_name)
            os.makedirs(dest, exist_ok=True)
            for arc_path in file_list:
                rel_within_plugin = "/".join(arc_path.split("/")[2:])
                if not rel_within_plugin:
                    continue
                target = os.path.join(dest, rel_within_plugin)
                os.makedirs(os.path.dirname(target), exist_ok=True)
                with zf.open(arc_path) as src, open(target, "wb") as dst:
                    shutil.copyfileobj(src, dst)
            count += 1
        return count

    def _apply_plugin_configs(self, zf, ids, renames=None):
        configs_dir = _plugin_configs_dir()
        os.makedirs(configs_dir, exist_ok=True)
        count = 0
        for info in zf.infolist():
            if not info.filename.startswith("plugin_configs/") or not info.filename.endswith(".json"):
                continue
            fname = info.filename.split("/")[-1]
            plugin_id = fname[:-5]
            if ids and plugin_id not in ids:
                continue
            target = os.path.join(configs_dir, fname)
            with zf.open(info.filename) as src, open(target, "wb") as dst:
                shutil.copyfileobj(src, dst)
            count += 1
        return count

    def _apply_plugin_data(self, zf, ids, renames=None):
        if not self._contributor_registry:
            return 0
        count = 0
        for contributor in self._contributor_registry.get_all():
            try:
                cid = contributor.contributor_id
                fname = f"plugin_data/{cid}.json"
                if fname not in zf.namelist():
                    continue
                data = json.loads(zf.read(fname))
                preview = contributor.preview_items(data)
                target_ids = [it.item_id for it in preview if not ids or it.item_id in ids]
                if target_ids and contributor.import_items(data, target_ids):
                    count += len(target_ids)
            except Exception:
                pass
        return count

    # ─────────────────────────────────────────────────────────────────────
    # Helpers
    # ─────────────────────────────────────────────────────────────────────

    def _get_custom_templates(self) -> List[dict]:
        """Return user-custom analysis templates if a project is open, else []."""
        system_ids = _load_system_template_ids()
        pm = self._project_manager
        if pm is None:
            return []
        db = getattr(pm, "db_docs", None)
        if db is None:
            return []
        try:
            all_tmpl = db.get_analysis_templates()
            return [t for t in all_tmpl if t.get("id") not in system_ids]
        except Exception:
            return []

    @staticmethod
    def _app_version() -> str:
        try:
            from core import __version__
            return __version__
        except Exception:
            return "unknown"
