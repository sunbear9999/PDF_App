"""
core/services/pack_dependency.py

Dependency analysis for the .ppack import/export system.

Analyses which custom items (prompts, steps) a blueprint or analysis template
requires, so the user can be warned when exporting/importing without all deps.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Dict, List, Optional, Set

if TYPE_CHECKING:
    from core.prompt_manager import PromptManager
    from core.engine.step_manager import StepManager

# Matches {prompt:Some Prompt Key} inside strings
_PROMPT_TEMPLATE_RE = re.compile(r'\{prompt:([^}]+)\}')

# Step types that don't reference prompts or library steps
_PROMPT_FREE_TYPES = frozenset({"RAG_SEARCH", "PYTHON_SCRIPT", "BRANCH"})

# Categories that require a security warning before import
UNSAFE_CATEGORIES: frozenset = frozenset({"plugins", "blueprints", "steps"})

# Categories that are always safe
SAFE_CATEGORIES: frozenset = frozenset({"themes", "layouts", "shortcuts", "plugin_configs"})


# ─────────────────────────────────────────────────────────────────────────────
# Low-level helpers
# ─────────────────────────────────────────────────────────────────────────────

def extract_prompt_refs_from_step(step) -> Set[str]:
    """Return all prompt key strings referenced by a single ActionStep."""
    refs: Set[str] = set()
    if getattr(step, "prompt_key", None):
        refs.add(step.prompt_key)
    if getattr(step, "system_prompt", None):
        refs.update(_PROMPT_TEMPLATE_RE.findall(step.system_prompt))
    for val in (getattr(step, "inputs", None) or {}).values():
        if isinstance(val, str):
            refs.update(_PROMPT_TEMPLATE_RE.findall(val))
    return refs


def _walk_steps(steps) -> tuple[Set[str], Set[str]]:
    """
    Recursively walk a step list and return:
      (all_prompt_keys, all_step_refs)
    """
    prompt_keys: Set[str] = set()
    step_refs: Set[str] = set()
    for step in steps or []:
        prompt_keys |= extract_prompt_refs_from_step(step)
        if getattr(step, "step_type", "") == "LIBRARY_REF" and getattr(step, "step_ref", None):
            step_refs.add(step.step_ref)
        sub_p, sub_s = _walk_steps(getattr(step, "if_true", []) or [])
        prompt_keys |= sub_p
        step_refs |= sub_s
        sub_p, sub_s = _walk_steps(getattr(step, "if_false", []) or [])
        prompt_keys |= sub_p
        step_refs |= sub_s
    return prompt_keys, step_refs


_TEMPLATE_PROMPT_FIELDS = (
    "chunk_prompt_key",
    "master_prompt_key",
    "synthesis_prompt_key",
    "chunk_query_prompt_key",
    "master_query_prompt_key",
    "synthesis_query_prompt_key",
)


# ─────────────────────────────────────────────────────────────────────────────
# Dependency result models
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class ItemDeps:
    """Dependencies for a single exportable/importable item."""
    item_id: str
    category: str
    prompt_deps: List[str] = field(default_factory=list)
    step_deps: List[str] = field(default_factory=list)

    @property
    def has_deps(self) -> bool:
        return bool(self.prompt_deps or self.step_deps)


@dataclass
class ExportDepWarning:
    """A dependency that is missing from the user's export selection."""
    item_id: str          # the item being exported
    category: str         # its category
    missing_dep_id: str   # the dependency not included in selection
    dep_category: str     # category of the missing dependency
    dep_exists_locally: bool = True  # False if it's not a user-custom item


@dataclass
class ImportDepWarning:
    """A dependency that is required by an imported item but absent locally."""
    item_id: str          # the item being imported
    category: str         # its category
    missing_dep_id: str   # the required dep not present in user's app
    dep_category: str     # category of the missing dep
    dep_in_pack: bool = False  # True if the dep is also in the .ppack


@dataclass
class ConflictItem:
    """An item in the import pack that collides with an existing local item."""
    item_id: str
    category: str
    display_name: str


# ─────────────────────────────────────────────────────────────────────────────
# Export dependency analysis
# ─────────────────────────────────────────────────────────────────────────────

def get_blueprint_deps(
    blueprint,
    prompt_manager: Optional["PromptManager"],
    step_manager: Optional["StepManager"],
) -> ItemDeps:
    """Return all user-custom prompt and step dependencies of a blueprint."""
    prompt_keys, step_refs = _walk_steps(getattr(blueprint, "steps", []) or [])

    custom_prompt_keys = set(getattr(prompt_manager, "custom_prompts", {}).keys())
    custom_step_keys = {
        k for k in getattr(step_manager, "library", {}).keys()
        if not k.startswith("core_")
    }

    bp_name = getattr(blueprint, "name", "")
    return ItemDeps(
        item_id=bp_name,
        category="blueprints",
        prompt_deps=sorted(prompt_keys & custom_prompt_keys),
        step_deps=sorted(step_refs & custom_step_keys),
    )


def get_step_deps(
    step_id: str,
    step,
    prompt_manager: Optional["PromptManager"],
) -> ItemDeps:
    """Return all user-custom prompt dependencies of a library step."""
    prompt_keys = extract_prompt_refs_from_step(step)
    custom_prompt_keys = set(getattr(prompt_manager, "custom_prompts", {}).keys())
    return ItemDeps(
        item_id=step_id,
        category="steps",
        prompt_deps=sorted(prompt_keys & custom_prompt_keys),
    )


def get_template_deps(
    template: dict,
    prompt_manager: Optional["PromptManager"],
) -> ItemDeps:
    """Return all user-custom prompt dependencies of an analysis template."""
    custom_prompt_keys = set(getattr(prompt_manager, "custom_prompts", {}).keys())
    deps = []
    for field_name in _TEMPLATE_PROMPT_FIELDS:
        val = template.get(field_name)
        if val and val in custom_prompt_keys:
            deps.append(val)
    return ItemDeps(
        item_id=template.get("id", ""),
        category="analysis_templates",
        prompt_deps=sorted(set(deps)),
    )


def build_export_dep_map(
    selection: Dict[str, List[str]],
    blueprint_manager,
    step_manager,
    prompt_manager,
    analysis_templates: Optional[List[dict]] = None,
) -> Dict[str, ItemDeps]:
    """
    For a given export selection, compute the full dependency map.
    Returns {item_key: ItemDeps} for all items that have deps.
    """
    dep_map: Dict[str, ItemDeps] = {}

    selected_bp_ids = selection.get("blueprints", [])
    all_bps = getattr(blueprint_manager, "blueprints", {})
    for bp_id, bp in all_bps.items():
        if selected_bp_ids and bp_id not in selected_bp_ids:
            continue
        deps = get_blueprint_deps(bp, prompt_manager, step_manager)
        if deps.has_deps:
            dep_map[bp_id] = deps

    selected_step_ids = selection.get("steps", [])
    all_steps = {
        k: v for k, v in getattr(step_manager, "library", {}).items()
        if not k.startswith("core_")
    }
    for step_id, step in all_steps.items():
        if selected_step_ids and step_id not in selected_step_ids:
            continue
        deps = get_step_deps(step_id, step, prompt_manager)
        if deps.has_deps:
            dep_map[step_id] = deps

    selected_tmpl_ids = selection.get("analysis_templates", [])
    for tmpl in (analysis_templates or []):
        tid = tmpl.get("id", "")
        if selected_tmpl_ids and tid not in selected_tmpl_ids:
            continue
        deps = get_template_deps(tmpl, prompt_manager)
        if deps.has_deps:
            dep_map[tid] = deps

    return dep_map


def get_export_warnings(
    selection: Dict[str, List[str]],
    dep_map: Dict[str, ItemDeps],
) -> List[ExportDepWarning]:
    """
    Check which dependencies of selected items are NOT included in the selection.
    Returns a list of warnings for deps that should be added.
    """
    selected_prompts = set(selection.get("prompts", []))
    selected_steps = set(selection.get("steps", []))

    warnings: List[ExportDepWarning] = []
    for item_id, deps in dep_map.items():
        for p in deps.prompt_deps:
            if p not in selected_prompts:
                warnings.append(ExportDepWarning(
                    item_id=item_id,
                    category=deps.category,
                    missing_dep_id=p,
                    dep_category="prompts",
                    dep_exists_locally=True,
                ))
        for s in deps.step_deps:
            if s not in selected_steps:
                warnings.append(ExportDepWarning(
                    item_id=item_id,
                    category=deps.category,
                    missing_dep_id=s,
                    dep_category="steps",
                    dep_exists_locally=True,
                ))
    return warnings


# ─────────────────────────────────────────────────────────────────────────────
# Import dependency analysis
# ─────────────────────────────────────────────────────────────────────────────

def _extract_dep_ids_from_packed_blueprint(data: dict) -> tuple[Set[str], Set[str]]:
    """Parse a raw blueprint dict from a .ppack and return (prompt_keys, step_refs)."""
    import dataclasses
    from core.engine.action_model import AIActionBlueprint
    try:
        bp = AIActionBlueprint.from_dict(data)
        return _walk_steps(bp.steps or [])
    except Exception:
        return set(), set()


def get_import_warnings(
    pack_categories: Dict[str, List],
    selection: Dict[str, List[str]],
    prompt_manager,
    step_manager,
    all_pack_item_ids: Dict[str, Set[str]],
) -> List[ImportDepWarning]:
    """
    Analyse a pack's items (already parsed for preview) and return warnings
    about deps that are neither in the user's installation nor in the pack.

    pack_categories: {cat: [PackItem, ...]} from preview_pack()
    selection:       {cat: [item_id, ...]} what the user wants to import
    all_pack_item_ids: {cat: {item_id, ...}} everything inside the pack
    """
    local_prompts = set(getattr(prompt_manager, "custom_prompts", {}).keys())
    local_steps = {
        k for k in getattr(step_manager, "library", {}).keys()
        if not k.startswith("core_")
    }
    pack_prompts = all_pack_item_ids.get("prompts", set())
    pack_steps = all_pack_item_ids.get("steps", set())
    importing_prompts = set(selection.get("prompts", []))
    importing_steps = set(selection.get("steps", []))

    warnings: List[ImportDepWarning] = []

    for item in pack_categories.get("blueprints", []):
        if selection.get("blueprints") and item.item_id not in selection["blueprints"]:
            continue
        prompt_deps = set(item.metadata.get("dep_prompts", []))
        step_deps = set(item.metadata.get("dep_steps", []))

        for p in prompt_deps:
            if p not in local_prompts and p not in importing_prompts:
                warnings.append(ImportDepWarning(
                    item_id=item.item_id,
                    category="blueprints",
                    missing_dep_id=p,
                    dep_category="prompts",
                    dep_in_pack=p in pack_prompts,
                ))
        for s in step_deps:
            if s not in local_steps and s not in importing_steps:
                warnings.append(ImportDepWarning(
                    item_id=item.item_id,
                    category="blueprints",
                    missing_dep_id=s,
                    dep_category="steps",
                    dep_in_pack=s in pack_steps,
                ))

    for item in pack_categories.get("steps", []):
        if selection.get("steps") and item.item_id not in selection["steps"]:
            continue
        for p in item.metadata.get("dep_prompts", []):
            if p not in local_prompts and p not in importing_prompts:
                warnings.append(ImportDepWarning(
                    item_id=item.item_id,
                    category="steps",
                    missing_dep_id=p,
                    dep_category="prompts",
                    dep_in_pack=p in pack_prompts,
                ))

    for item in pack_categories.get("analysis_templates", []):
        if selection.get("analysis_templates") and item.item_id not in selection["analysis_templates"]:
            continue
        for p in item.metadata.get("dep_prompts", []):
            if p not in local_prompts and p not in importing_prompts:
                warnings.append(ImportDepWarning(
                    item_id=item.item_id,
                    category="analysis_templates",
                    missing_dep_id=p,
                    dep_category="prompts",
                    dep_in_pack=p in pack_prompts,
                ))

    return warnings


def find_import_conflicts(
    pack_categories: Dict[str, List],
    selection: Dict[str, List[str]],
    prompt_manager,
    step_manager,
    blueprint_manager,
    theme_manager,
    layout_manager,
) -> List[ConflictItem]:
    """
    Return all items in the import selection that already exist locally.
    """
    conflicts: List[ConflictItem] = []

    local_prompts = set(getattr(prompt_manager, "custom_prompts", {}).keys())
    local_steps = {
        k for k in getattr(step_manager, "library", {}).keys()
        if not k.startswith("core_")
    }
    local_blueprints = set(getattr(blueprint_manager, "blueprints", {}).keys())
    local_themes = set(getattr(theme_manager, "_user_themes", {}).keys()) if theme_manager else set()

    layout_names = set()
    if layout_manager:
        try:
            layout_names = set(layout_manager.get_template_names())
        except Exception:
            pass

    def _check(cat, items, local_set):
        sel = selection.get(cat, [])
        for item in items:
            if sel and item.item_id not in sel:
                continue
            if item.item_id in local_set:
                conflicts.append(ConflictItem(item.item_id, cat, item.display_name))

    _check("prompts", pack_categories.get("prompts", []), local_prompts)
    _check("steps", pack_categories.get("steps", []), local_steps)
    _check("blueprints", pack_categories.get("blueprints", []), local_blueprints)
    _check("themes", pack_categories.get("themes", []), local_themes)
    _check("layouts", pack_categories.get("layouts", []), layout_names)

    return conflicts
