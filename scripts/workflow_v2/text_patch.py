"""Deterministic CAS-protected text patching for Workflow v2."""

from __future__ import annotations

import difflib
import re
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


def _require_line(value: int | None, name: str) -> int | None:
    if value is None:
        return None
    if type(value) is not int or value < 1:
        raise TextPatchError(f"{name} must be a positive integer")
    return value


def _scope_text(
    text: str,
    line_start: int | None,
    line_end: int | None,
) -> tuple[str, str, str]:
    line_start = _require_line(line_start, "line_start")
    line_end = _require_line(line_end, "line_end")
    if line_start is None and line_end is None:
        return "", text, ""

    lines = text.splitlines(keepends=True)
    if not lines:
        raise TextPatchError("line scope is invalid for an empty file")
    total = len(lines)
    start = line_start if line_start is not None else 1
    end = line_end if line_end is not None else total
    if start > end:
        raise TextPatchError(f"line_start {start} must be <= line_end {end}")
    if start > total or end > total:
        raise TextPatchError(
            f"line scope {start}-{end} exceeds file line count {total}"
        )
    return (
        "".join(lines[: start - 1]),
        "".join(lines[start - 1 : end]),
        "".join(lines[end:]),
    )


def _replace_scope(
    scope: str,
    *,
    old: str,
    new: str,
    regex: bool,
) -> tuple[str, int]:
    if not regex:
        if old == "":
            raise TextPatchError("old must not be empty in literal mode")
        return scope.replace(old, new), scope.count(old)

    try:
        pattern = re.compile(old)
    except re.error as exc:
        raise TextPatchError(f"invalid regular expression: {exc}") from exc
    try:
        return pattern.subn(new, scope)
    except re.error as exc:
        raise TextPatchError(f"invalid regular-expression replacement: {exc}") from exc


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

    try:
        original = storage.read(path)
    except StorageError as exc:
        raise TextPatchError(f"cannot read patch target {path!r}: {exc}") from exc

    try:
        text = original.content.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise TextPatchError(f"patch target {path!r} is not valid UTF-8") from exc

    prefix, scope, suffix = _scope_text(text, line_start, line_end)
    updated_scope, match_count = _replace_scope(
        scope,
        old=old,
        new=new,
        regex=regex,
    )
    if match_count != expected_count:
        raise TextPatchError(
            f"expected {expected_count} match(es), observed {match_count} in {path!r}"
        )

    updated = prefix + updated_scope + suffix
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
