"""Argparse/filesystem adapter for atomic Workflow v2 finalization."""

from __future__ import annotations

import argparse
import json
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import Any
from uuid import uuid4

from .coordination import FINALIZATION_PATH
from .filesystem import FilesystemStorage
from .finalize import (
    FinalizationError,
    FinalizationManager,
    render_quality_gates_markdown,
    render_state_markdown,
)
from .repository import RepositoryError, WorkflowStateRepository
from .review_report import render_review_report_markdown
from .schemas import SchemaError, SchemaKind
from .storage import (
    StorageAlreadyExists,
    StorageError,
    StorageNotFound,
    StorageVersionConflict,
)


STATE_PATH = "STATE.md"
QUALITY_GATES_PATH = "FINAL_QUALITY_GATES.md"
REVIEW_REPORT_PATH = "REVIEW_REPORT.md"
REPORT_PATHS = (STATE_PATH, QUALITY_GATES_PATH, REVIEW_REPORT_PATH)


class FinalizeCliError(RuntimeError):
    """Expected finalize command error suitable for concise CLI output."""


Preflight = Callable[[str], tuple[Sequence[str], Mapping[str, Any]]]
ErrorFactory = Callable[[str], Exception]


def _book_directory(root: Path, slug: str) -> Path:
    if not isinstance(slug, str) or not slug or "/" in slug or "\\" in slug or slug in {".", ".."}:
        raise FinalizeCliError("book slug must be one directory name under books/")
    books_root = (root / "books").resolve(strict=False)
    book_dir = (books_root / slug).resolve(strict=False)
    try:
        book_dir.relative_to(books_root)
    except ValueError as exc:
        raise FinalizeCliError("book slug escapes books/") from exc
    if not book_dir.is_dir():
        raise FinalizeCliError(f"Book directory does not exist: books/{slug}")
    return book_dir


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


def _render_reports(snapshot: Mapping[str, Any]) -> dict[str, bytes]:
    try:
        rendered = {
            STATE_PATH: render_state_markdown(snapshot),
            QUALITY_GATES_PATH: render_quality_gates_markdown(snapshot),
            REVIEW_REPORT_PATH: render_review_report_markdown(snapshot["review"]),
        }
    except (KeyError, TypeError, ValueError) as exc:
        raise FinalizeCliError(f"cannot render completion reports: {exc}") from exc
    return {path: text.encode("utf-8") for path, text in rendered.items()}


def _write_report(storage: FilesystemStorage, path: str, content: bytes) -> str:
    """Persist one projection with create/CAS and never blindly overwrite."""

    try:
        current = storage.read(path)
    except StorageNotFound:
        try:
            storage.create_if_absent(path, content)
        except StorageAlreadyExists as exc:
            raise FinalizeCliError(f"completion report changed concurrently: {path}") from exc
        except StorageError as exc:
            raise FinalizeCliError(f"cannot create completion report {path}: {exc}") from exc
        return "created"

    if current.content == content:
        return "unchanged"
    try:
        storage.write_if_version(path, content, current.version)
    except StorageVersionConflict as exc:
        raise FinalizeCliError(f"completion report changed concurrently: {path}") from exc
    except StorageError as exc:
        raise FinalizeCliError(f"cannot update completion report {path}: {exc}") from exc
    return "updated"


def _verify_report_bytes(storage: FilesystemStorage, expected: Mapping[str, bytes]) -> None:
    for path in REPORT_PATHS:
        try:
            current = storage.read(path)
        except StorageError as exc:
            raise FinalizeCliError(f"completion report is unavailable after write: {path}: {exc}") from exc
        if current.content != expected[path]:
            raise FinalizeCliError(f"completion report does not match authoritative snapshot: {path}")


def _delete_finalization_marker(
    repository: WorkflowStateRepository,
    *,
    expected_progress_revision: str,
) -> None:
    try:
        marker = repository.read(FINALIZATION_PATH, SchemaKind.FINALIZATION_LOCK)
    except (StorageNotFound, StorageError, RepositoryError, SchemaError) as exc:
        raise FinalizeCliError(f"finalization marker is unavailable before completion cleanup: {exc}") from exc
    if marker.data.get("phase") != "promoted":
        raise FinalizeCliError("finalization marker is not promoted after completion reports")
    if marker.data.get("promoted_progress_revision") != expected_progress_revision:
        raise FinalizeCliError("finalization marker progress revision does not match completed progress")
    try:
        repository.delete_if_version(
            FINALIZATION_PATH,
            SchemaKind.FINALIZATION_LOCK,
            marker.version,
        )
    except (StorageError, RepositoryError, SchemaError) as exc:
        raise FinalizeCliError(f"cannot remove completed finalization marker: {exc}") from exc


def finalize_command(
    args: argparse.Namespace,
    root: Path,
    preflight: Preflight,
) -> int:
    book_dir = _book_directory(root, args.slug)
    storage = FilesystemStorage(book_dir)
    repository = WorkflowStateRepository(storage)
    artifact_reader = _artifact_reader(book_dir)
    session_id = args.session_id or uuid4().hex

    manager = FinalizationManager(
        repository,
        artifact_reader=artifact_reader,
        preflight=lambda: preflight(args.slug),
    )
    try:
        result = manager.finalize(session_id=session_id)
        report_bytes = _render_reports(result.snapshot)
        dispositions = {
            path: _write_report(storage, path, report_bytes[path])
            for path in REPORT_PATHS
        }

        # A fresh pass verifies authoritative state after all projection writes.
        # A promoted marker makes this recovery-safe and prevents a progress rewrite.
        fresh = manager.finalize(session_id=session_id)
        if fresh.progress_revision != result.progress_revision:
            raise FinalizeCliError("progress revision changed during completion report generation")
        if fresh.snapshot != result.snapshot:
            raise FinalizeCliError("authoritative completion snapshot changed during report generation")
        expected = _render_reports(fresh.snapshot)
        if expected != report_bytes:
            raise FinalizeCliError("completion reports changed across post-validation")
        _verify_report_bytes(storage, expected)
        _delete_finalization_marker(
            repository,
            expected_progress_revision=fresh.progress_revision,
        )
    except (FinalizationError, StorageError, RepositoryError, SchemaError) as exc:
        raise FinalizeCliError(str(exc)) from exc

    if args.json:
        print(json.dumps(fresh.snapshot, ensure_ascii=False, sort_keys=True))
    else:
        coverage = fresh.snapshot["review"]["summary"]["pass_coverage"]
        report_state = ", ".join(f"{path}={dispositions[path]}" for path in REPORT_PATHS)
        print(
            f"finalized books/{args.slug} progress={fresh.progress_revision} "
            f"pass={coverage['passed']}/{coverage['total']}"
        )
        print(report_state)
    return 0


def _adapt_errors(command: Callable[[argparse.Namespace], int], error_factory: ErrorFactory):
    def run(args: argparse.Namespace) -> int:
        try:
            return command(args)
        except FinalizeCliError as exc:
            raise error_factory(str(exc)) from exc

    return run


def register_finalize_command(
    subparsers: argparse._SubParsersAction,
    root: Path,
    *,
    preflight: Preflight,
    error_factory: ErrorFactory = FinalizeCliError,
) -> None:
    """Register the book-level finalize command exactly once."""

    if "finalize" in subparsers.choices:
        return
    parser = subparsers.add_parser(
        "finalize",
        help="Atomically finalize reviewed lifecycle state and completion reports.",
    )
    parser.add_argument("slug", help="Book slug under books/.")
    parser.add_argument(
        "--session-id",
        help="Optional finalizer session identity; defaults to a generated UUID.",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Emit deterministic machine-readable completion snapshot.",
    )
    parser.set_defaults(
        func=_adapt_errors(
            lambda args: finalize_command(args, root, preflight),
            error_factory,
        )
    )
