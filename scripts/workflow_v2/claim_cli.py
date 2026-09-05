"""Argparse integration for Workflow v2 durable claim coordination."""

from __future__ import annotations

import argparse
import json
import subprocess
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from .claims import ActiveClaim, ClaimError, ClaimLifecycleResult, ClaimManager
from .filesystem import FilesystemStorage
from .repository import RepositoryError, WorkflowStateRepository
from .schemas import SchemaError, SchemaKind
from .storage import StorageError


class ClaimCliError(RuntimeError):
    """Expected claim-command error suitable for Book Translator CLI output."""


def _book_directory(root: Path, slug: str) -> Path:
    if not isinstance(slug, str) or not slug or "/" in slug or "\\" in slug or slug in {".", ".."}:
        raise ClaimCliError("book slug must be one directory name under books/")
    books_root = (root / "books").resolve(strict=False)
    book_dir = (books_root / slug).resolve(strict=False)
    try:
        book_dir.relative_to(books_root)
    except ValueError as exc:
        raise ClaimCliError("book slug escapes books/") from exc
    if not book_dir.is_dir():
        raise ClaimCliError(f"Book directory does not exist: books/{slug}")
    return book_dir


def _repository(root: Path, slug: str) -> WorkflowStateRepository:
    return WorkflowStateRepository(FilesystemStorage(_book_directory(root, slug)))


def _load_progress(repository: WorkflowStateRepository) -> tuple[dict[str, Any], str]:
    try:
        loaded = repository.read("progress.json", SchemaKind.PROGRESS, allow_legacy=True)
    except (SchemaError, RepositoryError, StorageError) as exc:
        raise ClaimCliError(f"Invalid progress.json: {exc}") from exc
    return loaded.data, loaded.version


def _load_workflow_revision(repository: WorkflowStateRepository) -> str:
    try:
        metadata = repository.read("metadata.json", SchemaKind.METADATA, allow_legacy=True).data
    except (SchemaError, RepositoryError, StorageError) as exc:
        raise ClaimCliError(f"Invalid metadata.json: {exc}") from exc
    workflow = metadata.get("workflow")
    if not isinstance(workflow, Mapping):
        raise ClaimCliError("metadata workflow revision is unavailable; refusing to fabricate provenance")
    resolved = workflow.get("resolved_revision")
    requested = workflow.get("requested_ref")
    for candidate in (resolved, requested):
        if isinstance(candidate, str) and candidate.strip():
            return candidate
    raise ClaimCliError("metadata workflow revision is unavailable; refusing to fabricate provenance")


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


def _claim_json(claim: ActiveClaim) -> dict[str, Any]:
    return {**claim.data, "revision": claim.version}


def _result_json(result: ClaimLifecycleResult) -> dict[str, Any]:
    payload: dict[str, Any] = {"unit_id": result.unit_id, "status": result.status}
    if result.request_event_id is not None:
        payload["request_event_id"] = result.request_event_id
    if result.completion_event_id is not None:
        payload["completion_event_id"] = result.completion_event_id
    return payload


def _print_json(payload: Mapping[str, Any]) -> None:
    print(json.dumps(payload, ensure_ascii=False, sort_keys=True))


def claim_command(args: argparse.Namespace, root: Path) -> int:
    repository = _repository(root, args.slug)
    progress, progress_revision = _load_progress(repository)
    workflow_revision = _load_workflow_revision(repository)
    manager = ClaimManager(repository)
    base_commit = args.base_commit if args.base_commit is not None else _git_head(root)
    try:
        claims = manager.acquire(
            progress,
            args.selector,
            role=args.role,
            session_id=args.session_id,
            base_revision=progress_revision,
            base_commit=base_commit,
            workflow_revision=workflow_revision,
            lease_seconds=args.lease_seconds,
        )
    except (ClaimError, SchemaError, RepositoryError, StorageError) as exc:
        raise ClaimCliError(str(exc)) from exc

    if args.json:
        _print_json({"claims": [_claim_json(claim) for claim in claims]})
    else:
        for claim in claims:
            print(
                f"claimed {claim.data['unit_id']} as {claim.data['role']} "
                f"for session {claim.data['session_id']} until {claim.data['expires_at']}"
            )
    return 0


def claims_command(args: argparse.Namespace, root: Path) -> int:
    repository = _repository(root, args.slug)
    manager = ClaimManager(repository)
    try:
        claims = manager.list_active()
    except (ClaimError, SchemaError, RepositoryError, StorageError) as exc:
        raise ClaimCliError(str(exc)) from exc

    if args.json:
        _print_json({"claims": [_claim_json(claim) for claim in claims]})
    elif not claims:
        print("no active claims")
    else:
        for claim in claims:
            data = claim.data
            print(
                f"{data['unit_id']} {data['role']} session={data['session_id']} "
                f"expires={data['expires_at']}"
            )
    return 0


def release_command(args: argparse.Namespace, root: Path) -> int:
    repository = _repository(root, args.slug)
    progress, _ = _load_progress(repository)
    manager = ClaimManager(repository)
    try:
        results = manager.release(
            progress,
            args.selector,
            session_id=args.session_id,
        )
    except (ClaimError, SchemaError, RepositoryError, StorageError) as exc:
        raise ClaimCliError(str(exc)) from exc

    if args.json:
        _print_json({"results": [_result_json(result) for result in results]})
    else:
        for result in results:
            print(f"{result.status} {result.unit_id}")
    return 0


def cleanup_claims_command(args: argparse.Namespace, root: Path) -> int:
    repository = _repository(root, args.slug)
    manager = ClaimManager(repository)
    try:
        results = manager.cleanup_expired()
    except (ClaimError, SchemaError, RepositoryError, StorageError) as exc:
        raise ClaimCliError(str(exc)) from exc

    if args.json:
        _print_json({"results": [_result_json(result) for result in results]})
    else:
        for result in results:
            print(f"{result.status} {result.unit_id}")
    return 0


def register_claim_commands(subparsers: argparse._SubParsersAction, root: Path) -> None:
    """Register claim coordination subcommands on the main book.py parser."""

    claim = subparsers.add_parser("claim", help="Acquire a durable claim for one chapter or range.")
    claim.add_argument("slug", help="Book slug under books/.")
    claim.add_argument("selector", help="Chapter number or inclusive numeric range N-M.")
    claim.add_argument("--role", choices=("translator", "reviewer"), required=True)
    claim.add_argument("--session-id", required=True, help="Stable identity of the active worker/session.")
    claim.add_argument("--lease-seconds", type=int, default=3600, help="Lease duration in seconds (default: 3600).")
    claim.add_argument("--base-commit", help="Git commit associated with dispatch; best-effort HEAD when omitted.")
    claim.add_argument("--json", action="store_true", help="Emit deterministic machine-readable JSON.")
    claim.set_defaults(func=lambda args: claim_command(args, root))

    claims = subparsers.add_parser("claims", help="List active durable claims.")
    claims.add_argument("slug", help="Book slug under books/.")
    claims.add_argument("--json", action="store_true", help="Emit deterministic machine-readable JSON.")
    claims.set_defaults(func=lambda args: claims_command(args, root))

    release = subparsers.add_parser("release", help="Release claims owned by one session.")
    release.add_argument("slug", help="Book slug under books/.")
    release.add_argument("selector", help="Chapter number or inclusive numeric range N-M.")
    release.add_argument("--session-id", required=True, help="Owning session identity.")
    release.add_argument("--json", action="store_true", help="Emit deterministic machine-readable JSON.")
    release.set_defaults(func=lambda args: release_command(args, root))

    cleanup = subparsers.add_parser("cleanup-claims", help="Clean up expired claims with audit evidence.")
    cleanup.add_argument("slug", help="Book slug under books/.")
    cleanup.add_argument("--json", action="store_true", help="Emit deterministic machine-readable JSON.")
    cleanup.set_defaults(func=lambda args: cleanup_claims_command(args, root))
