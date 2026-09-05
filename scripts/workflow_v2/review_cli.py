"""Argparse integration for Workflow v2 machine review evidence."""

from __future__ import annotations

import argparse
import json
import subprocess
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from .filesystem import FilesystemStorage
from .repository import RepositoryError, WorkflowStateRepository
from .reviews import (
    AcceptReviewResult,
    ReviewError,
    ReviewLedgerManager,
    ReviewRecordResult,
    ReviewResolution,
)
from .schemas import SchemaError, SchemaKind
from .storage import StorageError


class ReviewCliError(RuntimeError):
    """Expected review-command error suitable for Book Translator CLI output."""


def _book_directory(root: Path, slug: str) -> Path:
    if not isinstance(slug, str) or not slug or "/" in slug or "\\" in slug or slug in {".", ".."}:
        raise ReviewCliError("book slug must be one directory name under books/")
    books_root = (root / "books").resolve(strict=False)
    book_dir = (books_root / slug).resolve(strict=False)
    try:
        book_dir.relative_to(books_root)
    except ValueError as exc:
        raise ReviewCliError("book slug escapes books/") from exc
    if not book_dir.is_dir():
        raise ReviewCliError(f"Book directory does not exist: books/{slug}")
    return book_dir


def _repository(root: Path, slug: str) -> tuple[Path, WorkflowStateRepository]:
    book_dir = _book_directory(root, slug)
    return book_dir, WorkflowStateRepository(FilesystemStorage(book_dir))


def _load_progress(repository: WorkflowStateRepository) -> tuple[dict[str, Any], str]:
    try:
        loaded = repository.read("progress.json", SchemaKind.PROGRESS, allow_legacy=True)
    except (SchemaError, RepositoryError, StorageError) as exc:
        raise ReviewCliError(f"Invalid progress.json: {exc}") from exc
    return loaded.data, loaded.version


def _load_metadata(repository: WorkflowStateRepository) -> dict[str, Any]:
    try:
        return repository.read("metadata.json", SchemaKind.METADATA, allow_legacy=True).data
    except (SchemaError, RepositoryError, StorageError) as exc:
        raise ReviewCliError(f"Invalid metadata.json: {exc}") from exc


def _artifact_reader(book_dir: Path):
    root = book_dir.resolve(strict=False)

    def read(relative_path: str) -> bytes:
        if not isinstance(relative_path, str) or not relative_path.strip():
            raise OSError("artifact path must be a non-empty relative path")
        target = (root / relative_path).resolve(strict=False)
        try:
            target.relative_to(root)
        except ValueError as exc:
            raise OSError(f"artifact path escapes book workspace: {relative_path}") from exc
        return target.read_bytes()

    return read


def _git_head(root: Path) -> str | None:
    try:
        result = subprocess.run(
            ["git", "-C", str(root), "rev-parse", "HEAD"],
            capture_output=True,
            text=True,
            timeout=5,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if result.returncode != 0:
        return None
    value = result.stdout.strip()
    return value or None


def _manager(book_dir: Path, repository: WorkflowStateRepository) -> ReviewLedgerManager:
    return ReviewLedgerManager(repository, artifact_reader=_artifact_reader(book_dir))


def _print_json(payload: Mapping[str, Any]) -> None:
    print(json.dumps(payload, ensure_ascii=False, sort_keys=True))


def _resolution_json(resolution: ReviewResolution) -> dict[str, Any]:
    return {
        "chapter_number": resolution.chapter_number,
        "current_record": resolution.current_record,
        "history": list(resolution.history),
        "source_sha256": resolution.source_sha256,
        "state": resolution.state,
        "translation_sha256": resolution.translation_sha256,
        "unit_id": resolution.unit_id,
    }


def _record_json(result: ReviewRecordResult) -> dict[str, Any]:
    return {
        "ledger_revision": result.ledger_revision,
        "record": result.record,
    }


def _accept_json(result: AcceptReviewResult) -> dict[str, Any]:
    return {
        "changed": result.changed,
        "progress_revision": result.progress_revision,
        "status": result.status,
        "unit_id": result.unit_id,
    }


def review_record_command(args: argparse.Namespace, root: Path) -> int:
    book_dir, repository = _repository(root, args.slug)
    progress, progress_revision = _load_progress(repository)
    metadata = _load_metadata(repository)
    manager = _manager(book_dir, repository)
    review_commit = args.review_commit if args.review_commit is not None else _git_head(root)
    try:
        result = manager.record(
            progress,
            progress_revision,
            metadata,
            args.chapter,
            outcome=args.outcome,
            reviewer_session_id=args.session_id,
            review_commit=review_commit,
        )
    except (ReviewError, SchemaError, RepositoryError, StorageError) as exc:
        raise ReviewCliError(str(exc)) from exc

    if args.json:
        _print_json(_record_json(result))
    else:
        record = result.record
        print(
            f"recorded {record['outcome']} for {record['unit_id']} "
            f"sequence={record['sequence']} translation_sha256={record['translation_sha256']}"
        )
    return 0


def reviews_command(args: argparse.Namespace, root: Path) -> int:
    book_dir, repository = _repository(root, args.slug)
    progress, _ = _load_progress(repository)
    metadata = _load_metadata(repository)
    manager = _manager(book_dir, repository)
    try:
        resolutions = manager.resolve_all(progress, metadata)
    except (ReviewError, SchemaError, RepositoryError, StorageError) as exc:
        raise ReviewCliError(str(exc)) from exc

    payload = {"reviews": [_resolution_json(item) for item in resolutions]}
    if args.json:
        _print_json(payload)
    else:
        for item in resolutions:
            print(
                f"{item.unit_id} state={item.state} "
                f"source_sha256={item.source_sha256 or '-'} "
                f"translation_sha256={item.translation_sha256 or '-'}"
            )
    return 0


def accept_review_command(args: argparse.Namespace, root: Path) -> int:
    book_dir, repository = _repository(root, args.slug)
    progress, progress_revision = _load_progress(repository)
    metadata = _load_metadata(repository)
    manager = _manager(book_dir, repository)
    try:
        result = manager.accept_review(progress, progress_revision, metadata, args.chapter)
    except (ReviewError, SchemaError, RepositoryError, StorageError) as exc:
        raise ReviewCliError(str(exc)) from exc

    if args.json:
        _print_json(_accept_json(result))
    else:
        suffix = "unchanged" if not result.changed else "updated"
        print(f"accepted {result.unit_id} as reviewed ({suffix})")
    return 0


def _positive_chapter(value: str) -> int:
    try:
        number = int(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("chapter must be a positive integer") from exc
    if number < 1 or str(number) != value:
        raise argparse.ArgumentTypeError("chapter must be a positive integer")
    return number


def register_review_commands(subparsers: argparse._SubParsersAction, root: Path) -> None:
    """Register review evidence commands on the main book.py parser."""

    record = subparsers.add_parser("review-record", help="Record hash-bound Reviewer evidence.")
    record.add_argument("slug", help="Book slug under books/.")
    record.add_argument("chapter", type=_positive_chapter, help="Positive chapter number.")
    record.add_argument("--outcome", choices=("PASS", "CORRECTIONS_REQUIRED"), required=True)
    record.add_argument("--session-id", required=True, help="Owning reviewer session identity.")
    record.add_argument("--review-commit", help="Relevant Git commit; best-effort HEAD when omitted.")
    record.add_argument("--json", action="store_true", help="Emit deterministic machine-readable JSON.")
    record.set_defaults(func=lambda args: review_record_command(args, root))

    reviews = subparsers.add_parser("reviews", help="Resolve current review evidence for every chapter.")
    reviews.add_argument("slug", help="Book slug under books/.")
    reviews.add_argument("--json", action="store_true", help="Emit deterministic machine-readable JSON.")
    reviews.set_defaults(func=lambda args: reviews_command(args, root))

    accept = subparsers.add_parser("accept-review", help="Promote one chapter using current PASS evidence.")
    accept.add_argument("slug", help="Book slug under books/.")
    accept.add_argument("chapter", type=_positive_chapter, help="Positive chapter number.")
    accept.add_argument("--json", action="store_true", help="Emit deterministic machine-readable JSON.")
    accept.set_defaults(func=lambda args: accept_review_command(args, root))
