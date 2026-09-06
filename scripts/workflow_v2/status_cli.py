"""Argparse integration for read-only Workflow v2 status and resume."""

from __future__ import annotations

import argparse
import importlib
import json
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import Any

from .filesystem import FilesystemStorage
from .repository import RepositoryError, WorkflowStateRepository
from .schemas import SchemaError
from .source_cli import normalize_structural_errors, register_source_overrides, source_storage_mode
from .status import StatusError, StatusResolver
from .storage import StorageError


class StatusCliError(RuntimeError):
    """Expected status/resume command error suitable for Book Translator CLI output."""


Preflight = Callable[[str], tuple[Sequence[str], Mapping[str, Any]]]
ErrorFactory = Callable[[str], Exception]


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


def _default_preflight(root: Path, slug: str) -> tuple[Sequence[str], Mapping[str, Any]]:
    """Reuse the existing structural and corpus validators without copying hash logic."""

    try:
        book_module = importlib.import_module("book")
        structural_errors, _ = book_module.validate_book(slug)
        book_dir, metadata, progress = book_module.load_book(slug)
        structural_errors = normalize_structural_errors(book_dir, metadata, structural_errors)
    except Exception as exc:
        raise StatusCliError(f"cannot run structural preflight: {exc}") from exc

    manifest_path = book_dir / "source-manifest.json"
    mode = source_storage_mode(metadata)
    if not manifest_path.is_file():
        if mode is not None:
            return structural_errors, {
                "state": "invalid",
                "storage_mode": mode,
                "error": "source-manifest.json is missing for explicit-source book",
            }
        return structural_errors, {"state": "unsealed"}

    try:
        corpus_module = importlib.import_module("corpus")
        manifest = corpus_module.load_source_manifest(book_dir)
        if manifest is None:
            if mode is not None:
                return structural_errors, {
                    "state": "invalid",
                    "storage_mode": mode,
                    "error": "source-manifest.json is missing for explicit-source book",
                }
            return structural_errors, {"state": "unsealed"}
        verified = corpus_module.verify_manifest(book_dir, metadata, progress, manifest)
    except Exception as exc:
        payload: dict[str, Any] = {"state": "invalid", "error": str(exc)}
        if mode is not None:
            payload["storage_mode"] = mode
        return structural_errors, payload

    return structural_errors, dict(verified)


def _snapshot(
    args: argparse.Namespace,
    root: Path,
    preflight: Preflight,
) -> dict[str, Any]:
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
    corpus = payload["corpus"]
    line = f"claims={len(payload['claims'])} corpus={corpus.get('state', 'unknown')}"
    mode = corpus.get("storage_mode")
    if mode:
        attached = corpus.get("source_attached")
        attached_text = "yes" if attached is True else "no" if attached is False else "unknown"
        line += f" source={mode} attached={attached_text}"
    print(line)
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


def _adapt_errors(command: Callable[[argparse.Namespace], int], error_factory: ErrorFactory):
    def run(args: argparse.Namespace) -> int:
        try:
            return command(args)
        except StatusCliError as exc:
            raise error_factory(str(exc)) from exc

    return run


def register_status_commands(
    subparsers: argparse._SubParsersAction,
    root: Path,
    *,
    preflight: Preflight | None = None,
    error_factory: ErrorFactory = StatusCliError,
) -> None:
    """Register explicit-source overrides plus read-only status/resume commands."""

    register_source_overrides(subparsers, root, error_factory=error_factory)
    resolved_preflight = preflight or (lambda slug: _default_preflight(root, slug))

    status = subparsers.add_parser("status", help="Report repository-authoritative workflow status.")
    status.add_argument("slug", help="Book slug under books/.")
    status.add_argument("--json", action="store_true", help="Emit deterministic machine-readable JSON.")
    status.set_defaults(
        func=_adapt_errors(
            lambda args: status_command(args, root, resolved_preflight),
            error_factory,
        )
    )

    resume = subparsers.add_parser("resume", help="Select the next valid operation without mutating state.")
    resume.add_argument("slug", help="Book slug under books/.")
    resume.add_argument("--json", action="store_true", help="Emit deterministic machine-readable JSON.")
    resume.set_defaults(
        func=_adapt_errors(
            lambda args: resume_command(args, root, resolved_preflight),
            error_factory,
        )
    )
