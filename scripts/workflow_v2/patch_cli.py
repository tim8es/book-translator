"""Argparse integration for deterministic Workflow v2 text patching."""

from __future__ import annotations

import argparse
from pathlib import Path

from .filesystem import FilesystemStorage
from .text_patch import TextPatchError, patch_text


class PatchCliError(RuntimeError):
    """Expected patch-command error suitable for book.py output."""


def patch_command(args: argparse.Namespace, root: Path) -> int:
    storage = FilesystemStorage(root)
    try:
        result = patch_text(
            storage,
            args.path,
            old=args.old,
            new=args.new,
            expected_count=args.expected_count,
            regex=args.regex,
            line_start=args.line_start,
            line_end=args.line_end,
            dry_run=args.dry_run,
        )
    except TextPatchError as exc:
        raise PatchCliError(str(exc)) from exc

    if result.diff:
        print(result.diff, end="" if result.diff.endswith("\n") else "\n")
    changed = "yes" if result.changed else "no"
    mode = "dry-run" if result.dry_run else "apply"
    print(
        f"patch {result.path}: matches={result.match_count} "
        f"changed={changed} mode={mode}"
    )
    return 0


def register_patch_command(
    subparsers: argparse._SubParsersAction,
    root: Path,
) -> None:
    """Register repository-relative safe text patching on the main parser."""

    patch = subparsers.add_parser(
        "patch",
        help="Safely patch one repository-relative UTF-8 text file.",
    )
    patch.add_argument("path", help="Repository-relative target path.")
    patch.add_argument("--old", required=True, help="Literal text or regex pattern to replace.")
    patch.add_argument("--new", required=True, help="Replacement text.")
    patch.add_argument("--expected-count", type=int, required=True, help="Exact required match count.")
    patch.add_argument("--regex", action="store_true", help="Treat --old/--new as Python regex pattern/replacement.")
    patch.add_argument("--line-start", type=int, help="Optional 1-based inclusive first line.")
    patch.add_argument("--line-end", type=int, help="Optional 1-based inclusive last line.")
    patch.add_argument("--dry-run", action="store_true", help="Show diff without writing.")
    patch.set_defaults(func=lambda args: patch_command(args, root))
