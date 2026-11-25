"""Pure tag-folder hierarchy construction for Document Explorer."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterable, Mapping


UNTAGGED_KEY = "__untagged__"


@dataclass
class TagFolder:
    key: object
    name: str
    color: str = "#808080"
    children: list["TagFolder"] = field(default_factory=list)
    sources: list[Mapping[str, Any]] = field(default_factory=list)


def build_tag_folders(
    sources: Iterable[Mapping[str, Any]],
    all_tags: Iterable[Mapping[str, Any]] = (),
) -> list[TagFolder]:
    """Use explicit parents first, then infer safe strict-set parents."""
    records = list(sources)
    tag_info, members = _tag_memberships(records, all_tags)
    parents = _explicit_parents(tag_info)
    parents.update(_nearest_parents(tag_info, members, parents))
    folders = {
        key: TagFolder(key, info["name"], info.get("color") or "#808080")
        for key, info in tag_info.items()
    }
    roots = []
    for key, folder in folders.items():
        parent = parents.get(key)
        (folders[parent].children if parent in folders else roots).append(folder)
    _place_sources(records, folders, parents, roots)
    roots = [folder for folder in roots if _prune_empty(folder)]
    _sort_folders(roots)
    return roots


def _tag_memberships(records, all_tags):
    info = {tag.get("id"): tag for tag in all_tags if tag.get("id") is not None}
    members = {key: set() for key in info}
    for index, source in enumerate(records):
        for tag in source.get("tags", ()):
            key = tag.get("id")
            if key is None:
                continue
            info[key] = {**info.get(key, {}), **tag}
            members.setdefault(key, set()).add(index)
    return info, members


def _explicit_parents(info):
    candidates = {
        key: tag.get("parent_tag_id")
        for key, tag in info.items()
        if tag.get("parent_tag_id") in info and tag.get("parent_tag_id") != key
    }
    return {
        child: parent for child, parent in candidates.items()
        if child not in _ancestors(parent, candidates)
    }


def _nearest_parents(info, members, existing):
    parents = {}
    for child, child_docs in members.items():
        if child in existing or not child_docs:
            continue
        candidates = [
            parent for parent, parent_docs in members.items()
            if child != parent and child_docs < parent_docs and child not in _ancestors(parent, existing)
        ]
        if candidates:
            parents[child] = min(
                candidates,
                key=lambda key: (len(members[key]), info[key]["name"].casefold()),
            )
    return parents


def _place_sources(records, folders, parents, roots):
    untagged = []
    for source in records:
        keys = [tag.get("id") for tag in source.get("tags", ()) if tag.get("id") in folders]
        if not keys:
            untagged.append(source)
            continue
        ancestors = {_ancestor for key in keys for _ancestor in _ancestors(key, parents)}
        for key in keys:
            if key not in ancestors:
                folders[key].sources.append(source)
    if untagged:
        roots.append(TagFolder(UNTAGGED_KEY, "Untagged", sources=untagged))


def _ancestors(key, parents):
    result, seen = [], set()
    while key in parents and key not in seen:
        seen.add(key)
        key = parents[key]
        result.append(key)
    return result


def _prune_empty(folder):
    folder.children = [child for child in folder.children if _prune_empty(child)]
    return bool(folder.sources or folder.children)


def _sort_folders(folders):
    folders.sort(key=lambda folder: folder.name.casefold())
    for folder in folders:
        folder.sources.sort(key=lambda source: str(source.get("title") or "").casefold())
        _sort_folders(folder.children)
