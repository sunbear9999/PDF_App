"""Security boundary for reading and installing Papyrus pack archives."""

from __future__ import annotations

import os
import stat
import tempfile
import unicodedata
import zipfile
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Iterator, Sequence


_MIB = 1024 * 1024
_ALLOWED_COMPRESSION = {zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED}
_WINDOWS_DEVICE_NAMES = {
    "aux",
    "con",
    "nul",
    "prn",
    *(f"com{index}" for index in range(1, 10)),
    *(f"lpt{index}" for index in range(1, 10)),
}


class UnsafeArchiveError(ValueError):
    """Raised when an archive cannot be handled within the pack trust boundary."""


@dataclass(frozen=True)
class ArchiveLimits:
    """Resource limits applied before any untrusted archive member is read."""

    max_archive_bytes: int = 256 * _MIB
    max_members: int = 4096
    max_member_bytes: int = 64 * _MIB
    max_total_uncompressed_bytes: int = 512 * _MIB
    max_compression_ratio: float = 200.0
    max_manifest_bytes: int = 1 * _MIB


DEFAULT_ARCHIVE_LIMITS = ArchiveLimits()


@contextmanager
def open_validated_archive(
    file_path: str | os.PathLike[str],
    *,
    limits: ArchiveLimits = DEFAULT_ARCHIVE_LIMITS,
) -> Iterator[zipfile.ZipFile]:
    """Open a ZIP and validate the complete pack before yielding it."""
    with zipfile.ZipFile(file_path, "r") as archive:
        validate_archive(file_path, archive, limits=limits)
        yield archive


def validate_archive(
    file_path: str | os.PathLike[str],
    archive: zipfile.ZipFile,
    *,
    limits: ArchiveLimits = DEFAULT_ARCHIVE_LIMITS,
) -> tuple[zipfile.ZipInfo, ...]:
    """Validate all members and resource budgets before preview or import."""
    archive_size = os.path.getsize(file_path)
    if archive_size > limits.max_archive_bytes:
        raise UnsafeArchiveError(
            f"Pack is too large ({archive_size} bytes; limit {limits.max_archive_bytes})."
        )

    members = tuple(archive.infolist())
    if len(members) > limits.max_members:
        raise UnsafeArchiveError(
            f"Pack contains too many entries ({len(members)}; limit {limits.max_members})."
        )

    seen: dict[str, str] = {}
    path_spellings: dict[str, str] = {}
    regular_files: set[str] = set()
    total_uncompressed = 0
    manifest: zipfile.ZipInfo | None = None

    for member in members:
        canonical_name = _validate_member(member, limits)
        _reject_aliased_ancestor(canonical_name, path_spellings)
        collision_key = _collision_key(canonical_name)
        if collision_key in seen:
            raise UnsafeArchiveError(
                "Pack contains duplicate or case-conflicting entries: "
                f"{seen[collision_key]!r} and {member.filename!r}."
            )
        seen[collision_key] = member.filename

        if not member.is_dir():
            regular_files.add(collision_key)
            total_uncompressed += member.file_size
            if total_uncompressed > limits.max_total_uncompressed_bytes:
                raise UnsafeArchiveError(
                    "Pack expands beyond the allowed total size "
                    f"({limits.max_total_uncompressed_bytes} bytes)."
                )
        if canonical_name == "manifest.json" and not member.is_dir():
            manifest = member

    _reject_file_directory_conflicts(regular_files, seen)
    if manifest is None:
        raise UnsafeArchiveError("Pack does not contain manifest.json.")
    if manifest.file_size > limits.max_manifest_bytes:
        raise UnsafeArchiveError(
            f"Pack manifest exceeds {limits.max_manifest_bytes} bytes."
        )
    return members


def extract_member(
    archive: zipfile.ZipFile,
    member: zipfile.ZipInfo,
    destination_root: str | os.PathLike[str],
    relative_name: str,
    *,
    limits: ArchiveLimits = DEFAULT_ARCHIVE_LIMITS,
) -> Path:
    """Copy one validated regular member beneath a destination root."""
    if member.is_dir():
        raise UnsafeArchiveError(f"Cannot extract directory as a file: {member.filename!r}.")
    _validate_member(member, limits)
    safe_relative = _validate_path(relative_name)
    target = _contained_path(destination_root, safe_relative)
    target.parent.mkdir(parents=True, exist_ok=True)

    temporary_path: str | None = None
    try:
        with archive.open(member, "r") as source:
            with tempfile.NamedTemporaryFile(
                mode="wb", dir=target.parent, prefix=f".{target.name}.", delete=False
            ) as temporary:
                temporary_path = temporary.name
                copied = _copy_bounded(source, temporary, member.file_size)
        if copied != member.file_size:
            raise UnsafeArchiveError(
                f"Pack entry size changed while reading {member.filename!r}."
            )
        os.replace(temporary_path, target)
        temporary_path = None
        return target
    finally:
        if temporary_path is not None:
            try:
                os.unlink(temporary_path)
            except FileNotFoundError:
                pass


def publish_staged_directories(
    staging_root: str | os.PathLike[str],
    destination_root: str | os.PathLike[str],
    directory_names: Sequence[str],
) -> None:
    """Publish staged directories with rollback if any replacement fails."""
    staging = Path(staging_root).resolve()
    destination = Path(destination_root).resolve()
    rollback_root = Path(tempfile.mkdtemp(prefix=".rollback-", dir=staging))
    published: list[tuple[Path, Path, Path, bool]] = []

    try:
        for name in directory_names:
            safe_name = _validate_single_component(name)
            source = _contained_path(staging, safe_name)
            target = _contained_path(destination, safe_name)
            backup = _contained_path(rollback_root, safe_name)
            if not source.is_dir():
                raise UnsafeArchiveError(f"Staged plugin {name!r} is incomplete.")

            had_existing = os.path.lexists(target)
            if had_existing:
                os.replace(target, backup)
            try:
                os.replace(source, target)
            except Exception:
                if had_existing and os.path.lexists(backup):
                    os.replace(backup, target)
                raise
            published.append((source, target, backup, had_existing))
    except Exception:
        for source, target, backup, had_existing in reversed(published):
            if os.path.lexists(target):
                os.replace(target, source)
            if had_existing and os.path.lexists(backup):
                os.replace(backup, target)
        raise


def _validate_member(member: zipfile.ZipInfo, limits: ArchiveLimits) -> str:
    canonical_name = _validate_path(member.filename)
    if member.flag_bits & 0x1:
        raise UnsafeArchiveError(f"Encrypted pack entry is not supported: {member.filename!r}.")
    if member.compress_type not in _ALLOWED_COMPRESSION:
        raise UnsafeArchiveError(
            f"Unsupported compression method for pack entry {member.filename!r}."
        )
    if member.file_size < 0 or member.compress_size < 0:
        raise UnsafeArchiveError(f"Invalid size metadata for {member.filename!r}.")
    if member.file_size > limits.max_member_bytes:
        raise UnsafeArchiveError(
            f"Pack entry {member.filename!r} exceeds {limits.max_member_bytes} bytes."
        )
    if member.file_size:
        if member.compress_size == 0:
            raise UnsafeArchiveError(f"Invalid compressed size for {member.filename!r}.")
        ratio = member.file_size / member.compress_size
        if ratio > limits.max_compression_ratio:
            raise UnsafeArchiveError(
                f"Pack entry {member.filename!r} has a suspicious compression ratio."
            )
    _validate_entry_type(member)
    return canonical_name


def _validate_path(raw_name: str) -> str:
    if not raw_name or "\x00" in raw_name:
        raise UnsafeArchiveError("Pack contains an empty or NUL-containing path.")
    if "\\" in raw_name:
        raise UnsafeArchiveError(f"Pack path uses a backslash: {raw_name!r}.")
    if raw_name.startswith("/") or raw_name.startswith("//"):
        raise UnsafeArchiveError(f"Pack path is absolute: {raw_name!r}.")
    if any(ord(character) < 32 or ord(character) == 127 for character in raw_name):
        raise UnsafeArchiveError(f"Pack path contains a control character: {raw_name!r}.")

    without_directory_suffix = raw_name[:-1] if raw_name.endswith("/") else raw_name
    components = without_directory_suffix.split("/")
    if not without_directory_suffix or any(part in {"", ".", ".."} for part in components):
        raise UnsafeArchiveError(f"Pack path is not normalized: {raw_name!r}.")
    if any(":" in part for part in components):
        raise UnsafeArchiveError(f"Pack path contains a drive or stream separator: {raw_name!r}.")
    if any(part.endswith((" ", ".")) for part in components):
        raise UnsafeArchiveError(f"Pack path has a non-portable suffix: {raw_name!r}.")
    if any(part.split(".", 1)[0].casefold() in _WINDOWS_DEVICE_NAMES for part in components):
        raise UnsafeArchiveError(f"Pack path uses a reserved device name: {raw_name!r}.")

    path = PurePosixPath(without_directory_suffix)
    if path.is_absolute() or any(part == ".." for part in path.parts):
        raise UnsafeArchiveError(f"Pack path escapes its destination: {raw_name!r}.")
    return path.as_posix()


def _validate_entry_type(member: zipfile.ZipInfo) -> None:
    unix_mode = member.external_attr >> 16
    file_type = stat.S_IFMT(unix_mode)
    allowed_type = stat.S_IFDIR if member.is_dir() else stat.S_IFREG
    if file_type not in {0, allowed_type}:
        raise UnsafeArchiveError(f"Pack contains a special file: {member.filename!r}.")


def _collision_key(name: str) -> str:
    return unicodedata.normalize("NFC", name).casefold()


def _reject_aliased_ancestor(name: str, path_spellings: dict[str, str]) -> None:
    parts = name.split("/")
    for index in range(1, len(parts) + 1):
        prefix = "/".join(parts[:index])
        collision_key = _collision_key(prefix)
        previous = path_spellings.setdefault(collision_key, prefix)
        if previous != prefix:
            raise UnsafeArchiveError(
                "Pack contains paths that alias on case-insensitive filesystems: "
                f"{previous!r} and {prefix!r}."
            )


def _reject_file_directory_conflicts(
    regular_files: set[str], all_entries: dict[str, str]
) -> None:
    for entry_key, display_name in all_entries.items():
        parts = entry_key.split("/")
        for index in range(1, len(parts)):
            prefix = "/".join(parts[:index])
            if prefix in regular_files:
                raise UnsafeArchiveError(
                    f"Pack path {display_name!r} is nested beneath file {all_entries[prefix]!r}."
                )


def _contained_path(root: str | os.PathLike[str], relative_name: str) -> Path:
    root_path = Path(root).resolve()
    target = (root_path / relative_name).resolve()
    try:
        target.relative_to(root_path)
    except ValueError as exc:
        raise UnsafeArchiveError(
            f"Pack path escapes its destination: {relative_name!r}."
        ) from exc
    return target


def _validate_single_component(name: str) -> str:
    safe_name = _validate_path(name)
    if "/" in safe_name:
        raise UnsafeArchiveError(f"Expected one path component, received {name!r}.")
    return safe_name


def _copy_bounded(source, destination, expected_size: int) -> int:
    copied = 0
    while True:
        chunk = source.read(min(1024 * 1024, expected_size - copied + 1))
        if not chunk:
            return copied
        copied += len(chunk)
        if copied > expected_size:
            raise UnsafeArchiveError("Pack entry expanded beyond its declared size.")
        destination.write(chunk)
