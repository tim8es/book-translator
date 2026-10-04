"""CLI adapter for durable source-edition updates."""

from __future__ import annotations

import argparse
import hashlib
import importlib
import json
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any

from .repository import RepositoryError
from .schemas import SchemaError, SchemaKind
from .source_updates import (
    SourceRevisionConflict,
    SourceRevisionDecisionRequired,
    SourceRevisionError,
    SourceRevisionManager,
    initialize_source_revisions,
)
from .storage import StorageError, StorageNotFound


class SourceUpdateCliError(RuntimeError):
    """Expected source-update error suitable for concise CLI output."""


ErrorFactory = Callable[[str], Exception]


def _active_book_module():
    main = sys.modules.get("__main__")
    if main is not None and hasattr(main, "extract_chapters") and hasattr(main, "state_repository"):
        return main
    return importlib.import_module("book")


def _book_context(root: Path, slug: str):
    book = _active_book_module()
    book_dir = root / "books" / slug
    if not book_dir.is_dir():
        raise SourceUpdateCliError(f"Book directory does not exist: books/{slug}")
    repository = book.state_repository(book_dir)
    return book, book_dir, repository


def _print_json(value: Any) -> None:
    print(json.dumps(value, ensure_ascii=False, sort_keys=True))


def _ensure_revision_catalog(repository) -> None:
    try:
        repository.read("source-revisions.json", SchemaKind.SOURCE_REVISIONS)
    except StorageNotFound:
        try:
            initialize_source_revisions(repository)
        except SourceRevisionError as exc:
            raise SourceUpdateCliError(
                "cannot bootstrap source revision history from the current durable state: " + str(exc)
            ) from exc
    except (StorageError, RepositoryError, SchemaError) as exc:
        raise SourceUpdateCliError(f"source revision catalog is invalid: {exc}") from exc


def source_revisions_command(args: argparse.Namespace, root: Path) -> int:
    _, _, repository = _book_context(root, args.slug)
    _ensure_revision_catalog(repository)
    try:
        catalog = SourceRevisionManager(repository).catalog().data
    except SourceRevisionError as exc:
        raise SourceUpdateCliError(str(exc)) from exc
    if args.json:
        _print_json(catalog)
    else:
        print(f"active source revision: {catalog['active_revision']}")
        for entry in catalog["revisions"]:
            print(
                f"- {entry['revision_id']} {entry['state']} "
                f"sha256={entry['source_sha256']} file={entry['source_file']}"
            )
    return 0


def update_source_command(args: argparse.Namespace, root: Path) -> int:
    book, book_dir, repository = _book_context(root, args.slug)
    _ensure_revision_catalog(repository)

    # Current state must be valid before deriving any new edition.
    from .source_cli import _current_workspace_errors

    errors = _current_workspace_errors(book, args.slug)
    if errors:
        raise SourceUpdateCliError(
            "current book state must pass preflight before staging a new source edition:\n- "
            + "\n- ".join(errors)
        )

    source = Path(args.source).expanduser().resolve()
    if not source.is_file():
        raise SourceUpdateCliError(f"Source file does not exist: {source}")
    try:
        source_format = book.detect_format(source)
        chapters, _ = book.extract_chapters(source, source_format)
    except Exception as exc:
        raise SourceUpdateCliError(f"cannot extract candidate source edition: {exc}") from exc

    raw = source.read_bytes()
    source_sha = hashlib.sha256(raw).hexdigest()
    try:
        metadata = repository.read("metadata.json", SchemaKind.METADATA).data
    except (StorageError, RepositoryError, SchemaError) as exc:
        raise SourceUpdateCliError(f"cannot read current metadata: {exc}") from exc

    current_source = metadata.get("source")
    inherited_private = (
        isinstance(current_source, dict)
        and current_source.get("storage_mode") == "private_external"
    )
    private_source = inherited_private if args.private_source is None else bool(args.private_source)

    manager = SourceRevisionManager(repository)
    try:
        staged = manager.stage(
            source_filename=source.name,
            source_format=source_format,
            source_sha256=source_sha,
            source_size_bytes=len(raw),
            source_bytes=None if private_source else raw,
            private_source=private_source,
            candidate_units=[
                {"title": chapter.title, "content": chapter.content}
                for chapter in chapters
            ],
        )
    except (SourceRevisionError, StorageError, RepositoryError, SchemaError) as exc:
        raise SourceUpdateCliError(str(exc)) from exc

    if staged["state"] == "already_active":
        result = staged
    else:
        delta = staged["delta"]
        requires_decision = int(delta.get("deleted", 0)) > 0 and not args.allow_deletions
        if args.stage_only or requires_decision:
            result = {
                "state": "staged_requires_decision" if requires_decision else "staged",
                "revision_id": staged["revision_id"],
                "delta": delta,
            }
        else:
            try:
                promoted = manager.promote(
                    staged["revision_id"],
                    allow_deletions=bool(args.allow_deletions),
                    session_id=f"source-update-{staged['revision_id']}",
                )
            except SourceRevisionDecisionRequired as exc:
                result = {
                    "state": "staged_requires_decision",
                    "revision_id": staged["revision_id"],
                    "delta": delta,
                    "reason": str(exc),
                }
            except (SourceRevisionError, StorageError, RepositoryError, SchemaError) as exc:
                raise SourceUpdateCliError(str(exc)) from exc
            else:
                result = promoted
                result["delta"] = delta

    if args.json:
        _print_json(result)
    else:
        print(
            f"{result['state']}: {result['revision_id']} "
            f"delta={result.get('delta', {})}"
        )
    del book_dir
    return 0


def promote_source_update_command(args: argparse.Namespace, root: Path) -> int:
    _, _, repository = _book_context(root, args.slug)
    _ensure_revision_catalog(repository)
    try:
        result = SourceRevisionManager(repository).promote(
            args.revision_id,
            allow_deletions=bool(args.allow_deletions),
            session_id=f"source-promotion-{args.revision_id}",
        )
    except SourceRevisionDecisionRequired as exc:
        raise SourceUpdateCliError(str(exc)) from exc
    except (SourceRevisionError, StorageError, RepositoryError, SchemaError) as exc:
        raise SourceUpdateCliError(str(exc)) from exc
    if args.json:
        _print_json(result)
    else:
        print(f"{result['state']}: {result['revision_id']}")
    return 0


def discard_source_update_command(args: argparse.Namespace, root: Path) -> int:
    _, _, repository = _book_context(root, args.slug)
    _ensure_revision_catalog(repository)
    try:
        result = SourceRevisionManager(repository).discard(args.revision_id)
    except (SourceRevisionError, StorageError, RepositoryError, SchemaError) as exc:
        raise SourceUpdateCliError(str(exc)) from exc
    if args.json:
        _print_json(result)
    else:
        print(f"{result['state']}: {result['revision_id']}")
    return 0


def _adapt(command: Callable[[argparse.Namespace], int], error_factory: ErrorFactory):
    def run(args: argparse.Namespace) -> int:
        try:
            return command(args)
        except SourceUpdateCliError as exc:
            raise error_factory(str(exc)) from exc
    return run


def _revision_id(value: str) -> str:
    if not isinstance(value, str) or not value.startswith("source-") or len(value) != 13:
        raise argparse.ArgumentTypeError("source revision must match source-000001")
    suffix = value[7:]
    if not suffix.isdigit() or int(suffix) < 1:
        raise argparse.ArgumentTypeError("source revision must match source-000001")
    return value


def register_source_update_commands(
    subparsers: argparse._SubParsersAction,
    root: Path,
    *,
    error_factory: ErrorFactory = SourceUpdateCliError,
) -> None:
    if "update-source" in subparsers.choices:
        return

    revisions = subparsers.add_parser(
        "source-revisions",
        help="Inspect immutable source-edition revision history.",
    )
    revisions.add_argument("slug", help="Book slug under books/.")
    revisions.add_argument("--json", action="store_true", help="Emit deterministic machine-readable JSON.")
    revisions.set_defaults(
        func=_adapt(lambda args: source_revisions_command(args, root), error_factory)
    )

    update = subparsers.add_parser(
        "update-source",
        help="Stage a later source edition, reuse unchanged reviewed units, and promote safe deltas.",
    )
    update.add_argument("slug", help="Book slug under books/.")
    update.add_argument("source", help="Path to the later EPUB/HTML/XHTML/TXT/Markdown source.")
    storage = update.add_mutually_exclusive_group()
    storage.add_argument(
        "--private-source",
        dest="private_source",
        action="store_true",
        default=None,
        help="Record source identity but keep the new source binary outside the workspace.",
    )
    storage.add_argument(
        "--embedded-source",
        dest="private_source",
        action="store_false",
        help="Persist the exact new source binary in the workspace.",
    )
    update.add_argument(
        "--stage-only",
        action="store_true",
        help="Create an immutable candidate revision without promoting it.",
    )
    update.add_argument(
        "--allow-deletions",
        action="store_true",
        help="Explicitly permit promotion of an edition that deletes source units.",
    )
    update.add_argument("--json", action="store_true", help="Emit deterministic machine-readable JSON.")
    update.set_defaults(func=_adapt(lambda args: update_source_command(args, root), error_factory))

    promote = subparsers.add_parser(
        "promote-source-update",
        help="Promote one staged source edition through the crash-safe source barrier.",
    )
    promote.add_argument("slug", help="Book slug under books/.")
    promote.add_argument("revision_id", type=_revision_id)
    promote.add_argument("--allow-deletions", action="store_true")
    promote.add_argument("--json", action="store_true")
    promote.set_defaults(
        func=_adapt(lambda args: promote_source_update_command(args, root), error_factory)
    )

    discard = subparsers.add_parser(
        "discard-source-update",
        help="Mark a staged source edition discarded without deleting immutable evidence.",
    )
    discard.add_argument("slug", help="Book slug under books/.")
    discard.add_argument("revision_id", type=_revision_id)
    discard.add_argument("--json", action="store_true")
    discard.set_defaults(
        func=_adapt(lambda args: discard_source_update_command(args, root), error_factory)
    )
