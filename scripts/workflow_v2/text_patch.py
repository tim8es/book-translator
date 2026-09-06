"""Deterministic CAS-protected text patching for Workflow v2."""

from __future__ import annotations

import difflib
from dataclasses import dataclass

from .storage import StorageBackend, StorageError, StorageVersionConflict


class TextPatchError(RuntimeError):
    """A text patch cannot be applied safely."""


@dataclass(frozen=True)
class TextPatchResult:
    path: str
    match_count: int
    changed: bool
    dry_run: bool
    original_version: str
    new_version: str | None
    diff: str


def _require_text(value: object, name: str) -> str:
    if not isinstance(value, str):
        raise TextPatchError(f"{name} must be a string")
    return value


def _unified_diff(path: str, original: str, updated: str) -> str:
    return "".join(
        difflib.unified_diff(
            original.splitlines(keepends=True),
            updated.splitlines(keepends=True),
            fromfile=f"a/{path}",
            tofile=f"b/{path}",
        )
    )


def patch_text(
    storage: StorageBackend,
    path: str,
    *,
    old: str,
    new: str,
    expected_count: int,
    regex: bool = False,
    line_start: int | None = None,
    line_end: int | None = None,
    dry_run: bool = False,
) -> TextPatchResult:
    """Patch one UTF-8 text file only when exact preconditions still hold."""

    old = _require_text(old, "old")
    new = _require_text(new, "new")
    if type(expected_count) is not int or expected_count < 0:
        raise TextPatchError("expected_count must be an integer >= 0")
    if not isinstance(regex, bool):
        raise TextPatchError("regex must be a boolean")
    if not isinstance(dry_run, bool):
        raise TextPatchError("dry_run must be a boolean")
    if old == "":
        raise TextPatchError("old must not be empty in literal mode")
    if regex:
        raise TextPatchError("regex mode is not implemented")
    if line_start is not None or line_end is not None:
        raise TextPatchError("line scoping is not implemented")

    try:
        original = storage.read(path)
    except StorageError as exc:
        raise TextPatchError(f"cannot read patch target {path!r}: {exc}") from exc

    try:
        text = original.content.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise TextPatchError(f"patch target {path!r} is not valid UTF-8") from exc

    match_count = text.count(old)
    if match_count != expected_count:
        raise TextPatchError(
            f"expected {expected_count} match(es), observed {match_count} in {path!r}"
        )

    updated = text.replace(old, new)
    updated_bytes = updated.encode("utf-8")
    changed = updated_bytes != original.content
    diff = _unified_diff(path, text, updated) if changed else ""

    if not changed or dry_run:
        return TextPatchResult(
            path=path,
            match_count=match_count,
            changed=changed,
            dry_run=dry_run,
            original_version=original.version,
            new_version=None,
            diff=diff,
        )

    try:
        new_version = storage.write_if_version(path, updated_bytes, original.version)
    except StorageVersionConflict as exc:
        raise TextPatchError(f"patch target {path!r} changed before commit") from exc
    except StorageError as exc:
        raise TextPatchError(f"cannot write patch target {path!r}: {exc}") from exc

    return TextPatchResult(
        path=path,
        match_count=match_count,
        changed=True,
        dry_run=False,
        original_version=original.version,
        new_version=new_version,
        diff=diff,
    )
