"""Argparse integration for read-only Workflow v2 status and resume."""

from __future__ import annotations

import argparse
import json
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import Any

from .filesystem import FilesystemStorage
from .repository import RepositoryError, WorkflowStateRepository
from .schemas import SchemaError
from .status import StatusError, StatusResolver
from .storage import StorageError


class StatusCliError(RuntimeError):
    """Expected status/resume command error suitable for Book Translator CLI output."""


Preflight = Callable[[str], tuple[Sequence[str], Mapping[str, Any]]]


def _book_directory(root: Path, slug: str) -> Path:
    if not isinstance(slug, str) or not slug or "/" in slug or "\\" in slug or slug in {".", ".."}:
        raise StatusCliError("book slug must be one directory name under books/")
    books_root = (root / "books").resolve(strict=False)
    book_dir = (books_root / slug).resolve(strict=False)
    try:
        book_dir.relative_to(books_root)
    except ValueError as exc:
        raise StatusCliError("book slug escapes books/") from exc
    if not book_dir.is_dir():
        raise StatusCliError(f"Book directory does not exist: books/{slug}")
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


def _resolver(root: Path, slug: str) -> StatusResolver:
    book_dir = _book_directory(root, slug)
    repository = WorkflowStateRepository(FilesystemStorage(book_dir))
    return StatusResolver(repository, artifact_reader=_artifact_reader(book_dir))


def _snapshot(args: argparse.Namespace, root: Path, preflight: Preflight) -> dict[str, Any]:
    resolver = _resolver(root, args.slug)
    structural_errors, corpus = preflight(args.slug)
    try:
        return resolver.status(structural_errors=structural_errors, corpus=corpus)
    except (StatusError, SchemaError, RepositoryError, StorageError) as exc:
        raise StatusCliError(str(exc)) from exc


def _print_json(payload: Mapping[str, Any]) -> None:
    print(json.dumps(payload, ensure_ascii=False, sort_keys=True))


def _counts(label: str, values: Mapping[str, Any]) -> str:
    return label + " " + " ".join(f"{key}={values[key]}" for key in sorted(values))


def status_command(args: argparse.Namespace, root: Path, preflight: Preflight) -> int:
    payload = _snapshot(args, root, preflight)
    if args.json:
        _print_json(payload)
        return 0

    state = "valid" if payload["valid"] else "invalid"
    print(f"{args.slug}: {state} workflow={payload.get('workflow_revision') or '-'}")
    print(_counts("lifecycle", payload["lifecycle"]))
    print(_counts("reviews", payload["reviews"]))
    print(f"claims={len(payload['claims'])} corpus={payload['corpus'].get('state', 'unknown')}")
    for error in payload["errors"]:
        print(f"ERROR: {error}")
    return 0


def resume_command(args: argparse.Namespace, root: Path, preflight: Preflight) -> int:
    status = _snapshot(args, root, preflight)
    resolver = _resolver(root, args.slug)
    try:
        payload = resolver.resume(status)
    except StatusError as exc:
        raise StatusCliError(str(exc)) from exc

    if args.json:
        _print_json(payload)
    elif payload["operation"] == "blocked":
        unit = payload.get("unit_id")
        suffix = f" unit={unit}" if unit else ""
        print(f"next=blocked reason={payload.get('reason')}{suffix}")
        for error in payload.get("errors", ()):
            print(f"ERROR: {error}")
    elif payload["operation"] == "complete":
        print("next=complete")
    else:
        print(
            f"next={payload['operation']} unit={payload['unit_id']} "
            f"chapter={payload['chapter_number']} role={payload['context']['role']}"
        )

    return 1 if payload["operation"] == "blocked" else 0


def register_status_commands(
    subparsers: argparse._SubParsersAction,
    root: Path,
    *,
    preflight: Preflight,
) -> None:
    """Register read-only status/resume commands on the main book.py parser."""

    status = subparsers.add_parser("status", help="Report repository-authoritative workflow status.")
    status.add_argument("slug", help="Book slug under books/.")
    status.add_argument("--json", action="store_true", help="Emit deterministic machine-readable JSON.")
    status.set_defaults(func=lambda args: status_command(args, root, preflight))

    resume = subparsers.add_parser("resume", help="Select the next valid operation without mutating state.")
    resume.add_argument("slug", help="Book slug under books/.")
    resume.add_argument("--json", action="store_true", help="Emit deterministic machine-readable JSON.")
    resume.set_defaults(func=lambda args: resume_command(args, root, preflight))
